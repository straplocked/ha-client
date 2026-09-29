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
            "cls": "thermostat",
            "room": "Den",
            "floor": "1st Floor",
            "floor_key": "Floor_1",
            "xy_ft": [-10.0, -5.0],
        },
        {
            "entity": "light_recessed.kitchen.01",
            "domain": "light",
            "cls": "light_recessed",
            "room": "Kitchen",
            "floor": "1st Floor",
            "floor_key": "Floor_1",
            "xy_ft": [5.0, -5.0],
        },
    ],
}

# A floor's exact feet->pixel affine transform, as build.py's
# render_dashboard_plans/_fit_transform emits it: pixel_x = a*x+b*y+c,
# pixel_y = d*x+e*y+f. Chosen so it is trivially checkable by hand -- 10
# pixels per foot, centred on a 400x300 image, no rotation.
PLAN_TRANSFORM = {
    "Floor_1": {
        "a": 10.0, "b": 0.0, "c": 200.0,
        "d": 0.0, "e": -10.0, "f": 150.0,
        "image_width_px": 400, "image_height_px": 300,
    }
}

BACKPLATE_URL = "https://dispatch.example.com/assets/backplate.png"
GLB_URL = "https://dispatch.example.com/assets/model.glb"
HOTSPOTS_URL = "https://dispatch.example.com/assets/hotspots.json"
HA_MAP_URL = "https://dispatch.example.com/assets/ha_map.json"
PLAN_TRANSFORM_URL = "https://dispatch.example.com/assets/plan_transform.json"
PLAN_BACKPLATE_URL = "https://dispatch.example.com/assets/plan_Floor_1.png"

ASSETS = {
    BACKPLATE_URL: b"\x89PNGfakepixels",
    GLB_URL: b"glTFfakemodel",
    HOTSPOTS_URL: json.dumps(HOTSPOTS),
    HA_MAP_URL: json.dumps(HA_MAP),
    PLAN_TRANSFORM_URL: json.dumps(PLAN_TRANSFORM),
    PLAN_BACKPLATE_URL: b"\x89PNGfakeplanpixels",
}


def _job(job_id=101, **overrides):
    job = {
        "id": job_id,
        "job_id": "b6b6b6b6-0000-0000-0000-000000000000",
        "backplate_url": BACKPLATE_URL,
        "glb_url": GLB_URL,
        "hotspots_url": HOTSPOTS_URL,
        "ha_map_url": HA_MAP_URL,
        "plan_transform_url": PLAN_TRANSFORM_URL,
        "plan_backplate_urls": {"Floor_1": PLAN_BACKPLATE_URL},
    }
    job.update(overrides)
    return job


def _legacy_job(job_id=101, **overrides):
    """A pre-plan-transform deploy job -- the four original assets only."""
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
        card = floorplan_mod.build_picture_elements_card(
            hass, HOTSPOTS["Floor_1"], [], "/local/x.png"
        )

        labels = sorted(e["title"] for e in card["elements"] if e["type"] == "icon")
        self.assertEqual(labels, ["Den", "Kitchen"])

    def test_a_matched_device_becomes_a_state_icon_bound_to_the_real_entity(self):
        hass = FakeHass(states=[FakeState("climate.den_thermostat", "Den Thermostat")])
        card = floorplan_mod.build_picture_elements_card(
            hass, HOTSPOTS["Floor_1"], HA_MAP["devices"], "/local/x.png"
        )

        state_icons = [e for e in card["elements"] if e["type"] == "state-icon"]
        self.assertEqual([e["entity"] for e in state_icons], ["climate.den_thermostat"])

    def test_an_unmatched_device_is_left_off_the_card_entirely(self):
        # No light entities exist at all, so the kitchen light in HA_MAP has
        # nothing to bind to and must not appear as a fabricated entity id.
        hass = FakeHass(states=[FakeState("climate.den_thermostat", "Den Thermostat")])
        card = floorplan_mod.build_picture_elements_card(
            hass, HOTSPOTS["Floor_1"], HA_MAP["devices"], "/local/x.png"
        )

        state_icon_entities = {e["entity"] for e in card["elements"] if e["type"] == "state-icon"}
        self.assertNotIn("light_recessed.kitchen.01", state_icon_entities)
        self.assertEqual(len(state_icon_entities), 1)

    def test_the_card_references_the_local_backplate_url(self):
        hass = FakeHass(states=[])
        card = floorplan_mod.build_picture_elements_card(
            hass, HOTSPOTS["Floor_1"], [], "/local/ha_dispatch/floorplan/backplate.png"
        )
        self.assertEqual(card["type"], "picture-elements")
        self.assertEqual(card["image"], "/local/ha_dispatch/floorplan/backplate.png")

    def test_a_matched_device_gets_a_domain_appropriate_icon(self):
        hass = FakeHass(states=[FakeState("climate.den_thermostat", "Den Thermostat")])
        card = floorplan_mod.build_picture_elements_card(
            hass, HOTSPOTS["Floor_1"], HA_MAP["devices"], "/local/x.png"
        )
        state_icons = [e for e in card["elements"] if e["type"] == "state-icon"]
        self.assertEqual(state_icons[0]["icon"], "mdi:thermostat")

    def test_with_no_transform_positions_fall_back_to_the_bounding_box(self):
        hass = FakeHass(states=[FakeState("climate.den_thermostat", "Den Thermostat")])
        card = floorplan_mod.build_picture_elements_card(
            hass, HOTSPOTS["Floor_1"], HA_MAP["devices"], "/local/x.png", transform=None
        )
        self.assertEqual(card["aspect_ratio"], "8:5")
        state_icon = [e for e in card["elements"] if e["type"] == "state-icon"][0]
        # Den's centroid (-10, -5) sits left-of-centre and below-centre of
        # the Floor_1 hotspot bounding box (x: -15..10, y: -10..0).
        self.assertLess(float(state_icon["style"]["left"].rstrip("%")), 50.0)

    def test_with_a_transform_positions_use_the_exact_affine_map(self):
        hass = FakeHass(states=[FakeState("climate.den_thermostat", "Den Thermostat")])
        transform = PLAN_TRANSFORM["Floor_1"]
        card = floorplan_mod.build_picture_elements_card(
            hass, HOTSPOTS["Floor_1"], HA_MAP["devices"], "/local/plan.png", transform=transform
        )
        self.assertEqual(card["aspect_ratio"], "400:300")
        state_icon = [e for e in card["elements"] if e["type"] == "state-icon"][0]
        # Den's device sits at (-10, -5) ft. pixel = (10*-10+200, -10*-5+150)
        # = (100, 200) on a 400x300 image -> (25.0%, 66.7%).
        self.assertEqual(state_icon["style"]["left"], "25.0%")
        self.assertEqual(state_icon["style"]["top"], "66.7%")

    def test_a_second_device_in_the_same_room_is_fanned_out_not_stacked(self):
        hass = FakeHass(
            states=[
                FakeState("light.den_recessed_one", "Den Recessed One"),
                FakeState("light.den_recessed_two", "Den Recessed Two"),
            ]
        )
        devices = [
            {"entity": "light_recessed.den.01", "domain": "light", "cls": "light_recessed",
             "room": "Den", "xy_ft": [-10.0, -5.0]},
            {"entity": "light_recessed.den.02", "domain": "light", "cls": "light_recessed",
             "room": "Den", "xy_ft": [-10.0, -5.0]},
        ]
        card = floorplan_mod.build_picture_elements_card(
            hass, [], devices, "/local/plan.png", transform=PLAN_TRANSFORM["Floor_1"]
        )
        positions = {(e["style"]["left"], e["style"]["top"]) for e in card["elements"]}
        self.assertEqual(len(positions), 2)


class DashboardCardsTest(unittest.TestCase):
    def test_hero_card_leads_followed_by_one_plan_card_per_floor(self):
        hass = FakeHass(states=[FakeState("climate.den_thermostat", "Den Thermostat")])
        cards = floorplan_mod.build_dashboard_cards(
            hass, HOTSPOTS, HA_MAP, "/local/hero.png",
            {"Floor_1": "/local/plan_Floor_1.png"}, PLAN_TRANSFORM,
        )
        self.assertEqual(cards[0], {"type": "picture", "image": "/local/hero.png"})
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[1]["type"], "picture-elements")
        self.assertEqual(cards[1]["image"], "/local/plan_Floor_1.png")

    def test_a_single_floor_with_no_floor_key_still_gets_its_devices(self):
        # Mirrors a manifest predating floor_key: devices only carry the
        # human "floor" label. With exactly one hotspots floor that is moot.
        legacy_map = {"devices": [dict(d) for d in HA_MAP["devices"]]}
        for d in legacy_map["devices"]:
            d.pop("floor_key", None)
        hass = FakeHass(states=[FakeState("climate.den_thermostat", "Den Thermostat")])
        cards = floorplan_mod.build_dashboard_cards(
            hass, HOTSPOTS, legacy_map, "/local/hero.png", {}, {}
        )
        plan_card = cards[-1]
        state_icons = [e for e in plan_card["elements"] if e["type"] == "state-icon"]
        self.assertEqual(len(state_icons), 1)

    def test_with_no_hero_url_the_hero_card_is_omitted(self):
        hass = FakeHass(states=[])
        cards = floorplan_mod.build_dashboard_cards(
            hass, HOTSPOTS, {"devices": []}, None, {"Floor_1": "/local/plan.png"}, {}
        )
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["type"], "picture-elements")


class DashboardConfigTest(unittest.TestCase):
    def test_the_view_is_a_panel_with_a_single_vertical_stack(self):
        config = floorplan_mod.build_dashboard_config(
            [{"type": "picture", "image": "/local/hero.png"}, {"type": "picture-elements"}]
        )
        view = config["views"][0]
        self.assertTrue(view["panel"])
        self.assertEqual(view["path"], "home-3d")
        stack = view["cards"][0]
        self.assertEqual(stack["type"], "vertical-stack")
        self.assertEqual(len(stack["cards"]), 2)


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
            self.assertTrue(os.path.isfile(os.path.join(asset_dir, "plan_transform.json")))
            self.assertTrue(os.path.isfile(os.path.join(asset_dir, "plan_Floor_1.png")))

            self.assertEqual(len(api.reports), 1)
            self.assertEqual(api.reports[0]["status"], "done")
            self.assertEqual(api.reports[0]["job_id"], "101")

            registry = manager._dashboards_store.data
            self.assertEqual(len(registry["items"]), 1)
            self.assertEqual(registry["items"][0]["url_path"], "home-3d")
            self.assertEqual(registry["items"][0]["title"], "Home 3D")
            self.assertEqual(registry["items"][0]["mode"], "storage")

            dashboard = manager._dashboard_store.data
            view = dashboard["config"]["views"][0]
            self.assertTrue(view["panel"])
            stack = view["cards"][0]
            self.assertEqual(stack["type"], "vertical-stack")
            # Hero picture card first, then the one Floor_1 plan card.
            self.assertEqual(stack["cards"][0]["type"], "picture")
            plan_card = stack["cards"][1]
            self.assertEqual(plan_card["type"], "picture-elements")
            entities = [e["entity"] for e in plan_card["elements"] if e["type"] == "state-icon"]
            self.assertEqual(entities, ["climate.den_thermostat"])

            self.assertEqual(len(notifications.created), 1)
            self.assertEqual(notifications.created[0]["notification_id"], const.FLOORPLAN_NOTIFICATION_ID)

            self.assertEqual(manager._running, set())

    def test_a_legacy_job_with_no_plan_transform_still_deploys(self):
        # An older deploy job predating exact per-floor plans: the dashboard
        # must still come together, using the hero backplate and the old
        # bounding-box approximation instead of failing the deploy.
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(
                api, tmp, states=[FakeState("climate.den_thermostat", "Den Thermostat")]
            )

            _run(manager._async_run(_legacy_job()))

            self.assertEqual(api.reports[-1]["status"], "done")
            dashboard = manager._dashboard_store.data
            stack = dashboard["config"]["views"][0]["cards"][0]
            plan_card = stack["cards"][-1]
            self.assertEqual(plan_card["image"], "/local/ha_dispatch/floorplan/backplate.png")
            entities = [e["entity"] for e in plan_card["elements"] if e["type"] == "state-icon"]
            self.assertEqual(entities, ["climate.den_thermostat"])

    def test_redeploying_updates_the_same_dashboard_registry_entry_not_a_second_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[])

            _run(manager._async_run(_job(job_id=1)))
            _run(manager._async_run(_job(job_id=2)))

            registry = manager._dashboards_store.data
            self.assertEqual(len(registry["items"]), 1)


websocket_api_mod = sys.modules["homeassistant.components.websocket_api"]
lovelace_const_mod = sys.modules["homeassistant.components.lovelace.const"]
lovelace_dashboard_mod = sys.modules["homeassistant.components.lovelace.dashboard"]
frontend_mod = sys.modules["homeassistant.components.frontend"]


class FakeDashboardsCollection:
    """Mimics lovelace's live DashboardsCollection well enough to exercise
    floorplan.py's create/update path, including the side effect its real
    change listener has: a LovelaceStorage object appearing in (or being
    updated in place within) hass.data[LOVELACE_DATA].dashboards."""

    def __init__(self, hass, lovelace_data):
        self.hass = hass
        self.lovelace_data = lovelace_data
        self.data = {}
        self.create_calls = []
        self.update_calls = []

    async def async_create_item(self, data):
        item_id = data["url_path"]
        item = {"id": item_id, **data}
        self.data[item_id] = item
        self.create_calls.append(item)
        self.lovelace_data.dashboards[item_id] = lovelace_dashboard_mod.LovelaceStorage(
            self.hass, item
        )
        return item

    async def async_update_item(self, item_id, updates):
        item = dict(self.data[item_id])
        item.update(updates)
        self.data[item_id] = item
        self.update_calls.append(item)
        # Real lovelace just updates .config on the existing live object.
        self.lovelace_data.dashboards[item_id].config = item
        return item


class FakeDashboardsCollectionWebsocket:
    """Stands in for the object whose bound ws_list_item floorplan.py
    reaches through inspect.unwrap(handler).__self__.storage_collection."""

    def __init__(self, storage_collection):
        self.storage_collection = storage_collection

    def ws_list_item(self, hass, connection, msg):
        """Never actually called by these tests -- only its __self__ is."""


class FakeLovelaceData:
    def __init__(self):
        self.dashboards = {}


def _wire_live_lovelace(hass):
    """Register the live lovelace collection the way lovelace/__init__.py
    does, so floorplan.py's websocket-handler lookup succeeds."""
    lovelace_data = FakeLovelaceData()
    storage_collection = FakeDashboardsCollection(hass, lovelace_data)
    ws = FakeDashboardsCollectionWebsocket(storage_collection)
    hass.data[websocket_api_mod.DOMAIN] = {
        "lovelace/dashboards/list": (ws.ws_list_item, None),
    }
    hass.data[lovelace_const_mod.LOVELACE_DATA] = lovelace_data
    return storage_collection, lovelace_data


class LiveDashboardTest(unittest.TestCase):
    """The fix: a deploy should register/update the dashboard through HA's
    live lovelace objects so it appears without a restart, falling back to
    the old direct-Store-write behaviour only if that live path is
    unreachable."""

    def test_a_new_deploy_registers_through_the_live_collection_with_no_restart_needed(self):
        notifications.reset()
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[])
            storage_collection, lovelace_data = _wire_live_lovelace(hass)

            _run(manager._async_run(_job()))

            self.assertEqual(api.reports[-1]["status"], "done")

            self.assertEqual(len(storage_collection.create_calls), 1)
            item = storage_collection.data["home-3d"]
            self.assertEqual(item["title"], "Home 3D")
            self.assertEqual(item["mode"], "storage")
            self.assertTrue(item["show_in_sidebar"])
            self.assertFalse(item["require_admin"])

            live_dashboard = lovelace_data.dashboards["home-3d"]
            self.assertEqual(len(live_dashboard.saved_configs), 1)
            self.assertIn("views", live_dashboard.saved_configs[-1])

            # The live path worked, so the old direct-Store fallback must
            # never have run.
            self.assertIsNone(manager._dashboards_store.data)
            self.assertIsNone(manager._dashboard_store.data)

            self.assertEqual(len(notifications.created), 1)
            self.assertNotIn("Restart", notifications.created[0]["message"])

    def test_redeploying_updates_the_live_dashboard_instead_of_creating_a_second_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[])
            storage_collection, lovelace_data = _wire_live_lovelace(hass)

            _run(manager._async_run(_job(job_id=1)))
            _run(manager._async_run(_job(job_id=2)))

            self.assertEqual(len(storage_collection.data), 1)
            self.assertEqual(len(storage_collection.create_calls), 1)
            self.assertEqual(len(storage_collection.update_calls), 1)

            live_dashboard = lovelace_data.dashboards["home-3d"]
            self.assertEqual(len(live_dashboard.saved_configs), 2)

    def test_when_the_live_websocket_handler_is_missing_it_falls_back_and_flags_a_restart(self):
        notifications.reset()
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[])
            # No websocket handler, no LOVELACE_DATA at all -- the worst case.

            _run(manager._async_run(_job()))

            registry = manager._dashboards_store.data
            self.assertEqual(registry["items"][0]["url_path"], "home-3d")
            dashboard = manager._dashboard_store.data
            self.assertIn("views", dashboard["config"])

            self.assertEqual(len(notifications.created), 1)
            self.assertIn("Restart Home Assistant to see it", notifications.created[0]["message"])

    def test_when_only_the_websocket_handler_is_missing_the_fallback_still_goes_live(self):
        # LOVELACE_DATA exists (lovelace is loaded) but, hypothetically, the
        # lovelace/dashboards/list handler could not be found -- the
        # best-effort panel splice in the fallback should still manage to
        # make the dashboard visible without a restart.
        notifications.reset()
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[])
            lovelace_data = FakeLovelaceData()
            hass.data[lovelace_const_mod.LOVELACE_DATA] = lovelace_data
            frontend_mod.reset()

            _run(manager._async_run(_job()))

            # The old direct-Store write still happened...
            registry = manager._dashboards_store.data
            self.assertEqual(registry["items"][0]["url_path"], "home-3d")

            # ...but so did the best-effort live splice.
            live_dashboard = lovelace_data.dashboards["home-3d"]
            self.assertEqual(len(live_dashboard.saved_configs), 1)
            self.assertTrue(frontend_mod.registered_panels)
            self.assertEqual(
                frontend_mod.registered_panels[-1]["frontend_url_path"], "home-3d"
            )

            self.assertEqual(len(notifications.created), 1)
            self.assertNotIn("Restart", notifications.created[0]["message"])

    def test_a_storage_collection_missing_the_expected_attribute_falls_back_cleanly(self):
        # A future Home Assistant renames or removes storage_collection off
        # the websocket handler's __self__ -- the lookup must degrade to the
        # file fallback rather than raising.
        notifications.reset()
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[])

            class NoCollectionWebsocket:
                def ws_list_item(self, hass, connection, msg):
                    pass

            ws = NoCollectionWebsocket()
            hass.data[websocket_api_mod.DOMAIN] = {
                "lovelace/dashboards/list": (ws.ws_list_item, None),
            }

            _run(manager._async_run(_job()))

            self.assertEqual(api.reports[-1]["status"], "done")
            registry = manager._dashboards_store.data
            self.assertEqual(registry["items"][0]["url_path"], "home-3d")


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
