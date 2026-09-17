"""Home Assistant specific health signal collection.

Host metrics (CPU, memory, disk) tell you a machine is busy. They do not tell
you the front door sensor stopped reporting four hours ago, that an integration
failed to set up after the last update, or that six battery devices are about
to die. Those are the things that generate support calls, and they are only
visible from inside Home Assistant.

Everything here reads in-memory state, so it is safe to call from the event
loop without executor offloading.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_BATTERY_CRITICAL_PERCENT,
    DEFAULT_BATTERY_LOW_PERCENT,
    DEFAULT_STALE_ENTITY_HOURS,
    HEALTH_ITEM_CAP,
    HEALTH_TYPE_DISABLED_AUTOMATION,
    HEALTH_TYPE_FAILED_INTEGRATION,
    HEALTH_TYPE_LOW_BATTERY,
    HEALTH_TYPE_STALE_ENTITY,
    HEALTH_TYPE_UNAVAILABLE_AUTOMATION,
    HEALTH_TYPE_UNAVAILABLE_ENTITY,
    STALE_CANDIDATE_DOMAINS,
)

_LOGGER = logging.getLogger(__name__)

# Config entry states that mean the integration is not working. NOT_LOADED is
# excluded deliberately -- it is the normal resting state for an entry the user
# disabled on purpose, and reporting it would train people to ignore this list.
_FAILED_ENTRY_STATES = frozenset(
    {
        ConfigEntryState.SETUP_ERROR,
        ConfigEntryState.SETUP_RETRY,
        ConfigEntryState.MIGRATION_ERROR,
        ConfigEntryState.FAILED_UNLOAD,
    }
)


def _last_reported(state: State):
    """Return when the entity last wrote its state.

    last_changed only moves when the *value* changes, so a thermostat sitting at
    20.0 all day looks stale even though it is reporting normally. last_reported
    moves on every write, which is what "has this thing gone quiet" actually
    means. Fall back for older cores that lack it.
    """
    return getattr(state, "last_reported", None) or state.last_updated


def _battery_percent(state: State) -> float | None:
    """Extract a battery percentage from a state, if it carries one."""
    if state.attributes.get("device_class") == "battery":
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    level = state.attributes.get("battery_level")
    if level is not None:
        try:
            return float(level)
        except (TypeError, ValueError):
            return None

    return None


def collect_health(
    hass: HomeAssistant,
    *,
    stale_hours: int = DEFAULT_STALE_ENTITY_HOURS,
    battery_low: int = DEFAULT_BATTERY_LOW_PERCENT,
    battery_critical: int = DEFAULT_BATTERY_CRITICAL_PERCENT,
) -> dict[str, Any]:
    """Build the health payload sent alongside metrics.

    Returns rollup counts plus per-item detail. The counts are computed over
    everything; the item list is capped, so the counts remain truthful even when
    the detail is truncated by a mass outage.
    """
    now = dt_util.utcnow()
    stale_cutoff = now - timedelta(hours=stale_hours)

    unavailable_items: list[dict[str, Any]] = []
    stale_items: list[dict[str, Any]] = []
    battery_items: list[dict[str, Any]] = []

    entities_total = 0
    entities_unavailable = 0
    entities_stale = 0
    batteries_low = 0
    batteries_critical = 0

    automations_total = 0
    automations_problem = 0
    automation_items: list[dict[str, Any]] = []

    for state in hass.states.async_all():
        domain = state.domain

        if domain == "automation":
            automations_total += 1
            if state.state == STATE_UNAVAILABLE:
                automations_problem += 1
                automation_items.append(
                    {
                        "type": HEALTH_TYPE_UNAVAILABLE_AUTOMATION,
                        "key": state.entity_id,
                        "label": state.name,
                        "severity": "critical",
                        "detail": {"last_triggered": _iso(state.attributes.get("last_triggered"))},
                    }
                )
            elif state.state == STATE_OFF:
                automations_problem += 1
                automation_items.append(
                    {
                        "type": HEALTH_TYPE_DISABLED_AUTOMATION,
                        "key": state.entity_id,
                        "label": state.name,
                        "severity": "info",
                        "detail": {"last_triggered": _iso(state.attributes.get("last_triggered"))},
                    }
                )
            continue

        entities_total += 1

        if state.state == STATE_UNAVAILABLE:
            entities_unavailable += 1
            unavailable_items.append(
                {
                    "type": HEALTH_TYPE_UNAVAILABLE_ENTITY,
                    "key": state.entity_id,
                    "label": state.name,
                    "severity": "warning",
                    "detail": {
                        "domain": domain,
                        "device_class": state.attributes.get("device_class"),
                    },
                }
            )
            # An unavailable entity is already reported; don't double-count it
            # as stale or as a dead battery.
            continue

        if state.state == STATE_UNKNOWN:
            continue

        battery = _battery_percent(state)
        if battery is not None:
            if battery <= battery_critical:
                batteries_critical += 1
                battery_items.append(
                    _battery_item(state, battery, "critical")
                )
            elif battery <= battery_low:
                batteries_low += 1
                battery_items.append(_battery_item(state, battery, "warning"))

        if domain in STALE_CANDIDATE_DOMAINS:
            reported_at = _last_reported(state)
            if reported_at is not None and reported_at < stale_cutoff:
                entities_stale += 1
                stale_items.append(
                    {
                        "type": HEALTH_TYPE_STALE_ENTITY,
                        "key": state.entity_id,
                        "label": state.name,
                        "severity": "warning",
                        "detail": {
                            "domain": domain,
                            "last_reported": _iso(reported_at),
                            "threshold_hours": stale_hours,
                        },
                    }
                )

    integrations_total, integrations_failed, integration_items = _collect_integrations(hass)

    # Ordered most to least actionable, because the cap truncates from the end.
    items = (
        integration_items
        + [i for i in automation_items if i["type"] == HEALTH_TYPE_UNAVAILABLE_AUTOMATION]
        + [i for i in battery_items if i["severity"] == "critical"]
        + unavailable_items
        + [i for i in battery_items if i["severity"] != "critical"]
        + stale_items
        + [i for i in automation_items if i["type"] == HEALTH_TYPE_DISABLED_AUTOMATION]
    )

    if len(items) > HEALTH_ITEM_CAP:
        _LOGGER.debug(
            "Health item detail truncated from %s to %s; rollup counts are unaffected",
            len(items),
            HEALTH_ITEM_CAP,
        )
        items = items[:HEALTH_ITEM_CAP]

    return {
        "entities": {
            "total": entities_total,
            "unavailable": entities_unavailable,
            "stale": entities_stale,
        },
        "integrations": {
            "total": integrations_total,
            "failed": integrations_failed,
        },
        "batteries": {
            "low": batteries_low,
            "critical": batteries_critical,
        },
        "automations": {
            "total": automations_total,
            "problem": automations_problem,
        },
        "items": items,
    }


def _battery_item(state: State, percent: float, severity: str) -> dict[str, Any]:
    """Build a low battery health item."""
    return {
        "type": HEALTH_TYPE_LOW_BATTERY,
        "key": state.entity_id,
        "label": state.name,
        "severity": severity,
        "detail": {"battery_percent": percent},
    }


def _collect_integrations(hass: HomeAssistant) -> tuple[int, int, list[dict[str, Any]]]:
    """Count config entries and describe the ones that are failing."""
    total = 0
    failed = 0
    items: list[dict[str, Any]] = []

    for entry in hass.config_entries.async_entries():
        if entry.disabled_by is not None:
            continue

        total += 1

        if entry.state not in _FAILED_ENTRY_STATES:
            continue

        failed += 1
        items.append(
            {
                "type": HEALTH_TYPE_FAILED_INTEGRATION,
                "key": entry.entry_id,
                "label": entry.title or entry.domain,
                "severity": "critical",
                "detail": {
                    "domain": entry.domain,
                    "state": str(entry.state),
                    "reason": entry.reason,
                },
            }
        )

    return total, failed, items


def _iso(value: Any) -> str | None:
    """Best-effort ISO 8601 rendering for timestamps that may be str or datetime."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return value.isoformat()
    except AttributeError:
        return None
