"""Home Assistant areas: what the deploy report says about them, and filing a
plan's devices into them.

The promises tested here are the ones that make writing to the homeowner's
own registry acceptable: nothing is touched unless the deploy asked, nothing
is touched without enhanced permissions on this client's own record, `fill`
never moves an entity the homeowner filed, an area is created only to put
something in it, and only entities are written -- never devices.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from homeassistant.components import persistent_notification as notifications  # noqa: E402
from test_floorplan import (  # noqa: E402
    _load,
    ASSETS,
    FakeApi,
    FakeArea,
    FakeDevice,
    FakeRegistry,
    FakeRegistryEntry,
    FakeState,
    _install_registries,
    _job,
    _manager,
    _run,
)

areas_mod = _load("areas")
const = _load("const")


class WritableRegistry(FakeRegistry):
    """FakeRegistry plus the three writers/listers a sync uses, recording
    every write so a test can say exactly what was changed."""

    def __init__(self, entities=(), devices=None, areas=None):
        super().__init__(entities, devices, areas)
        for area_id, area in self._areas.items():
            area.id = area_id
        self.created = []
        self.updates = []

    def async_list_areas(self):
        return list(self._areas.values())

    def async_create(self, name):
        area = FakeArea(name)
        area.id = f"new_{len(self.created)}"
        self._areas[area.id] = area
        self.created.append(name)
        return area

    def async_update_entity(self, entity_id, area_id):
        self.entities[entity_id].area_id = area_id
        self.updates.append((entity_id, area_id))


class FakeGrant:
    def __init__(self, granted, granted_after_poll=None):
        self.granted = granted
        self._after = granted if granted_after_poll is None else granted_after_poll
        self.polls = 0

    async def async_poll(self):
        self.polls += 1
        self.granted = self._after


class PlanAreaSyncTest(unittest.TestCase):
    def test_fill_leaves_an_entity_the_homeowner_filed_elsewhere_alone(self):
        plan = areas_mod.plan_area_sync(
            [("light.a", "Kitchen"), ("light.b", "Kitchen")],
            {"light.a": "Den", "light.b": None},
            {"light.a", "light.b"},
            const.AREA_SYNC_FILL,
        )
        self.assertEqual(plan["file"], {"Kitchen": ["light.b"]})
        self.assertEqual(
            plan["skipped"],
            [{"entity": "light.a", "area": "Kitchen", "why": "filed_elsewhere", "current": "Den"}],
        )

    def test_move_refiles_it(self):
        plan = areas_mod.plan_area_sync(
            [("light.a", "Kitchen")], {"light.a": "Den"}, {"light.a"}, const.AREA_SYNC_MOVE,
        )
        self.assertEqual(plan["file"], {"Kitchen": ["light.a"]})
        self.assertEqual(plan["skipped"], [])

    def test_an_entity_already_in_its_room_is_counted_not_rewritten(self):
        # Compared the way Home Assistant compares area names.
        plan = areas_mod.plan_area_sync(
            [("light.a", "Living  Room")], {"light.a": "living room"}, {"light.a"},
            const.AREA_SYNC_MOVE,
        )
        self.assertEqual(plan["file"], {})
        self.assertEqual(plan["already"], 1)

    def test_an_entity_with_no_registry_entry_is_reported_not_written(self):
        plan = areas_mod.plan_area_sync(
            [("light.yaml_only", "Kitchen")], {}, set(), const.AREA_SYNC_FILL,
        )
        self.assertEqual(plan["file"], {})
        self.assertEqual(plan["skipped"][0]["why"], "not_registered")

    def test_one_entity_standing_for_two_rooms_is_not_guessed(self):
        plan = areas_mod.plan_area_sync(
            [("light.group", "Kitchen"), ("light.group", "Dining Room")],
            {}, {"light.group"}, const.AREA_SYNC_FILL,
        )
        self.assertEqual(plan["file"], {})
        self.assertEqual(plan["skipped"][0]["why"], "two_rooms")


class AsyncSyncAreasTest(unittest.TestCase):
    def _registry(self):
        return WritableRegistry(
            entities=[
                FakeRegistryEntry("light.kitchen_1"),
                FakeRegistryEntry("light.den_lamp", device_id="dev1"),
                FakeRegistryEntry("climate.hall", area_id="hall"),
            ],
            devices={"dev1": FakeDevice("den")},
            areas={"den": FakeArea("den"), "hall": FakeArea("Hallway")},
        )

    def test_creates_only_the_areas_it_files_into_and_reuses_existing_ones(self):
        registry = self._registry()
        self.addCleanup(_install_registries(registry))

        report = areas_mod.async_sync_areas(
            object(),
            [("light.kitchen_1", "Kitchen"), ("light.den_lamp", "Den"),
             ("climate.hall", "Closet")],
            const.AREA_SYNC_FILL,
        )

        self.assertEqual(report["status"], "done")
        # Den already exists (as "den"); Closet would hold only an entity the
        # homeowner filed in Hallway, so under `fill` it is never created.
        self.assertEqual(registry.created, ["Kitchen"])
        self.assertEqual(report["created"], ["Kitchen"])
        self.assertEqual(registry.updates, [("light.kitchen_1", "new_0")])
        self.assertEqual(report["already"], 1)
        self.assertEqual(report["filed_count"], 1)
        self.assertEqual(
            report["skipped"],
            [{"entity": "climate.hall", "area": "Closet", "why": "filed_elsewhere",
              "current": "Hallway"}],
        )

    def test_move_writes_the_entity_never_its_device(self):
        registry = self._registry()
        self.addCleanup(_install_registries(registry))

        areas_mod.async_sync_areas(
            object(), [("light.den_lamp", "Kitchen")], const.AREA_SYNC_MOVE,
        )

        self.assertEqual(registry.updates, [("light.den_lamp", "new_0")])
        self.assertEqual(registry._devices["dev1"].area_id, "den")

    def test_no_registry_is_a_failed_report_not_an_exception(self):
        report = areas_mod.async_sync_areas(
            object(), [("light.a", "Kitchen")], const.AREA_SYNC_FILL,
        )
        self.assertEqual(report["status"], "failed")


class DeployAreaTest(unittest.TestCase):
    """The deploy's side: when a sync runs, under whose permission, and what
    the report and the homeowner's notification say about it."""

    def _deploy(self, job, grant=None):
        registry = WritableRegistry(
            entities=[FakeRegistryEntry("climate.den_thermostat")],
            areas={"office": FakeArea("Office")},
        )
        self.addCleanup(_install_registries(registry))
        notifications.reset()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        api = FakeApi(assets=ASSETS)
        manager, _hass = _manager(
            api, tmp.name, states=[FakeState("climate.den_thermostat", "Den Thermostat")]
        )
        if grant is not None:
            manager.coordinator.enhanced_permissions = grant
        _run(manager._async_run(job))
        return api.reports[0], registry

    def test_a_deploy_that_did_not_ask_leaves_the_registry_alone(self):
        report, registry = self._deploy(_job(), FakeGrant(True))

        self.assertIsNone(report["area_sync"])
        self.assertEqual(registry.created, [])
        self.assertEqual(registry.updates, [])
        # The house's areas ride every report, for naming drawn rooms.
        self.assertEqual(report["areas"], ["Office"])
        self.assertIn("area", report["devices"][0])

    def test_a_deploy_that_asked_files_under_the_grant_and_tells_the_homeowner(self):
        report, registry = self._deploy(_job(area_sync="fill"), FakeGrant(True))

        self.assertEqual(report["status"], "done")
        self.assertEqual(report["area_sync"]["status"], "done")
        self.assertEqual(registry.created, ["Den"])
        self.assertEqual(registry.updates, [("climate.den_thermostat", "new_0")])
        # The report's per-device area is the one the house has now.
        bound = [d for d in report["devices"] if d["bound"]]
        self.assertEqual([d["area"] for d in bound], ["Den"])
        self.assertEqual(sorted(report["areas"]), ["Den", "Office"])
        self.assertIn("1 device(s)", notifications.created[0]["message"])
        self.assertIn("Den", notifications.created[0]["message"])

    def test_without_the_grant_it_refuses_and_the_dashboard_still_deploys(self):
        grant = FakeGrant(False)
        report, registry = self._deploy(_job(area_sync="move"), grant)

        self.assertEqual(report["status"], "done")
        self.assertEqual(report["area_sync"]["status"], "refused")
        self.assertEqual(report["area_sync"]["reason"], "no_grant")
        self.assertEqual(registry.updates, [])
        self.assertEqual(registry.created, [])
        # Asked once more before a "no" is believed.
        self.assertEqual(grant.polls, 1)

    def test_a_grant_that_only_just_landed_is_seen(self):
        report, registry = self._deploy(
            _job(area_sync="fill"), FakeGrant(False, granted_after_poll=True)
        )
        self.assertEqual(report["area_sync"]["status"], "done")
        self.assertEqual(registry.created, ["Den"])

    def test_no_enhanced_permissions_manager_at_all_is_a_refusal(self):
        report, registry = self._deploy(_job(area_sync="fill"))
        self.assertEqual(report["area_sync"]["status"], "refused")
        self.assertEqual(registry.updates, [])


if __name__ == "__main__":
    unittest.main()
