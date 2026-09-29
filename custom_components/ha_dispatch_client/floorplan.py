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

    ASSUMPTION -- undocumented anywhere in the render pipeline, and worth
    flagging loudly: the backplate used as the card's background is the
    pipeline's "hero" 3/4-perspective render (build.py's VIEWS[0], az=205
    elev=38), not its orthographic top-down "05_plan" view. There is no exact
    linear map from flat plan feet to a perspective image's pixels; a true
    mapping would need the same camera projection build.py uses to place its
    cameras, which is out of scope here. This linearly rescales the plan's
    own bounding box to 0-100% instead. That keeps hotspots and devices
    positioned consistently with each other and roughly with the floor
    plan's layout, but not precisely with the perspective backplate
    underneath -- good enough for a demo, not a finished feature.

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


# --- card building ----------------------------------------------------------


def build_picture_elements_card(
    hass: HomeAssistant,
    hotspots_by_floor: Dict[str, List[Dict[str, Any]]],
    ha_map: Dict[str, Any],
    image_url: str,
) -> Dict[str, Any]:
    """Build the picture-elements card: backplate, room labels, device icons.

    Room hotspots have no entity behind them, so each degrades to a labeled
    point at its centroid -- an icon element with a tooltip title, coloured
    with a theme variable rather than a hard-coded hex so it reads in both
    light and dark companion-app themes. Devices use `state-icon`, which
    already reflects the bound entity's state and colour per the active
    theme, so no extra styling is needed there.
    """
    bounds = _bounding_box(hotspots_by_floor)
    elements: List[Dict[str, Any]] = []

    if bounds is not None:
        for rooms in hotspots_by_floor.values():
            if not isinstance(rooms, list):
                continue
            for room in rooms:
                if not isinstance(room, dict):
                    continue
                centroid = room.get("centroid")
                name = room.get("name")
                if not centroid or len(centroid) < 2 or not name:
                    continue
                left, top = _project(centroid[0], centroid[1], bounds)
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

        for device in ha_map.get("devices") or []:
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

            left, top = _project(xy[0], xy[1], bounds)
            elements.append(
                {
                    "type": "state-icon",
                    "entity": entity_id,
                    "style": {
                        "top": f"{top}%",
                        "left": f"{left}%",
                    },
                    "tap_action": {"action": "more-info"},
                }
            )
    else:
        _LOGGER.warning("Floorplan hotspots carried no usable coordinates; card has no elements")

    return {
        "type": "picture-elements",
        "image": image_url,
        # 8:5 matches the render pipeline's 1600x1000 output (build.py's
        # RES). A fixed ratio keeps the hotspot/device percentages meaningful
        # and keeps the card from rendering tall and narrow on a phone-width
        # companion-app view. Sizing is not verified on an actual tablet.
        "aspect_ratio": "8:5",
        "elements": elements,
    }


def build_dashboard_config(card: Dict[str, Any]) -> Dict[str, Any]:
    """The dashboard's views/cards config, as Lovelace storage mode expects it."""
    return {
        "title": FLOORPLAN_DASHBOARD_TITLE,
        "views": [
            {
                "title": FLOORPLAN_DASHBOARD_TITLE,
                "path": FLOORPLAN_DASHBOARD_URL_PATH,
                "icon": FLOORPLAN_DASHBOARD_ICON,
                "cards": [card],
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
            hotspots, ha_map = await self._async_load_manifests(paths)
            card = build_picture_elements_card(
                self.hass, hotspots, ha_map, paths["image_url"]
            )
            await self._async_write_dashboard(card)
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
        """Download the four assets into /config/www/ha_dispatch/floorplan/."""
        directory = Path(self.hass.config.path(*FLOORPLAN_ASSET_DIR))
        await self.hass.async_add_executor_job(_ensure_dir, directory)

        downloads = (
            ("backplate_url", FLOORPLAN_BACKPLATE_NAME, "backplate"),
            ("glb_url", FLOORPLAN_GLB_NAME, "glb"),
            ("hotspots_url", FLOORPLAN_HOTSPOTS_NAME, "hotspots"),
            ("ha_map_url", FLOORPLAN_HA_MAP_NAME, "ha_map"),
        )

        paths: Dict[str, Any] = {}
        for url_key, filename, result_key in downloads:
            url = job.get(url_key)
            if not url:
                raise FloorplanApplyError(f"Deploy job carried no {url_key}")
            dest = directory / filename
            await self.api.download_floorplan_asset(url, dest)
            paths[result_key] = dest

        paths["image_url"] = f"{FLOORPLAN_LOCAL_URL_PREFIX}/{FLOORPLAN_BACKPLATE_NAME}"
        return paths

    # -- dashboard storage ----------------------------------------------------

    async def _async_write_dashboard(self, card: Dict[str, Any]) -> None:
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
        await self._dashboard_store.async_save({"config": build_dashboard_config(card)})

    async def _async_load_manifests(
        self, paths: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Read the two downloaded JSON manifests, off the event loop."""
        try:
            hotspots = await self.hass.async_add_executor_job(_read_json, paths["hotspots"])
            ha_map = await self.hass.async_add_executor_job(_read_json, paths["ha_map"])
        except _SOFT_ERRORS as err:
            raise FloorplanApplyError(f"Could not read a floorplan manifest: {err}") from err

        if not isinstance(hotspots, dict):
            raise FloorplanApplyError("hotspots.json did not contain a floor map")
        if not isinstance(ha_map, dict):
            raise FloorplanApplyError("ha_map.json did not contain a device map")

        return hotspots, ha_map

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
    "build_dashboard_config",
    "build_picture_elements_card",
]
