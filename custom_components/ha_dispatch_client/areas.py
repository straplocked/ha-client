"""The house's own Home Assistant areas: reading them, and filing into them.

Two jobs, both about the area registry, kept out of floorplan.py so that
module stays about drawing a dashboard.

Reading: which area each entity is in. An entity inherits its device's area
when it has none of its own, exactly as the frontend presents it. The deploy
report carries the area of every device it placed, so Dispatch's room editor
can suggest a room for a device from where the homeowner already filed it,
and the list of the house's area names, so a drawn room can be named the way
the house already names it.

Writing: "use these rooms as Home Assistant areas". After a deploy that asked
for it, each placed entity is filed into the area named after its room,
creating the area when the house has none of that name, so the house's own
Home Assistant -- and its voice assistant -- knows the rooms too. This writes
to the homeowner's registry, so:

- It happens only when the deploy asked (`area_sync` in the pending payload),
  only under enhanced permissions, and the client checks its own record of
  that grant rather than taking the server's word alone.
- `fill` touches only entities with no area at all. `move` also refiles an
  entity filed somewhere else; nothing else ever does.
- An area is created only when an entity is about to be filed into it, so a
  closet with no controls does not become an empty area.
- Entities only, never devices: setting a device's area would move every
  entity on it, including ones the plan never mentioned.
- Every change is reported back, and the homeowner is told in the deploy's
  own notification.

Registry access is best-effort and attribute-tolerant, like the rest of this
integration's reads: an older core shaped differently, or a test host with no
registries, reads as "no areas" and a sync reports `failed` rather than
raising into the deploy.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from homeassistant.core import HomeAssistant

from .const import (
    AREA_SYNC_DONE,
    AREA_SYNC_FAILED,
    AREA_SYNC_FILL,
    AREA_SYNC_MODES,
    AREA_SYNC_MOVE,
    AREA_SYNC_REPORT_LIMIT,
    AREA_SYNC_SKIP_FILED_ELSEWHERE,
    AREA_SYNC_SKIP_NOT_REGISTERED,
    AREA_SYNC_SKIP_TWO_ROOMS,
)

_LOGGER = logging.getLogger(__name__)

_SOFT_ERRORS = (AttributeError, KeyError, TypeError, ValueError)


def _registries(hass: HomeAssistant) -> Optional[Tuple[Any, Any, Any]]:
    """(areas, devices, entities), or None where the registries are not there."""
    try:
        from homeassistant.helpers import area_registry, device_registry, entity_registry
    except ImportError:
        return None
    try:
        return (
            area_registry.async_get(hass),
            device_registry.async_get(hass),
            entity_registry.async_get(hass),
        )
    except Exception as err:  # noqa: BLE001 - best-effort, see the module docstring
        _LOGGER.debug("Area registry unavailable: %s", err)
        return None


def _area(areas: Any, area_id: Any) -> Optional[Any]:
    if not area_id:
        return None
    getter = getattr(areas, "async_get_area", None)
    return getter(area_id) if getter is not None else None


def _effective_area_id(devices: Any, entry: Any) -> Optional[str]:
    """The entity's own area, else its device's -- what the frontend shows."""
    area_id = getattr(entry, "area_id", None)
    if area_id:
        return str(area_id)
    device_id = getattr(entry, "device_id", None)
    if device_id:
        getter = getattr(devices, "async_get", None)
        device = getter(device_id) if getter is not None else None
        area_id = getattr(device, "area_id", None)
        if area_id:
            return str(area_id)
    return None


def entity_areas(hass: HomeAssistant) -> Dict[str, Dict[str, str]]:
    """Each registered entity's area as {"area_id", "area"}, keyed by entity id.

    Entities with no area are absent, and so is everything when the
    registries cannot be read.
    """
    registries = _registries(hass)
    if registries is None:
        return {}
    areas, devices, entities = registries

    result: Dict[str, Dict[str, str]] = {}
    try:
        for entity_id, entry in dict(getattr(entities, "entities", {}) or {}).items():
            area_id = _effective_area_id(devices, entry)
            name = getattr(_area(areas, area_id), "name", None)
            if area_id and name:
                result[str(entity_id)] = {"area_id": area_id, "area": str(name)}
    except _SOFT_ERRORS as err:
        _LOGGER.debug("Could not read the entity registry for areas: %s", err)
        return {}
    return result


def area_names(hass: HomeAssistant) -> List[str]:
    """Every area the house has, by name, sorted. Empty when unreadable."""
    registries = _registries(hass)
    if registries is None:
        return []
    lister = getattr(registries[0], "async_list_areas", None)
    if lister is None:
        return []
    try:
        names = {str(getattr(a, "name", "") or "").strip() for a in lister()}
    except _SOFT_ERRORS:
        return []
    return sorted(n for n in names if n)


def annotate_areas(
    decisions: List[Dict[str, Any]], areas_by_entity: Dict[str, Dict[str, str]]
) -> None:
    """Put the bound entity's area on each deploy-report record, in place.

    `area` and `area_id` are None for a record with no binding, or one whose
    entity has no area: the server reads absence and null alike.
    """
    for record in decisions:
        found = areas_by_entity.get(str(record.get("bound") or ""))
        record["area"] = found["area"] if found else None
        record["area_id"] = found["area_id"] if found else None


def _key(name: Any) -> str:
    """Area names compared the way Home Assistant compares them: case and
    surrounding/inner whitespace ignored."""
    return " ".join(str(name or "").split()).casefold()


def plan_area_sync(
    placements: List[Tuple[str, str]],
    current: Dict[str, Optional[str]],
    registered: set,
    mode: str,
) -> Dict[str, Any]:
    """Decide, without touching anything, what a sync would do.

    `placements` is (entity id, room display name) for every placed entity;
    `current` maps an entity to the NAME of the area it is in now (None for
    none); `registered` is the set of entity ids that have a registry entry
    (an entity without one cannot be given an area at all).

    Returns {"file": {area name: [entity ids]}, "already": int,
    "skipped": [{"entity", "area", "why", "current"}]}. The area name is the
    room's own spelling; matching against existing areas is the writer's job.
    """
    rooms_by_entity: Dict[str, set] = {}
    spelling: Dict[str, str] = {}
    for entity_id, room in placements:
        if not entity_id or not str(room or "").strip():
            continue
        rooms_by_entity.setdefault(entity_id, set()).add(_key(room))
        spelling.setdefault(_key(room), str(room).strip())

    file: Dict[str, List[str]] = {}
    skipped: List[Dict[str, Any]] = []
    already = 0
    for entity_id in sorted(rooms_by_entity):
        rooms = rooms_by_entity[entity_id]
        if len(rooms) > 1:
            # One real entity standing for devices in two rooms: filing it in
            # either would be a guess.
            skipped.append({
                "entity": entity_id,
                "area": ", ".join(sorted(spelling[r] for r in rooms)),
                "why": AREA_SYNC_SKIP_TWO_ROOMS,
                "current": current.get(entity_id),
            })
            continue
        target = spelling[next(iter(rooms))]
        now = current.get(entity_id)
        if now is not None and _key(now) == _key(target):
            already += 1
            continue
        if entity_id not in registered:
            skipped.append({
                "entity": entity_id, "area": target,
                "why": AREA_SYNC_SKIP_NOT_REGISTERED, "current": now,
            })
            continue
        if now is not None and mode != AREA_SYNC_MOVE:
            skipped.append({
                "entity": entity_id, "area": target,
                "why": AREA_SYNC_SKIP_FILED_ELSEWHERE, "current": now,
            })
            continue
        file.setdefault(target, []).append(entity_id)
    return {"file": file, "already": already, "skipped": skipped}


def async_sync_areas(
    hass: HomeAssistant, placements: List[Tuple[str, str]], mode: str
) -> Dict[str, Any]:
    """File each placed entity into its room's area. Never raises.

    Runs in the event loop (the registries' writers are callbacks). Returns
    the report the deploy carries back:
    {"mode", "status", "reason", "created": [names],
     "filed": [{"entity", "area"}], "filed_count", "already",
     "skipped": [...], "skipped_count"}.
    """
    report: Dict[str, Any] = {
        "mode": mode, "status": AREA_SYNC_DONE, "reason": None,
        "created": [], "filed": [], "filed_count": 0, "already": 0,
        "skipped": [], "skipped_count": 0,
    }
    if mode not in AREA_SYNC_MODES:
        report.update(status=AREA_SYNC_FAILED, reason="unknown_mode")
        return report

    registries = _registries(hass)
    if registries is None:
        report.update(status=AREA_SYNC_FAILED, reason="no_registry")
        return report
    areas, devices, entities = registries

    try:
        entries = dict(getattr(entities, "entities", {}) or {})
        current: Dict[str, Optional[str]] = {}
        for entity_id, _room in placements:
            entry = entries.get(entity_id)
            if entry is None:
                continue
            current[entity_id] = getattr(
                _area(areas, _effective_area_id(devices, entry)), "name", None
            )
        plan = plan_area_sync(placements, current, set(entries), mode)

        by_key = {
            _key(getattr(a, "name", "")): a
            for a in (getattr(areas, "async_list_areas", lambda: [])() or [])
        }
        for name, entity_ids in plan["file"].items():
            area = by_key.get(_key(name))
            if area is None:
                area = areas.async_create(name)
                by_key[_key(name)] = area
                report["created"].append(name)
            for entity_id in entity_ids:
                entities.async_update_entity(entity_id, area_id=area.id)
                report["filed"].append({"entity": entity_id, "area": name})
    except Exception as err:  # noqa: BLE001 - a deploy that drew must not fail here
        _LOGGER.error("Filing the plan's devices into areas stopped: %s", err)
        report.update(status=AREA_SYNC_FAILED, reason=str(err)[:200])
        report["filed_count"] = len(report["filed"])
        report["filed"] = report["filed"][:AREA_SYNC_REPORT_LIMIT]
        return report

    report["already"] = plan["already"]
    report["filed_count"] = len(report["filed"])
    report["filed"] = report["filed"][:AREA_SYNC_REPORT_LIMIT]
    report["skipped_count"] = len(plan["skipped"])
    report["skipped"] = plan["skipped"][:AREA_SYNC_REPORT_LIMIT]
    _LOGGER.info(
        "Filed %d entities into areas (%d already there, %d left alone; created %s)",
        report["filed_count"], report["already"], report["skipped_count"],
        ", ".join(report["created"]) or "none",
    )
    return report


__all__ = [
    "AREA_SYNC_FILL",
    "AREA_SYNC_MOVE",
    "annotate_areas",
    "area_names",
    "async_sync_areas",
    "entity_areas",
    "plan_area_sync",
]
