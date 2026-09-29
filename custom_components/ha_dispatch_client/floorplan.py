"""Floor-plan deploy: render pipeline output, turned into a Lovelace dashboard.

A technician uploads a floor-plan sketch to HA Dispatch. A render pipeline
there (out of scope here, and built by a different agent) turns it into a GLB
3D model, a handful of PNG "backplate" renders, a hotspots.json describing
each room as a polygon, and a ha_map.json placing devices in those rooms. This
module is the last leg: notice the job, pull the four assets down, and turn
them into a "Home 3D" Lovelace dashboard a homeowner can actually tap on.

Modelled on updates.py's polling shape, not tunnel.py's live-request relay: a
floorplan deploy is a multi-step background job (download, write files, build
a dashboard, confirm), not a request Dispatch is waiting on synchronously.

ha_map.json's device slugs (e.g. `thermostat.great_room.01`) are invented by
the render pipeline from an insurance estimate line item -- they are HA-style
slugs, not real Home Assistant entity ids. Every device is resolved against
this installation's actual entities by a fuzzy match on domain and room name;
anything with no confident match is left off the dashboard rather than
fabricated onto it, per the rule: device icons bound to real entities where a
matching entity exists, skip the ones that don't.
"""
from __future__ import annotations

import json
import logging
import math
import re
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
    FLOORPLAN_DASHBOARD_ICON,
    FLOORPLAN_DASHBOARD_STORAGE_KEY,
    FLOORPLAN_DASHBOARD_TITLE,
    FLOORPLAN_DASHBOARD_URL_PATH,
    FLOORPLAN_DASHBOARDS_STORAGE_KEY,
    FLOORPLAN_GLB_NAME,
    FLOORPLAN_HA_MAP_NAME,
    FLOORPLAN_HOTSPOTS_NAME,
    FLOORPLAN_LOCAL_URL_PREFIX,
    FLOORPLAN_NOTIFICATION_ID,
    FLOORPLAN_PLAN_BACKPLATE_NAME_FMT,
    FLOORPLAN_PLAN_TRANSFORM_NAME,
    FLOORPLAN_REPORT_STATUS_DONE,
    FLOORPLAN_REPORT_STATUS_FAILED,
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


def async_match_entity(hass: HomeAssistant, device: Dict[str, Any]) -> Optional[str]:
    """Resolve one ha_map.json device record to a real entity, or None.

    ha_map.json's `entity` field (e.g. `thermostat.great_room.01`) is a slug
    the render pipeline invented from an insurance estimate line item -- it is
    not a Home Assistant entity id and must never be used as one. What this
    installation can actually confirm is the device's likely HA domain
    (already normalised into the `domain` field, e.g. "climate") and which
    room it is in, so matching is domain-filtered and then scored on how many
    of the room name's words show up in a candidate's friendly name or entity
    id.

    Uses hass.states.async_all(), the same enumeration idiom already used
    elsewhere in this integration (health.py's collect_health,
    updates.py's SupervisorUpdater._update_states) rather than the entity or
    area registries, which nothing else here touches. A confident match needs
    at least one shared word between the room name and the candidate; with no
    such candidate this returns None; skip the device on the dashboard, never
    guess an entity id.
    """
    domain = str(device.get("domain") or "").strip()
    if not domain:
        return None

    room_tokens = _tokens(device.get("room"))
    if not room_tokens:
        return None

    lister = getattr(getattr(hass, "states", None), "async_all", None)
    if lister is None:
        return None

    try:
        candidates = list(lister(domain))
    except _SOFT_ERRORS as err:
        _LOGGER.debug("Could not enumerate %s entities: %s", domain, err)
        return None

    best_entity: Optional[str] = None
    best_score = 0
    for state in candidates:
        candidate_tokens = _tokens(getattr(state, "name", None)) | _tokens(
            getattr(state, "entity_id", None)
        )
        score = len(room_tokens & candidate_tokens)
        if score > best_score:
            best_score = score
            best_entity = getattr(state, "entity_id", None)

    return best_entity if best_score > 0 else None


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


# --- device icons -------------------------------------------------------------

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
    carry.
    """
    icon = _ICON_BY_CLS.get(str(device.get("cls") or ""))
    if icon:
        return icon
    return _ICON_BY_DOMAIN.get(str(device.get("domain") or ""))


# --- card building ----------------------------------------------------------


def build_picture_elements_card(
    hass: HomeAssistant,
    rooms: List[Dict[str, Any]],
    devices: List[Dict[str, Any]],
    image_url: str,
    transform: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build one floor's picture-elements card: plan backplate, room labels,
    device icons.

    `transform`, when given, is that floor's exact plan_transform.json entry
    -- feet map onto the backplate's own pixels exactly, because it is a real
    top-down orthographic render of the same scene. With no transform (an
    older deploy job, or a level whose dashboard plan render did not come
    through) this falls back to linearly rescaling the floor's own hotspot
    bounding box.

    Room hotspots have no entity behind them, so each degrades to a labeled
    point at its centroid -- an icon element with a tooltip title, coloured
    with a theme variable rather than a hard-coded hex so it reads in both
    light and dark companion-app themes. Devices use `state-icon`, which
    already reflects the bound entity's state and colour per the active
    theme; an explicit domain-appropriate `icon` is layered on top so every
    device reads as its own kind of thing rather than one generic glyph.
    """
    bounds: Optional[Tuple[float, float, float, float]] = None
    if transform is None:
        bounds = _bounding_box({"_": rooms})
        if bounds is None:
            _LOGGER.warning(
                "Floorplan floor carried no usable coordinates; card has no elements"
            )
            return {"type": "picture-elements", "image": image_url,
                    "aspect_ratio": "8:5", "elements": []}

    def project(x: float, y: float) -> Tuple[float, float]:
        if transform is not None:
            return _project_exact(x, y, transform)
        assert bounds is not None
        return _project(x, y, bounds)

    elements: List[Dict[str, Any]] = []

    for room in rooms:
        if not isinstance(room, dict):
            continue
        centroid = room.get("centroid")
        name = room.get("name")
        if not centroid or len(centroid) < 2 or not name:
            continue
        left, top = project(centroid[0], centroid[1])
        elements.append(
            {
                "type": "icon",
                "icon": "mdi:floor-plan",
                "title": str(name),
                "style": {
                    "top": f"{top}%",
                    "left": f"{left}%",
                    "color": "var(--primary-text-color)",
                    "--mdc-icon-size": "20px",
                },
            }
        )

    # Multiple devices in one room are anchored to the same plan label (see
    # ha-dispatch's SKILL.md, "Anchor to the plan LABEL, not a room cell"), so
    # without some spread every icon in a room stacks exactly on top of the
    # first. Fan later ones out a little -- the same polar jitter build.py
    # uses for the 3D empties -- so the first device in a room keeps its
    # exact anchor and the rest read as separate icons.
    room_seen: Dict[str, int] = {}

    for device in devices:
        if not isinstance(device, dict):
            continue
        xy = device.get("xy_ft")
        if not xy or len(xy) < 2:
            continue

        entity_id = async_match_entity(hass, device)
        if entity_id is None:
            _LOGGER.debug(
                "No matching entity for floorplan device %s in %s; skipping",
                device.get("entity"),
                device.get("room"),
            )
            continue

        room_name = str(device.get("room") or "")
        idx = room_seen.get(room_name, 0)
        room_seen[room_name] = idx + 1
        x, y = float(xy[0]), float(xy[1])
        if idx:
            angle, radius = 2.4 * idx, 0.8 + 0.5 * idx
            x += math.cos(angle) * radius
            y += math.sin(angle) * radius

        left, top = project(x, y)
        element: Dict[str, Any] = {
            "type": "state-icon",
            "entity": entity_id,
            "style": {
                "top": f"{top}%",
                "left": f"{left}%",
            },
            "tap_action": {"action": "more-info"},
        }
        icon = _domain_icon(device)
        if icon:
            element["icon"] = icon
        elements.append(element)

    aspect_ratio = "8:5"
    if transform is not None:
        w, h = transform.get("image_width_px"), transform.get("image_height_px")
        if w and h:
            aspect_ratio = f"{int(w)}:{int(h)}"

    return {
        "type": "picture-elements",
        "image": image_url,
        "aspect_ratio": aspect_ratio,
        "elements": elements,
    }


def build_dashboard_cards(
    hass: HomeAssistant,
    hotspots_by_floor: Dict[str, List[Dict[str, Any]]],
    ha_map: Dict[str, Any],
    hero_image_url: Optional[str],
    plan_image_urls: Dict[str, str],
    plan_transform: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """The full stack of cards for the Home 3D view: the angled hero render
    on top (eye candy -- no exact coordinate map), then one top-down plan
    card per floor with the real room labels and device icons.

    Devices are assigned to a floor by ha_map.json's `floor_key` (matches
    hotspots.json's own keys exactly) when present. An older manifest that
    predates `floor_key` only carries a human label ("1st Floor") that will
    not match a hotspots key directly; with exactly one floor that mismatch
    does not matter, so that case falls back to "every device belongs to the
    one floor there is" rather than silently dropping every device.
    """
    devices = [d for d in (ha_map.get("devices") or []) if isinstance(d, dict)]
    floor_keys = [k for k, v in hotspots_by_floor.items() if isinstance(v, list)]
    single_floor = len(floor_keys) == 1

    cards: List[Dict[str, Any]] = []
    if hero_image_url:
        cards.append({"type": "picture", "image": hero_image_url})

    for key in floor_keys:
        rooms = hotspots_by_floor.get(key) or []
        image_url = plan_image_urls.get(key) or hero_image_url
        if not image_url:
            _LOGGER.warning("No backplate available for floor %s; skipping its card", key)
            continue

        floor_devices = devices if single_floor else [
            d for d in devices if d.get("floor_key") == key
        ]

        transform = plan_transform.get(key) if isinstance(plan_transform, dict) else None
        cards.append(
            build_picture_elements_card(hass, rooms, floor_devices, image_url, transform)
        )

    return cards


def build_dashboard_config(cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The dashboard's views/cards config, as Lovelace storage mode expects it.

    One panel view -- full width, no dashboard chrome -- tablet-first: the
    plan card(s) fill the screen instead of sharing it with sidebar/header
    furniture. `panel: true` only ever shows a single card, so every card
    (hero + one per floor) is wrapped in one vertical-stack.
    """
    return {
        "title": FLOORPLAN_DASHBOARD_TITLE,
        "views": [
            {
                "title": FLOORPLAN_DASHBOARD_TITLE,
                "path": FLOORPLAN_DASHBOARD_URL_PATH,
                "icon": FLOORPLAN_DASHBOARD_ICON,
                "panel": True,
                "cards": [{"type": "vertical-stack", "cards": cards}],
            }
        ],
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

        try:
            paths = await self._async_download_assets(job)
            hotspots, ha_map, plan_transform = await self._async_load_manifests(paths)
            cards = build_dashboard_cards(
                self.hass,
                hotspots,
                ha_map,
                paths["image_url"],
                paths.get("plan_image_urls") or {},
                plan_transform,
            )
            await self._async_write_dashboard(cards)
        except Exception as err:  # noqa: BLE001 - always report, never crash the poll loop
            _LOGGER.error("Floorplan deploy %s failed: %s", job_id, err)
            await self._async_report(job_id, FLOORPLAN_REPORT_STATUS_FAILED, str(err))
            self._running.discard(job_id)
            return

        _LOGGER.info("Floorplan deploy %s applied; Home 3D dashboard updated", job_id)
        await self._async_report(
            job_id, FLOORPLAN_REPORT_STATUS_DONE, "Home 3D dashboard deployed"
        )
        self._notify_success()
        self._running.discard(job_id)

    # -- applying one job -------------------------------------------------

    async def _async_download_assets(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """Download this job's assets into /config/www/ha_dispatch/floorplan/.

        Four are required (unchanged contract): the hero backplate, the GLB,
        and the two manifests. Two more are optional, downloaded best-effort
        -- an older deploy job predating exact per-floor plans simply lacks
        them, and the dashboard falls back to the hero backplate plus a
        bounding-box approximation rather than failing the whole deploy.
        """
        directory = Path(self.hass.config.path(*FLOORPLAN_ASSET_DIR))
        await self.hass.async_add_executor_job(_ensure_dir, directory)

        required = (
            ("backplate_url", FLOORPLAN_BACKPLATE_NAME, "backplate"),
            ("glb_url", FLOORPLAN_GLB_NAME, "glb"),
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

        paths["image_url"] = f"{FLOORPLAN_LOCAL_URL_PREFIX}/{FLOORPLAN_BACKPLATE_NAME}"

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

    async def _async_write_dashboard(self, cards: List[Dict[str, Any]]) -> None:
        """Register (or update) the "Home 3D" storage-mode dashboard.

        Home Assistant's own lovelace integration keeps two things in
        storage: a small registry of every dashboard it knows about
        (.storage/lovelace_dashboards) and, for each storage-mode dashboard,
        that dashboard's own views/cards config (.storage/lovelace.<url_
        path>). Both are themselves Store-backed, so this writes them the
        same way the rest of this integration writes its own state --
        directly through homeassistant.helpers.storage.Store -- rather than
        reaching into hass.data["lovelace"]'s live in-memory collections.

        That is a deliberate shortcut, not an oversight: Home Assistant is
        not installed in this dev environment (see CLAUDE.md), so the exact
        shape of those live collections cannot be verified here, and
        guessing at unstable, version-specific internals is worse than a
        dashboard that needs one reload to appear. Writing storage directly
        produces a correct dashboard the next time Home Assistant (or just
        the frontend/lovelace integration) reloads it -- which a freshly
        booted demo container does anyway. A follow-up that can test against
        a real running core should prefer calling the live dashboards
        collection so an update lands without any reload at all.
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
        await self._dashboard_store.async_save({"config": build_dashboard_config(cards)})

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
        self, job_id: str, status: str, detail: Optional[str]
    ) -> None:
        """Report the outcome, tolerating a server that cannot take it."""
        try:
            await self.api.report_floorplan(
                self.installation_id, job_id, status, detail=detail
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

    def _notify_success(self) -> None:
        """An informational heads-up -- no approval needed, unlike consent prompts.

        Unlike remote_access.py's consent notifications, nothing here is
        asking the homeowner to decide anything: the deploy already happened.
        """
        try:
            persistent_notification.async_create(
                self.hass,
                "Your installer just updated your Home 3D dashboard.",
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
    "build_dashboard_cards",
    "build_dashboard_config",
    "build_picture_elements_card",
]
