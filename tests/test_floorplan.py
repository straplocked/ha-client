"""Tests for the floorplan deploy feature.

The feature these cover: a technician uploads a floor-plan sketch to HA
Dispatch; a render pipeline there turns it into a GLB model, a backplate
render, a room hotspot map, and a device placement map; this agent notices the
job, downloads the four assets into /config/www/, builds a "Home 3D" Lovelace
dashboard from them, and reports back -- resolving ha_map.json's invented
device slugs against real entities and skipping anything with no confident
match rather than fabricating an entity id.

Home Assistant is not installed here; tests/conftest.py stubs the symbols the
integration imports, including a recording persistent_notification and an
in-memory Store. Network and disk access go through fakes defined below.
"""
import asyncio
import importlib
import json
import os
import sys
import tempfile
import types
import unittest

import aiohttp


def _load(module: str):
    base = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "custom_components", "ha_dispatch_client",
    )
    if "ha_dispatch_client" not in sys.modules:
        pkg = types.ModuleType("ha_dispatch_client")
        pkg.__path__ = [base]
        sys.modules["ha_dispatch_client"] = pkg
    return importlib.import_module(f"ha_dispatch_client.{module}")


api_client = _load("api_client")
const = _load("const")
floorplan_mod = _load("floorplan")

HADispatchFloorplan = floorplan_mod.HADispatchFloorplan
FloorplanUnavailable = api_client.FloorplanUnavailable

notifications = sys.modules["homeassistant.components.persistent_notification"]


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --- fixtures ------------------------------------------------------------

HOTSPOTS = {
    "Floor_1": [
        {
            "name": "Den",
            "sf": 200.0,
            "centroid": [-10.0, -5.0],
            "poly_ft": [[-15.0, -10.0], [-5.0, -10.0], [-5.0, 0.0], [-15.0, 0.0]],
        },
        {
            "name": "Kitchen",
            "sf": 150.0,
            "centroid": [5.0, -5.0],
            "poly_ft": [[0.0, -10.0], [10.0, -10.0], [10.0, 0.0], [0.0, 0.0]],
        },
    ]
}

HA_MAP = {
    "source": "Xactimate ESX sketch (surveyed)",
    "units": "feet",
    "devices": [
        {
            "entity": "thermostat.den.01",
            "domain": "climate",
            "room": "Den",
            "floor": "1st Floor",
            "xy_ft": [-10.0, -5.0],
        },
        {
            "entity": "light_recessed.kitchen.01",
            "domain": "light",
            "room": "Kitchen",
            "floor": "1st Floor",
            "xy_ft": [5.0, -5.0],
        },
    ],
}

BACKPLATE_URL = "https://dispatch.example.com/assets/backplate.png"
GLB_URL = "https://dispatch.example.com/assets/model.glb"
HOTSPOTS_URL = "https://dispatch.example.com/assets/hotspots.json"
HA_MAP_URL = "https://dispatch.example.com/assets/ha_map.json"

ASSETS = {
    BACKPLATE_URL: b"\x89PNGfakepixels",
    GLB_URL: b"glTFfakemodel",
    HOTSPOTS_URL: json.dumps(HOTSPOTS),
    HA_MAP_URL: json.dumps(HA_MAP),
}


def _job(job_id=101, **overrides):
    job = {
        "id": job_id,
        "job_id": "b6b6b6b6-0000-0000-0000-000000000000",
        "backplate_url": BACKPLATE_URL,
        "glb_url": GLB_URL,
        "hotspots_url": HOTSPOTS_URL,
        "ha_map_url": HA_MAP_URL,
    }
    job.update(overrides)
    return job


# --- fakes -----------------------------------------------------------------


class FakeApi:
    """Records reports and serves/rejects asset downloads from a fixed map."""

    def __init__(
        self,
        assets=None,
        pending=None,
        pending_error=None,
        download_errors=None,
    ):
        self._assets = assets or {}
        self._pending = pending
        self._pending_error = pending_error
        self._download_errors = download_errors or {}
        self.reports = []

    async def fetch_pending_floorplan(self, installation_id):
        if self._pending_error is not None:
            raise self._pending_error
        return self._pending

    async def report_floorplan(self, installation_id, job_id, status, detail=None):
        self.reports.append({"job_id": job_id, "status": status, "detail": detail})
        return {"status": "ok"}

    async def download_floorplan_asset(self, url, dest_path):
        if url in self._download_errors:
            raise self._download_errors[url]
        content = self._assets.get(url, b"")
        mode = "w" if isinstance(content, str) else "wb"
        with open(dest_path, mode) as handle:
            handle.write(content)
        return len(content)


class FakeCoordinator:
    def __init__(self, api):
        self.api_client = api
        self.installation_id = "inst-1"


class FakeState:
    def __init__(self, entity_id, name=None):
        self.entity_id = entity_id
        self.name = name or entity_id


class FakeStates:
    def __init__(self, states=None):
        self._states = states or []

    def async_all(self, domain=None):
        if domain is None:
            return list(self._states)
        return [s for s in self._states if s.entity_id.split(".", 1)[0] == domain]


class FakeConfig:
    def __init__(self, base_dir):
        self._base_dir = base_dir

    def path(self, *parts):
        return os.path.join(self._base_dir, *parts)


class FakeHass:
    """Records scheduled tasks without running them, and fakes the executor
    as a plain synchronous call -- there is no real event loop contention to
    model here."""

    def __init__(self, base_dir=".", states=None):
        self.data = {}
        self.config = FakeConfig(base_dir)
        self.states = FakeStates(states)
        self.scheduled = []

    def async_create_task(self, coro, name=None):
        coro.close()
        self.scheduled.append(name)
        return object()

    async def async_add_executor_job(self, func, *args):
        return func(*args)


def _manager(api, base_dir=".", states=None):
    hass = FakeHass(base_dir, states=states)
    return HADispatchFloorplan(hass, FakeCoordinator(api)), hass


# --- entity matching ---------------------------------------------------------


class EntityMatchingTest(unittest.TestCase):
    def test_a_device_with_no_matching_entity_is_skipped_not_fabricated(self):
        hass = FakeHass(states=[])
        device = {"domain": "light", "room": "Kitchen"}
        self.assertIsNone(floorplan_mod.async_match_entity(hass, device))

    def test_a_device_with_entities_in_other_domains_only_is_skipped(self):
        # A climate entity named "Kitchen" exists, but the device we're
        # matching wants a light -- domain has to match, not just the words.
        hass = FakeHass(states=[FakeState("climate.kitchen_thermostat", "Kitchen Thermostat")])
        device = {"domain": "light", "room": "Kitchen"}
        self.assertIsNone(floorplan_mod.async_match_entity(hass, device))

    def test_a_device_matches_on_a_shared_room_word(self):
        hass = FakeHass(states=[FakeState("climate.den_thermostat", "Den Thermostat")])
        device = {"domain": "climate", "room": "Den"}
        self.assertEqual(
            floorplan_mod.async_match_entity(hass, device), "climate.den_thermostat"
        )

    def test_the_best_scoring_candidate_wins_over_a_weaker_one(self):
        hass = FakeHass(
            states=[
                FakeState("light.hallway_light", "Hallway Light"),
                FakeState("light.den_recessed_light", "Den Recessed Light"),
            ]
        )
        device = {"domain": "light", "room": "Den"}
        self.assertEqual(
            floorplan_mod.async_match_entity(hass, device), "light.den_recessed_light"
        )


# --- card building -----------------------------------------------------------


class CardBuildingTest(unittest.TestCase):
    def test_room_hotspots_become_labeled_points_regardless_of_entities(self):
        hass = FakeHass(states=[])
        card = floorplan_mod.build_picture_elements_card(hass, HOTSPOTS, {"devices": []}, "/local/x.png")

        labels = sorted(e["title"] for e in card["elements"] if e["type"] == "icon")
        self.assertEqual(labels, ["Den", "Kitchen"])

    def test_a_matched_device_becomes_a_state_icon_bound_to_the_real_entity(self):
        hass = FakeHass(states=[FakeState("climate.den_thermostat", "Den Thermostat")])
        card = floorplan_mod.build_picture_elements_card(hass, HOTSPOTS, HA_MAP, "/local/x.png")

        state_icons = [e for e in card["elements"] if e["type"] == "state-icon"]
        self.assertEqual([e["entity"] for e in state_icons], ["climate.den_thermostat"])

    def test_an_unmatched_device_is_left_off_the_card_entirely(self):
        # No light entities exist at all, so the kitchen light in HA_MAP has
        # nothing to bind to and must not appear as a fabricated entity id.
        hass = FakeHass(states=[FakeState("climate.den_thermostat", "Den Thermostat")])
        card = floorplan_mod.build_picture_elements_card(hass, HOTSPOTS, HA_MAP, "/local/x.png")

        state_icon_entities = {e["entity"] for e in card["elements"] if e["type"] == "state-icon"}
        self.assertNotIn("light_recessed.kitchen.01", state_icon_entities)
        self.assertEqual(len(state_icon_entities), 1)

    def test_the_card_references_the_local_backplate_url(self):
        hass = FakeHass(states=[])
        card = floorplan_mod.build_picture_elements_card(
            hass, HOTSPOTS, {"devices": []}, "/local/ha_dispatch/floorplan/backplate.png"
        )
        self.assertEqual(card["type"], "picture-elements")
        self.assertEqual(card["image"], "/local/ha_dispatch/floorplan/backplate.png")


# --- the full deploy pipeline -------------------------------------------------


class SuccessfulDeployTest(unittest.TestCase):
    def test_a_successful_deploy_downloads_assets_writes_the_dashboard_and_reports_done(self):
        notifications.reset()
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(
                api, tmp, states=[FakeState("climate.den_thermostat", "Den Thermostat")]
            )

            _run(manager._async_run(_job()))

            asset_dir = os.path.join(tmp, "www", "ha_dispatch", "floorplan")
            self.assertTrue(os.path.isfile(os.path.join(asset_dir, "backplate.png")))
            self.assertTrue(os.path.isfile(os.path.join(asset_dir, "model.glb")))
            self.assertTrue(os.path.isfile(os.path.join(asset_dir, "hotspots.json")))
            self.assertTrue(os.path.isfile(os.path.join(asset_dir, "ha_map.json")))

            self.assertEqual(len(api.reports), 1)
            self.assertEqual(api.reports[0]["status"], "done")
            self.assertEqual(api.reports[0]["job_id"], "101")

            registry = manager._dashboards_store.data
            self.assertEqual(len(registry["items"]), 1)
            self.assertEqual(registry["items"][0]["url_path"], "home-3d")
            self.assertEqual(registry["items"][0]["title"], "Home 3D")
            self.assertEqual(registry["items"][0]["mode"], "storage")

            dashboard = manager._dashboard_store.data
            card = dashboard["config"]["views"][0]["cards"][0]
            self.assertEqual(card["type"], "picture-elements")
            entities = [e["entity"] for e in card["elements"] if e["type"] == "state-icon"]
            self.assertEqual(entities, ["climate.den_thermostat"])

            self.assertEqual(len(notifications.created), 1)
            self.assertEqual(notifications.created[0]["notification_id"], const.FLOORPLAN_NOTIFICATION_ID)

            self.assertEqual(manager._running, set())

    def test_redeploying_updates_the_same_dashboard_registry_entry_not_a_second_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[])

            _run(manager._async_run(_job(job_id=1)))
            _run(manager._async_run(_job(job_id=2)))

            registry = manager._dashboards_store.data
            self.assertEqual(len(registry["items"]), 1)


class FailedDeployTest(unittest.TestCase):
    def test_a_download_error_is_reported_as_failed_with_no_notification(self):
        notifications.reset()
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(
                assets=ASSETS,
                download_errors={GLB_URL: aiohttp.ClientError("connection reset")},
            )
            manager, hass = _manager(api, tmp, states=[])

            _run(manager._async_run(_job()))

            self.assertEqual(len(api.reports), 1)
            self.assertEqual(api.reports[0]["status"], "failed")
            self.assertIn("connection reset", api.reports[0]["detail"])
            self.assertEqual(notifications.created, [])
            self.assertEqual(manager._running, set())

    def test_a_job_missing_an_asset_url_is_reported_as_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[])

            incomplete = _job()
            del incomplete["glb_url"]

            _run(manager._async_run(incomplete))

            self.assertEqual(api.reports[-1]["status"], "failed")
            self.assertIn("glb_url", api.reports[-1]["detail"])

    def test_a_malformed_manifest_is_reported_as_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_assets = dict(ASSETS)
            bad_assets[HOTSPOTS_URL] = json.dumps([1, 2, 3])  # not a dict
            api = FakeApi(assets=bad_assets)
            manager, hass = _manager(api, tmp, states=[])

            _run(manager._async_run(_job()))

            self.assertEqual(api.reports[-1]["status"], "failed")
            self.assertIn("hotspots.json", api.reports[-1]["detail"])

    def test_no_notification_ever_fires_on_a_failed_deploy(self):
        notifications.reset()
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets={}, download_errors={BACKPLATE_URL: ValueError("boom")})
            manager, hass = _manager(api, tmp, states=[])

            _run(manager._async_run(_job()))

            self.assertEqual(notifications.created, [])


# --- the poll loop -------------------------------------------------------------


class PollLoopTest(unittest.TestCase):
    def test_a_pending_deploy_is_scheduled(self):
        api = FakeApi(pending=_job())
        manager, hass = _manager(api)

        _run(manager.async_poll_pending())

        self.assertEqual(len(hass.scheduled), 1)
        self.assertEqual(manager._running, {"101"})

    def test_a_job_already_running_is_not_restarted(self):
        api = FakeApi(pending=_job())
        manager, hass = _manager(api)
        manager._running.add("101")

        _run(manager.async_poll_pending())

        self.assertEqual(hass.scheduled, [])

    def test_nothing_pending_schedules_nothing(self):
        api = FakeApi(pending=None)
        manager, hass = _manager(api)

        _run(manager.async_poll_pending())

        self.assertEqual(hass.scheduled, [])

    def test_a_server_without_floorplan_deploy_stops_being_asked(self):
        api = FakeApi(pending_error=FloorplanUnavailable("no endpoint"))
        manager, hass = _manager(api)

        _run(manager.async_poll_pending())

        self.assertFalse(manager.available)

    def test_a_transient_fetch_failure_leaves_the_manager_enabled(self):
        api = FakeApi(pending_error=TimeoutError())
        manager, hass = _manager(api)

        _run(manager.async_poll_pending())

        self.assertTrue(manager.available)
        self.assertEqual(manager._running, set())

    def test_a_disabled_manager_does_not_poll_at_all(self):
        api = FakeApi(pending=_job())
        manager, hass = _manager(api)
        manager.async_disable()

        _run(manager.async_poll_pending())

        self.assertEqual(hass.scheduled, [])


if __name__ == "__main__":
    unittest.main()
