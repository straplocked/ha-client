"""Tests for the floorplan deploy feature.

The feature these cover: a technician uploads a floor-plan sketch to HA
Dispatch; a render pipeline there turns it into a top-down plan render per
floor, a room hotspot map, and a device placement map; this agent notices the
job, downloads those assets into /config/www/, builds a "Home 3D" Lovelace
dashboard from them -- one view per floor -- and reports back, resolving
ha_map.json's invented device slugs against real entities and skipping
anything with no confident match rather than fabricating an entity id. The
pipeline's GLB model and hero render are the installer's preview on the
Dispatch side and are never downloaded here.

Home Assistant is not installed here; tests/conftest.py stubs the symbols the
integration imports, including a recording persistent_notification and an
in-memory Store. Network and disk access go through fakes defined below.
"""
import asyncio
import importlib
import json
import os
import re
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
        "name": "1st Floor", "elev": 0.0,
    }
}

# Two storeys, written into hotspots.json upper floor first on purpose: the
# dashboard orders views by elevation, not by whatever order the manifest
# happens to be in.
TWO_FLOOR_HOTSPOTS = {
    "Floor_2": [{"name": "Loft", "sf": 100.0, "centroid": [0.0, 0.0],
                 "poly_ft": [[-5.0, -5.0], [5.0, -5.0], [5.0, 5.0], [-5.0, 5.0]]}],
    "Floor_1": HOTSPOTS["Floor_1"],
}
TWO_FLOOR_TRANSFORM = {
    "Floor_1": dict(PLAN_TRANSFORM["Floor_1"]),
    "Floor_2": dict(PLAN_TRANSFORM["Floor_1"], name="2nd Floor", elev=9.5),
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
        # Every asset the client asked the server for. What proves a
        # rebuild from disk is really a rebuild from disk.
        self.downloads = []

    async def fetch_pending_floorplan(self, installation_id):
        if self._pending_error is not None:
            raise self._pending_error
        return self._pending

    async def report_floorplan(self, installation_id, job_id, status, detail=None, devices=None):
        self.reports.append(
            {"job_id": job_id, "status": status, "detail": detail, "devices": devices}
        )
        return {"status": "ok"}

    async def download_floorplan_asset(self, url, dest_path):
        self.downloads.append(url)
        if url in self._download_errors:
            raise self._download_errors[url]
        content = self._assets.get(url, b"")
        mode = "w" if isinstance(content, str) else "wb"
        with open(dest_path, mode) as handle:
            handle.write(content)
        return len(content)


class FakeCoordinator:
    def __init__(self, api, client_version=None):
        self.api_client = api
        self.installation_id = "inst-1"
        # What the real coordinator carries once setup has read the installed
        # manifest; the room-plan card's cache-busting URL is stamped with it.
        self.client_version = client_version


class FakeState:
    """Stand-in for homeassistant.core.State.

    Extra keyword arguments land in `attributes`, the way a real state
    carries `device_class` -- which is what tells an indoor temperature
    readout apart from a humidity sensor that happens to share a room word.
    A state constructed without any declares none, exactly like an entity
    whose integration never set one.
    """

    def __init__(self, entity_id, name=None, **attributes):
        self.entity_id = entity_id
        self.name = name or entity_id
        self.attributes = attributes


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


def _manager(api, base_dir=".", states=None, client_version=None):
    hass = FakeHass(base_dir, states=states)
    return HADispatchFloorplan(hass, FakeCoordinator(api, client_version)), hass


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


class FakeRegistryEntry:
    def __init__(self, entity_id, area_id=None, device_id=None):
        self.entity_id = entity_id
        self.area_id = area_id
        self.device_id = device_id


class FakeRegistry:
    """Stands in for the entity, device and area registries at once: the
    three attributes async_get() callers reach for are all here."""

    def __init__(self, entities=(), devices=None, areas=None):
        self.entities = {e.entity_id: e for e in entities}
        self._devices = devices or {}
        self._areas = areas or {}

    def async_get(self, device_id):
        return self._devices.get(device_id)

    def async_get_area(self, area_id):
        return self._areas.get(area_id)


class FakeArea:
    def __init__(self, name):
        self.name = name


class FakeDevice:
    def __init__(self, area_id):
        self.area_id = area_id


def _install_registries(registry):
    """Register stub area/device/entity registry helpers for one test, all
    returning the same FakeRegistry, and hand back the undo."""
    names = ("area_registry", "device_registry", "entity_registry")
    helpers = sys.modules["homeassistant.helpers"]
    saved = {}
    for name in names:
        full = f"homeassistant.helpers.{name}"
        saved[name] = (sys.modules.get(full), getattr(helpers, name, None))
        module = types.ModuleType(full)
        module.async_get = lambda hass, _r=registry: _r
        sys.modules[full] = module
        setattr(helpers, name, module)

    def undo():
        for name, (module, attr) in saved.items():
            full = f"homeassistant.helpers.{name}"
            if module is None:
                sys.modules.pop(full, None)
            else:
                sys.modules[full] = module
            if attr is None:
                if hasattr(helpers, name):
                    delattr(helpers, name)
            else:
                setattr(helpers, name, attr)

    return undo


class MatchingRulesTest(unittest.TestCase):
    """The first real deploy showed no icons at all; these are the rules
    added to put one there without ever guessing an entity id."""

    def test_the_only_entity_in_a_domain_binds_regardless_of_room(self):
        hass = FakeHass(states=[FakeState("climate.hallway", "Ecobee")])
        device = {"entity": "thermostat.den.01", "domain": "climate", "room": "Den"}
        self.assertEqual(floorplan_mod.async_match_entity(hass, device), "climate.hallway")

    def test_with_several_candidates_a_shared_room_word_is_still_required(self):
        hass = FakeHass(states=[FakeState("light.hue_lamp_3", "Hue lamp 3"),
                                FakeState("light.hue_lamp_4", "Hue lamp 4")])
        device = {"entity": "light.den.01", "domain": "light", "room": "Den"}
        self.assertIsNone(floorplan_mod.async_match_entity(hass, device))

    def test_the_area_an_entity_is_filed_under_counts_as_a_room_word(self):
        hass = FakeHass(states=[FakeState("light.hue_lamp_3", "Hue lamp 3"),
                                FakeState("light.hue_lamp_4", "Hue lamp 4")])
        registry = FakeRegistry(
            entities=[FakeRegistryEntry("light.hue_lamp_3", area_id="den"),
                      FakeRegistryEntry("light.hue_lamp_4", area_id="kitchen")],
            areas={"den": FakeArea("Den"), "kitchen": FakeArea("Kitchen")},
        )
        undo = _install_registries(registry)
        self.addCleanup(undo)
        device = {"entity": "light.den.01", "domain": "light", "room": "Den"}
        self.assertEqual(floorplan_mod.async_match_entity(hass, device), "light.hue_lamp_3")

    def test_an_entity_inherits_its_devices_area(self):
        hass = FakeHass(states=[FakeState("light.hue_lamp_3", "Hue lamp 3"),
                                FakeState("light.hue_lamp_4", "Hue lamp 4")])
        registry = FakeRegistry(
            entities=[FakeRegistryEntry("light.hue_lamp_3", device_id="bridge-3"),
                      FakeRegistryEntry("light.hue_lamp_4", device_id="bridge-4")],
            devices={"bridge-3": FakeDevice("kitchen"), "bridge-4": FakeDevice("den")},
            areas={"den": FakeArea("Den"), "kitchen": FakeArea("Kitchen")},
        )
        undo = _install_registries(registry)
        self.addCleanup(undo)
        device = {"entity": "light.den.01", "domain": "light", "room": "Den"}
        self.assertEqual(floorplan_mod.async_match_entity(hass, device), "light.hue_lamp_4")

    def test_without_registries_name_matching_still_works_as_before(self):
        # conftest installs no registry helpers, so this is the plain path.
        hass = FakeHass(states=[FakeState("light.den_lamp", "Den Lamp"),
                                FakeState("light.hue_lamp_4", "Hue lamp 4")])
        device = {"entity": "light.den.01", "domain": "light", "room": "Den"}
        self.assertEqual(floorplan_mod.async_match_entity(hass, device), "light.den_lamp")

    def test_every_decision_is_logged_at_info_with_its_reason(self):
        hass = FakeHass(states=[FakeState("light.hue_lamp_3", "Hue lamp 3"),
                                FakeState("light.hue_lamp_4", "Hue lamp 4")])
        device = {"entity": "light.den.01", "domain": "light", "room": "Den"}
        with self.assertLogs(floorplan_mod._LOGGER, level="INFO") as captured:
            floorplan_mod.async_match_entity(hass, device)
        self.assertEqual(len(captured.records), 1)
        self.assertIn("light.den.01 in Den", captured.output[0])
        self.assertIn("skipped", captured.output[0])
        self.assertIn("light.hue_lamp_3", captured.output[0])


class IndoorReadingTest(unittest.TestCase):
    """Reported from the field: "lots of the temperature displays on the home
    3d screens are using OUTDOOR temps to represent indoor temps."

    The cause was two-fold. `cls` rode all the way through the pipeline into
    ha_map.json and was then used only to pick an icon, so a room's
    temperature readout would bind to any sensor sharing a room word. And the
    only-entity-in-its-domain rule bound a house whose single temperature
    sensor was the weather integration's outdoor one as every room's indoor
    temperature -- confidently, with a log line claiming success.
    """

    # A "heat/ac register" estimate line, which the render pipeline classes
    # as a `vent` in the `sensor` domain: a room's temperature readout.
    KITCHEN_VENT = {
        "entity": "vent.kitchen.01",
        "domain": "sensor",
        "cls": "vent",
        "room": "Kitchen",
        "floor_key": "Floor_1",
        "source_line": "Heat/AC register - Kitchen",
    }

    @staticmethod
    def _outdoor():
        return FakeState(
            "sensor.outdoor_temperature", "Outdoor Temperature", device_class="temperature"
        )

    def _match(self, states, device=None, decisions=None):
        hass = FakeHass(states=states)
        return floorplan_mod.async_match_entity(
            hass, dict(device or self.KITCHEN_VENT), None, decisions
        )

    # --- the guard -------------------------------------------------------

    def test_a_house_whose_only_temperature_sensor_is_outdoors_binds_nothing(self):
        # One temperature sensor in the whole house, and it is outdoors.
        # The old only-entity rule bound it as the Kitchen's temperature.
        bound = self._match([self._outdoor()])
        self.assertNotEqual(bound, "sensor.outdoor_temperature")
        self.assertIsNone(bound)

    def test_the_kitchen_sensor_is_bound_when_the_house_has_one(self):
        bound = self._match(
            [
                self._outdoor(),
                FakeState(
                    "sensor.kitchen_temperature", "Kitchen Temperature",
                    device_class="temperature",
                ),
            ]
        )
        self.assertNotEqual(bound, "sensor.outdoor_temperature")
        self.assertEqual(bound, "sensor.kitchen_temperature")

    # --- device_class as a real filter -----------------------------------

    def test_a_sensor_of_the_wrong_device_class_never_satisfies_a_reading(self):
        # Both share the room word "Kitchen"; neither is a temperature.
        bound = self._match(
            [
                FakeState("sensor.kitchen_humidity", "Kitchen Humidity",
                          device_class="humidity"),
                FakeState("sensor.kitchen_power", "Kitchen Power", device_class="power"),
            ]
        )
        self.assertIsNone(bound)

    def test_a_sensor_declaring_no_device_class_is_not_a_temperature(self):
        bound = self._match(
            [
                FakeState("sensor.kitchen_readout", "Kitchen Readout"),
                FakeState("sensor.den_readout", "Den Readout"),
            ]
        )
        self.assertIsNone(bound)

    def test_a_class_that_pins_down_no_reading_is_matched_on_domain_as_before(self):
        # The rule the docstring is about, and it must survive: one `climate`
        # entity is the house's thermostat whichever room it is named after.
        # A climate entity carries no device_class at all, so demanding one
        # here would have taken every thermostat off every plan.
        hass = FakeHass(states=[FakeState("climate.hallway", "Ecobee")])
        device = {"entity": "thermostat.den.01", "domain": "climate",
                  "cls": "thermostat", "room": "Den"}
        self.assertEqual(floorplan_mod.async_match_entity(hass, device), "climate.hallway")

    # --- outdoor-derived readings ----------------------------------------

    def test_an_outdoor_word_in_the_name_rules_a_sensor_out_for_every_room(self):
        for name, entity_id in (
            ("Patio Temperature", "sensor.t1"),
            ("Back Deck Temp", "sensor.t2"),
            ("Ambient Temperature", "sensor.t3"),
            ("Kitchen Temperature", "sensor.outside_t4"),
        ):
            with self.subTest(name=name):
                bound = self._match(
                    [FakeState(entity_id, name, device_class="temperature")]
                )
                self.assertIsNone(bound)

    def test_a_weather_entity_is_outdoor_derived_whatever_it_is_called(self):
        # Its name and entity id carry no outdoor word; the domain is enough.
        self.assertEqual(
            floorplan_mod._outdoor_marker(FakeState("weather.home", "Home"), {}), "weather"
        )

    def test_the_area_an_entity_is_filed_under_can_mark_it_outdoor(self):
        # Named "Temp 1", so only the Area says where it is.
        hass = FakeHass(
            states=[FakeState("sensor.temp_1", "Temp 1", device_class="temperature")]
        )
        registry = FakeRegistry(
            entities=[FakeRegistryEntry("sensor.temp_1", area_id="patio")],
            areas={"patio": FakeArea("Patio")},
        )
        self.addCleanup(_install_registries(registry))
        self.assertIsNone(
            floorplan_mod.async_match_entity(hass, dict(self.KITCHEN_VENT))
        )

    # --- what the deploy report says -------------------------------------

    def test_the_report_says_each_candidate_was_outdoor_or_the_wrong_class(self):
        decisions = []
        self._match(
            [
                self._outdoor(),
                FakeState("sensor.kitchen_humidity", "Kitchen Humidity",
                          device_class="humidity"),
            ],
            decisions=decisions,
        )
        record = decisions[0]
        self.assertIsNone(record["bound"])
        self.assertEqual(record["reason"], "outdoor_only")
        # The house's whole in-domain entity list is still reported.
        self.assertEqual(
            sorted(record["candidates"]),
            ["sensor.kitchen_humidity", "sensor.outdoor_temperature"],
        )
        by_entity = {r["entity"]: r for r in record["rejected"]}
        self.assertEqual(by_entity["sensor.outdoor_temperature"]["why"], "outdoor")
        self.assertEqual(by_entity["sensor.outdoor_temperature"]["marker"], "outdoor")
        self.assertEqual(by_entity["sensor.kitchen_humidity"]["why"], "wrong_device_class")
        self.assertEqual(by_entity["sensor.kitchen_humidity"]["wanted"], "temperature")
        self.assertEqual(by_entity["sensor.kitchen_humidity"]["declared"], "humidity")

    def test_a_house_with_no_temperature_sensor_at_all_is_its_own_reason(self):
        decisions = []
        self._match(
            [FakeState("sensor.kitchen_humidity", "Kitchen Humidity",
                       device_class="humidity")],
            decisions=decisions,
        )
        self.assertEqual(decisions[0]["reason"], "no_device_class_match")

    # --- and the display is omitted, never relabelled ---------------------

    def test_a_room_with_no_indoor_reading_gets_no_display_on_the_card(self):
        hass = FakeHass(states=[self._outdoor()])
        card = floorplan_mod.build_room_plan_card(
            hass,
            HOTSPOTS["Floor_1"],
            [dict(self.KITCHEN_VENT, xy_ft=[5.0, -5.0])],
            "/local/x.png",
        )
        # The Kitchen's controls hold nothing: there is no indoor reading to
        # show, and the outdoor one is not an acceptable substitute.
        self.assertEqual(_room_entities(card)["Kitchen"], [])
        # Nothing anywhere on the card points at the outdoor sensor.
        self.assertNotIn("sensor.outdoor_temperature", json.dumps(card))
        # The room itself still renders and still taps -- it is the reading
        # that is gone, not the room.
        self.assertIn("Kitchen", [room["name"] for room in card["rooms"]])


class DecisionRecordTest(unittest.TestCase):
    """What the deploy report carries back: one record per device, bound or
    skipped with its reason, and the entities the house had in that domain."""

    def _decide(self, states, device):
        decisions = []
        bound = floorplan_mod.async_match_entity(FakeHass(states=states), device, None, decisions)
        self.assertEqual(len(decisions), 1)
        return bound, decisions[0]

    def test_a_bound_device_records_the_entity_and_how_it_was_chosen(self):
        bound, record = self._decide(
            [FakeState("light.den_lamp", "Den Lamp"), FakeState("light.hue_4", "Hue 4")],
            {"entity": "light.den.01", "domain": "light", "room": "Den",
             "floor_key": "Floor_1", "cls": "light", "source_line": "Light fixture - Den"},
        )
        self.assertEqual(bound, "light.den_lamp")
        self.assertEqual(record["bound"], "light.den_lamp")
        self.assertEqual(record["reason"], "room_word")
        self.assertEqual(record["via"], "name")
        self.assertEqual(record["label"], "Light fixture - Den")
        self.assertEqual(record["floor"], "Floor_1")
        self.assertEqual(sorted(record["candidates"]), ["light.den_lamp", "light.hue_4"])

    def test_the_only_entity_rule_is_named_as_the_reason(self):
        _, record = self._decide(
            [FakeState("climate.hallway", "Ecobee")],
            {"entity": "thermostat.den.01", "domain": "climate", "room": "Den"},
        )
        self.assertEqual(record["bound"], "climate.hallway")
        self.assertEqual(record["reason"], "only_entity")

    def test_a_skipped_device_records_why_and_what_the_house_had_instead(self):
        _, record = self._decide(
            [FakeState("light.hue_3", "Hue lamp 3"), FakeState("light.hue_4", "Hue lamp 4")],
            {"entity": "light.den.01", "domain": "light", "room": "Den"},
        )
        self.assertIsNone(record["bound"])
        self.assertEqual(record["reason"], "no_shared_word")
        # Across a report, these candidate lists are the house's entity list.
        self.assertEqual(sorted(record["candidates"]), ["light.hue_3", "light.hue_4"])

    def test_a_domain_the_house_does_not_have_is_its_own_reason(self):
        _, record = self._decide(
            [FakeState("light.hue_3", "Hue lamp 3")],
            {"entity": "camera.porch.01", "domain": "camera", "room": "Porch"},
        )
        self.assertEqual(record["reason"], "no_entities")
        self.assertEqual(record["candidates"], [])

    def test_candidates_are_capped(self):
        states = [FakeState(f"sensor.s{i}", f"Sensor {i}") for i in range(40)]
        _, record = self._decide(
            states, {"entity": "sensor.den.01", "domain": "sensor", "room": "Den"}
        )
        self.assertEqual(len(record["candidates"]), 10)

    def test_a_device_in_no_room_on_the_plan_and_one_on_no_floor_are_accounted_for(self):
        # Under the room metaphor a device is placed by its room, not by its
        # coordinates: nothing anchors a loose icon any more. So "the Loft, on
        # Floor_1" -- a room that floor does not have -- is the unplaceable
        # case, and a floor the plan does not have at all is the other.
        hass = FakeHass(states=[FakeState("climate.den_thermostat", "Den Thermostat")])
        ha_map = {"devices": HA_MAP["devices"] + [
            {"entity": "light.loft.01", "domain": "light", "room": "Loft",
             "floor_key": "Floor_1"},                       # Loft is upstairs
            {"entity": "light.attic.01", "domain": "light", "room": "Attic",
             "floor_key": "Attic", "xy_ft": [0.0, 0.0]},    # no such floor
        ]}
        decisions = []
        floorplan_mod.build_dashboard_views(
            hass, TWO_FLOOR_HOTSPOTS, ha_map,
            {"Floor_1": "/local/plan_Floor_1.png", "Floor_2": "/local/plan_Floor_2.png"},
            TWO_FLOOR_TRANSFORM, decisions=decisions,
        )
        by_entity = {d["entity"]: d["reason"] for d in decisions}
        self.assertEqual(by_entity["light.loft.01"], "no_room_on_plan")
        self.assertEqual(by_entity["light.attic.01"], "no_floor")
        self.assertEqual(by_entity["thermostat.den.01"], "only_entity")
        # Every device the estimate listed is in the report exactly once.
        self.assertEqual(len(decisions), len(ha_map["devices"]))

    def test_a_device_with_no_coordinates_still_lands_in_its_room(self):
        # xy_ft placed an icon; nothing places an icon now. A device the
        # pipeline could not anchor to a coordinate is still perfectly
        # placeable, because what places it is the room it is in.
        hass = FakeHass(states=[FakeState("climate.den_thermostat", "Den Thermostat")])
        device = {"entity": "thermostat.den.01", "domain": "climate",
                  "cls": "thermostat", "room": "Den"}          # no xy_ft
        card = floorplan_mod.build_room_plan_card(
            hass, HOTSPOTS["Floor_1"], [device], "/local/plan.png",
            transform=PLAN_TRANSFORM["Floor_1"],
        )
        self.assertEqual(
            _room_entities(card),
            {"Den": ["climate.den_thermostat"], "Kitchen": []},
        )


def _room_entities(card):
    """{room name: [entity id, ...]} off a room-plan card -- which room's
    controls actually hold each device."""
    return {room["name"]: [e["entity"] for e in room["entities"]]
            for room in card["rooms"]}


def _room(card, name):
    return [room for room in card["rooms"] if room["name"] == name][0]


class RoomPlanCardTest(unittest.TestCase):
    """The room-first card the dashboard is built out of.

    The customer's own words: "inventory should be assigned to rooms and when
    tapping on a room, opens a modal box that has the controls for that room
    ... we need to use this as the primary UI metaphor for the software." So
    rooms are what the plan draws and what a tap lands on, and a device's
    controls live inside its room rather than as a loose icon somewhere near
    it.
    """

    def test_the_card_is_the_custom_room_plan_card_not_picture_elements(self):
        hass = FakeHass(states=[])
        card = floorplan_mod.build_room_plan_card(
            hass, HOTSPOTS["Floor_1"], [], "/local/x.png"
        )
        self.assertEqual(card["type"], "custom:ayvra-room-plan")
        # No loose icons on the plan at all any more.
        self.assertNotIn("state-icon", json.dumps(card))
        self.assertNotIn("picture-elements", json.dumps(card))

    def test_every_room_is_a_tappable_polygon_with_its_own_name(self):
        hass = FakeHass(states=[])
        card = floorplan_mod.build_room_plan_card(
            hass, HOTSPOTS["Floor_1"], [], "/local/x.png",
            transform=PLAN_TRANSFORM["Floor_1"],
        )
        self.assertEqual(sorted(r["name"] for r in card["rooms"]), ["Den", "Kitchen"])
        for room in card["rooms"]:
            # A polygon, so the tap hits the room's real shape rather than
            # its bounding box.
            self.assertGreaterEqual(len(room["poly"]), 3)

    def test_a_rooms_polygon_is_projected_point_by_point_onto_the_backplate(self):
        hass = FakeHass(states=[])
        card = floorplan_mod.build_room_plan_card(
            hass, HOTSPOTS["Floor_1"], [], "/local/plan.png",
            transform=PLAN_TRANSFORM["Floor_1"],
        )
        # Hand-computed from PLAN_TRANSFORM (10 px/ft, origin (200, 150) on a
        # 400x300 image), never from the code's own projection. The Den's
        # corners are (-15,-10) (-5,-10) (-5,0) (-15,0) ft:
        #   (-15,-10) -> ( 50, 250) -> (12.5%, 83.3%)
        #   ( -5,-10) -> (150, 250) -> (37.5%, 83.3%)
        #   ( -5,  0) -> (150, 150) -> (37.5%, 50.0%)
        #   (-15,  0) -> ( 50, 150) -> (12.5%, 50.0%)
        self.assertEqual(
            _room(card, "Den")["poly"],
            [[12.5, 83.3], [37.5, 83.3], [37.5, 50.0], [12.5, 50.0]],
        )
        # And the room's name sits at its centroid, (-10, -5) ft.
        self.assertEqual(_room(card, "Den")["label"], list(DEN_LABEL))

    def test_a_matched_device_lands_in_its_rooms_control_list(self):
        hass = FakeHass(states=[FakeState("climate.den_thermostat", "Den Thermostat")])
        card = floorplan_mod.build_room_plan_card(
            hass, HOTSPOTS["Floor_1"], HA_MAP["devices"], "/local/x.png"
        )
        self.assertEqual(
            _room_entities(card),
            {"Den": ["climate.den_thermostat"], "Kitchen": []},
        )

    def test_an_unmatched_device_is_left_out_of_every_room(self):
        # No light entities exist at all, so the kitchen light in HA_MAP has
        # nothing to bind to and must not appear as a fabricated entity id.
        hass = FakeHass(states=[FakeState("climate.den_thermostat", "Den Thermostat")])
        card = floorplan_mod.build_room_plan_card(
            hass, HOTSPOTS["Floor_1"], HA_MAP["devices"], "/local/x.png"
        )
        self.assertNotIn("light_recessed.kitchen.01", json.dumps(card))
        self.assertEqual(_room_entities(card)["Kitchen"], [])

    def test_an_empty_room_still_renders_and_still_taps(self):
        # A room nobody put anything in is a fact the installer has to be able
        # to check, so it is drawn and tappable like any other; the card says
        # so in as many words once it is open.
        hass = FakeHass(states=[])
        card = floorplan_mod.build_room_plan_card(
            hass, HOTSPOTS["Floor_1"], HA_MAP["devices"], "/local/x.png"
        )
        self.assertEqual(len(card["rooms"]), 2)
        self.assertEqual([len(r["entities"]) for r in card["rooms"]], [0, 0])
        self.assertGreaterEqual(len(_room(card, "Kitchen")["poly"]), 3)

    def test_the_card_references_the_local_backplate_url(self):
        hass = FakeHass(states=[])
        card = floorplan_mod.build_room_plan_card(
            hass, HOTSPOTS["Floor_1"], [], "/local/ha_dispatch/floorplan/backplate.png"
        )
        self.assertEqual(card["image"], "/local/ha_dispatch/floorplan/backplate.png")

    def test_a_control_carries_a_domain_appropriate_icon_and_the_estimates_line(self):
        hass = FakeHass(states=[FakeState("climate.den_thermostat", "Den Thermostat")])
        devices = [dict(HA_MAP["devices"][0], source_line="Thermostat - Den")]
        card = floorplan_mod.build_room_plan_card(
            hass, HOTSPOTS["Floor_1"], devices, "/local/x.png"
        )
        control = _room(card, "Den")["entities"][0]
        self.assertEqual(control["icon"], "mdi:thermostat")
        self.assertEqual(control["name"], "Thermostat - Den")

    def test_a_control_without_an_estimate_line_carries_no_label_at_all(self):
        # ha_map.json's device slug is not a Home Assistant entity id and must
        # never be shown as though it were, so a device the estimate gave no
        # line item for carries no name and the card falls back to the real
        # entity's own friendly name.
        hass = FakeHass(states=[FakeState("climate.den_thermostat", "Den Thermostat")])
        devices = [{k: v for k, v in HA_MAP["devices"][0].items()}]
        card = floorplan_mod.build_room_plan_card(
            hass, HOTSPOTS["Floor_1"], devices, "/local/x.png"
        )
        control = _room(card, "Den")["entities"][0]
        self.assertEqual(control["entity"], "climate.den_thermostat")
        self.assertNotIn("name", control)
        self.assertNotIn("thermostat.den.01", json.dumps(card))

    def test_the_card_names_the_floor_it_is_drawing(self):
        hass = FakeHass(states=[])
        card = floorplan_mod.build_room_plan_card(
            hass, HOTSPOTS["Floor_1"], [], "/local/x.png",
            floor_key="Floor_1", floor_name="1st Floor",
        )
        self.assertEqual(card["floor_key"], "Floor_1")
        self.assertEqual(card["floor_name"], "1st Floor")

    def test_with_no_transform_positions_fall_back_to_the_bounding_box(self):
        hass = FakeHass(states=[])
        card = floorplan_mod.build_room_plan_card(
            hass, HOTSPOTS["Floor_1"], [], "/local/x.png", transform=None
        )
        self.assertEqual(card["aspect_ratio"], "8:5")
        # Den's centroid (-10, -5) sits left-of-centre of the Floor_1 hotspot
        # bounding box (x: -15..10, y: -10..0).
        self.assertLess(_room(card, "Den")["label"][0], 50.0)

    def test_with_a_transform_the_aspect_ratio_is_the_backplates_own(self):
        hass = FakeHass(states=[])
        card = floorplan_mod.build_room_plan_card(
            hass, HOTSPOTS["Floor_1"], [], "/local/plan.png",
            transform=PLAN_TRANSFORM["Floor_1"],
        )
        self.assertEqual(card["aspect_ratio"], "400:300")

    def test_two_devices_in_one_room_share_that_rooms_control_list(self):
        # The old card had to fan loose icons out so they did not stack; a
        # room's modal just lists them.
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
        card = floorplan_mod.build_room_plan_card(
            hass, HOTSPOTS["Floor_1"], devices, "/local/plan.png",
            transform=PLAN_TRANSFORM["Floor_1"],
        )
        # Both lights share a room word with "Den", so the matcher binds the
        # same best candidate twice; the room lists it once.
        self.assertEqual(len(_room(card, "Den")["entities"]), 1)

    def test_a_room_the_installer_spelled_differently_still_takes_its_devices(self):
        hass = FakeHass(states=[FakeState("climate.great_room_stat", "Great Room Stat")])
        rooms = [{"name": "Great Room", "centroid": [0.0, 0.0],
                  "poly_ft": [[-5.0, -5.0], [5.0, -5.0], [5.0, 5.0], [-5.0, 5.0]]}]
        devices = [{"entity": "thermostat.great_room.01", "domain": "climate",
                    "cls": "thermostat", "room": "great  room", "xy_ft": [0.0, 0.0]}]
        card = floorplan_mod.build_room_plan_card(
            hass, rooms, devices, "/local/plan.png",
            transform=PLAN_TRANSFORM["Floor_1"],
        )
        self.assertEqual(
            _room_entities(card), {"Great Room": ["climate.great_room_stat"]}
        )

    def test_a_floor_with_no_usable_coordinates_at_all_yields_no_rooms(self):
        hass = FakeHass(states=[])
        card = floorplan_mod.build_room_plan_card(
            hass, [{"name": "Den"}], HA_MAP["devices"], "/local/x.png"
        )
        self.assertEqual(card["type"], "custom:ayvra-room-plan")
        self.assertEqual(card["rooms"], [])


class DashboardViewsTest(unittest.TestCase):
    def test_one_panel_view_per_floor_holding_only_that_floors_plan_card(self):
        hass = FakeHass(states=[FakeState("climate.den_thermostat", "Den Thermostat")])
        views = floorplan_mod.build_dashboard_views(
            hass, HOTSPOTS, HA_MAP, {"Floor_1": "/local/plan_Floor_1.png"}, PLAN_TRANSFORM,
        )
        self.assertEqual(len(views), 1)
        view = views[0]
        self.assertTrue(view["panel"])
        self.assertEqual(view["title"], "1st Floor")
        self.assertEqual(view["path"], "floor-1")
        self.assertEqual(len(view["cards"]), 1)
        self.assertEqual(view["cards"][0]["type"], "custom:ayvra-room-plan")
        self.assertEqual(view["cards"][0]["image"], "/local/plan_Floor_1.png")
        self.assertEqual(view["cards"][0]["floor_key"], "Floor_1")
        self.assertEqual(view["cards"][0]["floor_name"], "1st Floor")
        self.assertNotIn("vertical-stack", json.dumps(views))
        self.assertNotIn("picture-elements", json.dumps(views))

    def test_floors_are_ordered_lowest_first_whatever_the_manifest_order(self):
        hass = FakeHass(states=[])
        views = floorplan_mod.build_dashboard_views(
            hass, TWO_FLOOR_HOTSPOTS, {"devices": []},
            {"Floor_1": "/local/plan_Floor_1.png", "Floor_2": "/local/plan_Floor_2.png"},
            TWO_FLOOR_TRANSFORM,
        )
        self.assertEqual([v["title"] for v in views], ["1st Floor", "2nd Floor"])
        self.assertEqual([v["path"] for v in views], ["floor-1", "floor-2"])
        self.assertEqual([v["cards"][0]["image"] for v in views],
                         ["/local/plan_Floor_1.png", "/local/plan_Floor_2.png"])
        # Paths are unique within the dashboard; the dashboard url_path is
        # not one of them -- that stays on the dashboard itself.
        self.assertEqual(len({v["path"] for v in views}), 2)
        self.assertNotIn("home-3d", [v["path"] for v in views])

    def test_a_transform_without_a_level_name_gets_a_readable_title_from_the_key(self):
        hass = FakeHass(states=[])
        nameless = {k: {kk: vv for kk, vv in v.items() if kk != "name"}
                    for k, v in PLAN_TRANSFORM.items()}
        views = floorplan_mod.build_dashboard_views(
            hass, HOTSPOTS, {"devices": []}, {"Floor_1": "/local/plan.png"}, nameless,
        )
        self.assertEqual(views[0]["title"], "Floor 1")

    def test_devices_land_on_their_own_floors_view(self):
        # Two lights, so neither is "the only light" and both have to earn
        # their floor by room word.
        hass = FakeHass(states=[FakeState("climate.den_thermostat", "Den Thermostat"),
                                FakeState("light.kitchen_spot", "Kitchen Spot"),
                                FakeState("light.loft_lamp", "Loft Lamp")])
        ha_map = {"devices": HA_MAP["devices"] + [{
            "entity": "light.loft.01", "domain": "light", "cls": "light",
            "room": "Loft", "floor": "2nd Floor", "floor_key": "Floor_2",
            "xy_ft": [0.0, 0.0],
        }]}
        views = floorplan_mod.build_dashboard_views(
            hass, TWO_FLOOR_HOTSPOTS, ha_map,
            {"Floor_1": "/local/plan_Floor_1.png", "Floor_2": "/local/plan_Floor_2.png"},
            TWO_FLOOR_TRANSFORM,
        )
        bound = {v["title"]: _room_entities(v["cards"][0]) for v in views}
        self.assertEqual(
            bound,
            {
                "1st Floor": {"Den": ["climate.den_thermostat"],
                              "Kitchen": ["light.kitchen_spot"]},
                "2nd Floor": {"Loft": ["light.loft_lamp"]},
            },
        )

    def test_a_single_floor_with_no_floor_key_still_gets_its_devices(self):
        # Mirrors a manifest predating floor_key: devices only carry the
        # human "floor" label. With exactly one hotspots floor that is moot.
        legacy_map = {"devices": [dict(d) for d in HA_MAP["devices"]]}
        for d in legacy_map["devices"]:
            d.pop("floor_key", None)
        hass = FakeHass(states=[FakeState("climate.den_thermostat", "Den Thermostat")])
        views = floorplan_mod.build_dashboard_views(
            hass, HOTSPOTS, legacy_map, {}, {}, fallback_image_url="/local/hero.png"
        )
        plan_card = views[-1]["cards"][0]
        self.assertEqual(
            _room_entities(plan_card),
            {"Den": ["climate.den_thermostat"], "Kitchen": []},
        )

    def test_a_floor_with_no_plan_render_falls_back_to_the_legacy_hero_image(self):
        hass = FakeHass(states=[])
        views = floorplan_mod.build_dashboard_views(
            hass, HOTSPOTS, {"devices": []}, {}, {}, fallback_image_url="/local/hero.png"
        )
        self.assertEqual(len(views), 1)
        self.assertEqual(views[0]["cards"][0]["image"], "/local/hero.png")

    def test_a_floor_with_no_image_at_all_is_left_out(self):
        hass = FakeHass(states=[])
        views = floorplan_mod.build_dashboard_views(
            hass, TWO_FLOOR_HOTSPOTS, {"devices": []},
            {"Floor_1": "/local/plan_Floor_1.png"}, TWO_FLOOR_TRANSFORM,
        )
        self.assertEqual([v["title"] for v in views], ["1st Floor"])


class DashboardConfigTest(unittest.TestCase):
    def test_the_views_are_carried_as_given_under_the_dashboard_title(self):
        views = [{"title": "1st Floor", "path": "floor-1", "panel": True, "cards": [{}]},
                 {"title": "2nd Floor", "path": "floor-2", "panel": True, "cards": [{}]}]
        config = floorplan_mod.build_dashboard_config(views)
        self.assertEqual(config["title"], "Home 3D")
        self.assertEqual(config["views"], views)


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
            # The GLB is offered by the server and deliberately never fetched.
            self.assertFalse(os.path.exists(os.path.join(asset_dir, "model.glb")))
            self.assertTrue(os.path.isfile(os.path.join(asset_dir, "hotspots.json")))
            self.assertTrue(os.path.isfile(os.path.join(asset_dir, "ha_map.json")))
            self.assertTrue(os.path.isfile(os.path.join(asset_dir, "plan_transform.json")))
            self.assertTrue(os.path.isfile(os.path.join(asset_dir, "plan_Floor_1.png")))

            self.assertEqual(len(api.reports), 1)
            self.assertEqual(api.reports[0]["status"], "done")
            self.assertEqual(api.reports[0]["job_id"], "101")
            # The report says what happened to each device, and the summary
            # line says it in one sentence for the audit trail.
            devices = api.reports[0]["devices"]
            self.assertEqual(
                {d["entity"]: d["bound"] for d in devices},
                {"thermostat.den.01": "climate.den_thermostat", "light_recessed.kitchen.01": None},
            )
            self.assertIn("1 of 2 devices placed", api.reports[0]["detail"])

            registry = manager._dashboards_store.data
            self.assertEqual(len(registry["items"]), 1)
            self.assertEqual(registry["items"][0]["url_path"], "home-3d")
            self.assertEqual(registry["items"][0]["title"], "Home 3D")
            self.assertEqual(registry["items"][0]["mode"], "storage")

            dashboard = manager._dashboard_store.data
            views = dashboard["config"]["views"]
            self.assertEqual(len(views), 1)
            view = views[0]
            self.assertTrue(view["panel"])
            self.assertEqual(view["title"], "1st Floor")
            # One card per view: that floor's plan, nothing stacked above it.
            self.assertEqual(len(view["cards"]), 1)
            plan_card = view["cards"][0]
            self.assertEqual(plan_card["type"], "custom:ayvra-room-plan")
            self.assertEqual(plan_card["image"], "/local/ha_dispatch/floorplan/plan_Floor_1.png")
            self.assertEqual(
                _room_entities(plan_card),
                {"Den": ["climate.den_thermostat"], "Kitchen": []},
            )

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
            plan_card = dashboard["config"]["views"][0]["cards"][0]
            self.assertEqual(plan_card["image"], "/local/ha_dispatch/floorplan/backplate.png")
            self.assertEqual(
                _room_entities(plan_card)["Den"], ["climate.den_thermostat"]
            )

    def test_redeploying_updates_the_same_dashboard_registry_entry_not_a_second_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[])

            _run(manager._async_run(_job(job_id=1)))
            _run(manager._async_run(_job(job_id=2)))

            registry = manager._dashboards_store.data
            self.assertEqual(len(registry["items"]), 1)

    def test_a_job_with_neither_glb_nor_hero_render_deploys_on_the_plan_renders_alone(self):
        # What the render pipeline produces for a dashboard job now: no GLB,
        # no angled hero, just the per-floor plans and manifests.
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[])

            _run(manager._async_run(_job(glb_url=None, backplate_url=None)))

            self.assertEqual(api.reports[-1]["status"], "done")
            asset_dir = os.path.join(tmp, "www", "ha_dispatch", "floorplan")
            self.assertFalse(os.path.exists(os.path.join(asset_dir, "backplate.png")))
            plan_card = manager._dashboard_store.data["config"]["views"][0]["cards"][0]
            self.assertEqual(plan_card["image"], "/local/ha_dispatch/floorplan/plan_Floor_1.png")

    def test_a_hero_render_that_fails_to_download_does_not_fail_the_deploy(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(
                assets=ASSETS,
                download_errors={BACKPLATE_URL: aiohttp.ClientError("connection reset")},
            )
            manager, hass = _manager(api, tmp, states=[])

            _run(manager._async_run(_job()))

            self.assertEqual(api.reports[-1]["status"], "done")

    def test_a_job_with_no_floor_image_of_any_kind_is_reported_as_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[])

            _run(manager._async_run(_legacy_job(backplate_url=None, glb_url=None)))

            self.assertEqual(api.reports[-1]["status"], "failed")
            self.assertIn("plan render", api.reports[-1]["detail"])


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


class FakeResourceCollection:
    """Mimics lovelace's live ResourceStorageCollection: the list that decides
    whether a browser loads the room-plan card at all. Real lovelace reads its
    file lazily, so `loaded` and async_load() are part of the contract
    floorplan.py has to honour."""

    def __init__(self):
        self.items = []
        self.loaded = False
        self.load_calls = 0
        self.create_calls = []
        self.update_calls = []

    async def async_load(self):
        self.loaded = True
        self.load_calls += 1

    def async_items(self):
        return list(self.items)

    async def async_create_item(self, data):
        assert sorted(data) == ["res_type", "url"], data
        item = {"id": f"res{len(self.items) + 1}", "type": data["res_type"],
                "url": data["url"]}
        self.items.append(item)
        self.create_calls.append(dict(data))
        return item

    async def async_update_item(self, item_id, updates):
        item = [i for i in self.items if i["id"] == item_id][0]
        if "res_type" in updates:
            item["type"] = updates["res_type"]
        if "url" in updates:
            item["url"] = updates["url"]
        self.update_calls.append({"id": item_id, **updates})
        return item


class FakeLovelaceData:
    def __init__(self):
        self.dashboards = {}
        self.resources = FakeResourceCollection()


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
                download_errors={HOTSPOTS_URL: aiohttp.ClientError("connection reset")},
            )
            manager, hass = _manager(api, tmp, states=[])

            _run(manager._async_run(_job()))

            self.assertEqual(len(api.reports), 1)
            self.assertEqual(api.reports[0]["status"], "failed")
            self.assertIn("connection reset", api.reports[0]["detail"])
            self.assertIsNone(api.reports[0]["devices"])
            self.assertEqual(notifications.created, [])
            self.assertEqual(manager._running, set())

    def test_a_job_missing_an_asset_url_is_reported_as_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[])

            incomplete = _job()
            del incomplete["hotspots_url"]

            _run(manager._async_run(incomplete))

            self.assertEqual(api.reports[-1]["status"], "failed")
            self.assertIn("hotspots_url", api.reports[-1]["detail"])

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
            api = FakeApi(assets={}, download_errors={HOTSPOTS_URL: ValueError("boom")})
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


# --- the installer's corrections ---------------------------------------------
#
# The automatic placement is "weak at BEST" until there is a definitive
# inventory per project, so an installer standing in the house has to be able
# to correct it -- and a correction is worthless unless it survives the next
# redeploy, which rebuilds the whole dashboard from freshly downloaded
# manifests. These assert on where a device actually ends up rendered, not on
# what the override store reads back: a test that reads back what it just
# wrote proves only that Store works.

# Two thermostats, one per room, so the matcher has a real choice -- the
# "only entity in its domain" rule would otherwise bind whichever one exists
# and nothing a correction did could be told apart from it.
def _two_thermostat_states():
    return [
        FakeState("climate.den_thermostat", "Den Thermostat"),
        FakeState("climate.kitchen_thermostat", "Kitchen Thermostat"),
    ]


# Where each room's name is drawn on the card, hand-computed from
# PLAN_TRANSFORM (10 px per foot, origin at (200, 150) on a 400x300 image),
# never from the code's own projection:
#   Den     centroid (-10, -5) -> (10*-10+200, -10*-5+150) = (100, 200)
#                              -> (25.0%, 66.7%)
#   Kitchen centroid (  5, -5) -> (10*  5+200, -10*-5+150) = (250, 200)
#                              -> (62.5%, 66.7%)
DEN_LABEL = (25.0, 66.7)
KITCHEN_LABEL = (62.5, 66.7)

# A second deploy's ha_map.json: the same two device slugs, listed in the
# opposite order, with the thermostat still placed in the Den. A correction
# keyed on anything but the slug -- a list position, or the entity the last
# deploy bound -- would not survive this.
REDEPLOY_HA_MAP = {
    "source": "Xactimate ESX sketch (surveyed)",
    "units": "feet",
    "devices": [
        {
            "entity": "light_recessed.kitchen.01",
            "domain": "light",
            "cls": "light_recessed",
            "room": "Kitchen",
            "floor": "1st Floor",
            "floor_key": "Floor_1",
            "xy_ft": [5.0, -5.0],
        },
        {
            "entity": "thermostat.den.01",
            "domain": "climate",
            "cls": "thermostat",
            "room": "Den",
            "floor": "1st Floor",
            "floor_key": "Floor_1",
            "xy_ft": [-10.0, -5.0],
        },
    ],
}
REDEPLOY_HA_MAP_URL = "https://dispatch.example.com/assets/ha_map.2.json"
OVERRIDE_ASSETS = dict(ASSETS, **{REDEPLOY_HA_MAP_URL: json.dumps(REDEPLOY_HA_MAP)})


def _deployed_card(manager, view=0):
    return manager._dashboard_store.data["config"]["views"][view]["cards"][0]


def _placed(manager, view=0):
    """{entity: room name} off a deployed dashboard's floor -- whose room's
    controls the homeowner actually finds each device under."""
    card = _deployed_card(manager, view)
    return {e["entity"]: room["name"]
            for room in card["rooms"] for e in room["entities"]}


class OverrideLayerTest(unittest.TestCase):
    """The correction layer on its own: matching first, a human on top."""

    def test_a_device_with_no_correction_is_planned_exactly_as_the_matcher_said(self):
        hass = FakeHass(states=_two_thermostat_states())
        devices, dropped = floorplan_mod.async_plan_devices(hass, HA_MAP, HOTSPOTS, {})
        self.assertEqual(dropped, [])
        plan = devices[0]["_ha_dispatch_plan"]
        self.assertEqual(plan["bound"], "climate.den_thermostat")
        self.assertEqual(plan["matched"], "climate.den_thermostat")
        self.assertEqual(plan["reason"], "room_word")
        self.assertIsNone(plan["override"])

    def test_a_corrected_device_is_re_matched_in_the_room_it_was_moved_to(self):
        hass = FakeHass(states=_two_thermostat_states())
        overrides = {"thermostat.den.01": {"room": "Kitchen", "floor_key": "Floor_1",
                                           "bound_entity": None}}
        devices, dropped = floorplan_mod.async_plan_devices(
            hass, HA_MAP, HOTSPOTS, overrides
        )
        self.assertEqual(dropped, [])
        device = devices[0]
        self.assertEqual(device["room"], "Kitchen")
        # Re-anchored to the Kitchen's own centroid: a device belongs to a
        # room, so leaving it on the Den's coordinates would render it in the
        # Den while claiming the Kitchen.
        self.assertEqual(device["xy_ft"], [5.0, -5.0])
        plan = device["_ha_dispatch_plan"]
        self.assertEqual(plan["bound"], "climate.kitchen_thermostat")
        # ...and the report can still say what the matcher had concluded.
        self.assertEqual(plan["matched"], "climate.den_thermostat")
        self.assertEqual(plan["reason"], "override_room")
        self.assertEqual(plan["override"]["was_room"], "Den")

    def test_a_pinned_entity_outranks_every_matching_rule(self):
        hass = FakeHass(
            states=_two_thermostat_states() + [FakeState("climate.ecobee_3", "Ecobee 3")]
        )
        overrides = {"thermostat.den.01": {"room": None, "floor_key": None,
                                           "bound_entity": "climate.ecobee_3"}}
        devices, _ = floorplan_mod.async_plan_devices(hass, HA_MAP, HOTSPOTS, overrides)
        plan = devices[0]["_ha_dispatch_plan"]
        self.assertEqual(plan["bound"], "climate.ecobee_3")
        self.assertEqual(plan["matched"], "climate.den_thermostat")
        self.assertEqual(plan["reason"], "override_entity")

    def test_a_pin_is_the_only_thing_that_can_place_a_device_the_matcher_skipped(self):
        # Two lights, neither sharing a room word: the matcher refuses, which
        # is the rule. A human naming the entity is the one way past it.
        states = [FakeState("light.hue_lamp_3", "Hue lamp 3"),
                  FakeState("light.hue_lamp_4", "Hue lamp 4")]
        hass = FakeHass(states=states)
        devices, _ = floorplan_mod.async_plan_devices(hass, HA_MAP, HOTSPOTS, {})
        self.assertIsNone(devices[1]["_ha_dispatch_plan"]["bound"])

        overrides = {"light_recessed.kitchen.01": {"room": None, "floor_key": None,
                                                   "bound_entity": "light.hue_lamp_4"}}
        devices, _ = floorplan_mod.async_plan_devices(hass, HA_MAP, HOTSPOTS, overrides)
        self.assertEqual(devices[1]["_ha_dispatch_plan"]["bound"], "light.hue_lamp_4")

    def test_a_room_name_matches_however_the_installer_capitalised_it(self):
        hass = FakeHass(states=_two_thermostat_states())
        overrides = {"thermostat.den.01": {"room": "  kitchen ", "floor_key": None,
                                           "bound_entity": None}}
        devices, dropped = floorplan_mod.async_plan_devices(
            hass, HA_MAP, HOTSPOTS, overrides
        )
        self.assertEqual(dropped, [])
        # Re-spelled as hotspots.json spells it, not as it was typed.
        self.assertEqual(devices[0]["room"], "Kitchen")

    def test_a_correction_can_move_a_device_to_another_floor_entirely(self):
        hass = FakeHass(states=_two_thermostat_states())
        overrides = {"thermostat.den.01": {"room": "Loft", "floor_key": "Floor_2",
                                           "bound_entity": "climate.den_thermostat"}}
        devices, dropped = floorplan_mod.async_plan_devices(
            hass, HA_MAP, TWO_FLOOR_HOTSPOTS, overrides
        )
        self.assertEqual(dropped, [])
        self.assertEqual(devices[0]["floor_key"], "Floor_2")
        views = floorplan_mod.build_dashboard_views(
            hass, TWO_FLOOR_HOTSPOTS, dict(HA_MAP, devices=devices),
            {"Floor_1": "/local/plan_Floor_1.png", "Floor_2": "/local/plan_Floor_2.png"},
            TWO_FLOOR_TRANSFORM,
        )
        # Lowest floor first, so view 0 is Floor_1 and view 1 is Floor_2. The
        # thermostat is on the upper floor now and nowhere else; the Loft's
        # centroid (0, 0) is (10*0+200, -10*0+150) = (200, 150) on 400x300,
        # i.e. dead centre.
        self.assertEqual(
            _room_entities(views[0]["cards"][0]), {"Den": [], "Kitchen": []}
        )
        self.assertEqual(
            _room_entities(views[1]["cards"][0]),
            {"Loft": ["climate.den_thermostat"]},
        )

    def test_a_correction_for_a_room_this_plan_does_not_have_is_dropped(self):
        hass = FakeHass(states=_two_thermostat_states())
        overrides = {"thermostat.den.01": {"room": "Wine Cellar", "floor_key": None,
                                           "bound_entity": None}}
        devices, dropped = floorplan_mod.async_plan_devices(
            hass, HA_MAP, HOTSPOTS, overrides
        )
        self.assertEqual(
            [(d["entity"], d["why"]) for d in dropped],
            [("thermostat.den.01", "unknown_room")],
        )
        # The matcher's own conclusion stands rather than the device vanishing.
        self.assertEqual(devices[0]["room"], "Den")
        self.assertEqual(devices[0]["_ha_dispatch_plan"]["bound"], "climate.den_thermostat")

    def test_a_plan_annotation_arriving_in_the_manifest_is_never_trusted(self):
        # ha_map.json comes from the server; it must not be able to forge a
        # placement decision by naming this module's own annotation key.
        hass = FakeHass(states=_two_thermostat_states())
        ha_map = {"devices": [dict(HA_MAP["devices"][0],
                                   _ha_dispatch_plan={"bound": "climate.anything",
                                                      "reason": "forged"})]}
        devices, _ = floorplan_mod.async_plan_devices(hass, ha_map, HOTSPOTS, {})
        plan = devices[0]["_ha_dispatch_plan"]
        self.assertEqual(plan["bound"], "climate.den_thermostat")
        self.assertEqual(plan["reason"], "room_word")


class OverrideNormalisationTest(unittest.TestCase):
    def test_the_stored_shape_is_the_contract_shape(self):
        self.assertEqual(
            floorplan_mod.normalise_override(
                {"room": "Kitchen", "floor_key": "Floor_1", "bound_entity": "climate.k"}
            ),
            {"room": "Kitchen", "floor_key": "Floor_1", "bound_entity": "climate.k"},
        )

    def test_a_partial_correction_keeps_the_keys_it_does_not_set(self):
        self.assertEqual(
            floorplan_mod.normalise_override({"bound_entity": "climate.k"}),
            {"room": None, "floor_key": None, "bound_entity": "climate.k"},
        )

    def test_a_correction_that_corrects_nothing_is_not_one(self):
        self.assertIsNone(floorplan_mod.normalise_override({}))
        self.assertIsNone(floorplan_mod.normalise_override({"room": "  "}))
        self.assertIsNone(floorplan_mod.normalise_override("Kitchen"))

    def test_junk_read_back_from_disk_does_not_become_a_correction(self):
        self.assertEqual(
            floorplan_mod.normalise_overrides(
                {"thermostat.den.01": {"room": "Kitchen"}, "": {"room": "Den"},
                 "camera.porch.01": None, "lock.front.01": {}}
            ),
            {"thermostat.den.01": {"room": "Kitchen", "floor_key": None,
                                   "bound_entity": None}},
        )
        self.assertEqual(floorplan_mod.normalise_overrides(["Kitchen"]), {})


class OverrideSurvivesRedeployTest(unittest.TestCase):
    """The guard this feature exists for.

    A redeploy rebuilds the dashboard from manifests that have just been
    downloaded afresh. If a correction is not laid over the *new* manifests,
    the installer's work is destroyed by the next deploy -- which is the one
    thing this feature promises cannot happen.
    """

    def test_a_correction_still_wins_after_a_redeploy_rebinds_the_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=OVERRIDE_ASSETS)
            manager, hass = _manager(api, tmp, states=_two_thermostat_states())

            # 1. Deploy. The estimate puts the thermostat in the Den, and the
            #    matcher agrees with it.
            _run(manager._async_run(_job(job_id=1)))
            self.assertEqual(_placed(manager), {"climate.den_thermostat": "Den"})

            # 2. The installer, standing in the house, says it is the
            #    Kitchen's thermostat.
            _run(manager.async_set_override("thermostat.den.01", room="Kitchen",
                                            floor_key="Floor_1"))

            # 3. Deploy again, with a fresh ha_map that reorders the devices
            #    and still places this one in the Den. Before asserting the
            #    correction wins, prove the fresh manifest really would bind
            #    it elsewhere: if it would not, this test could not tell the
            #    difference between a correction being applied and nothing
            #    happening at all.
            plain_devices, _ = floorplan_mod.async_plan_devices(
                hass, REDEPLOY_HA_MAP, HOTSPOTS, {}
            )
            uncorrected = [d for d in plain_devices
                           if d["entity"] == "thermostat.den.01"][0]
            self.assertEqual(
                uncorrected["_ha_dispatch_plan"]["bound"], "climate.den_thermostat"
            )
            self.assertEqual(uncorrected["xy_ft"], [-10.0, -5.0])

            _run(manager._async_run(_job(job_id=2, ha_map_url=REDEPLOY_HA_MAP_URL)))

            # The correction won: the Kitchen's thermostat, under the
            # Kitchen's controls, from a manifest that said neither.
            self.assertEqual(_placed(manager), {"climate.kitchen_thermostat": "Kitchen"})
            record = [d for d in api.reports[-1]["devices"]
                      if d["entity"] == "thermostat.den.01"][0]
            self.assertEqual(record["room"], "Kitchen")
            self.assertEqual(record["bound"], "climate.kitchen_thermostat")
            self.assertEqual(record["reason"], "override_room")
            # The report names what the matcher had concluded instead, so the
            # installer can see which placements a human had to fix.
            self.assertEqual(record["matched"], "climate.den_thermostat")
            self.assertEqual(record["override"]["was_room"], "Den")

    def test_a_correction_read_back_from_disk_survives_a_restart(self):
        # The store is the only thing carrying a correction across a restart,
        # so a fresh manager must find it there.
        shared = {}

        class SharedStore:
            def __init__(self, hass, version, key):
                self.key = key

            async def async_load(self):
                return shared.get(self.key)

            async def async_save(self, data):
                shared[self.key] = data

        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=OVERRIDE_ASSETS)
            saved = floorplan_mod.Store
            floorplan_mod.Store = SharedStore
            try:
                first, _ = _manager(api, tmp, states=_two_thermostat_states())
                _run(first.async_set_override("thermostat.den.01", room="Kitchen"))

                second, _ = _manager(api, tmp, states=_two_thermostat_states())
                _run(second.async_load())
                _run(second._async_run(_job(job_id=7,
                                            ha_map_url=REDEPLOY_HA_MAP_URL)))
                card = shared["lovelace.home-3d"]["config"]["views"][0]["cards"][0]
                grouped = _room_entities(card)
            finally:
                floorplan_mod.Store = saved

            self.assertEqual(
                grouped, {"Den": [], "Kitchen": ["climate.kitchen_thermostat"]}
            )


class OverrideDropTest(unittest.TestCase):
    """A correction the plan has moved past is dropped and said out loud."""

    def test_a_correction_for_a_device_this_manifest_no_longer_has_is_dropped_and_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=OVERRIDE_ASSETS)
            manager, hass = _manager(api, tmp, states=_two_thermostat_states())

            _run(manager.async_set_override("camera.porch.09", room="Kitchen",
                                            floor_key="Floor_1"))
            _run(manager.async_set_override("thermostat.den.01", room="Kitchen",
                                            floor_key="Floor_1"))

            _run(manager._async_run(_job()))

            report = api.reports[-1]
            dropped = [d for d in report["devices"]
                       if d["reason"] == "override_dropped"]
            self.assertEqual([d["entity"] for d in dropped], ["camera.porch.09"])
            self.assertEqual(dropped[0]["override"]["dropped"], "unknown_device")
            self.assertIsNone(dropped[0]["bound"])
            self.assertIn("1 installer correction(s) dropped", report["detail"])

            # Retaining it forever is how this becomes unmaintainable; the
            # correction for a device that IS still there is untouched.
            self.assertEqual(
                sorted(_run(manager.async_overrides())), ["thermostat.den.01"]
            )
            self.assertEqual(_placed(manager), {"climate.kitchen_thermostat": "Kitchen"})

    def test_a_deploy_with_no_corrections_reports_no_drops(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=OVERRIDE_ASSETS)
            manager, hass = _manager(api, tmp, states=_two_thermostat_states())
            _run(manager._async_run(_job()))
            report = api.reports[-1]
            self.assertNotIn("dropped", report["detail"])
            self.assertEqual(
                [d["reason"] for d in report["devices"] if "override" in d["reason"]], []
            )
            self.assertEqual(report["detail"], "Home 3D dashboard deployed; 1 of 2 devices placed")


class OverrideServiceSupportTest(unittest.TestCase):
    """What the assign_floorplan_device service calls into."""

    def test_setting_and_clearing_a_correction_moves_the_device_and_puts_it_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=OVERRIDE_ASSETS)
            manager, hass = _manager(api, tmp, states=_two_thermostat_states())
            _run(manager._async_run(_job()))
            self.assertEqual(_placed(manager), {"climate.den_thermostat": "Den"})

            # Corrected and redrawn from the assets already on disk -- the
            # installer is at the tablet and must see it now, not at the next
            # deploy.
            _run(manager.async_set_override("thermostat.den.01", room="Kitchen"))
            self.assertTrue(_run(manager.async_reapply()))
            self.assertEqual(_placed(manager), {"climate.kitchen_thermostat": "Kitchen"})

            self.assertTrue(_run(manager.async_clear_override("thermostat.den.01")))
            self.assertTrue(_run(manager.async_reapply()))
            self.assertEqual(_placed(manager), {"climate.den_thermostat": "Den"})

    def test_a_rebuild_asks_the_server_for_nothing(self):
        """The property the phone redeploy rests on.

        A client that has just been updated has to be able to redraw the
        dashboard the OLD client wrote, using the plan files this house
        already has. If that needed a round trip, updating the card would mean
        waking the server and spending a consent on a change the house already
        holds every byte of.
        """
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=OVERRIDE_ASSETS)
            manager, hass = _manager(api, tmp, states=_two_thermostat_states())
            _run(manager._async_run(_job()))

            # Everything the deploy asked of the server, up to this point.
            downloads_after_deploy = len(api.downloads)
            reports_after_deploy = len(api.reports)

            self.assertTrue(_run(manager.async_reapply()))

            # The wrong answer first: if a rebuild re-fetched the plan, or
            # reported itself to Dispatch, these would have moved.
            self.assertEqual(len(api.downloads), downloads_after_deploy)
            self.assertEqual(len(api.reports), reports_after_deploy)
            # ...and it really did rebuild.
            self.assertEqual(_placed(manager), {"climate.den_thermostat": "Den"})

    def test_a_rebuild_with_nothing_deployed_says_so_instead_of_failing(self):
        # A house that has never had a plan: the service must be safe to call.
        with tempfile.TemporaryDirectory() as tmp:
            manager, hass = _manager(FakeApi(), tmp)
            self.assertFalse(_run(manager.async_reapply()))

    def test_a_correction_is_merged_rather_than_replaced(self):
        manager, _ = _manager(FakeApi(), states=_two_thermostat_states())
        _run(manager.async_set_override("thermostat.den.01", room="Kitchen",
                                        floor_key="Floor_1"))
        _run(manager.async_set_override("thermostat.den.01",
                                        bound_entity="climate.ecobee_3"))
        self.assertEqual(
            _run(manager.async_overrides())["thermostat.den.01"],
            {"room": "Kitchen", "floor_key": "Floor_1",
             "bound_entity": "climate.ecobee_3"},
        )

    def test_clearing_a_correction_nobody_made_changes_nothing(self):
        manager, _ = _manager(FakeApi())
        self.assertFalse(_run(manager.async_clear_override("thermostat.den.01")))
        self.assertEqual(_run(manager.async_overrides()), {})

    def test_a_correction_needs_a_device_slug(self):
        manager, _ = _manager(FakeApi())
        self.assertFalse(_run(manager.async_set_override("  ", room="Kitchen")))
        self.assertEqual(_run(manager.async_overrides()), {})

    def test_reapplying_before_anything_has_been_deployed_fails_quietly(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager, _ = _manager(FakeApi(), tmp)
            self.assertFalse(_run(manager.async_reapply()))


# --- the room-first dashboard -------------------------------------------------
#
# Three rooms, and an ha_map that lists their devices in a DIFFERENT order
# from hotspots.json on purpose: a fixture already in the right order cannot
# tell correct grouping from accidental grouping. hotspots order is
# Den, Kitchen, Primary Bath; the devices are Primary Bath, Den, Kitchen.

THREE_ROOM_HOTSPOTS = {
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
        {
            "name": "Primary Bath",
            "sf": 90.0,
            "centroid": [20.0, -5.0],
            "poly_ft": [[15.0, -10.0], [25.0, -10.0], [25.0, 0.0], [15.0, 0.0]],
        },
    ]
}

THREE_ROOM_HA_MAP = {
    "source": "Xactimate ESX sketch (surveyed)",
    "units": "feet",
    "devices": [
        {
            "entity": "light_vanity.primary_bath.01",
            "domain": "light",
            "cls": "light",
            "room": "Primary Bath",
            "floor": "1st Floor",
            "floor_key": "Floor_1",
            "xy_ft": [20.0, -5.0],
            "source_line": "Light fixture - Primary Bath",
        },
        {
            "entity": "thermostat.den.01",
            "domain": "climate",
            "cls": "thermostat",
            "room": "Den",
            "floor": "1st Floor",
            "floor_key": "Floor_1",
            "xy_ft": [-10.0, -5.0],
            "source_line": "Thermostat - Den",
        },
        {
            "entity": "light_recessed.kitchen.01",
            "domain": "light",
            "cls": "light_recessed",
            "room": "Kitchen",
            "floor": "1st Floor",
            "floor_key": "Floor_1",
            "xy_ft": [5.0, -5.0],
            "source_line": "Light fixture - Kitchen",
        },
    ],
}

THREE_ROOM_HOTSPOTS_URL = "https://dispatch.example.com/assets/hotspots.3.json"
THREE_ROOM_HA_MAP_URL = "https://dispatch.example.com/assets/ha_map.3.json"
THREE_ROOM_ASSETS = dict(
    ASSETS,
    **{
        THREE_ROOM_HOTSPOTS_URL: json.dumps(THREE_ROOM_HOTSPOTS),
        THREE_ROOM_HA_MAP_URL: json.dumps(THREE_ROOM_HA_MAP),
    },
)


def _three_room_states():
    """Two climate entities and two lights, so no device can be bound by the
    "only entity in its domain" rule -- every one has to earn its room."""
    return [
        FakeState("climate.den_thermostat", "Den Thermostat"),
        FakeState("climate.kitchen_thermostat", "Kitchen Thermostat"),
        FakeState("light.kitchen_spot", "Kitchen Spot"),
        FakeState("light.primary_bath_vanity", "Primary Bath Vanity"),
    ]


def _three_room_job(job_id=301, **overrides):
    return _job(
        job_id,
        hotspots_url=THREE_ROOM_HOTSPOTS_URL,
        ha_map_url=THREE_ROOM_HA_MAP_URL,
        **overrides,
    )


class RoomFirstDashboardTest(unittest.TestCase):
    """The guard for the room metaphor itself, on the dashboard a real deploy
    actually writes.

    What it protects: the dashboard is built out of the custom room card (no
    stock card can draw a room polygon as a hit target, let alone grey the
    floor out around the room in focus); every room on the plan is tappable,
    whether or not anything was matched into it; and a device is in its OWN
    room's control list and in nobody else's. The last one is why the fixture
    lists devices in a different order from the rooms -- "the kitchen light is
    in the kitchen" is true of correct grouping and of a bug that puts every
    device in whichever room came first, and only a shuffled fixture can tell
    those apart.
    """

    def _deploy(self):
        self.addCleanup(self._tmp_cleanup)
        self._tmp = tempfile.TemporaryDirectory()
        api = FakeApi(assets=THREE_ROOM_ASSETS)
        manager, hass = _manager(api, self._tmp.name, states=_three_room_states())
        _run(manager._async_run(_three_room_job()))
        self.assertEqual(api.reports[-1]["status"], "done")
        return manager

    def _tmp_cleanup(self):
        self._tmp.cleanup()

    def test_the_dashboard_is_built_from_the_custom_room_card(self):
        card = _deployed_card(self._deploy())
        self.assertEqual(card["type"], "custom:ayvra-room-plan")
        # The old loose-icon plan is gone, not merely hidden.
        self.assertNotIn("picture-elements", json.dumps(card))
        self.assertNotIn("state-icon", json.dumps(card))

    def test_every_room_on_the_plan_is_a_tappable_room_on_the_card(self):
        card = _deployed_card(self._deploy())
        self.assertEqual(
            [room["name"] for room in card["rooms"]],
            ["Den", "Kitchen", "Primary Bath"],
        )
        for room in card["rooms"]:
            self.assertGreaterEqual(len(room["poly"]), 3)
            self.assertEqual(len(room["label"]), 2)

    def test_a_device_is_in_its_own_rooms_controls_and_in_no_other_rooms(self):
        grouped = _room_entities(_deployed_card(self._deploy()))

        # The wrong answers first: a fixture that cannot distinguish them
        # would pass the right answer by accident.
        self.assertNotIn("light.kitchen_spot", grouped["Den"])
        self.assertNotIn("light.kitchen_spot", grouped["Primary Bath"])
        self.assertNotIn("climate.den_thermostat", grouped["Kitchen"])
        self.assertNotIn("light.primary_bath_vanity", grouped["Den"])

        # ...and then the right one.
        self.assertIn("light.kitchen_spot", grouped["Kitchen"])
        self.assertEqual(
            grouped,
            {
                "Den": ["climate.den_thermostat"],
                "Kitchen": ["light.kitchen_spot"],
                "Primary Bath": ["light.primary_bath_vanity"],
            },
        )


class RoomPlanAssetTest(unittest.TestCase):
    """Shipping the card: the file a browser fetches, and the resource row
    that makes it fetch it.

    The card is JavaScript and this suite is pytest, so the card's own
    behaviour is not unit-tested here (see the manual checks in
    docs/user/deployment.md). What is testable from Python is exactly what
    goes wrong silently: the file not being copied where /local/ serves it,
    and the resource row being absent, duplicated, or stamped with a version
    that never changes -- which leaves every wall tablet running the card it
    downloaded months ago.
    """

    def test_the_resource_url_is_the_card_stamped_with_the_version(self):
        self.assertEqual(
            floorplan_mod.card_resource_url("1.8.0"),
            "/local/ha_dispatch/floorplan/ayvra-room-plan.js?v=1.8.0",
        )

    def test_an_unknown_version_leaves_the_url_unstamped_rather_than_faking_one(self):
        # A stamp that never changes reads as a stamp and is worse than none.
        self.assertEqual(
            floorplan_mod.card_resource_url(""),
            "/local/ha_dispatch/floorplan/ayvra-room-plan.js",
        )

    def test_a_deploy_copies_the_card_next_to_the_plan_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[])
            _run(manager._async_run(_job()))

            shipped = os.path.join(
                tmp, "www", "ha_dispatch", "floorplan", "ayvra-room-plan.js"
            )
            self.assertTrue(os.path.isfile(shipped))
            with open(shipped) as handle:
                source = handle.read()
            # The real card, not a placeholder: its custom element is defined
            # and its config contract is the one the generator emits.
            self.assertIn('customElements.define("ayvra-room-plan"', source)
            self.assertIn("setConfig", source)

    def test_the_card_resource_is_registered_once_with_the_clients_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[], client_version="9.9.9")
            _, lovelace_data = _wire_live_lovelace(hass)

            _run(manager._async_run(_job()))

            resources = lovelace_data.resources
            self.assertEqual(
                resources.async_items(),
                [{"id": "res1", "type": "module",
                  "url": "/local/ha_dispatch/floorplan/ayvra-room-plan.js?v=9.9.9"}],
            )
            # Lovelace reads its resource file lazily; an unloaded collection
            # has to be loaded before its contents mean anything.
            self.assertEqual(resources.load_calls, 1)

    def test_redeploying_the_same_version_does_not_add_a_second_resource_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[], client_version="9.9.9")
            _, lovelace_data = _wire_live_lovelace(hass)

            _run(manager._async_run(_job(job_id=1)))
            _run(manager._async_run(_job(job_id=2)))

            resources = lovelace_data.resources
            self.assertEqual(len(resources.async_items()), 1)
            self.assertEqual(len(resources.create_calls), 1)
            # Nothing changed, so nothing was written again either.
            self.assertEqual(resources.update_calls, [])

    def test_a_new_client_version_re_points_the_one_row_instead_of_adding_another(self):
        # The caching caveat this feature exists for: HA caches frontend
        # resources hard, so the URL has to change with the card -- and the
        # row has to be the same row, or the browser loads two copies of the
        # card and the older one may win.
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[], client_version="9.9.9")
            _, lovelace_data = _wire_live_lovelace(hass)
            _run(manager._async_run(_job(job_id=1)))

            manager.coordinator.client_version = "9.10.0"
            _run(manager._async_run(_job(job_id=2)))

            resources = lovelace_data.resources
            self.assertEqual(
                [item["url"] for item in resources.async_items()],
                ["/local/ha_dispatch/floorplan/ayvra-room-plan.js?v=9.10.0"],
            )
            self.assertEqual(len(resources.create_calls), 1)
            self.assertEqual(len(resources.update_calls), 1)

    def test_with_no_version_to_hand_the_url_is_still_stamped(self):
        # An installation that cannot tell us its version still must not be
        # served a stale card forever, so the stamp falls back to the
        # manifest on disk (and, failing that, the copy's mtime). The exact
        # value moves with the release, so only its presence is asserted
        # here; the hand-written expectation lives in the test above.
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[], client_version=None)
            _, lovelace_data = _wire_live_lovelace(hass)

            _run(manager._async_run(_job()))

            url = lovelace_data.resources.async_items()[0]["url"]
            base, _, stamp = url.partition("?v=")
            self.assertEqual(base, "/local/ha_dispatch/floorplan/ayvra-room-plan.js")
            self.assertTrue(stamp)

    def test_without_a_live_resource_collection_the_row_is_written_to_storage(self):
        # YAML-mode lovelace, or a future core where the collection moved:
        # the row is still recorded, and the homeowner is told a restart is
        # what is needed rather than left with missing-card boxes.
        notifications.reset()
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[], client_version="9.9.9")

            _run(manager._async_run(_job()))

            self.assertEqual(
                manager._resources_store.data,
                {"items": [{"id": "ha_dispatch_client_room_plan_card",
                            "type": "module",
                            "url": "/local/ha_dispatch/floorplan/"
                                   "ayvra-room-plan.js?v=9.9.9"}]},
            )
            self.assertEqual(len(notifications.created), 1)
            self.assertIn("Restart", notifications.created[0]["message"])

    def test_the_storage_fallback_re_points_its_row_rather_than_appending(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[], client_version="9.9.9")
            _run(manager._async_run(_job(job_id=1)))
            manager.coordinator.client_version = "9.10.0"
            _run(manager._async_run(_job(job_id=2)))

            items = manager._resources_store.data["items"]
            self.assertEqual(
                [item["url"] for item in items],
                ["/local/ha_dispatch/floorplan/ayvra-room-plan.js?v=9.10.0"],
            )

    def test_a_card_that_cannot_be_copied_fails_the_deploy_out_loud(self):
        # The card IS the dashboard now: views pointing at a custom element
        # that is not there render "Custom element doesn't exist" on every
        # floor, so this is a failed deploy, not a degraded one.
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, hass = _manager(api, tmp, states=[])

            def explode(source, dest):
                raise OSError("read-only file system")

            saved = floorplan_mod._copy_card
            floorplan_mod._copy_card = explode
            try:
                _run(manager._async_run(_job()))
            finally:
                floorplan_mod._copy_card = saved

            self.assertEqual(api.reports[-1]["status"], "failed")
            self.assertIn("room plan card", api.reports[-1]["detail"])


class ServiceWiringTest(unittest.TestCase):
    """Every service is registered, torn down, and described in both files.

    __init__.py cannot be imported here (voluptuous is not installed in the
    dev environment), so this reads it rather than running it -- enough to
    catch the half-wired service, which is the failure that actually happens:
    a handler with no services.yaml entry has no UI fields, and one missing
    from SERVICES is never torn down when the last config entry unloads.
    """

    @staticmethod
    def _init_source():
        import os as _os
        return open(_os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
            "custom_components", "ha_dispatch_client", "__init__.py",
        )).read()

    @staticmethod
    def _component(name):
        import os as _os
        return _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
            "custom_components", "ha_dispatch_client", name,
        )

    def test_every_registered_service_is_declared_torn_down_and_described(self):
        import ast
        import re
        import yaml

        source = self._init_source()
        declared = None
        for node in ast.parse(source).body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "SERVICES":
                declared = [el.value for el in node.value.elts]
        self.assertIsNotNone(declared, "SERVICES tuple not found in __init__.py")

        registered = re.findall(r'async_register\(\s*DOMAIN,\s*"([a-z_]+)"', source)
        with open(self._component("services.yaml")) as handle:
            documented = yaml.safe_load(handle)
        with open(self._component("strings.json")) as handle:
            described = json.load(handle)["services"]

        self.assertEqual(sorted(registered), sorted(declared))
        self.assertEqual(sorted(documented), sorted(declared))
        self.assertEqual(sorted(described), sorted(declared))

    def test_the_assignment_service_offers_the_fields_the_correction_needs(self):
        import yaml
        with open(self._component("services.yaml")) as handle:
            documented = yaml.safe_load(handle)["assign_floorplan_device"]
        self.assertEqual(
            sorted(documented["fields"]),
            ["clear", "device", "entity_id", "floor_key", "room"],
        )
        self.assertTrue(documented["fields"]["device"]["required"])


# --- view rotation ------------------------------------------------------------

ROTATED_TRANSFORM = {
    "Floor_1": dict(PLAN_TRANSFORM["Floor_1"], plan_rotation_deg=90),
}


class RotationTransformTest(unittest.TestCase):
    """What a quarter turn means, pinned down.

    rotate_percent_point is the normative definition of this feature's
    rotation, shared by the card's JS and by the ha-dispatch importer, so it
    is worth more than the usual amount of hand-written arithmetic. Every
    expected pair below was worked out on paper from the picture, never from
    the function: a test that asks the code what the code thinks passes for a
    rotation that turns the wrong way, or not at all.
    """

    # A point near one identifiable corner of the plan: 10% in from the left
    # edge and 20% down from the top, i.e. just inside the TOP-LEFT corner.
    # Asymmetric on both axes and nowhere near the centre on purpose -- a
    # point like (50, 50) or (20, 20) survives a sign error unchanged.
    NEAR_TOP_LEFT = (10.0, 20.0)

    def test_the_corner_lands_where_a_clockwise_quarter_turn_puts_it(self):
        # Turn the plan a quarter CLOCKWISE and the top-left corner is now the
        # top-right corner, so a point just inside it is near the top, well
        # over to the right: 10% from the top edge, 20% in from the right
        # edge -> left 80%, top 10%.
        self.assertEqual(
            floorplan_mod.rotate_percent_point(*self.NEAR_TOP_LEFT, 90),
            (80.0, 10.0),
        )

    def test_the_corner_lands_where_a_half_turn_puts_it(self):
        # Upside down: top-left becomes bottom-right. 10% in from the left
        # edge is now 10% in from the right (left 90%), and 20% down from the
        # top is 20% up from the bottom (top 80%).
        self.assertEqual(
            floorplan_mod.rotate_percent_point(*self.NEAR_TOP_LEFT, 180),
            (90.0, 80.0),
        )

    def test_the_corner_lands_where_a_three_quarter_turn_puts_it(self):
        # 270 clockwise is a quarter anticlockwise: top-left becomes
        # bottom-left. 20% down from the top is now 20% in from the left
        # (left 20%), and 10% in from the left is 10% up from the bottom
        # (top 90%).
        self.assertEqual(
            floorplan_mod.rotate_percent_point(*self.NEAR_TOP_LEFT, 270),
            (20.0, 90.0),
        )

    def test_no_rotation_leaves_the_corner_alone(self):
        self.assertEqual(
            floorplan_mod.rotate_percent_point(*self.NEAR_TOP_LEFT, 0),
            (10.0, 20.0),
        )

    def test_the_four_angles_are_four_different_places(self):
        # Guards the guard: if two of the four agreed, one of the assertions
        # above would be passing by coincidence rather than by rotation.
        landings = {
            floorplan_mod.rotate_percent_point(*self.NEAR_TOP_LEFT, degrees)
            for degrees in (0, 90, 180, 270)
        }
        self.assertEqual(len(landings), 4)

    def test_four_quarter_turns_bring_every_point_home(self):
        # The round trip. A single step can be wrong in a way that the corner
        # assertions catch; a step that is its own inverse (a swapped axis
        # with no flip) would come home after two AND after four, which is
        # why both tests exist rather than just this one.
        for point in [(10.0, 20.0), (0.0, 0.0), (100.0, 0.0), (99.0, 1.0),
                      (50.0, 50.0), (37.5, 83.3)]:
            with self.subTest(point=point):
                turned = point
                for _ in range(4):
                    turned = floorplan_mod.rotate_percent_point(*turned, 90)
                self.assertEqual(turned, point)
                # And one step away from home, so "returns" is not "never moved".
                if point != (50.0, 50.0):
                    self.assertNotEqual(
                        floorplan_mod.rotate_percent_point(*point, 90), point
                    )


class PlanRotationReadingTest(unittest.TestCase):
    """plan_transform.json's plan_rotation_deg, read as a default.

    The cross-repo contract: an integer, one of 0/90/180/270, clockwise;
    absent or invalid means 0. ha-dispatch is growing the field concurrently,
    so every plan already in the field is the "absent" case and must keep
    rendering exactly as it does today.
    """

    def test_a_legal_rotation_is_read_through(self):
        for degrees in (0, 90, 180, 270):
            with self.subTest(degrees=degrees):
                self.assertEqual(
                    floorplan_mod.plan_rotation({"plan_rotation_deg": degrees}),
                    degrees,
                )

    def test_a_malformed_rotation_is_no_rotation_and_does_not_raise(self):
        for value in ["90", "", "ninety", 45, -90, 450, 90.5, 1, True, False,
                      None, [90], {"deg": 90}, float("nan")]:
            with self.subTest(value=value):
                self.assertEqual(
                    floorplan_mod.plan_rotation({"plan_rotation_deg": value}), 0
                )

    def test_an_absent_field_or_absent_transform_is_no_rotation(self):
        for transform in [{}, {"name": "1st Floor", "elev": 0.0}, None, "nonsense", []]:
            with self.subTest(transform=transform):
                self.assertEqual(floorplan_mod.plan_rotation(transform), 0)

    def test_an_integral_float_is_accepted_because_json_floats_happen(self):
        self.assertEqual(floorplan_mod.plan_rotation({"plan_rotation_deg": 180.0}), 180)


class RotationOnTheCardTest(unittest.TestCase):
    """The default reaches the panel, and the geometry does not move."""

    def test_the_floors_default_rotation_rides_the_card_config(self):
        hass = FakeHass(states=[])
        views = floorplan_mod.build_dashboard_views(
            hass, HOTSPOTS, {"devices": []},
            {"Floor_1": "/local/plan_Floor_1.png"}, ROTATED_TRANSFORM,
        )
        card = views[0]["cards"][0]
        self.assertEqual(card["rotation"], 90)
        # The card needs this only to key the panel's own override; it is
        # passed rather than hardcoded in the JS so the two cannot drift.
        self.assertEqual(card["dashboard_path"], "home-3d")

    def test_a_plan_from_before_the_field_existed_is_unrotated(self):
        hass = FakeHass(states=[])
        views = floorplan_mod.build_dashboard_views(
            hass, HOTSPOTS, {"devices": []},
            {"Floor_1": "/local/plan_Floor_1.png"}, PLAN_TRANSFORM,
        )
        self.assertEqual(views[0]["cards"][0]["rotation"], 0)

    def test_each_floor_carries_its_own_rotation(self):
        hass = FakeHass(states=[])
        transform = {
            "Floor_1": dict(TWO_FLOOR_TRANSFORM["Floor_1"], plan_rotation_deg=270),
            "Floor_2": dict(TWO_FLOOR_TRANSFORM["Floor_2"], plan_rotation_deg=90),
        }
        views = floorplan_mod.build_dashboard_views(
            hass, TWO_FLOOR_HOTSPOTS, {"devices": []},
            {"Floor_1": "/local/plan_Floor_1.png", "Floor_2": "/local/plan_Floor_2.png"},
            transform,
        )
        self.assertEqual(
            {v["title"]: v["cards"][0]["rotation"] for v in views},
            {"1st Floor": 270, "2nd Floor": 90},
        )

    def test_a_rotated_floor_still_ships_unrotated_geometry(self):
        """Rotation is applied in the browser, to the picture and the shapes
        together. Pre-rotating the polygons here while the PNG stayed put is
        exactly how the rooms would slide off the plan underneath them, so
        the coordinates a rotated floor ships must be byte-for-byte the ones
        an unrotated floor ships."""
        hass = FakeHass(states=[])
        plain = floorplan_mod.build_room_plan_card(
            hass, HOTSPOTS["Floor_1"], [], "/local/plan.png",
            transform=PLAN_TRANSFORM["Floor_1"],
        )
        turned = floorplan_mod.build_room_plan_card(
            hass, HOTSPOTS["Floor_1"], [], "/local/plan.png",
            transform=ROTATED_TRANSFORM["Floor_1"], rotation=90,
        )
        self.assertEqual(turned["rotation"], 90)
        self.assertEqual(turned["rooms"], plain["rooms"])
        # Including the aspect ratio: it describes the IMAGE, which the
        # browser swaps when it turns the plan.
        self.assertEqual(turned["aspect_ratio"], plain["aspect_ratio"])
        self.assertEqual(_room(turned, "Den")["label"], list(DEN_LABEL))

    def test_an_out_of_contract_rotation_cannot_reach_the_card(self):
        hass = FakeHass(states=[])
        for degrees in (45, -90, 450, "90", None, True):
            with self.subTest(degrees=degrees):
                card = floorplan_mod.build_room_plan_card(
                    hass, HOTSPOTS["Floor_1"], [], "/local/plan.png",
                    transform=PLAN_TRANSFORM["Floor_1"], rotation=degrees,
                )
                self.assertEqual(card["rotation"], 0)

    def test_a_floor_with_no_geometry_at_all_still_carries_its_rotation(self):
        hass = FakeHass(states=[])
        card = floorplan_mod.build_room_plan_card(
            hass, [{"name": "Nowhere"}], [], "/local/plan.png", rotation=180,
        )
        self.assertEqual(card["rooms"], [])
        self.assertEqual(card["rotation"], 180)
        self.assertEqual(card["dashboard_path"], "home-3d")


class RotationThroughADeployTest(unittest.TestCase):
    """End to end: plan_transform.json on disk -> the written dashboard."""

    ROTATED_TRANSFORM_URL = "https://dispatch.example.com/assets/plan_transform.r.json"

    def _assets(self):
        return dict(ASSETS, **{
            self.ROTATED_TRANSFORM_URL: json.dumps(ROTATED_TRANSFORM),
        })

    def test_the_deployed_dashboard_carries_the_imports_rotation(self):
        notifications.reset()
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=self._assets())
            manager, _hass = _manager(api, tmp)
            _run(manager._async_run(
                _job(plan_transform_url=self.ROTATED_TRANSFORM_URL)
            ))
            card = manager._dashboard_store.data["config"]["views"][0]["cards"][0]
            self.assertEqual(card["rotation"], 90)
            self.assertEqual(card["dashboard_path"], "home-3d")
            # And the report names it, so an installer looking at a plan that
            # is not facing the way they expect can tell whether the import
            # asked for that or somebody turned that one tablet.
            self.assertIn("plan rotated by default: Floor_1 90deg",
                          api.reports[-1]["detail"])

    def test_an_unrotated_deploy_says_nothing_about_rotation(self):
        notifications.reset()
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeApi(assets=ASSETS)
            manager, _hass = _manager(api, tmp)
            _run(manager._async_run(_job()))
            self.assertEqual(
                manager._dashboard_store.data["config"]["views"][0]["cards"][0]["rotation"],
                0,
            )
            self.assertNotIn("rotat", api.reports[-1]["detail"])


class RotationControlSourceTest(unittest.TestCase):
    """The card's half of the rotation contract, read off the shipped file.

    The suite is pytest and the card is hand-written JavaScript with no
    toolchain (deliberately -- see the project's CLAUDE.md), so its behaviour
    cannot be executed here. What can be checked is that the file still
    honours the parts of the contract that are strings: the localStorage key,
    the four legal angles, and that no storage call is made outside a
    try/catch. A kiosk browser with site data blocked throws on these.
    """

    @staticmethod
    def _card_source():
        return open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "custom_components", "ha_dispatch_client", "www", "ayvra-room-plan.js",
        )).read()

    def test_the_override_is_keyed_exactly_as_the_contract_says(self):
        source = self._card_source()
        self.assertIn(
            "`ayvra.room-plan.rotation.${this._config.dashboard_path}."
            "${this._config.floor_key}`",
            source,
        )

    def test_only_quarter_turns_are_offered(self):
        self.assertIn("const ROTATIONS = [0, 90, 180, 270];", self._card_source())

    def test_every_localstorage_call_sits_inside_a_try(self):
        source = self._card_source()
        calls = [line for line in source.splitlines()
                 if "window.localStorage" in line]
        self.assertEqual(len(calls), 2, calls)
        for call in calls:
            # The statement itself, and the `try {` that must precede it.
            before = source.split(call)[0]
            self.assertGreater(before.count("try {"), before.count("} catch"))

    def test_the_card_reads_the_rotation_default_out_of_its_config(self):
        source = self._card_source()
        self.assertIn("rotation: normaliseRotation(config.rotation)", source)
        self.assertIn("stored === null ? this._config.rotation : stored", source)


class FocusTreatmentSourceTest(unittest.TestCase):
    """The focus treatment's half of the contract, read off the shipped card.

    Be clear about what this class is and is not. The focus treatment is CSS
    inside a JavaScript custom element: the grey-out, the full-colour copy of
    the focused room, the docked sheet and the transition are all things a
    BROWSER does, and pytest cannot see any of them. Nothing here claims to.

    What it does pin are the three decisions that are invisible in a diff and
    expensive to lose: that no colour is hardcoded past a theme variable (the
    card would then fight a dark theme), that the grey-out is applied to the
    plan layer and never to the layer holding the focused room (the focused
    room would then be greyed with everything else, which is the opposite of
    what the customer asked for), and that a device asking for less motion
    gets none. Each is a string in the file, and a string is what is checked.

    The behaviour itself was verified by rendering the card in a headless
    browser and measuring the result; what that run covered, and what still
    needs a human at a tablet, is written down in docs/user/deployment.md.
    """

    @staticmethod
    def _source():
        return open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "custom_components", "ha_dispatch_client", "www", "ayvra-room-plan.js",
        )).read()

    @classmethod
    def _stylesheet(cls, source=None):
        """The card's stylesheet, with its comments removed."""
        styles = (source or cls._source()).split("const STYLES = `", 1)[1]
        styles = styles.split("\n`;", 1)[0]
        return re.sub(r"/\*.*?\*/", "", styles, flags=re.S)

    @staticmethod
    def _rules(stylesheet):
        """[(selector, declarations)] -- enough of a parser for flat CSS."""
        rules = []
        for chunk in stylesheet.split("}"):
            if "{" not in chunk:
                continue
            selector, declarations = chunk.split("{", 1)
            rules.append((" ".join(selector.split()), declarations))
        return rules

    def test_every_colour_the_card_paints_is_a_theme_variable(self):
        # A card that hardcodes even one colour is a card that looks wrong in
        # whichever theme the homeowner did not have on the day it was built.
        # Colour literals are allowed in exactly one place -- as the fallback
        # inside a var() on :host, for a Home Assistant that is missing that
        # theme variable entirely.
        stylesheet = self._stylesheet()
        host = stylesheet.split(":host {", 1)[1].split("\n  }", 1)[0]
        elsewhere = stylesheet.replace(host, "")
        literals = re.compile(r"#[0-9a-fA-F]{3,8}|\brgba?\(")
        self.assertEqual(
            literals.findall(elsewhere), [],
            "a colour is painted outside :host instead of through a variable",
        )
        for line in host.splitlines():
            if literals.search(line):
                self.assertIn(
                    "var(", line,
                    f"colour literal that is not a var() fallback: {line.strip()}",
                )

    def test_the_themes_own_variables_are_what_those_fallbacks_fall_back_from(self):
        # Hand-written, not read out of the file: these are Home Assistant's
        # own theme variable names, and deriving from them is what makes the
        # card follow the homeowner's theme instead of declaring its own.
        host = self._stylesheet().split(":host {", 1)[1].split("\n  }", 1)[0]
        for variable in (
            "--primary-text-color",
            "--secondary-text-color",
            "--card-background-color",
            "--secondary-background-color",
            "--divider-color",
            "--primary-color",
            "--text-primary-color",
        ):
            self.assertIn(variable, host)

    def test_the_grey_out_treats_the_plan_and_never_the_focused_room(self):
        # "An active in focus room to retain the main colors and almost have
        # the rest of the UI grey out." The honest reading is that the focused
        # room is not treated AT ALL, which only holds while the filter stays
        # off the layer that carries it.
        filtered = [
            selector for selector, declarations in self._rules(self._stylesheet())
            if "filter:" in declarations
        ]
        self.assertTrue(filtered, "nothing is filtered; the grey-out is gone")
        for selector in filtered:
            # `.wrap.focused .layer.plan` is the grey-out itself and is meant
            # to be here; what must never pick up a filter is the layer that
            # carries the focused room's full-colour copy.
            self.assertIn(".layer.plan", selector)
            self.assertNotIn("focus-layer", selector)
            self.assertNotIn("focus-plate", selector)

    def test_the_focused_room_is_an_untreated_copy_of_the_same_backplate(self):
        # Same picture, same box, same rotating layer, cut to the room's own
        # outline: that is what makes the full-colour room land on the room it
        # was cut from without anything having to be kept in step by hand.
        source = self._source()
        self.assertIn('focusPlate.className = "focus-plate";', source)
        self.assertIn("focusLayer.appendChild(focusPlate);", source)
        self.assertIn("this._els.focusPlate.style.clipPath = clip;", source)
        # Both plates take their src from the SAME call, so they cannot end up
        # showing two differently-lit renders of the same floor -- which is
        # what would happen if only one of them followed the rotation.
        self.assertIn(
            "focusPlate.src = this._plateFor(normaliseRotation(this._rotation));",
            source,
        )
        self.assertIn(
            "plate.src = this._plateFor(normaliseRotation(this._rotation));", source
        )
        # ...and when the rotation changes, both are swapped together.
        swap = source.split("_applyRotation() {", 1)[1].split("\n  _", 1)[0]
        self.assertIn("this._els.plate.src = plate;", swap)
        self.assertIn("this._els.focusPlate.src = plate;", swap)

    def test_a_device_that_asks_for_less_motion_gets_none(self):
        stylesheet = self._stylesheet()
        self.assertIn("@media (prefers-reduced-motion: reduce)", stylesheet)
        reduced = stylesheet.split("@media (prefers-reduced-motion: reduce) {", 1)[1]
        self.assertIn("--ayvra-plan-motion: 0ms;", reduced)

    def test_the_controls_sit_on_the_backdrop_rather_than_over_the_room(self):
        # The sheet docks to an edge, and the edge is chosen from where the
        # room ended up ON SCREEN -- which at 90 or 270 degrees is not where
        # its coordinates say it is.
        source = self._source()
        self.assertIn("rotatePercentPoint(anchor[0], anchor[1], this._rotation)", source)
        for dock in ("dock-left", "dock-right", "dock-top", "dock-bottom"):
            self.assertIn(dock, self._stylesheet(source))


# --- room names and label clutter --------------------------------------------

# A floor shaped like the dense middle of Paul Scott's first floor: one real
# room cut by the sketch into numbered sub-cells, a small room with nothing in
# it, and a small room that does have a control. Polygons are nonsense squares
# -- nothing here tests geometry -- but every room has one, which is what lets
# the tappability assertions mean something.
def _cell(name, sf, x):
    return {
        "name": name,
        "sf": sf,
        "centroid": [x, 0.0],
        "poly_ft": [[x - 1, -1], [x + 1, -1], [x + 1, 1], [x - 1, 1]],
    }


SUBCELL_FLOOR = [
    _cell("Master Bathroom", 88.7, -20.0),      # the room: has the controls
    _cell("Master Bathroom #4", 15.7, -16.0),   # sub-cells, all empty
    _cell("Master Bathroom #5", 32.1, -12.0),
    _cell("Master Bathroom #6", 6.0, -8.0),
    _cell("Half Bathroom", 39.5, -4.0),         # small, empty, stands alone
    _cell("Garage Entry", 64.8, 0.0),           # big enough to keep its name
    _cell("Powder Room", 20.0, 4.0),            # small BUT has a control
    _cell("Kitchen", 341.7, 8.0),
]

SUBCELL_DEVICES = [
    {"entity": "light.mb.01", "domain": "light", "cls": "light",
     "room": "Master Bathroom", "floor_key": "Floor_1"},
    {"entity": "light.powder.01", "domain": "light", "cls": "light",
     "room": "Powder Room", "floor_key": "Floor_1"},
]


def _subcell_card(rooms=None, devices=None):
    hass = FakeHass(states=[
        FakeState("light.master_bathroom", "Master Bathroom Light"),
        FakeState("light.powder_room", "Powder Room Light"),
    ])
    return floorplan_mod.build_room_plan_card(
        hass,
        SUBCELL_FLOOR if rooms is None else rooms,
        SUBCELL_DEVICES if devices is None else devices,
        "/local/plan.png",
        transform=PLAN_TRANSFORM["Floor_1"],
    )


def _by_name(card):
    return {room["name"]: room for room in card["rooms"]}


class RoomDisplayNameTest(unittest.TestCase):
    """The sketch's sub-cell numbering, kept out of a homeowner's way."""

    def test_a_sub_cell_number_is_stripped_for_display(self):
        for raw, shown in [
            ("Master Bathroom #4", "Master Bathroom"),
            ("Foyer #7", "Foyer"),
            ("Garage Side Stairs #2", "Garage Side Stairs"),
            ("Boys Full Bathroom #2", "Boys Full Bathroom"),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(floorplan_mod.room_display_name(raw), shown)

    def test_an_ordinary_name_is_left_exactly_alone(self):
        for name in ["Kitchen", "Great Room", "Master Bedroom", "Side Entry"]:
            with self.subTest(name=name):
                self.assertEqual(floorplan_mod.room_display_name(name), name)

    def test_a_number_that_is_not_a_sub_cell_suffix_survives(self):
        # Only a trailing #N is the sketch's numbering. A name that is really
        # about a number keeps it.
        self.assertEqual(floorplan_mod.room_display_name("Bedroom 2"), "Bedroom 2")
        self.assertEqual(floorplan_mod.room_display_name("Unit #4 Kitchen"),
                         "Unit #4 Kitchen")

    def test_a_name_that_is_nothing_but_a_suffix_is_not_emptied(self):
        # Stripping must never leave a room with no name at all.
        self.assertEqual(floorplan_mod.room_display_name("#4"), "#4")

    def test_the_join_key_is_not_renamed(self):
        # The whole reason `display` exists. `name` is matched against a
        # device's room and an installer's correction; renaming it to make it
        # pretty would silently unplace devices.
        card = _subcell_card()
        self.assertIn("Master Bathroom #4", _by_name(card))
        self.assertEqual(_by_name(card)["Master Bathroom #4"]["display"],
                         "Master Bathroom")


class RoomLabelTest(unittest.TestCase):
    """Which rooms carry a name chip -- and the promise that none of this
    costs a tap."""

    def test_every_room_stays_tappable_however_its_label_is_decided(self):
        # THE GUARANTEE. Hiding a chip must never remove a room, and must
        # never leave one without the polygon that makes it tappable.
        card = _subcell_card()
        self.assertEqual(len(card["rooms"]), len(SUBCELL_FLOOR))
        hidden = [r for r in card["rooms"] if not r["show_label"]]
        self.assertTrue(hidden, "this fixture is pointless if nothing is hidden")
        for room in card["rooms"]:
            with self.subTest(room=room["name"]):
                self.assertGreaterEqual(len(room["poly"]), 3)

    def test_empty_sub_cells_fold_into_the_room_they_belong_to(self):
        card = _subcell_card()
        rooms = _by_name(card)
        primary = card["rooms"].index(rooms["Master Bathroom"])
        for sub in ["Master Bathroom #4", "Master Bathroom #5", "Master Bathroom #6"]:
            with self.subTest(sub=sub):
                self.assertFalse(rooms[sub]["show_label"])
                self.assertEqual(rooms[sub]["same_room_as"], primary)
        # ...and the room itself is untouched.
        self.assertTrue(rooms["Master Bathroom"]["show_label"])
        self.assertIsNone(rooms["Master Bathroom"]["same_room_as"])

    def test_a_sub_cell_with_its_own_controls_is_never_folded_away(self):
        # Folding it would put its devices where no tap can reach them.
        rooms = SUBCELL_FLOOR + [_cell("Master Bathroom #9", 10.0, 12.0)]
        devices = SUBCELL_DEVICES + [
            {"entity": "light.mb9.01", "domain": "light", "cls": "light",
             "room": "Master Bathroom #9", "floor_key": "Floor_1"},
        ]
        card = _subcell_card(rooms=rooms, devices=devices)
        sub = _by_name(card)["Master Bathroom #9"]
        self.assertTrue(sub["entities"], "fixture must give this cell a control")
        self.assertIsNone(sub["same_room_as"])
        self.assertTrue(sub["show_label"])

    def test_a_small_room_with_nothing_in_it_goes_quiet(self):
        rooms = _by_name(_subcell_card())
        self.assertFalse(rooms["Half Bathroom"]["show_label"])
        # ...but it is nobody's sub-cell, so a tap still opens its own panel.
        self.assertIsNone(rooms["Half Bathroom"]["same_room_as"])

    def test_a_small_room_that_has_a_control_keeps_its_name(self):
        # The label is how you find the control. Size never overrides that.
        rooms = _by_name(_subcell_card())
        powder = rooms["Powder Room"]
        self.assertTrue(powder["entities"])
        self.assertTrue(powder["show_label"])

    def test_a_big_empty_room_keeps_its_name(self):
        self.assertTrue(_by_name(_subcell_card())["Garage Entry"]["show_label"])

    def test_a_manifest_with_no_areas_keeps_every_name(self):
        # Unknown area is not zero. An older manifest carries no `sf`, and
        # treating that as "tiny" would strip the names off a whole house.
        rooms = [{k: v for k, v in cell.items() if k != "sf"}
                 for cell in SUBCELL_FLOOR]
        card = _subcell_card(rooms=rooms, devices=[])
        standalone = [r for r in card["rooms"] if r["same_room_as"] is None]
        self.assertTrue(all(r["show_label"] for r in standalone))


# --- per-rotation backplates --------------------------------------------------


class PlanFilenameTest(unittest.TestCase):
    """Reading a backplate's floor and rotation back out of its name."""

    def test_an_unrotated_plan_keeps_the_historic_name(self):
        self.assertEqual(
            floorplan_mod._plan_filename("Floor_1", 0), "plan_Floor_1.png"
        )

    def test_a_rotated_plan_is_suffixed_with_its_degrees(self):
        self.assertEqual(
            floorplan_mod._plan_filename("Floor_1", 90), "plan_Floor_1_90.png"
        )

    def test_a_rotation_suffix_is_not_mistaken_for_part_of_the_floor(self):
        # THE BUG THIS EXISTS FOR. Splitting on ".png" alone reads
        # plan_Floor_1_90.png as a floor called "Floor_1_90" and puts a storey
        # on the dashboard that does not exist.
        self.assertEqual(
            floorplan_mod._parse_plan_filename("plan_Floor_1_90.png"), ("Floor_1", 90)
        )
        self.assertEqual(
            floorplan_mod._parse_plan_filename("plan_Floor_1.png"), ("Floor_1", 0)
        )

    def test_a_level_key_may_itself_contain_underscores_and_digits(self):
        # Which is why the ROTATION is matched, rather than assuming the key
        # has no underscore in it.
        self.assertEqual(
            floorplan_mod._parse_plan_filename("plan_Floor_1.png"), ("Floor_1", 0)
        )
        self.assertEqual(
            floorplan_mod._parse_plan_filename("plan_Level_2_B.png"), ("Level_2_B", 0)
        )
        self.assertEqual(
            floorplan_mod._parse_plan_filename("plan_Level_2_B_270.png"),
            ("Level_2_B", 270),
        )

    def test_a_number_that_is_not_a_legal_rotation_stays_in_the_key(self):
        # 45 is not a rotation this product has, so plan_Floor_45.png is a
        # floor called "Floor_45", not Floor at 45 degrees.
        self.assertEqual(
            floorplan_mod._parse_plan_filename("plan_Floor_45.png"), ("Floor_45", 0)
        )

    def test_something_that_is_not_a_plan_is_not_one(self):
        for name in ("hotspots.json", "backplate.png", "plan_.png", "plan.png"):
            with self.subTest(name=name):
                self.assertIsNone(floorplan_mod._parse_plan_filename(name))


class RotationBackplateTest(unittest.TestCase):
    """Carrying the per-rotation renders through to the card."""

    def _discover(self, names):
        import pathlib
        with tempfile.TemporaryDirectory() as tmp:
            directory = pathlib.Path(tmp)
            for name in names:
                (directory / name).write_bytes(b"x")
            return floorplan_mod._discover_assets(directory)

    def test_the_variants_are_grouped_under_their_own_floor(self):
        paths = self._discover([
            "plan_Floor_1.png", "plan_Floor_1_90.png",
            "plan_Floor_1_180.png", "plan_Floor_1_270.png",
            "plan_Floor_2.png",
        ])
        # The wrong answer first: a parser that swallowed the suffix would
        # report four extra floors here.
        self.assertEqual(sorted(paths["plan_image_urls"]), ["Floor_1", "Floor_2"])
        self.assertEqual(
            sorted(paths["plan_rotation_image_urls"]["Floor_1"]), [0, 90, 180, 270]
        )
        self.assertEqual(sorted(paths["plan_rotation_image_urls"]["Floor_2"]), [0])

    def test_the_card_carries_every_variant_it_was_given(self):
        hass = FakeHass(states=[])
        card = floorplan_mod.build_room_plan_card(
            hass, HOTSPOTS["Floor_1"], [], "/local/plan_Floor_1.png",
            transform=PLAN_TRANSFORM["Floor_1"],
            images={0: "/local/plan_Floor_1.png", 90: "/local/plan_Floor_1_90.png"},
        )
        # String keys: this is JSON by the time the card reads it.
        self.assertEqual(
            card["images"],
            {"0": "/local/plan_Floor_1.png", "90": "/local/plan_Floor_1_90.png"},
        )

    def test_an_illegal_rotation_cannot_reach_the_card(self):
        hass = FakeHass(states=[])
        card = floorplan_mod.build_room_plan_card(
            hass, HOTSPOTS["Floor_1"], [], "/local/plan.png",
            transform=PLAN_TRANSFORM["Floor_1"],
            images={45: "/local/nope.png", 90: "/local/plan_90.png"},
        )
        self.assertEqual(sorted(card["images"]), ["90"])

    def test_a_plan_from_before_the_variants_existed_still_builds(self):
        hass = FakeHass(states=[])
        card = floorplan_mod.build_room_plan_card(
            hass, HOTSPOTS["Floor_1"], [], "/local/plan.png",
            transform=PLAN_TRANSFORM["Floor_1"],
        )
        self.assertEqual(card["images"], {})
        self.assertEqual(card["image"], "/local/plan.png")

    def test_the_card_falls_back_to_the_one_image_it_has(self):
        source = open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "custom_components", "ha_dispatch_client", "www", "ayvra-room-plan.js",
        )).read()
        self.assertIn(
            "return (this._images && this._images[degrees]) || this._config.image;",
            source,
        )
