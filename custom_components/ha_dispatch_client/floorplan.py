"""Floor-plan deploy: render pipeline output, turned into a Lovelace dashboard.

A technician uploads a floor-plan sketch to HA Dispatch. A render pipeline
there (out of scope here, and built by a different agent) turns it into one
top-down PNG plan per floor with an exact feet->pixel transform, a
hotspots.json describing each room as a polygon, and a ha_map.json placing
devices in those rooms. This module is the last leg: notice the job, pull
those assets down, and turn them into a "Home 3D" Lovelace dashboard -- one
view per floor -- a homeowner can actually tap on. The pipeline's 3D model
and angled hero render are for the installer's preview on the Dispatch side
and are not part of a deploy.

Rooms are the interaction, not devices: each floor is drawn by this
integration's own custom card (www/ayvra-room-plan.js), which turns every
hotspots.json room into a tappable polygon and opens a modal holding that
room's controls -- the product's primary UI metaphor. So a device's resolved
entity is grouped into its room rather than pinned to a coordinate, and
build_room_plan_card's docstring is the authoritative description of the
config that card expects.

Modelled on updates.py's polling shape, not tunnel.py's live-request relay: a
floorplan deploy is a multi-step background job (download, write files, build
a dashboard, confirm), not a request Dispatch is waiting on synchronously.

ha_map.json's device slugs (e.g. `thermostat.great_room.01`) are invented by
the render pipeline from an insurance estimate line item -- they are HA-style
slugs, not real Home Assistant entity ids. Every device is resolved against
this installation's actual entities by domain, by the reading its device
class asks for (and never from an outdoor sensor, for a room inside the
house), then by the room name against the entity's name and the Area it is
filed under (see async_match_entity); anything with no confident match is
left off the dashboard rather than fabricated onto it, per the rule: device
icons bound to real entities where a matching entity exists, skip the ones
that don't.

That automatic placement is, in the customer's own words, "weak at BEST"
until there is a definitive inventory per project, so an installer standing
in the house can correct it: see async_plan_devices for the correction layer
that sits on top of the matcher, and HADispatchFloorplan's own store for why
a correction has to survive a redeploy, which rebuilds the whole dashboard
from freshly downloaded manifests.
"""
from __future__ import annotations

import inspect
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .api_client import FloorplanUnavailable
from .const import (
    DOMAIN,
    FLOORPLAN_ASSET_DIR,
    FLOORPLAN_BACKPLATE_NAME,
    FLOORPLAN_CARD_FILENAME,
    FLOORPLAN_CARD_SOURCE_DIR,
    FLOORPLAN_CARD_TYPE,
    FLOORPLAN_DASHBOARD_ICON,
    FLOORPLAN_DASHBOARD_STORAGE_KEY,
    FLOORPLAN_DASHBOARD_TITLE,
    FLOORPLAN_DASHBOARD_URL_PATH,
    FLOORPLAN_DASHBOARDS_STORAGE_KEY,
    FLOORPLAN_DEVICE_CLASS_BY_CLS,
    FLOORPLAN_HA_MAP_NAME,
    FLOORPLAN_HOTSPOTS_NAME,
    FLOORPLAN_LABEL_MIN_SF,
    FLOORPLAN_LOCAL_URL_PREFIX,
    FLOORPLAN_NOTIFICATION_ID,
    FLOORPLAN_OUTDOOR_DOMAINS,
    FLOORPLAN_OUTDOOR_TOKENS,
    FLOORPLAN_OVERRIDE_DROP_NO_DEVICE,
    FLOORPLAN_OVERRIDE_DROP_NO_ROOM,
    FLOORPLAN_OVERRIDES_STORAGE_KEY,
    FLOORPLAN_OVERRIDES_STORAGE_VERSION,
    FLOORPLAN_PLAN_BACKPLATE_NAME_FMT,
    FLOORPLAN_PLAN_TRANSFORM_NAME,
    FLOORPLAN_REASON_NO_ROOM_ON_PLAN,
    FLOORPLAN_REASON_OVERRIDE_DROPPED,
    FLOORPLAN_REASON_OVERRIDE_ENTITY,
    FLOORPLAN_REASON_OVERRIDE_ROOM,
    FLOORPLAN_REPORT_STATUS_DONE,
    FLOORPLAN_REPORT_STATUS_FAILED,
    FLOORPLAN_ROTATION_KEY,
    FLOORPLAN_ROTATIONS,
    FLOORPLAN_RESOURCE_ID,
    FLOORPLAN_RESOURCE_TYPE,
    FLOORPLAN_RESOURCES_STORAGE_KEY,
    FLOORPLAN_STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

# Errors any of the steps below may raise when a payload is not shaped the way
# it is expected to be, or a filesystem call fails. None of them are worth
# crashing the poll loop over -- the run reports "failed" and moves on. Same
# family updates.py and components.py already treat as soft.
_SOFT_ERRORS = (AttributeError, KeyError, TypeError, ValueError, OSError)


class FloorplanApplyError(Exception):
    """A deploy job could not be turned into a working dashboard.

    Distinct from a transport error talking to Dispatch: this means the
    download, the manifests, or the dashboard write itself failed, and it is
    reported to the server as a failed job.
    """


# --- entity matching -----------------------------------------------------


def _tokens(text: Any) -> set[str]:
    """Lowercase word tokens, for a cheap bag-of-words match.

    Good enough to tell "Den" apart from "Primary Bathroom" without pulling
    in a fuzzy-matching dependency this integration does not otherwise need.
    """
    return {token for token in re.split(r"[^a-z0-9]+", str(text or "").lower()) if token}


def area_tokens_by_entity(hass: HomeAssistant) -> Dict[str, set[str]]:
    """Each registered entity's Area name, as word tokens, keyed by entity id.

    The first real deploy (Paul's house) put no device icons on the plan at
    all: his entities are named after the hardware ("Hue lamp 3"), not the
    room, so a friendly-name match against the estimate's room names had
    nothing to bite on. The Area a homeowner files an entity under is a far
    better room signal than its name, and most installs have Areas because
    the onboarding wizard pushes them.

    An entity inherits its device's Area when it has none of its own, exactly
    as the frontend presents it. Every registry lookup here is best-effort:
    an older core where a helper is shaped differently, or a test host with
    no registries at all, yields an empty map and the matcher falls back to
    names alone -- the behaviour this integration shipped with.
    """
    try:
        from homeassistant.helpers import area_registry, device_registry, entity_registry
    except ImportError:
        return {}

    try:
        areas = area_registry.async_get(hass)
        devices = device_registry.async_get(hass)
        entities = entity_registry.async_get(hass)
    except Exception as err:  # noqa: BLE001 - best-effort signal, see docstring
        _LOGGER.debug("Area registry unavailable for floorplan matching: %s", err)
        return {}

    def area_name(area_id: Any) -> Optional[str]:
        if not area_id:
            return None
        getter = getattr(areas, "async_get_area", None)
        area = getter(area_id) if getter is not None else None
        return getattr(area, "name", None)

    result: Dict[str, set[str]] = {}
    try:
        for entity_id, entry in dict(getattr(entities, "entities", {}) or {}).items():
            name = area_name(getattr(entry, "area_id", None))
            if name is None and getattr(entry, "device_id", None):
                getter = getattr(devices, "async_get", None)
                device = getter(entry.device_id) if getter is not None else None
                name = area_name(getattr(device, "area_id", None))
            if name:
                result[str(entity_id)] = _tokens(name)
    except _SOFT_ERRORS as err:
        _LOGGER.debug("Could not read the entity registry for floorplan matching: %s", err)
        return {}

    return result


# How many candidate entity ids a decision record keeps. Enough to tell the
# installer what the house actually has in that domain; bounded so a house
# with two hundred sensors does not turn one report into a dump.
_DECISION_CANDIDATES = 10


def _state_device_class(state: Any) -> str:
    """A candidate entity's own `device_class`, as the rest of this
    integration reads a state attribute (health.py's battery and problem
    classification), or "" when it declares none.

    Best-effort on the attribute container too: a state object from an older
    core, or a fake in a test host, may not carry one at all.
    """
    attributes = getattr(state, "attributes", None) or {}
    try:
        return str(attributes.get("device_class") or "")
    except AttributeError:
        return ""


def _required_device_class(device: Dict[str, Any]) -> str:
    """The HA `device_class` a candidate must declare to satisfy this
    ha_map.json device, or "" when its class does not pin one down.

    This is what makes `cls` a real filter rather than a decoration: it rode
    all the way through the pipeline into ha_map.json and was then used only
    to pick an icon, so a room's temperature readout would bind to any
    sensor in the house that shared a room word -- a humidity sensor, a power
    meter, or the one outdoor thermometer. See FLOORPLAN_DEVICE_CLASS_BY_CLS.
    """
    return FLOORPLAN_DEVICE_CLASS_BY_CLS.get(str(device.get("cls") or "").strip(), "")


def _outdoor_marker(state: Any, area_tokens: Dict[str, set[str]]) -> Optional[str]:
    """Why this entity reads as outdoor-derived, or None if it does not.

    A per-room reading must never be satisfied from outside the house: the
    reported symptom was exactly this, indoor temperature displays showing
    the weather integration's outdoor value. The signal is the entity's
    domain, or an outdoor word in its entity id, its friendly name, or the
    Area the homeowner filed it under (see FLOORPLAN_OUTDOOR_TOKENS). The
    marker is returned rather than a bare True so the deploy report can say
    which word did it.
    """
    entity_id = str(getattr(state, "entity_id", "") or "")
    domain = entity_id.split(".", 1)[0]
    if domain in FLOORPLAN_OUTDOOR_DOMAINS:
        return domain

    tokens = (
        _tokens(entity_id)
        | _tokens(getattr(state, "name", None))
        | area_tokens.get(entity_id, set())
    )
    hits = sorted(tokens & FLOORPLAN_OUTDOOR_TOKENS)
    return hits[0] if hits else None


# Sentinel for _decide's `matched`: "the matcher's own conclusion is the
# binding, nobody corrected it". Distinct from None, which is a real
# conclusion ("the matcher found nothing").
_SAME_AS_BOUND = object()


def _decide(
    decisions: Optional[List[Dict[str, Any]]],
    device: Dict[str, Any],
    bound: Optional[str],
    reason: str,
    *,
    via: Optional[str] = None,
    candidates: Any = (),
    rejected: Any = (),
    matched: Any = _SAME_AS_BOUND,
    override: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Record one device's match outcome, and hand `bound` straight back.

    The record is what the deploy report carries to Dispatch, so that "why
    does the plan show no icons" can be answered from the installer's own
    screen rather than from a log on a house nobody can reach. Candidates
    are the entity ids this house actually has in the device's domain --
    which, across a whole report, is the entity list the matcher was
    working from. `rejected` says which of those candidates were ruled out
    before the room words were ever scored, and why -- "a wrong number" and
    "no number at all" look identical on a plan otherwise.

    `matched` and `override` keep the two layers apart. The automatic
    placement is weak enough that an installer standing in the house has to
    be able to correct it (see async_plan_devices), and a report that cannot
    say which records are the matcher's own conclusions and which a human
    overruled is a report nobody can learn from: `matched` is always what
    the matcher concluded, `bound` is what the dashboard actually shows, and
    `override` is the correction that separated them (None when nobody
    corrected anything, which is the overwhelming majority of records).
    """
    if decisions is not None:
        decisions.append(
            {
                "entity": str(device.get("entity") or ""),
                "label": str(device.get("source_line") or device.get("entity") or ""),
                "room": str(device.get("room") or ""),
                "floor": str(device.get("floor_key") or device.get("floor") or ""),
                "domain": str(device.get("domain") or ""),
                "cls": str(device.get("cls") or ""),
                "bound": bound,
                "reason": reason,
                "via": via,
                "candidates": [
                    str(getattr(c, "entity_id", c)) for c in list(candidates)[:_DECISION_CANDIDATES]
                ],
                "rejected": list(rejected)[:_DECISION_CANDIDATES],
                "matched": bound if matched is _SAME_AS_BOUND else matched,
                "override": override,
            }
        )
    return bound


def async_match_entity(
    hass: HomeAssistant,
    device: Dict[str, Any],
    area_tokens: Optional[Dict[str, set[str]]] = None,
    decisions: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    """Resolve one ha_map.json device record to a real entity, or None.

    ha_map.json's `entity` field (e.g. `thermostat.great_room.01`) is a slug
    the render pipeline invented from an insurance estimate line item -- it is
    not a Home Assistant entity id and must never be used as one. What this
    installation can actually confirm is the device's likely HA domain
    (already normalised into the `domain` field, e.g. "climate"), the kind of
    thing it is (`cls`, e.g. "vent" -- a room temperature readout), and which
    room it is in. So the candidate list is filtered by domain, then -- for a
    device whose class pins down a reading (see _required_device_class) -- by
    the candidate's own `device_class` and by whether that reading comes from
    outdoors, and only what survives is scored on how many of the room
    name's words show up in a candidate's friendly name, entity id, or --
    when `area_tokens` (see area_tokens_by_entity) knows it -- the Area the
    homeowner filed it under.

    Eligibility, before either rule is applied, for a device asking for a
    specific reading:

    0a. A candidate whose own `device_class` is not the one asked for is not
        a candidate. One that declares none at all is not either: the plan
        would be showing a number nobody has said is a temperature.
    0b. A candidate that reads from outdoors (see _outdoor_marker) can never
        satisfy an indoor room's display. This is the reported symptom: a
        house whose only temperature sensor is the weather integration's got
        that outdoor value shown as every room's indoor temperature.

    Then two rules decide a match, in order:

    1. A domain with exactly one eligible entity in the whole house binds it
       regardless of room. The estimate lists four thermostats; the house has
       one `climate` entity; that entity is the thermostat, whichever room
       the adjuster wrote it against. Room words cannot make this more
       certain and must not be allowed to make it less. "Eligible" is what
       keeps that reasoning from spreading: a `climate` entity declares no
       reading, so the thermostat case is untouched, while a per-room
       temperature readout can only ever reach this rule via a candidate
       that already passed 0a and 0b.
    2. Otherwise the candidate sharing the most words with the room name
       wins, needing at least one. No shared word, no match: the device is
       skipped on the dashboard, never guessed.

    Nothing here ever falls back to a near-enough entity. A room with no
    indoor reading to show has its display left off the dashboard rather
    than relabelled or filled with the outdoor value.

    Candidates come from hass.states.async_all(), the enumeration idiom the
    rest of this integration uses (health.py, updates.py). Every decision is
    logged at INFO with its reason and, when `decisions` is given, recorded
    there for the deploy report (see _decide) -- the first real house showed
    no icons and nobody could reach its log to learn why. The report keeps
    the house's whole in-domain entity list, plus why each ineligible one was
    ruled out.
    """
    label = f"{device.get('entity') or '?'} in {device.get('room') or '?'}"
    domain = str(device.get("domain") or "").strip()
    if not domain:
        _LOGGER.info("Floorplan device %s skipped: no domain", label)
        return _decide(decisions, device, None, "no_domain")

    lister = getattr(getattr(hass, "states", None), "async_all", None)
    if lister is None:
        return _decide(decisions, device, None, "unavailable")

    try:
        candidates = list(lister(domain))
    except _SOFT_ERRORS as err:
        _LOGGER.debug("Could not enumerate %s entities: %s", domain, err)
        return _decide(decisions, device, None, "unavailable")

    if not candidates:
        _LOGGER.info("Floorplan device %s skipped: no %s entities exist", label, domain)
        return _decide(decisions, device, None, "no_entities")

    # `candidates` stays the house's full in-domain list for the report (it
    # is the installer's only view of what the house has); `eligible` is what
    # may actually be bound.
    wanted_class = _required_device_class(device)
    eligible = candidates
    rejected: List[Dict[str, Any]] = []
    if wanted_class:
        if area_tokens is None:
            area_tokens = area_tokens_by_entity(hass)
        eligible = []
        for state in candidates:
            entity_id = str(getattr(state, "entity_id", "") or "")
            declared = _state_device_class(state)
            if declared != wanted_class:
                rejected.append(
                    {
                        "entity": entity_id,
                        "why": "wrong_device_class",
                        "wanted": wanted_class,
                        "declared": declared,
                    }
                )
                continue
            marker = _outdoor_marker(state, area_tokens)
            if marker is not None:
                rejected.append(
                    {"entity": entity_id, "why": "outdoor", "marker": marker}
                )
                continue
            eligible.append(state)

        if not eligible:
            outdoor_only = any(r["why"] == "outdoor" for r in rejected)
            reason = "outdoor_only" if outdoor_only else "no_device_class_match"
            _LOGGER.info(
                "Floorplan device %s skipped: none of %d %s entities is an indoor "
                "%s reading (%s)",
                label, len(candidates), domain, wanted_class,
                ", ".join(f"{r['entity']}={r['why']}" for r in rejected[:8]),
            )
            return _decide(
                decisions, device, None, reason,
                candidates=candidates, rejected=rejected,
            )

    if len(eligible) == 1:
        only = getattr(eligible[0], "entity_id", None)
        _LOGGER.info(
            "Floorplan device %s bound to %s: the only eligible %s entity",
            label, only, domain,
        )
        return _decide(
            decisions, device, only, "only_entity",
            candidates=candidates, rejected=rejected,
        )

    room_tokens = _tokens(device.get("room"))
    if not room_tokens:
        _LOGGER.info("Floorplan device %s skipped: no room name to match on", label)
        return _decide(
            decisions, device, None, "no_room", candidates=candidates, rejected=rejected
        )

    if area_tokens is None:
        area_tokens = area_tokens_by_entity(hass)

    best_entity: Optional[str] = None
    best_score = 0
    best_via = ""
    for state in eligible:
        entity_id = getattr(state, "entity_id", None)
        name_tokens = _tokens(getattr(state, "name", None)) | _tokens(entity_id)
        area = area_tokens.get(str(entity_id), set())
        score = len(room_tokens & (name_tokens | area))
        if score > best_score:
            best_score = score
            best_entity = entity_id
            best_via = "area" if room_tokens & area else "name"

    if best_score > 0:
        _LOGGER.info(
            "Floorplan device %s bound to %s: shares %d room word(s) by %s",
            label, best_entity, best_score, best_via,
        )
        return _decide(
            decisions, device, best_entity, "room_word", via=best_via,
            candidates=candidates, rejected=rejected,
        )

    _LOGGER.info(
        "Floorplan device %s skipped: none of %d %s entities share a room word (%s)",
        label, len(eligible), domain,
        ", ".join(str(getattr(s, "entity_id", "?")) for s in eligible[:8]),
    )
    return _decide(
        decisions, device, None, "no_shared_word",
        candidates=candidates, rejected=rejected,
    )


# --- installer corrections, applied on top of matching ----------------------

# This module's own annotation on a device record: the outcome of planning
# that device -- the matcher's conclusion, the correction (if any) laid over
# it, and the record fields the deploy report wants. Prefixed and stripped
# from anything arriving in ha_map.json so a manifest can never forge one.
_PLAN = "_ha_dispatch_plan"


def normalise_override(value: Any) -> Optional[Dict[str, Any]]:
    """One stored correction, in the shape the whole system agreed on, or
    None if there is nothing usable in it.

    {"room": <room name as hotspots.json spells it>,
     "floor_key": <level key>,
     "bound_entity": <real HA entity id, or None>}

    Tolerant on input because this is read back from disk and also handed
    straight in from a service call: anything missing becomes None rather
    than raising, and a record naming neither a room, a floor nor an entity
    is not a correction at all.
    """
    if not isinstance(value, dict):
        return None
    room = str(value.get("room") or "").strip()
    floor_key = str(value.get("floor_key") or "").strip()
    bound = value.get("bound_entity")
    bound = str(bound).strip() if bound else ""
    if not room and not floor_key and not bound:
        return None
    return {
        "room": room or None,
        "floor_key": floor_key or None,
        "bound_entity": bound or None,
    }


def normalise_overrides(value: Any) -> Dict[str, Dict[str, Any]]:
    """Every stored correction, keyed by ha_map.json's device slug.

    Keyed by the slug and nothing else: a redeploy's manifest may reorder
    its devices and may bind a device to a different entity, and a
    correction keyed on either of those would be lost exactly when it
    matters most.
    """
    if not isinstance(value, dict):
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    for slug, raw in value.items():
        key = str(slug or "").strip()
        override = normalise_override(raw)
        if key and override is not None:
            result[key] = override
    return result


def _rooms_by_floor(
    hotspots_by_floor: Dict[str, List[Dict[str, Any]]]
) -> Dict[str, Dict[frozenset, Dict[str, Any]]]:
    """Each floor's rooms, indexed by the word-token set of their names.

    Tokens rather than the raw string: a correction's room name is typed by
    an installer or picked from a list that has been through a round trip,
    so "Great Room", "great room" and "Great  Room" have to be the same
    room. The same tokeniser the matcher uses, for the same reason.
    """
    index: Dict[str, Dict[frozenset, Dict[str, Any]]] = {}
    for floor_key, rooms in (hotspots_by_floor or {}).items():
        if not isinstance(rooms, list):
            continue
        by_name: Dict[frozenset, Dict[str, Any]] = {}
        for room in rooms:
            if not isinstance(room, dict):
                continue
            key = frozenset(_tokens(room.get("name")))
            if key and key not in by_name:
                by_name[key] = room
        index[str(floor_key)] = by_name
    return index


def _find_room(
    rooms_by_floor: Dict[str, Dict[frozenset, Dict[str, Any]]],
    room: str,
    floor_key: str,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """The (floor key, hotspot) a correction's room names on this plan.

    A correction that names a floor is looked up only on that floor; one
    that names only a room is looked up across the plan, because a room
    name is unique enough in a house that an installer who typed one meant
    the one room that has it.
    """
    key = frozenset(_tokens(room))
    if not key:
        return None
    floors = (
        [str(floor_key)] if floor_key and str(floor_key) in rooms_by_floor
        else list(rooms_by_floor)
    )
    for candidate in floors:
        hotspot = rooms_by_floor.get(candidate, {}).get(key)
        if hotspot is not None:
            return candidate, hotspot
    return None


def _match_record(
    hass: HomeAssistant, device: Dict[str, Any], area_tokens: Dict[str, set[str]]
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Run the matcher over one device and hand back both its conclusion and
    the record it would have written -- candidates, rejections and all.

    Goes through async_match_entity untouched, with a scratch decision list,
    rather than reaching inside it: the correction layer must not change what
    the matcher concludes, only what is done with it afterwards.
    """
    scratch: List[Dict[str, Any]] = []
    bound = async_match_entity(hass, device, area_tokens, scratch)
    return bound, (scratch[0] if scratch else {})


def _plan_from_record(bound: Optional[str], record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "bound": bound,
        "matched": bound,
        "reason": str(record.get("reason") or ""),
        "via": record.get("via"),
        "candidates": list(record.get("candidates") or ()),
        "rejected": list(record.get("rejected") or ()),
        "override": None,
    }


def async_plan_devices(
    hass: HomeAssistant,
    ha_map: Dict[str, Any],
    hotspots_by_floor: Dict[str, List[Dict[str, Any]]],
    overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    area_tokens: Optional[Dict[str, set[str]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Resolve every device in ha_map.json, then lay the installer's own
    corrections over the result. Returns (devices, dropped corrections).

    Two layers, deliberately not one. The matcher runs first and unchanged
    (async_match_entity), so what it concluded is still on the record; a
    correction is then applied on top, and the device's plan annotation
    carries both -- `matched` is the matcher's conclusion and `bound` is what
    the dashboard will actually show. Folding corrections into the matcher
    would make the deploy report unable to say which placements a human had
    to fix, which is the one thing the installer's screen needs to show while
    the automatic placement is as weak as it currently is.

    A correction that names a room moves the device onto that room's floor
    and re-anchors it to that room's centroid, then re-runs the matcher
    against the corrected room -- the installer's room is better information
    than the estimate's, so it deserves the same matching rules rather than
    a different weaker path. A correction that pins an entity id skips
    matching for that device entirely: a human naming an entity outranks
    every rule here, and is also the only thing that can place a device the
    matcher could not resolve at all (an unmatched device is still omitted
    rather than fabricated onto the plan).

    `dropped` is every correction this manifest has moved past: its device
    slug is no longer in ha_map.json, or the room it names is no longer on
    the plan. They are reported with the deploy and pruned from the store --
    a correction that cannot be applied and cannot be seen is how this
    becomes unmaintainable.
    """
    devices_in = [d for d in (ha_map.get("devices") or []) if isinstance(d, dict)]
    if area_tokens is None:
        area_tokens = area_tokens_by_entity(hass)
    corrections = normalise_overrides(overrides)
    rooms_by_floor = _rooms_by_floor(hotspots_by_floor)

    planned: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    seen_slugs: set[str] = set()

    for raw in devices_in:
        device = {k: v for k, v in raw.items() if k != _PLAN}
        slug = str(device.get("entity") or "")
        seen_slugs.add(slug)

        bound, record = _match_record(hass, device, area_tokens)
        plan = _plan_from_record(bound, record)

        correction = corrections.get(slug) if slug else None
        if correction is not None:
            device, plan = _apply_override(
                hass, device, plan, correction, rooms_by_floor, area_tokens, dropped
            )

        device[_PLAN] = plan
        planned.append(device)

    for slug, correction in corrections.items():
        if slug not in seen_slugs:
            _LOGGER.warning(
                "Dropping the installer's correction for %s: this plan no longer "
                "has that device",
                slug,
            )
            dropped.append(
                {
                    "entity": slug,
                    "why": FLOORPLAN_OVERRIDE_DROP_NO_DEVICE,
                    "override": dict(correction),
                }
            )

    return planned, dropped


def _apply_override(
    hass: HomeAssistant,
    device: Dict[str, Any],
    plan: Dict[str, Any],
    correction: Dict[str, Any],
    rooms_by_floor: Dict[str, Dict[frozenset, Dict[str, Any]]],
    area_tokens: Dict[str, set[str]],
    dropped: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Lay one correction over one already-matched device.

    Hands back the corrected device record and its plan annotation, or the
    originals untouched when the correction names a room this plan no longer
    has -- in which case the whole correction goes, entity pin included, the
    matcher's own conclusion stands, and the correction is added to
    `dropped`. All of it rather than the room alone: honouring a pin whose
    room has gone would draw that entity in whichever room the estimate
    guessed, which is a confidently wrong answer where reverting is merely
    the old one, said out loud.
    """
    slug = str(device.get("entity") or "")
    room = str(correction.get("room") or "").strip()
    floor_key = str(correction.get("floor_key") or "").strip()
    pinned = str(correction.get("bound_entity") or "").strip()

    applied = dict(correction)
    applied["was_room"] = str(device.get("room") or "")
    applied["was_floor_key"] = str(device.get("floor_key") or "")

    corrected = dict(device)
    if room:
        found = _find_room(rooms_by_floor, room, floor_key)
        if found is None:
            _LOGGER.warning(
                "Dropping the installer's correction for %s: this plan has no room "
                "called %s",
                slug, room,
            )
            dropped.append(
                {
                    "entity": slug,
                    "why": FLOORPLAN_OVERRIDE_DROP_NO_ROOM,
                    "override": applied,
                }
            )
            return device, plan
        found_floor, hotspot = found
        corrected["room"] = str(hotspot.get("name") or room)
        corrected["floor_key"] = found_floor
        centroid = hotspot.get("centroid")
        if isinstance(centroid, (list, tuple)) and len(centroid) >= 2:
            # A device belongs to a room, not to a coordinate: re-anchor it to
            # the room it was corrected into, or it would render in the room
            # the estimate guessed while claiming to be in this one.
            corrected["xy_ft"] = [float(centroid[0]), float(centroid[1])]
    elif floor_key:
        # A floor with no room named: the device keeps its own coordinates
        # (every level's transform maps the same world frame) and its own
        # room label, and only moves to another floor's view.
        corrected["floor_key"] = floor_key

    if pinned:
        _LOGGER.info(
            "Floorplan device %s bound to %s: the installer pinned it (the matcher "
            "had concluded %s)",
            slug, pinned, plan.get("matched"),
        )
        plan = dict(
            plan,
            bound=pinned,
            reason=FLOORPLAN_REASON_OVERRIDE_ENTITY,
            via="pinned_entity",
            override=applied,
        )
        return corrected, plan

    bound, record = _match_record(hass, corrected, area_tokens)
    _LOGGER.info(
        "Floorplan device %s re-matched in %s after the installer moved it: %s "
        "(the matcher had concluded %s)",
        slug, corrected.get("room"), bound, plan.get("matched"),
    )
    plan = dict(
        plan,
        bound=bound,
        reason=FLOORPLAN_REASON_OVERRIDE_ROOM,
        via=record.get("via"),
        candidates=list(record.get("candidates") or ()),
        rejected=list(record.get("rejected") or ()),
        override=applied,
    )
    return corrected, plan


def dropped_override_decisions(
    dropped: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Dropped corrections, in the deploy report's own device-record shape.

    Carried on the same list the matcher's decisions ride, because the
    installer who made the correction reads that list: a correction that
    vanished without a word looks exactly like one that was never made.
    """
    records: List[Dict[str, Any]] = []
    for entry in dropped:
        override = dict(entry.get("override") or {})
        override["dropped"] = str(entry.get("why") or "")
        records.append(
            {
                "entity": str(entry.get("entity") or ""),
                "label": str(entry.get("entity") or ""),
                "room": str(override.get("room") or ""),
                "floor": str(override.get("floor_key") or ""),
                "domain": "",
                "cls": "",
                "bound": None,
                "reason": FLOORPLAN_REASON_OVERRIDE_DROPPED,
                "via": str(entry.get("why") or ""),
                "candidates": [],
                "rejected": [],
                "matched": None,
                "override": override,
            }
        )
    return records


# --- coordinate mapping ----------------------------------------------------


def _bounding_box(
    hotspots_by_floor: Dict[str, List[Dict[str, Any]]]
) -> Optional[Tuple[float, float, float, float]]:
    """The (min_x, min_y, max_x, max_y) covering every hotspot point.

    Used to rescale the render pipeline's top-down plan coordinates (feet,
    origin at the building's own centroid -- see
    pauls-house-3d/pipeline/build.py) into the 0-100% space a Lovelace
    picture-elements card positions its elements in.
    """
    xs: List[float] = []
    ys: List[float] = []
    for rooms in hotspots_by_floor.values():
        if not isinstance(rooms, list):
            continue
        for room in rooms:
            if not isinstance(room, dict):
                continue
            for point in room.get("poly_ft") or []:
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    xs.append(point[0])
                    ys.append(point[1])
            centroid = room.get("centroid")
            if isinstance(centroid, (list, tuple)) and len(centroid) >= 2:
                xs.append(centroid[0])
                ys.append(centroid[1])

    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _project(
    x: float, y: float, bounds: Tuple[float, float, float, float]
) -> Tuple[float, float]:
    """Map a plan-space (x, y) in feet onto (left%, top%) on the card.

    Fallback only, used when a deploy job carries no plan_transform.json (an
    older render, or a level whose exact top-down plan render did not come
    through) -- see _project_exact for the normal path. This linearly
    rescales the plan's own bounding box to 0-100%, which keeps points
    consistent with each other but not precisely with whatever backplate
    image is actually underneath.

    Y is flipped on the assumption that a larger plan-y is "further back" in
    a north-up plan and should read toward the top of the card. Not verified
    against the pipeline's actual axis convention.
    """
    min_x, min_y, max_x, max_y = bounds
    width = (max_x - min_x) or 1.0
    height = (max_y - min_y) or 1.0

    left = (x - min_x) / width * 100
    top = (max_y - y) / height * 100

    # Padded so a hotspot sitting exactly on the bounding edge is not drawn
    # literally under the card's border.
    left = min(97.0, max(3.0, left))
    top = min(97.0, max(3.0, top))
    return round(left, 1), round(top, 1)


def _project_exact(x: float, y: float, transform: Dict[str, Any]) -> Tuple[float, float]:
    """Map a plan-space (x, y) in feet onto (left%, top%) using the render
    pipeline's own exact affine feet->pixel transform (plan_transform.json;
    see pauls-house-3d/pipeline/build.py's _fit_transform).

    pixel_x = a*x + b*y + c ; pixel_y = d*x + e*y + f -- solved pipeline-side
    from the real top-down orthographic camera, so this lands precisely on
    the plan backplate underneath rather than approximating it.
    """
    width = float(transform.get("image_width_px") or 0) or 1.0
    height = float(transform.get("image_height_px") or 0) or 1.0
    px = transform.get("a", 0.0) * x + transform.get("b", 0.0) * y + transform.get("c", 0.0)
    py = transform.get("d", 0.0) * x + transform.get("e", 0.0) * y + transform.get("f", 0.0)
    left = min(99.0, max(1.0, px / width * 100))
    top = min(99.0, max(1.0, py / height * 100))
    return round(left, 1), round(top, 1)


# --- view rotation ------------------------------------------------------------


def plan_rotation(transform: Optional[Dict[str, Any]]) -> int:
    """This floor's DEFAULT view rotation, in degrees clockwise.

    Read out of the level's own plan_transform.json entry under
    FLOORPLAN_ROTATION_KEY ("plan_rotation_deg"), which ha-dispatch writes at
    import. Why it exists, in the customer's own words: "when an installer
    mounts an ipad or tablet (there will be one per floor) they need to be
    able to walk up to it and configure and make sure that their orientation
    matches where they are. This will help them not need to mentally flip an
    image in their head."

    This is only the default the panel starts from. The installer standing at
    the tablet steps it with the card's own rotate control and that choice is
    remembered per panel in the browser, so one house's three tablets can all
    be oriented differently off one deploy -- which is the whole requirement,
    and why rotation is never baked into the geometry here.

    Only exactly 0, 90, 180 or 270 is honoured, per the cross-repo contract.
    Everything else is 0: absent (every plan deployed before ha-dispatch grew
    the field), a string, null, 45, 450, -90, True, NaN. Deliberately strict
    rather than clever -- quietly reinterpreting -90 as 270 would make this
    client and the server disagree about what the file says, and a plan that
    silently renders at an angle nobody chose is worse than one that renders
    the way it always did.
    """
    if not isinstance(transform, dict):
        return 0
    value = transform.get(FLOORPLAN_ROTATION_KEY)
    if value is None:
        return 0
    # bool is an int subclass in Python, and True == 1 is not a rotation.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _LOGGER.warning(
            "Ignoring a non-numeric %s (%r); the plan renders unrotated",
            FLOORPLAN_ROTATION_KEY,
            value,
        )
        return 0
    if isinstance(value, float) and not value.is_integer():
        _LOGGER.warning(
            "Ignoring a fractional %s (%r); only 0/90/180/270 are legal",
            FLOORPLAN_ROTATION_KEY,
            value,
        )
        return 0
    degrees = int(value)
    if degrees not in FLOORPLAN_ROTATIONS:
        _LOGGER.warning(
            "Ignoring an out-of-contract %s (%r); only 0/90/180/270 are legal",
            FLOORPLAN_ROTATION_KEY,
            value,
        )
        return 0
    return degrees


def rotate_percent_point(
    left: float, top: float, degrees: Any
) -> Tuple[float, float]:
    """Rotate one (left%, top%) point clockwise by `degrees` within the card's
    own 0-100 percent space. THE NORMATIVE DEFINITION of what this feature's
    rotation means, in one place, in four lines.

    Percent-of-the-backplate is the one coordinate space in which a quarter
    turn needs no knowledge of the image's aspect ratio at all: a point at
    (l%, t%) of a w-by-h plan is the same physical spot as ((100-t)%, l%) of
    that plan turned a quarter clockwise, whose box is h-by-w. That identity
    is why the card can rotate the backplate, the room polygons, the labels
    and the tap targets as ONE transform on ONE element and have them stay
    registered with each other -- and it is what this function pins down.

    It is a specification, not a step in the deploy: nothing in the render
    path calls it, because the browser does the rotating (see
    www/ayvra-room-plan.js, which cites this function by name) and the card
    config always carries unrotated geometry. It is here, tested, and
    exported so that the contract shared by this repo, the card and the
    ha-dispatch importer has exactly one executable answer to "which way is
    clockwise, and where does the top-left corner end up" -- the question a
    second implementation gets wrong silently.

    Anything other than 90/180/270 -- including a malformed value that got
    past plan_rotation -- is treated as no rotation.
    """
    if degrees == 90:
        return (100.0 - top, left)
    if degrees == 180:
        return (100.0 - left, 100.0 - top)
    if degrees == 270:
        return (top, 100.0 - left)
    return (left, top)


# --- device icons, shown inside a room's controls -----------------------------

# Finer-grained than domain: ha_map.json's `cls` field is the render
# pipeline's own device class (devices.py's CATALOG), invented from the
# estimate's line-item description -- e.g. "light_recessed" and
# "light_chandelier" are both HA domain "light" but should not look
# identical on the plan. Checked first; `domain` is the fallback for a
# device whose `cls` is missing (an older manifest) or not in this map.
_ICON_BY_CLS: Dict[str, str] = {
    "thermostat": "mdi:thermostat",
    "camera": "mdi:cctv",
    "motion": "mdi:motion-sensor",
    "keypad": "mdi:dialpad",
    "alarm_panel": "mdi:shield-home",
    "smoke": "mdi:smoke-detector-variant",
    "blind": "mdi:blinds",
    "speaker": "mdi:speaker",
    "fan": "mdi:fan",
    "exhaust_fan": "mdi:fan",
    "light_recessed": "mdi:lightbulb-spot",
    "light_chandelier": "mdi:chandelier",
    "light": "mdi:lightbulb",
    "vent": "mdi:air-filter",
    "garage_door": "mdi:garage",
    "doorbell": "mdi:doorbell-video",
}

_ICON_BY_DOMAIN: Dict[str, str] = {
    "light": "mdi:lightbulb",
    "climate": "mdi:thermostat",
    "camera": "mdi:cctv",
    "lock": "mdi:lock",
    "switch": "mdi:toggle-switch",
    "fan": "mdi:fan",
    "cover": "mdi:window-shutter",
    "media_player": "mdi:speaker",
    "alarm_control_panel": "mdi:shield-home",
    "binary_sensor": "mdi:motion-sensor",
    "sensor": "mdi:gauge",
}


def _domain_icon(device: Dict[str, Any]) -> Optional[str]:
    """A domain-appropriate MDI icon for a device, or None to let the
    frontend's own entity/domain default stand. Every device reaching this
    already has a real bound entity (async_match_entity filtered out
    anything unmatched before this is called), so this is purely cosmetic --
    it just guarantees a light reads as a bulb and a thermostat reads as a
    thermostat instead of whichever generic glyph that entity happens to
    carry. The icon is shown on the device's row inside its room's modal;
    nothing is drawn loose on the plan any more.
    """
    icon = _ICON_BY_CLS.get(str(device.get("cls") or ""))
    if icon:
        return icon
    return _ICON_BY_DOMAIN.get(str(device.get("domain") or ""))


# --- card building ----------------------------------------------------------


_SUBCELL_SUFFIX = re.compile(r"\s*#\s*\d+\s*$")


def room_display_name(name: Any) -> str:
    """What a homeowner should see for a room the sketch calls `name`.

    The sketch numbers a room's sub-cells -- "Master Bathroom #4", "Foyer #7",
    "Garage Side Stairs #2" -- because a surveyor needs to tell the toilet
    closet from the vanity. A homeowner standing at a wall panel does not, and
    "#4" in front of them reads as a defect in the product.

    This strips that suffix for DISPLAY ONLY. The room's `name` is untouched
    and stays the join key: a device's room and a hotspot's room are matched on
    the name's word tokens (see build_room_plan_card), an installer's
    correction names a room by it, and the pipeline keys on it too. Renaming
    the key to make it prettier is exactly the move this product forbids
    elsewhere -- a display name is renameable, so it can never be an
    identifier.
    """
    return _SUBCELL_SUFFIX.sub("", str(name or "").strip()).strip() or str(name or "").strip()


def _room_area(room: Dict[str, Any]) -> Optional[float]:
    """A room's floor area in square feet, or None when the manifest has none.

    None is not zero, and the difference matters: an older manifest with no
    `sf` must not have every one of its labels hidden as "too small". Unknown
    area means the label stays.
    """
    try:
        area = float(room.get("sf"))
    except (TypeError, ValueError):
        return None
    return area if area > 0 else None


def assign_room_labels(
    entries: List[Dict[str, Any]], areas: List[Optional[float]]
) -> None:
    """Decide, in place, which rooms carry a name chip and which are sub-cells.

    Runs AFTER devices have been placed, because both decisions turn on
    whether a room ended up with any controls.

    Two rules, in this order:

    1. **Sub-cells fold into their room.** Several cells on one floor whose
       display names agree are one room the sketch happened to cut up. The
       member carrying the controls is the room; the rest lose their chip and
       their taps open the room's own panel (`same_room_as`), because tapping
       any corner of the master bathroom should open the master bathroom, not
       an empty panel that claims the same name. A sub-cell that has controls
       of its OWN is never folded away -- that would put its devices somewhere
       no tap could reach them -- so it keeps its chip and its panel even
       though a second chip reads the same.

    2. **Small and empty goes quiet.** A room under FLOORPLAN_LABEL_MIN_SF
       with no controls is drawn without a chip. It stays tappable: a room
       without a label is not a room without controls, it is a room whose name
       was not worth the ink.

    Every room keeps its polygon either way. Nothing here removes a tap target.
    """
    groups: Dict[frozenset, List[int]] = {}
    for index, entry in enumerate(entries):
        entry["display"] = room_display_name(entry["name"])
        entry["show_label"] = True
        entry["same_room_as"] = None
        key = frozenset(_tokens(entry["display"]))
        if key:
            groups.setdefault(key, []).append(index)

    for members in groups.values():
        if len(members) < 2:
            continue
        # The room is whichever cell holds the controls; area breaks a tie, so
        # a split with no devices anywhere still folds into its largest part.
        primary = max(
            members,
            key=lambda i: (len(entries[i]["entities"]), areas[i] or 0.0),
        )
        for index in members:
            if index == primary or entries[index]["entities"]:
                continue
            entries[index]["show_label"] = False
            entries[index]["same_room_as"] = primary

    for index, entry in enumerate(entries):
        if entry["entities"] or entry["same_room_as"] is not None:
            continue
        area = areas[index]
        if area is not None and area < FLOORPLAN_LABEL_MIN_SF:
            entry["show_label"] = False


def build_room_plan_card(
    hass: HomeAssistant,
    rooms: List[Dict[str, Any]],
    devices: List[Dict[str, Any]],
    image_url: str,
    transform: Optional[Dict[str, Any]] = None,
    area_tokens: Optional[Dict[str, set[str]]] = None,
    decisions: Optional[List[Dict[str, Any]]] = None,
    floor_key: str = "",
    floor_name: str = "",
    rotation: int = 0,
    dashboard_path: str = FLOORPLAN_DASHBOARD_URL_PATH,
) -> Dict[str, Any]:
    """Build one floor's room-first card config: the plan backplate, every
    room as a tappable polygon, and each room's own inventory inside it.

    THE CARD'S CONFIG CONTRACT. This is the authoritative description of the
    shape www/ayvra-room-plan.js's setConfig() expects; that file's header
    repeats it, and the two must be changed together or the dashboard renders
    a card that silently ignores half its config:

        {
          "type": "custom:ayvra-room-plan",
          "image": "/local/ha_dispatch/floorplan/plan_Floor_1.png",
          "aspect_ratio": "400:300",        # w:h of the backplate image
          "floor_key": "Floor_1",           # hotspots.json's own key
          "floor_name": "1st Floor",        # the human floor name
          "rotation": 90,                   # 0/90/180/270 clockwise, the
          #   DEFAULT this panel starts at; plan_transform.json's own
          #   "plan_rotation_deg" for this floor. See plan_rotation().
          "dashboard_path": "home-3d",      # the dashboard's url_path, which
          #   the card needs only to key its per-panel rotation override in
          #   localStorage ("ayvra.room-plan.rotation.<path>.<floor_key>").
          #   Passed rather than hardcoded in the JS so the two cannot drift.
          "rooms": [
            {
              "name": "Kitchen",            # the JOIN KEY: matched against a
              #   device's room and an installer's correction. Never shown.
              "display": "Kitchen",         # what the homeowner reads. The
              #   same thing with the sketch's sub-cell number stripped
              #   ("Master Bathroom #4" -> "Master Bathroom"). Display only --
              #   see room_display_name().
              "show_label": True,           # draw a name chip for this room.
              #   False for a folded sub-cell, and for a small room with no
              #   controls. The room is STILL TAPPABLE either way.
              "same_room_as": None,         # index of the room this cell is
              #   part of, when the sketch cut one room into several. A tap
              #   here opens THAT room. None for a room in its own right.
              "poly": [[left, top], ...],   # percent of the backplate
              "label": [left, top],         # percent, the room name anchor
              "entities": [                 # may be empty, and often is
                {"entity": "light.kitchen_spot",
                 "name": "Light fixture - Kitchen",   # optional: the
                 #   estimate's own line item, shown beside the entity's
                 #   friendly name. Absent when the estimate had none; the
                 #   card then shows the entity's name alone.
                 "icon": "mdi:lightbulb-spot"},       # optional
              ],
            },
          ],
        }

    Rooms, not devices, are what the plan draws: the room metaphor is the
    primary one in this product ("tapping on the kitchen area opens a box
    that then will offer the inventory controls"), so there are no loose
    device icons scattered over the plan any more. A device's controls live
    inside its room's modal, which is also why a device needs no coordinates
    of its own -- `xy_ft` placed an icon, and nothing places an icon now. What
    a device needs is a room that exists on this floor's plan; one whose room
    does not is recorded as FLOORPLAN_REASON_NO_ROOM_ON_PLAN and left off,
    same spirit as an unmatched entity being skipped rather than fabricated.

    Coordinates go through the same projection the old picture-elements card
    used: `transform`, when given, is that floor's exact plan_transform.json
    entry, so feet land on the backplate's own pixels; without one (an older
    deploy job) this falls back to rescaling the floor's hotspot bounding box.
    Polygons are projected point by point, so a tap lands inside the room's
    real shape rather than inside its bounding box.

    `rotation` is carried, never applied. The geometry in this config is
    always in the backplate's own un-rotated frame, because the backplate is
    a PNG this client cannot turn: pre-rotating the polygons here would slide
    every room off the picture underneath them, which is precisely the bug
    this feature could introduce. The card turns the image, the polygons, the
    labels and the tap targets together as one transform, starting from this
    default and honouring whatever the installer last set at that panel --
    see rotate_percent_point() for what a quarter turn means.

    Every room renders and every room taps, including one with nothing in it
    -- the card says so in as many words. A room that is invisible because it
    happens to be empty is a room the installer cannot check.
    """
    bounds: Optional[Tuple[float, float, float, float]] = None
    if transform is None:
        bounds = _bounding_box({"_": rooms})
        if bounds is None:
            _LOGGER.warning(
                "Floorplan floor carried no usable coordinates; card has no rooms"
            )
            return {
                "type": FLOORPLAN_CARD_TYPE,
                "image": image_url,
                "aspect_ratio": "8:5",
                "floor_key": floor_key,
                "floor_name": floor_name,
                "rotation": int(rotation) if rotation in FLOORPLAN_ROTATIONS else 0,
                "dashboard_path": dashboard_path,
                "rooms": [],
            }

    def project(x: float, y: float) -> Tuple[float, float]:
        if transform is not None:
            return _project_exact(x, y, transform)
        assert bounds is not None
        return _project(x, y, bounds)

    entries: List[Dict[str, Any]] = []
    # Parallel to `entries`: each room's real floor area, kept out of the
    # emitted config because the card has no use for it -- only the label
    # pass below does.
    areas: List[Optional[float]] = []
    # Keyed on the room name's word tokens, the same way a correction's room
    # name is looked up (_rooms_by_floor): a device's room and a hotspot's
    # room come from the same pipeline but have both been through a round
    # trip, and "Great Room" must not miss "great room". Two rooms sharing a
    # name on one floor both render; their devices all land in the first,
    # because nothing in either manifest can tell them apart.
    by_name: Dict[frozenset, Dict[str, Any]] = {}

    for room in rooms:
        if not isinstance(room, dict):
            continue
        name = room.get("name")
        if not name:
            continue
        poly: List[List[float]] = []
        for point in room.get("poly_ft") or []:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                left, top = project(float(point[0]), float(point[1]))
                poly.append([left, top])
        label: Optional[List[float]] = None
        centroid = room.get("centroid")
        if isinstance(centroid, (list, tuple)) and len(centroid) >= 2:
            left, top = project(float(centroid[0]), float(centroid[1]))
            label = [left, top]
        if len(poly) < 3 and label is None:
            # Neither a shape nor an anchor: there is nothing to draw and
            # nothing to tap, so this is not a room on a plan.
            _LOGGER.warning(
                "Floorplan room %s has no usable geometry; leaving it off the plan",
                name,
            )
            continue

        entry: Dict[str, Any] = {
            "name": str(name),
            "poly": poly,
            "label": label,
            "entities": [],
        }
        entries.append(entry)
        areas.append(_room_area(room))
        key = frozenset(_tokens(name))
        if key and key not in by_name:
            by_name[key] = entry

    for device in devices:
        if not isinstance(device, dict):
            continue

        # A device that has already been through the planning layer (see
        # async_plan_devices) carries its outcome with it: the matcher has
        # run, and an installer's correction may have overruled it. Matching
        # it again here would re-run the matcher against the corrected room
        # and quietly discard the correction. A device that has not been
        # planned -- an older caller, or a test exercising the matcher
        # directly -- is matched here exactly as before.
        plan = device.get(_PLAN)
        plan = plan if isinstance(plan, dict) else None

        room_entry = by_name.get(frozenset(_tokens(device.get("room"))))
        if room_entry is None:
            # The room this device claims is not on this floor's plan, so
            # there is no room modal to put it in. Recorded with whatever the
            # matcher had concluded, so the report can still say what it
            # would have bound.
            _decide(
                decisions,
                device,
                None,
                FLOORPLAN_REASON_NO_ROOM_ON_PLAN,
                matched=(plan or {}).get("matched"),
                override=(plan or {}).get("override"),
            )
            continue

        if plan is not None:
            entity_id = _decide(
                decisions,
                device,
                plan.get("bound"),
                str(plan.get("reason") or ""),
                via=plan.get("via"),
                candidates=plan.get("candidates") or (),
                rejected=plan.get("rejected") or (),
                matched=plan.get("matched"),
                override=plan.get("override"),
            )
        else:
            entity_id = async_match_entity(hass, device, area_tokens, decisions)
        if entity_id is None:
            continue

        # Two estimate line items can resolve to the same real entity (four
        # recessed lights, one `light` group). The report keeps both records;
        # the room's control list carries one row, because two identical rows
        # are two chances to be confused by the same switch.
        if any(e["entity"] == entity_id for e in room_entry["entities"]):
            continue

        control: Dict[str, Any] = {"entity": entity_id}
        # The estimate's own line item ("Heat/AC register - Kitchen"), which
        # is what the installer is holding, shown beside the entity's real
        # friendly name. Never the ha_map slug: it is not a Home Assistant
        # entity id and must not be put in front of anyone as though it were.
        source_line = str(device.get("source_line") or "").strip()
        if source_line:
            control["name"] = source_line
        icon = _domain_icon(device)
        if icon:
            control["icon"] = icon
        room_entry["entities"].append(control)

    aspect_ratio = "8:5"
    if transform is not None:
        w, h = transform.get("image_width_px"), transform.get("image_height_px")
        if w and h:
            aspect_ratio = f"{int(w)}:{int(h)}"


    assign_room_labels(entries, areas)
    return {
        "type": FLOORPLAN_CARD_TYPE,
        "image": image_url,
        "aspect_ratio": aspect_ratio,
        "floor_key": floor_key,
        "floor_name": floor_name,
        # Clamped here as well as at the source: build_room_plan_card is
        # public and a caller that has not been through plan_rotation must
        # not be able to put 45 in front of the card.
        "rotation": int(rotation) if rotation in FLOORPLAN_ROTATIONS else 0,
        "dashboard_path": dashboard_path,
        "rooms": entries,
    }


# --- the card asset and its Lovelace resource --------------------------------


def card_resource_url(version: str) -> str:
    """The card's Lovelace resource URL, stamped with `version`.

    The stamp is the whole point. Home Assistant caches frontend resources as
    hard as it caches manifest.json and strings.json (see the project's
    CLAUDE.md), so a redeploy that re-registers the same bare URL leaves every
    browser -- and every wall tablet, which never gets reloaded by hand --
    running the card it downloaded months ago. The version the client is
    actually running is the one thing that changes exactly when the card's
    code does, so it is what the query string carries.

    An empty version yields the bare URL rather than `?v=`: a stamp that
    never changes is worse than none, because it reads as a stamp.
    """
    base = f"{FLOORPLAN_LOCAL_URL_PREFIX}/{FLOORPLAN_CARD_FILENAME}"
    stamp = str(version or "").strip()
    return f"{base}?v={stamp}" if stamp else base


def _same_resource(url: Any, other: Any) -> bool:
    """Whether two resource URLs name the same file, ignoring the version.

    What stops a redeploy leaving a trail of resource rows behind it: the URL
    changes on every version, and a row matched on the full URL would never be
    recognised as the one already there.
    """
    return str(url or "").split("?", 1)[0] == str(other or "").split("?", 1)[0]


def _copy_card(source: Path, dest: Path) -> int:
    """Copy the card beside the plan images; return the copy's mtime.

    It ships inside the integration, which Home Assistant does not serve over
    HTTP; /config/www (i.e. /local/...) is the one directory it does, and is
    already where the plan renders live. The mtime is a last-resort cache
    stamp for an installation that cannot tell us its own version.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    return int(dest.stat().st_mtime)


def _read_manifest_version(path: Path) -> str:
    """The `version` out of the integration's own manifest.json, or "".

    Read off disk rather than from a constant for the same reason
    updater.py's async_installed_version is: after a self-update swaps the
    files, the manifest on disk is the truth about what is running -- and it
    is what the card's cache stamp has to follow.
    """
    with open(path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict):
        return ""
    return str(manifest.get("version") or "")


def _floor_title(key: str, transform: Optional[Dict[str, Any]]) -> str:
    """A human floor name: the level name the render pipeline wrote into
    plan_transform.json ("1st Floor"), or the key made readable ("Floor 1")
    for a transform from before it carried one."""
    name = (transform or {}).get("name") if isinstance(transform, dict) else None
    if name:
        return str(name)
    return re.sub(r"[_\s]+", " ", str(key)).strip() or str(key)


def _ordered_floor_keys(
    hotspots_by_floor: Dict[str, List[Dict[str, Any]]], plan_transform: Dict[str, Any]
) -> List[str]:
    """Floors lowest first, by the elevation plan_transform.json carries for
    each; a floor with no known elevation keeps hotspots.json's own order,
    after the ones that have one."""
    keys = [k for k, v in hotspots_by_floor.items() if isinstance(v, list)]

    def sort_key(item: Tuple[int, str]) -> Tuple[int, float, int]:
        index, key = item
        entry = plan_transform.get(key) if isinstance(plan_transform, dict) else None
        elev = entry.get("elev") if isinstance(entry, dict) else None
        if isinstance(elev, (int, float)):
            return (0, float(elev), index)
        return (1, 0.0, index)

    return [key for _, key in sorted(enumerate(keys), key=sort_key)]


def build_dashboard_views(
    hass: HomeAssistant,
    hotspots_by_floor: Dict[str, List[Dict[str, Any]]],
    ha_map: Dict[str, Any],
    plan_image_urls: Dict[str, str],
    plan_transform: Dict[str, Any],
    fallback_image_url: Optional[str] = None,
    decisions: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """One Lovelace view per floor, lowest floor first: a panel view holding
    that floor's top-down plan card with its room labels and device icons.

    A floor per view is what makes the dashboard usable on the wall: the
    homeowner taps a floor tab instead of scrolling a stack, and each plan
    fills the screen. There is no 3D hero card any more -- see CHANGELOG
    1.7.4; the GLB and the angled render are no longer part of a deploy.

    Devices are assigned to a floor by ha_map.json's `floor_key` (matches
    hotspots.json's own keys exactly) when present. An older manifest that
    predates `floor_key` only carries a human label ("1st Floor") that will
    not match a hotspots key directly; with exactly one floor that mismatch
    does not matter, so that case falls back to "every device belongs to the
    one floor there is" rather than silently dropping every device.

    `fallback_image_url` is the legacy hero backplate, used only as a floor's
    image when that floor has no plan render of its own (a deploy job from
    before per-floor plans).

    Each floor's view rotation default comes from its own plan_transform.json
    entry (plan_rotation); a floor whose entry predates the field, or carries
    something that is not one of 0/90/180/270, renders unrotated exactly as
    it always did. The default is all this side decides -- the installer at
    the panel has the last word, per panel.

    `decisions`, when given, collects one record per device in ha_map.json
    -- bound, or skipped with the reason -- for the deploy report. A device
    on a floor that has no view, or on no known floor at all, is recorded
    here as well, so the report accounts for every device the estimate
    listed.
    """
    devices = [d for d in (ha_map.get("devices") or []) if isinstance(d, dict)]
    floor_keys = _ordered_floor_keys(hotspots_by_floor, plan_transform)
    single_floor = len(floor_keys) == 1
    area_tokens = area_tokens_by_entity(hass)
    considered: List[Dict[str, Any]] = []

    views: List[Dict[str, Any]] = []
    for key in floor_keys:
        rooms = hotspots_by_floor.get(key) or []
        image_url = plan_image_urls.get(key) or fallback_image_url
        if not image_url:
            _LOGGER.warning("No backplate available for floor %s; skipping its view", key)
            continue

        floor_devices = devices if single_floor else [
            d for d in devices if d.get("floor_key") == key
        ]
        considered.extend(floor_devices)

        transform = plan_transform.get(key) if isinstance(plan_transform, dict) else None
        title = _floor_title(key, transform)
        card = build_room_plan_card(
            hass,
            rooms,
            floor_devices,
            image_url,
            transform,
            area_tokens,
            decisions,
            floor_key=key,
            floor_name=title,
            rotation=plan_rotation(transform),
            dashboard_path=FLOORPLAN_DASHBOARD_URL_PATH,
        )
        number = len(views) + 1
        views.append(
            {
                "title": title,
                "path": f"floor-{number}",
                "icon": f"mdi:home-floor-{number}" if number <= 3 else "mdi:floor-plan",
                "panel": True,
                "cards": [card],
            }
        )

    if decisions is not None:
        for device in devices:
            if not any(device is seen for seen in considered):
                _decide(decisions, device, None, "no_floor")

    return views


def build_dashboard_config(views: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The dashboard's views config, as Lovelace storage mode expects it.

    Every view is a panel view -- full width, no dashboard chrome --
    tablet-first: one plan card fills the screen instead of sharing it with
    sidebar/header furniture. The dashboard's own url_path is unchanged, so
    the live registration in _async_write_dashboard_live, which keys on it
    alone, neither knows nor cares how many views are inside.
    """
    return {
        "title": FLOORPLAN_DASHBOARD_TITLE,
        "views": list(views),
    }


# --- dashboard storage -------------------------------------------------------


async def _async_load_or_default(store: Store, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        loaded = await store.async_load()
    except (OSError, ValueError) as err:
        _LOGGER.warning("Could not read %s: %s", getattr(store, "key", store), err)
        return dict(default)
    return loaded if isinstance(loaded, dict) else dict(default)


# --- filesystem helpers, run in the executor --------------------------------


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _discover_assets(directory: Path) -> Dict[str, Any]:
    """The assets the last deploy left on disk, in _async_download_assets'
    own result shape.

    So a correction can redraw the dashboard without the server: the
    filenames are fixed by const.py, and a per-floor plan render carries its
    own level key in its name (FLOORPLAN_PLAN_BACKPLATE_NAME_FMT), which is
    the only thing that has to be recovered rather than assumed.
    """
    paths: Dict[str, Any] = {"image_url": None, "plan_image_urls": {}}
    if not directory.is_dir():
        return paths

    for name, key in (
        (FLOORPLAN_HOTSPOTS_NAME, "hotspots"),
        (FLOORPLAN_HA_MAP_NAME, "ha_map"),
        (FLOORPLAN_PLAN_TRANSFORM_NAME, "plan_transform"),
    ):
        candidate = directory / name
        if candidate.is_file():
            paths[key] = candidate

    if (directory / FLOORPLAN_BACKPLATE_NAME).is_file():
        paths["image_url"] = f"{FLOORPLAN_LOCAL_URL_PREFIX}/{FLOORPLAN_BACKPLATE_NAME}"

    prefix, suffix = FLOORPLAN_PLAN_BACKPLATE_NAME_FMT.split("{level_key}", 1)
    plan_image_urls: Dict[str, str] = {}
    for entry in sorted(directory.iterdir()):
        name = entry.name
        if not entry.is_file() or not name.startswith(prefix) or not name.endswith(suffix):
            continue
        level_key = name[len(prefix):len(name) - len(suffix)]
        if level_key:
            plan_image_urls[level_key] = f"{FLOORPLAN_LOCAL_URL_PREFIX}/{name}"
    paths["plan_image_urls"] = plan_image_urls

    return paths


# --- orchestration ------------------------------------------------------------


class HADispatchFloorplan:
    """Polls for a pending floorplan deploy, applies it, and reports back."""

    def __init__(self, hass: HomeAssistant, coordinator) -> None:
        """Initialise. Nothing touches the network until the first poll."""
        self.hass = hass
        self.coordinator = coordinator
        self.available = True

        # Job ids currently being applied, so a repeated poll never starts the
        # same job twice. No persisted "pending" record the way updates.py
        # keeps one -- unlike a Core/OS update, applying a deploy never
        # restarts Home Assistant out from under the run, so there is nothing
        # here that has to survive a restart to be confirmed.
        self._running: set[str] = set()

        # The two Lovelace storage files this writes -- see
        # _async_write_dashboard for why this goes straight to storage rather
        # than through hass.data["lovelace"]'s live collections.
        self._dashboards_store = Store(
            hass, FLOORPLAN_STORAGE_VERSION, FLOORPLAN_DASHBOARDS_STORAGE_KEY
        )
        self._dashboard_store = Store(
            hass, FLOORPLAN_STORAGE_VERSION, FLOORPLAN_DASHBOARD_STORAGE_KEY
        )
        # Lovelace's own resource registry -- the list that decides whether a
        # browser loads the room-plan card at all. Same fallback shape as the
        # two above: the live collection first, this file only if that is
        # unreachable.
        self._resources_store = Store(
            hass, FLOORPLAN_STORAGE_VERSION, FLOORPLAN_RESOURCES_STORAGE_KEY
        )

        # The installer's corrections to the pipeline's automatic placement,
        # in this integration's own store rather than Lovelace's: a redeploy
        # overwrites the dashboard config wholesale, so a correction kept in
        # there would last exactly until the next deploy, which is the one
        # moment it has to survive. Loaded lazily (async_load, or the first
        # deploy) -- nothing here touches the disk at construction time.
        self._overrides_store = Store(
            hass, FLOORPLAN_OVERRIDES_STORAGE_VERSION, FLOORPLAN_OVERRIDES_STORAGE_KEY
        )
        self._overrides: Optional[Dict[str, Dict[str, Any]]] = None

    @property
    def api(self):
        """The API client, which the coordinator may have re-tokened."""
        return self.coordinator.api_client

    @property
    def installation_id(self):
        """The installation id, which re-enrolment may have changed."""
        return self.coordinator.installation_id

    def async_disable(self) -> None:
        """Stop trying: this server does not offer floorplan deploys."""
        self.available = False

    # -- the installer's corrections ------------------------------------------

    async def async_load(self) -> None:
        """Read the stored corrections, so the first deploy already has them."""
        await self._async_overrides()

    async def _async_overrides(self) -> Dict[str, Dict[str, Any]]:
        """The stored corrections, read from disk once and then cached.

        Lazy rather than required: a deploy that arrives before setup has
        called async_load must still apply the corrections the installer
        already made, and the only thing worse than a correction that does
        not survive a redeploy is one that survives it intermittently.
        """
        if self._overrides is None:
            loaded = await _async_load_or_default(self._overrides_store, {})
            self._overrides = normalise_overrides(loaded.get("overrides"))
        return self._overrides

    async def _async_save_overrides(self) -> None:
        await self._overrides_store.async_save(
            {"overrides": dict(self._overrides or {})}
        )

    async def async_set_override(
        self,
        slug: str,
        room: Optional[str] = None,
        floor_key: Optional[str] = None,
        bound_entity: Optional[str] = None,
    ) -> bool:
        """Record where an installer says a device actually is.

        Merged into whatever correction that device already had, so moving a
        device and then pinning its entity takes two calls rather than
        forcing the second to restate the first. Keyed by ha_map.json's
        device slug -- the one identifier a redeploy cannot change.
        """
        key = str(slug or "").strip()
        if not key:
            return False
        overrides = await self._async_overrides()
        merged = dict(overrides.get(key) or {})
        if room is not None:
            merged["room"] = room
        if floor_key is not None:
            merged["floor_key"] = floor_key
        if bound_entity is not None:
            merged["bound_entity"] = bound_entity
        correction = normalise_override(merged)
        if correction is None:
            return await self.async_clear_override(key)
        overrides[key] = correction
        await self._async_save_overrides()
        _LOGGER.info("Recorded a floorplan correction for %s: %s", key, correction)
        return True

    async def async_clear_override(self, slug: str) -> bool:
        """Forget a correction, putting that device back under the matcher."""
        key = str(slug or "").strip()
        overrides = await self._async_overrides()
        if key not in overrides:
            return False
        overrides.pop(key)
        await self._async_save_overrides()
        _LOGGER.info("Cleared the floorplan correction for %s", key)
        return True

    async def async_overrides(self) -> Dict[str, Dict[str, Any]]:
        """A copy of the stored corrections, for diagnostics and tests."""
        return {k: dict(v) for k, v in (await self._async_overrides()).items()}

    async def _async_drop_overrides(self, dropped: List[Dict[str, Any]]) -> None:
        """Prune the corrections this deploy's manifests have moved past."""
        if not dropped:
            return
        overrides = await self._async_overrides()
        removed = [
            str(entry.get("entity") or "")
            for entry in dropped
            if str(entry.get("entity") or "") in overrides
        ]
        for slug in removed:
            overrides.pop(slug, None)
        if removed:
            await self._async_save_overrides()
            _LOGGER.warning(
                "Dropped %d installer correction(s) this plan has moved past: %s",
                len(removed), ", ".join(removed),
            )

    async def async_reapply(self) -> bool:
        """Rebuild the dashboard from the assets already on disk.

        What makes a correction usable by somebody standing in the house: the
        installer taps a device into the right room and the plan redraws,
        instead of waiting for Dispatch to push the whole deploy again. The
        manifests are read back out of /config/www/ha_dispatch/floorplan/,
        exactly where the last deploy left them, so this needs nothing from
        the server at all.
        """
        directory = Path(self.hass.config.path(*FLOORPLAN_ASSET_DIR))
        try:
            paths = await self.hass.async_add_executor_job(_discover_assets, directory)
        except OSError as err:
            _LOGGER.warning("Could not read the floorplan assets on disk: %s", err)
            return False
        if "hotspots" not in paths or "ha_map" not in paths:
            _LOGGER.warning(
                "No floorplan has been deployed to this installation yet; "
                "nothing to re-apply a correction to"
            )
            return False

        overrides = await self._async_overrides()
        decisions: List[Dict[str, Any]] = []
        try:
            await self._async_install_card()
            hotspots, ha_map, plan_transform = await self._async_load_manifests(paths)
            devices, dropped = async_plan_devices(
                self.hass, ha_map, hotspots, overrides
            )
            views = build_dashboard_views(
                self.hass,
                hotspots,
                dict(ha_map, devices=devices),
                paths.get("plan_image_urls") or {},
                plan_transform,
                fallback_image_url=paths.get("image_url"),
                decisions=decisions,
            )
            if not views:
                raise FloorplanApplyError("No floor had a plan render to show")
            await self._async_write_dashboard(views)
        except Exception as err:  # noqa: BLE001 - a service call must not raise
            _LOGGER.error("Could not re-apply floorplan corrections: %s", err)
            return False

        await self._async_drop_overrides(dropped)
        placed = sum(1 for d in decisions if d.get("bound"))
        _LOGGER.info(
            "Home 3D dashboard rebuilt from the assets on disk; %d of %d devices placed",
            placed, len(decisions),
        )
        return True

    # -- the card itself ------------------------------------------------------

    async def _async_install_card(self) -> Tuple[str, bool]:
        """Put the room-plan card where a browser can fetch it, and register
        it. Returns (resource url, whether it is live without a restart).

        Both halves are needed before the dashboard means anything: the card
        is the dashboard now, and a Lovelace view referring to
        `custom:ayvra-room-plan` with no resource loaded renders the
        frontend's "Custom element doesn't exist" box on every floor. Copying
        it is therefore fatal to the deploy if it fails; registration
        degrades, because its fallback is correct after one restart.
        """
        directory = Path(self.hass.config.path(*FLOORPLAN_ASSET_DIR))
        source = (
            Path(__file__).resolve().parent
            / FLOORPLAN_CARD_SOURCE_DIR
            / FLOORPLAN_CARD_FILENAME
        )
        dest = directory / FLOORPLAN_CARD_FILENAME
        try:
            stamp = await self.hass.async_add_executor_job(_copy_card, source, dest)
        except OSError as err:
            raise FloorplanApplyError(
                f"Could not install the room plan card: {err}"
            ) from err

        version = await self._async_card_version()
        url = card_resource_url(version or f"m{stamp}")
        went_live = await self._async_register_card_resource(url)
        return url, went_live

    async def _async_card_version(self) -> str:
        """The version to stamp the card's URL with.

        The coordinator's own client_version when setup has read it (the same
        value the server is told, and the one a self-update changes), falling
        back to the manifest beside this module.
        """
        version = getattr(self.coordinator, "client_version", None)
        if version:
            return str(version)
        manifest = Path(__file__).resolve().parent / "manifest.json"
        try:
            return await self.hass.async_add_executor_job(
                _read_manifest_version, manifest
            )
        except _SOFT_ERRORS as err:
            _LOGGER.debug("Could not read the client version for the card URL: %s", err)
            return ""

    async def _async_register_card_resource(self, url: str) -> bool:
        """Register (or re-point) the card's Lovelace resource row."""
        if await self._async_register_card_resource_live(url):
            return True
        return await self._async_register_card_resource_file(url)

    async def _async_register_card_resource_live(self, url: str) -> bool:
        """Register through lovelace's live resource collection.

        Same reasoning as _async_write_dashboard_live: the collection only
        reads its file at startup, so writing the file by hand leaves the
        resource unknown to the running frontend until a restart. Returns
        False, having already said why, when the live path is unavailable.

        An existing row whose URL differs only by its version stamp is
        *updated*, never added alongside -- otherwise every redeploy would
        leave another copy of the card in the resource list and the browser
        would load several versions of it at once.
        """
        try:
            from homeassistant.components.lovelace.const import LOVELACE_DATA
        except ImportError as err:
            _LOGGER.warning(
                "Lovelace not importable (%s); the room-plan card resource was"
                " not registered live",
                err,
            )
            return False

        lovelace_data = self.hass.data.get(LOVELACE_DATA)
        resources = getattr(lovelace_data, "resources", None)
        if resources is None or not hasattr(resources, "async_items"):
            _LOGGER.warning(
                "No live lovelace resource collection is available (a"
                " YAML-mode dashboard setup has none); add %s to your"
                " lovelace resources by hand if the Home 3D plan shows"
                " \"Custom element doesn't exist\"",
                url,
            )
            return False

        try:
            if not getattr(resources, "loaded", False):
                loader = getattr(resources, "async_load", None)
                if loader is not None:
                    await loader()

            existing = None
            for item in resources.async_items():
                if isinstance(item, dict) and _same_resource(item.get("url"), url):
                    existing = item
                    break

            if existing is None:
                await resources.async_create_item(
                    {"res_type": FLOORPLAN_RESOURCE_TYPE, "url": url}
                )
                _LOGGER.info("Registered the room-plan card resource: %s", url)
            elif existing.get("url") != url:
                await resources.async_update_item(
                    existing["id"],
                    {"res_type": FLOORPLAN_RESOURCE_TYPE, "url": url},
                )
                _LOGGER.info(
                    "Re-pointed the room-plan card resource to %s (was %s)",
                    url, existing.get("url"),
                )
        except Exception as err:  # noqa: BLE001 - any failure falls back
            _LOGGER.warning(
                "Could not register the room-plan card resource live (%s);"
                " writing it to storage instead",
                err,
            )
            return False

        return True

    async def _async_register_card_resource_file(self, url: str) -> bool:
        """Fallback: write the resource row straight to storage.

        Correct but not live -- lovelace reads this file at startup -- so this
        always returns False and the homeowner is told a restart is what is
        needed, rather than left looking at a dashboard full of missing-card
        boxes with no explanation.
        """
        registry = await _async_load_or_default(self._resources_store, {"items": []})
        items = list(registry.get("items") or [])

        for item in items:
            if isinstance(item, dict) and _same_resource(item.get("url"), url):
                item["url"] = url
                item["type"] = FLOORPLAN_RESOURCE_TYPE
                break
        else:
            items.append(
                {
                    "id": FLOORPLAN_RESOURCE_ID,
                    "type": FLOORPLAN_RESOURCE_TYPE,
                    "url": url,
                }
            )

        registry["items"] = items
        await self._resources_store.async_save(registry)
        _LOGGER.warning(
            "Wrote the room-plan card resource (%s) to storage; Home Assistant"
            " has to restart before a browser will load it",
            url,
        )
        return False

    # -- the poll loop --------------------------------------------------------

    async def async_poll_pending(self) -> None:
        """Fetch a pending deploy and apply it if one is not already running.

        Called on the coordinator's cadence, and deliberately incapable of
        raising: a customer's Home Assistant must keep reporting metrics even
        when a floorplan deploy is broken.
        """
        try:
            await self._async_poll_pending()
        except Exception as err:  # noqa: BLE001 - see the docstring
            _LOGGER.error("Unexpected error handling a floorplan deploy: %s", err)

    async def _async_poll_pending(self) -> None:
        if not self.available:
            return

        try:
            job = await self.api.fetch_pending_floorplan(self.installation_id)
        except FloorplanUnavailable:
            _LOGGER.info(
                "This Dispatch server has no floorplan deploy endpoint; not asking again"
            )
            self.async_disable()
            return
        except (aiohttp.ClientError, TimeoutError, KeyError, ValueError) as err:
            _LOGGER.debug("Could not fetch a pending floorplan deploy: %s", err)
            return

        if not job:
            return

        job_id = str(job.get("id") or "")
        if not job_id or job_id in self._running:
            return

        self._running.add(job_id)
        self.hass.async_create_task(self._async_run(job))

    async def _async_run(self, job: Dict[str, Any]) -> None:
        """Download, build, and write one deploy job; report the outcome."""
        job_id = str(job.get("id") or "")

        decisions: List[Dict[str, Any]] = []
        dropped: List[Dict[str, Any]] = []
        try:
            overrides = await self._async_overrides()
            paths = await self._async_download_assets(job)
            # The card is the dashboard: it has to be on disk and registered
            # before the views that reference it mean anything.
            _card_url, card_live = await self._async_install_card()
            hotspots, ha_map, plan_transform = await self._async_load_manifests(paths)
            # Matching first, then the installer's corrections on top of it,
            # and only then the dashboard. A redeploy rebuilds everything
            # from manifests that have just been downloaded afresh, so this
            # is the point where a correction either survives or is lost --
            # it has to be laid over the *new* manifests, not folded into
            # whatever the last deploy happened to conclude.
            devices, dropped = async_plan_devices(
                self.hass, ha_map, hotspots, overrides
            )
            views = build_dashboard_views(
                self.hass,
                hotspots,
                dict(ha_map, devices=devices),
                paths.get("plan_image_urls") or {},
                plan_transform,
                fallback_image_url=paths.get("image_url"),
                decisions=decisions,
            )
            if not views:
                raise FloorplanApplyError(
                    "No floor had a plan render to show; nothing to deploy"
                )
            went_live = await self._async_write_dashboard(views) and card_live
        except Exception as err:  # noqa: BLE001 - always report, never crash the poll loop
            _LOGGER.error("Floorplan deploy %s failed: %s", job_id, err)
            await self._async_report(job_id, FLOORPLAN_REPORT_STATUS_FAILED, str(err))
            self._running.discard(job_id)
            return

        placed = sum(1 for d in decisions if d.get("bound"))
        total = len(decisions)
        detail = f"Home 3D dashboard deployed; {placed} of {total} devices placed"
        if dropped:
            # Said out loud, in the one place the installer who made the
            # correction will look. A correction silently retained forever is
            # unmaintainable; a correction silently discarded is worse.
            await self._async_drop_overrides(dropped)
            decisions.extend(dropped_override_decisions(dropped))
            detail += f"; {len(dropped)} installer correction(s) dropped"
        # Named in the report because an installer looking at a plan that is
        # not oriented the way they expect needs to know whether the import
        # asked for that, or whether somebody turned that one tablet.
        rotated = sorted(
            f"{key} {plan_rotation(plan_transform.get(key))}deg"
            for key in (plan_transform if isinstance(plan_transform, dict) else {})
            if plan_rotation(plan_transform.get(key))
        )
        if rotated:
            detail += f"; plan rotated by default: {', '.join(rotated)}"
        _LOGGER.info(
            "Floorplan deploy %s applied; Home 3D dashboard updated, %d of %d devices placed",
            job_id, placed, total,
        )
        await self._async_report(
            job_id,
            FLOORPLAN_REPORT_STATUS_DONE,
            detail,
            devices=decisions,
        )
        self._notify_success(went_live)
        self._running.discard(job_id)

    # -- applying one job -------------------------------------------------

    async def _async_download_assets(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """Download this job's assets into /config/www/ha_dispatch/floorplan/.

        Two are required: the two manifests, hotspots.json and ha_map.json.
        Everything else is downloaded best-effort and the dashboard degrades
        without it: the per-floor plan renders and their transform are what
        the dashboard is actually built on, and the hero backplate is kept
        only as a floor's image of last resort for a deploy job from before
        per-floor plans existed.

        The GLB is not downloaded at all. Nothing on the dashboard ever used
        it -- it was tens of megabytes onto the homeowner's SD card for the
        installer's own 3D preview, which lives on the Dispatch side. The
        server may still advertise a `glb_url`; it is ignored.
        """
        directory = Path(self.hass.config.path(*FLOORPLAN_ASSET_DIR))
        await self.hass.async_add_executor_job(_ensure_dir, directory)

        required = (
            ("hotspots_url", FLOORPLAN_HOTSPOTS_NAME, "hotspots"),
            ("ha_map_url", FLOORPLAN_HA_MAP_NAME, "ha_map"),
        )

        paths: Dict[str, Any] = {}
        for url_key, filename, result_key in required:
            url = job.get(url_key)
            if not url:
                raise FloorplanApplyError(f"Deploy job carried no {url_key}")
            dest = directory / filename
            await self.api.download_floorplan_asset(url, dest)
            paths[result_key] = dest

        paths["image_url"] = None
        backplate_url = job.get("backplate_url")
        if backplate_url:
            dest = directory / FLOORPLAN_BACKPLATE_NAME
            try:
                await self.api.download_floorplan_asset(backplate_url, dest)
                paths["image_url"] = f"{FLOORPLAN_LOCAL_URL_PREFIX}/{FLOORPLAN_BACKPLATE_NAME}"
            except (aiohttp.ClientError, TimeoutError, OSError, ValueError) as err:
                _LOGGER.warning("Could not download the hero backplate: %s", err)

        transform_url = job.get("plan_transform_url")
        if transform_url:
            dest = directory / FLOORPLAN_PLAN_TRANSFORM_NAME
            try:
                await self.api.download_floorplan_asset(transform_url, dest)
                paths["plan_transform"] = dest
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                _LOGGER.warning("Could not download plan_transform.json: %s", err)

        plan_image_urls: Dict[str, str] = {}
        plan_backplate_urls = job.get("plan_backplate_urls")
        if isinstance(plan_backplate_urls, dict):
            for level_key, url in plan_backplate_urls.items():
                if not url:
                    continue
                filename = FLOORPLAN_PLAN_BACKPLATE_NAME_FMT.format(level_key=level_key)
                dest = directory / filename
                try:
                    await self.api.download_floorplan_asset(url, dest)
                except (aiohttp.ClientError, TimeoutError, OSError) as err:
                    _LOGGER.warning(
                        "Could not download plan backplate for %s: %s", level_key, err
                    )
                    continue
                plan_image_urls[level_key] = f"{FLOORPLAN_LOCAL_URL_PREFIX}/{filename}"
        paths["plan_image_urls"] = plan_image_urls

        return paths

    # -- dashboard storage ----------------------------------------------------

    async def _async_write_dashboard(self, views: List[Dict[str, Any]]) -> bool:
        """Register (or update) the "Home 3D" storage-mode dashboard.

        Home Assistant's own lovelace integration keeps two things in
        storage: a small registry of every dashboard it knows about
        (.storage/lovelace_dashboards) and, for each storage-mode dashboard,
        that dashboard's own views/cards config (.storage/lovelace.<url_
        path>). Both are themselves backed by DictStorageCollection/Store
        objects that lovelace only *reads* at startup -- writing the files
        directly (as this integration used to, unconditionally) leaves a
        freshly created dashboard invisible, and a redeploy onto an
        already-open one stale, until Home Assistant restarts.

        The fix tries the live path first: reach the running
        DashboardsCollection through the "lovelace/dashboards/list"
        websocket handler (lovelace never puts the collection itself in
        hass.data, only registers commands bound to it), create or update
        our entry through it exactly as the UI would, then push the actual
        views/cards onto the live LovelaceStorage object lovelace's own
        change listener just created. That updates the in-memory cache and
        fires ``lovelace_updated`` so any open frontend refreshes immediately
        -- no restart, no reload.

        If any part of that lookup fails -- a renamed attribute, a
        restructured websocket registry, lovelace not being loaded at all --
        this falls back to the old direct-Store-write behaviour, now also
        making a best-effort attempt to register the frontend panel and
        splice a live dashboard object in directly. Returns whether the
        dashboard is visible without a restart.
        """
        config = build_dashboard_config(views)

        if await self._async_write_dashboard_live(config):
            return True

        return await self._async_write_dashboard_files(config)

    async def _async_write_dashboard_live(self, config: Dict[str, Any]) -> bool:
        """Register/update the dashboard through HA's live lovelace objects.

        Returns True if the dashboard now reflects `config` without needing a
        restart, False if any step of the live path was unavailable (already
        logged at WARNING) and the caller should fall back to file writes.
        """
        try:
            from homeassistant.components import websocket_api
            from homeassistant.components.lovelace.const import LOVELACE_DATA
        except ImportError as err:
            _LOGGER.warning(
                "Lovelace/websocket_api not importable (%s); writing the Home"
                " 3D dashboard to storage directly -- a Home Assistant"
                " restart may be needed to see it",
                err,
            )
            return False

        handlers = self.hass.data.get(websocket_api.DOMAIN) or {}
        registered = handlers.get("lovelace/dashboards/list")
        if registered is None:
            _LOGGER.warning(
                "Could not find the live lovelace dashboards collection (no"
                " lovelace/dashboards/list websocket handler is registered);"
                " writing the Home 3D dashboard to storage directly -- a Home"
                " Assistant restart may be needed to see it"
            )
            return False

        handler = inspect.unwrap(registered[0])
        storage_collection = getattr(
            getattr(handler, "__self__", None), "storage_collection", None
        )
        if storage_collection is None:
            _LOGGER.warning(
                "Could not reach the live lovelace dashboards collection from"
                " its websocket handler; writing the Home 3D dashboard to"
                " storage directly -- a Home Assistant restart may be needed"
                " to see it"
            )
            return False

        fields = {
            "title": FLOORPLAN_DASHBOARD_TITLE,
            "icon": FLOORPLAN_DASHBOARD_ICON,
            "show_in_sidebar": True,
            "require_admin": False,
        }

        existing_id = None
        for item_id, item in storage_collection.data.items():
            if (
                isinstance(item, dict)
                and item.get("url_path") == FLOORPLAN_DASHBOARD_URL_PATH
            ):
                existing_id = item_id
                break

        try:
            if existing_id is None:
                await storage_collection.async_create_item(
                    {
                        **fields,
                        "url_path": FLOORPLAN_DASHBOARD_URL_PATH,
                        "mode": "storage",
                    }
                )
            else:
                await storage_collection.async_update_item(existing_id, fields)
        except Exception as err:  # noqa: BLE001 - any failure here falls back
            _LOGGER.warning(
                "Could not register the Home 3D dashboard through the live"
                " lovelace collection (%s); writing it to storage directly --"
                " a Home Assistant restart may be needed to see it",
                err,
            )
            return False

        lovelace_data = self.hass.data.get(LOVELACE_DATA)
        live_dashboard = None
        if lovelace_data is not None:
            live_dashboard = getattr(lovelace_data, "dashboards", {}).get(
                FLOORPLAN_DASHBOARD_URL_PATH
            )

        if live_dashboard is None:
            _LOGGER.warning(
                "Home 3D dashboard entry was registered live, but its live"
                " config object was not found; a Home Assistant restart may"
                " be needed to see the latest content"
            )
            return False

        await live_dashboard.async_save(config)
        return True

    async def _async_write_dashboard_files(self, config: Dict[str, Any]) -> bool:
        """Fallback: write the dashboard straight to storage files.

        Kept for a Home Assistant version where the live lookup above no
        longer works. Also makes a best-effort attempt to register the panel
        and splice a live dashboard object into hass.data so the change might
        still show up without a restart; if that best-effort attempt fails
        too, the write is still correct -- it will just need one restart (or
        a lovelace reload) to be picked up, same as this integration's
        original behaviour.
        """
        registry = await _async_load_or_default(self._dashboards_store, {"items": []})
        items = list(registry.get("items") or [])

        entry = None
        for candidate in items:
            if (
                isinstance(candidate, dict)
                and candidate.get("url_path") == FLOORPLAN_DASHBOARD_URL_PATH
            ):
                entry = candidate
                break

        is_new = entry is None
        if entry is None:
            entry = {
                "id": FLOORPLAN_DASHBOARD_URL_PATH,
                "url_path": FLOORPLAN_DASHBOARD_URL_PATH,
            }
            items.append(entry)

        entry.update(
            {
                "mode": "storage",
                "title": FLOORPLAN_DASHBOARD_TITLE,
                "icon": FLOORPLAN_DASHBOARD_ICON,
                "show_in_sidebar": True,
                "require_admin": False,
            }
        )

        registry["items"] = items
        await self._dashboards_store.async_save(registry)
        await self._dashboard_store.async_save({"config": config})

        return await self._async_best_effort_live_panel(entry, config, is_new)

    async def _async_best_effort_live_panel(
        self, entry: Dict[str, Any], config: Dict[str, Any], is_new: bool
    ) -> bool:
        """Try to make the fallback-written dashboard live anyway.

        Best-effort only: this runs after the storage files are already
        correctly written, so any failure here is swallowed (at DEBUG) --
        worst case is the pre-existing "needs a restart" behaviour, not a
        broken deploy.
        """
        try:
            from homeassistant.components import frontend
            from homeassistant.components.lovelace import dashboard as ll_dashboard
            from homeassistant.components.lovelace.const import LOVELACE_DATA
        except ImportError:
            return False

        try:
            lovelace_data = self.hass.data.get(LOVELACE_DATA)
            if lovelace_data is None:
                return False

            live_dashboard = lovelace_data.dashboards.get(
                FLOORPLAN_DASHBOARD_URL_PATH
            )
            if live_dashboard is None:
                live_dashboard = ll_dashboard.LovelaceStorage(self.hass, entry)
                lovelace_data.dashboards[FLOORPLAN_DASHBOARD_URL_PATH] = (
                    live_dashboard
                )

            await live_dashboard.async_save(config)

            frontend.async_register_built_in_panel(
                self.hass,
                "lovelace",
                frontend_url_path=FLOORPLAN_DASHBOARD_URL_PATH,
                sidebar_title=FLOORPLAN_DASHBOARD_TITLE,
                sidebar_icon=FLOORPLAN_DASHBOARD_ICON,
                show_in_sidebar=True,
                require_admin=False,
                config={"mode": "storage"},
                update=not is_new,
            )
        except Exception as err:  # noqa: BLE001 - best-effort only
            _LOGGER.debug(
                "Best-effort live panel registration for the fallback Home 3D"
                " dashboard write did not work out: %s",
                err,
            )
            return False

        return True

    async def _async_load_manifests(
        self, paths: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        """Read the downloaded JSON manifests, off the event loop.

        plan_transform.json is optional (see _async_download_assets); a
        missing or unreadable one degrades to an empty dict, which
        build_dashboard_cards treats as "no exact transform for any floor".
        """
        try:
            hotspots = await self.hass.async_add_executor_job(_read_json, paths["hotspots"])
            ha_map = await self.hass.async_add_executor_job(_read_json, paths["ha_map"])
        except _SOFT_ERRORS as err:
            raise FloorplanApplyError(f"Could not read a floorplan manifest: {err}") from err

        if not isinstance(hotspots, dict):
            raise FloorplanApplyError("hotspots.json did not contain a floor map")
        if not isinstance(ha_map, dict):
            raise FloorplanApplyError("ha_map.json did not contain a device map")

        plan_transform: Dict[str, Any] = {}
        transform_path = paths.get("plan_transform")
        if transform_path is not None:
            try:
                loaded = await self.hass.async_add_executor_job(_read_json, transform_path)
                if isinstance(loaded, dict):
                    plan_transform = loaded
            except _SOFT_ERRORS as err:
                _LOGGER.warning("Could not read plan_transform.json: %s", err)

        return hotspots, ha_map, plan_transform

    # -- reporting ----------------------------------------------------------

    async def _async_report(
        self,
        job_id: str,
        status: str,
        detail: Optional[str],
        devices: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Report the outcome, tolerating a server that cannot take it.

        `devices` is the matcher's decision list (see _decide): what each
        device from the estimate was bound to, or why it was not, and which
        entities the house had to offer. Sent with a successful deploy so the
        installer can read it off Dispatch instead of this house's log.
        """
        try:
            await self.api.report_floorplan(
                self.installation_id, job_id, status, detail=detail, devices=devices
            )
        except FloorplanUnavailable:
            _LOGGER.info(
                "Server no longer offers floorplan deploys; giving up on job %s", job_id
            )
            self.async_disable()
        except (aiohttp.ClientError, TimeoutError, KeyError, ValueError) as err:
            _LOGGER.warning(
                "Could not report floorplan deploy %s (%s): %s", job_id, status, err
            )

    def _notify_success(self, went_live: bool) -> None:
        """An informational heads-up -- no approval needed, unlike consent prompts.

        Unlike remote_access.py's consent notifications, nothing here is
        asking the homeowner to decide anything: the deploy already happened.
        `went_live` says whether the dashboard is already visible; if not
        (the live lovelace lookup failed and the fallback panel splice did
        too), the customer is told a restart is what is actually needed
        rather than left to wonder why tapping the sidebar shows nothing.
        """
        message = "Your installer just updated your Home 3D dashboard."
        if not went_live:
            message += " Restart Home Assistant to see it."
        try:
            persistent_notification.async_create(
                self.hass,
                message,
                title="Home 3D dashboard updated",
                notification_id=FLOORPLAN_NOTIFICATION_ID,
            )
        except Exception as err:  # noqa: BLE001 - notification is best-effort
            _LOGGER.warning("Could not raise the floorplan deploy notification: %s", err)


def async_all_managers(hass: HomeAssistant) -> List[HADispatchFloorplan]:
    """Every configured installation's floorplan manager."""
    return [
        coordinator.floorplan
        for coordinator in (hass.data.get(DOMAIN) or {}).values()
        if getattr(coordinator, "floorplan", None) is not None
    ]


__all__ = [
    "FloorplanApplyError",
    "HADispatchFloorplan",
    "async_all_managers",
    "async_match_entity",
    "async_plan_devices",
    "area_tokens_by_entity",
    "dropped_override_decisions",
    "normalise_override",
    "normalise_overrides",
    "build_dashboard_views",
    "build_dashboard_config",
    "build_room_plan_card",
    "room_display_name",
    "assign_room_labels",
    "plan_rotation",
    "rotate_percent_point",
]
