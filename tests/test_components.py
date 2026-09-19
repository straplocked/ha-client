"""Tests for component inventory reporting.

The bug these cover: the client never posted an inventory at all, so the
dashboard's Components tab was empty for every installation and the fleet's
update-risk intelligence had nothing to score.

Three things here are load-bearing, and all three are easy to break without
noticing:

1. Every report is a **full snapshot**. The server retires anything absent, so
   an agent that "optimised" away unchanged components would uninstall half the
   fleet's inventory on its first run.
2. Slugs are **stable**. Rename an add-on, retitle a config entry, rename a
   HACS repository's display name -- the slug must not move, or the timeline
   reads as a removal plus an install.
3. The inventory rides its **own cadence**, not the 60 s metrics poll.

Home Assistant is not installed here; tests/conftest.py stubs the symbols the
integration imports, including Supervisor helpers that answer the way a
Core-only installation does until a test says otherwise.
"""
import asyncio
import importlib
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone


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


components_mod = _load("components")
const = _load("const")
coordinator_mod = _load("coordinator")
updater_mod = _load("updater")

hassio = sys.modules["homeassistant.components.hassio"]
loader = sys.modules["homeassistant.loader"]
dt_module = sys.modules["homeassistant.util.dt"]
ConfigEntryState = sys.modules["homeassistant.config_entries"].ConfigEntryState

collect = components_mod.async_collect_components


# --- the installation under test ---------------------------------------------
#
# A supervised install with something of every kind, so "a whole kind is
# missing" is a deviation the tests can see rather than the default.

EXPECTED_INVENTORY = {
    ("core", "core"),
    ("os", "os"),
    ("supervisor", "supervisor"),
    ("addon", "core_mosquitto"),
    ("addon", "a0d7b954_nodered"),
    ("addon", "local_lab"),
    ("hacs", "blakeblackshear/frigate-hass-integration"),
    ("hacs", "straplocked/ha-dispatch-client"),
    ("integration", "zha"),
    ("integration", "hue"),
    ("integration", "frigate"),
    ("integration", "ha_dispatch_client"),
}

# The order the sources are read in, which is NOT the order the inventory is
# reported in. Written out so the ordering test can assert against it: if
# _prioritise ever became a no-op, this is what would come out.
COLLECTION_ORDER = [
    ("core", "core"),
    ("os", "os"),
    ("supervisor", "supervisor"),
    ("addon", "local_lab"),
    ("addon", "core_mosquitto"),
    ("addon", "a0d7b954_nodered"),
    ("hacs", "blakeblackshear/frigate-hass-integration"),
    ("hacs", "straplocked/ha-dispatch-client"),
    ("integration", "frigate"),
    ("integration", "ha_dispatch_client"),
    ("integration", "hue"),
    ("integration", "zha"),
]

# Platform first; then everything carrying a version, sorted; then everything
# without one, sorted. Hand-written, because the point of the rule is that a
# huge installation loses rows somebody chose for it to lose.
EXPECTED_ORDER = [
    ("core", "core"),
    ("os", "os"),
    ("supervisor", "supervisor"),
    ("addon", "a0d7b954_nodered"),
    ("addon", "core_mosquitto"),
    ("hacs", "blakeblackshear/frigate-hass-integration"),
    ("hacs", "straplocked/ha-dispatch-client"),
    ("integration", "frigate"),
    ("integration", "ha_dispatch_client"),
    ("addon", "local_lab"),
    ("integration", "hue"),
    ("integration", "zha"),
]

# Every kind the server will accept -- InstallationComponent::KINDS. Written
# out rather than imported: this is a wire contract, and a test that reads the
# client's own list would keep passing while the client invented a new one.
SERVER_KINDS = {"core", "os", "supervisor", "addon", "integration", "hacs"}


# --- fakes -------------------------------------------------------------------


class FakeConfigEntry:
    def __init__(self, domain, title, state=None, disabled_by=None):
        self.domain = domain
        self.title = title
        self.state = state if state is not None else ConfigEntryState.LOADED
        self.disabled_by = disabled_by
        # Only health collection reads these two. Present so the coordinator
        # tests below stay on their real code path instead of quietly
        # degrading to "no health signals".
        self.entry_id = f"entry-{domain}"
        self.reason = None


class FakeIntegration:
    """Stand-in for a resolved manifest."""

    def __init__(self, name, version=None):
        self.name = name
        self.version = version


class FakeHacsRepositoryData:
    def __init__(self, full_name, name, installed_version):
        self.full_name = full_name
        self.name = name
        self.installed_version = installed_version


class FakeHacsRepository:
    def __init__(self, full_name, name, installed_version):
        self.data = FakeHacsRepositoryData(full_name, name, installed_version)


class FakeHacs:
    def __init__(self, repositories):
        self.repositories = types.SimpleNamespace(list_downloaded=repositories)


class FakeHass:
    def __init__(self, entries=(), hacs=None, loaded=("hassio",)):
        self.data = {}
        if hacs is not None:
            self.data["hacs"] = hacs
        self.config = types.SimpleNamespace(components=set(loaded))
        self._entries = list(entries)
        self.config_entries = types.SimpleNamespace(
            async_entries=lambda: list(self._entries)
        )
        self.states = types.SimpleNamespace(async_all=lambda: [])


class FakeApiClient:
    """Records every inventory posted, so a delta shows up as a short list."""

    def __init__(self):
        self.component_reports = []
        self.submitted = []

    async def report_status(self, **kwargs):
        return {"installation_status": "online", "config_version": 0}

    async def fetch_configuration(self, **kwargs):
        return None

    async def submit_metrics(self, **kwargs):
        self.submitted.append(kwargs)
        return {"status": "ok"}

    async def report_components(self, **kwargs):
        self.component_reports.append(list(kwargs["components"]))
        return {"status": "ok", "recorded": len(kwargs["components"])}


class SupervisedInstallation(unittest.TestCase):
    """Base class wiring a whole fake Home Assistant, Supervisor and HACS."""

    def setUp(self):
        self.addons = {
            # Deliberately first, and deliberately versionless: Supervisor does
            # not always know a local add-on's version. It has to end up in the
            # tail of the report, not near the front where it was collected.
            "local_lab": {"name": "Lab tools", "state": "started"},
            "core_mosquitto": {
                "name": "Mosquitto broker",
                "version": "6.5.1",
                "state": "started",
            },
            "a0d7b954_nodered": {
                "name": "Node-RED",
                "version": "18.1.0",
                "state": "error",
            },
        }
        self.manifests = {
            "zha": FakeIntegration("Zigbee Home Automation"),
            "hue": FakeIntegration("Philips Hue"),
            "frigate": FakeIntegration("Frigate", "5.3.0"),
            "ha_dispatch_client": FakeIntegration("HA Dispatch Client", "1.6.1"),
        }
        self.custom = {
            "frigate": self.manifests["frigate"],
            "ha_dispatch_client": self.manifests["ha_dispatch_client"],
        }
        self.repositories = [
            FakeHacsRepository(
                "blakeblackshear/frigate-hass-integration", "Frigate", "5.3.0"
            ),
            FakeHacsRepository(
                "straplocked/ha-dispatch-client", "HA Dispatch Client", "1.6.1"
            ),
        ]
        self.hass = FakeHass(
            entries=[
                FakeConfigEntry("zha", "Downstairs Zigbee"),
                FakeConfigEntry("hue", "Hue Bridge", ConfigEntryState.SETUP_RETRY),
                FakeConfigEntry("frigate", "Cameras"),
                FakeConfigEntry("ha_dispatch_client", "HA Dispatch"),
            ],
            hacs=FakeHacs(self.repositories),
        )

        self._saved = {
            name: getattr(hassio, name)
            for name in (
                "is_hassio",
                "get_os_info",
                "get_supervisor_info",
                "get_addons_info",
            )
        }
        self._saved_loader = {
            name: getattr(loader, name)
            for name in ("async_get_integrations", "async_get_custom_components")
        }

        hassio.is_hassio = lambda hass: True
        hassio.get_os_info = lambda hass: {"version": "14.2"}
        hassio.get_supervisor_info = lambda hass: {"version": "2026.08.1"}
        hassio.get_addons_info = lambda hass: dict(self.addons)

        async def _integrations(hass, domains):
            return {d: self.manifests[d] for d in domains if d in self.manifests}

        async def _custom(hass):
            return dict(self.custom)

        loader.async_get_integrations = _integrations
        loader.async_get_custom_components = _custom

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(hassio, name, value)
        for name, value in self._saved_loader.items():
            setattr(loader, name, value)

    def inventory(self):
        return asyncio.run(collect(self.hass))

    @staticmethod
    def identities(inventory):
        return {(entry["kind"], entry["slug"]) for entry in inventory}

    @staticmethod
    def find(inventory, kind, slug):
        for entry in inventory:
            if entry["kind"] == kind and entry["slug"] == slug:
                return entry
        raise AssertionError(f"no {kind}:{slug} in the inventory")


# --- guard 1: full snapshot, never a delta -----------------------------------


class FullSnapshotTest(SupervisedInstallation):
    def test_the_first_report_carries_everything_installed(self):
        inventory = self.inventory()

        self.assertEqual(EXPECTED_INVENTORY, self.identities(inventory))
        self.assertEqual(12, len(inventory))

    def test_a_second_report_with_nothing_changed_still_carries_everything(self):
        # The delta guard. An agent that dropped unchanged components would
        # leave the server retiring almost the entire inventory on this call,
        # because absence is how removal is expressed.
        first = self.inventory()
        second = self.inventory()

        self.assertEqual(EXPECTED_INVENTORY, self.identities(second))
        self.assertEqual(self.identities(first), self.identities(second))

    def test_removing_a_component_is_expressed_by_its_absence(self):
        before = self.inventory()
        self.assertIn(("addon", "core_mosquitto"), self.identities(before))

        del self.addons["core_mosquitto"]
        after = self.inventory()

        self.assertNotIn(("addon", "core_mosquitto"), self.identities(after))
        # Everything else survives -- an uninstall is one absence, not a reset.
        self.assertEqual(
            EXPECTED_INVENTORY - {("addon", "core_mosquitto")},
            self.identities(after),
        )

    def test_the_posted_payload_is_the_whole_inventory_every_time(self):
        # Same guard one layer up: the coordinator and api_client must not
        # thin the payload either.
        api = FakeApiClient()
        coordinator = make_coordinator(api, self.hass)

        self.assertTrue(asyncio.run(coordinator.async_report_components(force=True)))
        self.assertTrue(asyncio.run(coordinator.async_report_components(force=True)))

        self.assertEqual(2, len(api.component_reports))
        for payload in api.component_reports:
            self.assertEqual(EXPECTED_INVENTORY, self.identities(payload))


# --- guard 2: slugs do not move when people rename things --------------------


class StableSlugTest(SupervisedInstallation):
    def test_renaming_things_changes_names_and_leaves_slugs_alone(self):
        before = self.inventory()

        # Hand-written, not read back out of the fixtures: these are the names
        # a technician would see before anybody touched anything.
        self.assertEqual(
            "Zigbee Home Automation",
            self.find(before, "integration", "zha")["name"],
        )
        self.assertEqual(
            "Mosquitto broker", self.find(before, "addon", "core_mosquitto")["name"]
        )
        self.assertEqual(
            "Frigate",
            self.find(
                before, "hacs", "blakeblackshear/frigate-hass-integration"
            )["name"],
        )

        # Everything a user is allowed to rename, renamed.
        self.manifests["zha"] = FakeIntegration("Zigbee (renamed)")
        self.hass._entries[0].title = "Upstairs Zigbee"
        self.addons["core_mosquitto"]["name"] = "MQTT"
        self.repositories[0].data.name = "Frigate NVR"

        after = self.inventory()

        # The renames really took, so the test below is not passing by accident.
        self.assertEqual(
            "Zigbee (renamed)", self.find(after, "integration", "zha")["name"]
        )
        self.assertEqual("MQTT", self.find(after, "addon", "core_mosquitto")["name"])
        self.assertEqual(
            "Frigate NVR",
            self.find(after, "hacs", "blakeblackshear/frigate-hass-integration")["name"],
        )

        # The wrong answers first: these are the slugs you get from deriving
        # identity out of whatever the thing is currently called.
        identities = self.identities(after)
        self.assertNotIn(("integration", "Upstairs Zigbee"), identities)
        self.assertNotIn(("integration", "upstairs_zigbee"), identities)
        self.assertNotIn(("integration", "Zigbee (renamed)"), identities)
        self.assertNotIn(("addon", "MQTT"), identities)
        self.assertNotIn(("hacs", "Frigate NVR"), identities)

        # And the right ones.
        self.assertIn(("integration", "zha"), identities)
        self.assertIn(("addon", "core_mosquitto"), identities)
        self.assertIn(("hacs", "blakeblackshear/frigate-hass-integration"), identities)

        # Nothing moved at all, in fact -- four renames, same inventory.
        self.assertEqual(self.identities(before), identities)

    def test_a_second_config_entry_does_not_become_a_second_component(self):
        # Two Hue bridges are one integration. Slugging on the entry title
        # would have made them two, and retired one of them next poll.
        self.hass._entries.append(FakeConfigEntry("hue", "Hue Bridge (garage)"))

        inventory = self.inventory()

        hue = [e for e in inventory if e["slug"] == "hue"]
        self.assertEqual(1, len(hue))
        self.assertEqual(EXPECTED_INVENTORY, self.identities(inventory))


# --- what the payload says ---------------------------------------------------


class PayloadTest(SupervisedInstallation):
    def test_versions_come_from_the_right_places(self):
        inventory = self.inventory()

        # Hand-written from the fixtures above, not derived from them.
        self.assertEqual("2026.6.4", self.find(inventory, "core", "core")["version"])
        self.assertEqual("14.2", self.find(inventory, "os", "os")["version"])
        self.assertEqual(
            "2026.08.1", self.find(inventory, "supervisor", "supervisor")["version"]
        )
        self.assertEqual(
            "6.5.1", self.find(inventory, "addon", "core_mosquitto")["version"]
        )
        self.assertEqual(
            "5.3.0", self.find(inventory, "integration", "frigate")["version"]
        )
        self.assertEqual(
            "1.6.1",
            self.find(inventory, "hacs", "straplocked/ha-dispatch-client")["version"],
        )

    def test_a_built_in_integration_reports_no_version_rather_than_a_wrong_one(self):
        # zha ships no manifest version. Inventing one -- the core version, say
        # -- would put a fabricated transition in the fleet's evidence.
        zha = self.find(self.inventory(), "integration", "zha")

        self.assertNotIn("version", zha)
        self.assertEqual("Zigbee Home Automation", zha["name"])

    def test_failing_is_set_where_home_assistant_knows_and_nowhere_else(self):
        inventory = self.inventory()

        # A config entry stuck in setup_retry is a fact.
        self.assertTrue(self.find(inventory, "integration", "hue")["failing"])
        # An add-on Supervisor could not start is a fact.
        self.assertTrue(self.find(inventory, "addon", "a0d7b954_nodered")["failing"])
        # A healthy entry is not.
        self.assertFalse(self.find(inventory, "integration", "zha")["failing"])
        self.assertFalse(self.find(inventory, "addon", "core_mosquitto")["failing"])

    def test_a_stopped_addon_is_not_a_failing_addon(self):
        # People stop add-ons on purpose. Calling that a fault would blame the
        # last upgrade for a deliberate act, fleet-wide.
        self.addons["core_mosquitto"]["state"] = "stopped"

        self.assertFalse(
            self.find(self.inventory(), "addon", "core_mosquitto")["failing"]
        )

    def test_a_disabled_integration_is_not_reported(self):
        self.hass._entries[0].disabled_by = "user"

        self.assertNotIn(("integration", "zha"), self.identities(self.inventory()))

    def test_every_entry_matches_what_the_server_validates(self):
        for entry in self.inventory():
            self.assertIn(entry["kind"], SERVER_KINDS)
            self.assertIsInstance(entry["slug"], str)
            self.assertTrue(0 < len(entry["slug"]) <= 191)
            self.assertIsInstance(entry["failing"], bool)
            self.assertLessEqual(len(entry.get("name") or ""), 255)
            self.assertLessEqual(len(entry.get("version") or ""), 64)
            self.assertEqual(set(), set(entry) - {
                "kind", "slug", "name", "version", "failing",
            })

    def test_over_long_fields_are_trimmed_rather_than_costing_the_report(self):
        self.addons["core_mosquitto"]["name"] = "M" * 400
        self.addons["core_mosquitto"]["version"] = "9" * 100

        addon = self.find(self.inventory(), "addon", "core_mosquitto")

        self.assertEqual(255, len(addon["name"]))
        self.assertEqual(64, len(addon["version"]))


class TruncationOrderTest(SupervisedInstallation):
    def test_the_platform_leads_and_versionless_rows_go_last(self):
        order = [(e["kind"], e["slug"]) for e in self.inventory()]

        # The wrong answer first: this is what comes out if the ordering rule
        # is dropped, and it differs in two ways that matter -- the add-ons
        # come out unsorted, and the versionless one sits near the front.
        self.assertNotEqual(COLLECTION_ORDER, order)
        self.assertEqual(EXPECTED_ORDER, order)

        # Spelled out, since these are the two rules being enforced.
        self.assertEqual(
            [("core", "core"), ("os", "os"), ("supervisor", "supervisor")], order[:3]
        )
        inventory = self.inventory()
        versioned = [i for i, e in enumerate(inventory) if "version" in e]
        versionless = [i for i, e in enumerate(inventory) if "version" not in e]
        self.assertLess(max(versioned), min(versionless))

    def test_a_huge_installation_is_truncated_here_not_at_the_server(self):
        self.addons.update(
            {
                f"local_addon_{i:04d}": {
                    "name": f"Add-on {i}",
                    "version": "1.0.0",
                    "state": "started",
                }
                for i in range(900)
            }
        )

        inventory = self.inventory()

        # 750, hand-written: under the server's 800 retained and far under its
        # 2000 hard limit, so nothing is ever cut at the far end.
        self.assertEqual(750, len(inventory))
        # The platform still leads, which is the whole point of the ordering.
        self.assertEqual(("core", "core"), (inventory[0]["kind"], inventory[0]["slug"]))
        self.assertIn(("os", "os"), self.identities(inventory))
        self.assertIn(("supervisor", "supervisor"), self.identities(inventory))


# --- a Core-only installation ------------------------------------------------


class CoreOnlyTest(unittest.TestCase):
    """Most installations have no Supervisor, no add-ons and no HACS."""

    def test_missing_kinds_are_normal_and_silent(self):
        hass = FakeHass(
            entries=[FakeConfigEntry("zha", "Zigbee")], hacs=None, loaded=()
        )

        inventory = asyncio.run(collect(hass))

        self.assertEqual(
            {("core", "core"), ("integration", "zha")},
            {(e["kind"], e["slug"]) for e in inventory},
        )

    def test_an_installation_always_has_at_least_itself_to_report(self):
        # An empty list is a 422 -- `components` is required -- so the core row
        # is what keeps a bare installation reportable at all.
        inventory = asyncio.run(collect(FakeHass(loaded=())))

        self.assertEqual(1, len(inventory))
        self.assertEqual("core", inventory[0]["kind"])


# --- guard 3: cadence --------------------------------------------------------


def make_coordinator(api, hass):
    coordinator = coordinator_mod.HADispatchCoordinator(
        hass=hass, api_client=api, installation_id=7, entry=None
    )
    return coordinator


class CadenceTest(SupervisedInstallation):
    def setUp(self):
        super().setUp()
        self.clock = [datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)]
        self._saved_utcnow = dt_module.utcnow
        dt_module.utcnow = lambda: self.clock[0]

    def tearDown(self):
        dt_module.utcnow = self._saved_utcnow
        super().tearDown()

    def tick(self, coordinator, seconds=0):
        self.clock[0] += timedelta(seconds=seconds)
        return asyncio.run(coordinator._async_update_data())

    def test_inventory_does_not_ride_the_sixty_second_metrics_poll(self):
        api = FakeApiClient()
        coordinator = make_coordinator(api, self.hass)

        self.tick(coordinator)
        # The first tick after a restart reports: nothing is known yet, and the
        # server treats a first inventory as enrolment rather than 11 installs.
        self.assertEqual(1, len(api.component_reports))

        self.tick(coordinator, seconds=60)
        # The wrong answer first -- this is what reporting on every poll does.
        self.assertNotEqual(2, len(api.component_reports))
        self.assertEqual(1, len(api.component_reports))

        self.tick(coordinator, seconds=60)
        self.assertEqual(1, len(api.component_reports))

        # Now past the interval.
        self.tick(coordinator, seconds=3600)
        self.assertEqual(2, len(api.component_reports))

        # Every one of those ticks really ran: metrics went out on all four.
        self.assertEqual(4, len(api.submitted))

    def test_the_cadence_is_the_fifteen_to_sixty_minutes_the_contract_asks_for(self):
        self.assertGreaterEqual(const.COMPONENT_REPORT_INTERVAL, 900)
        self.assertLessEqual(const.COMPONENT_REPORT_INTERVAL, 3600)
        self.assertNotEqual(
            const.DEFAULT_SCAN_INTERVAL, const.COMPONENT_REPORT_INTERVAL
        )

    def test_forcing_a_report_ignores_the_cadence(self):
        api = FakeApiClient()
        coordinator = make_coordinator(api, self.hass)

        self.tick(coordinator)
        self.assertEqual(1, len(api.component_reports))

        self.assertTrue(asyncio.run(coordinator.async_report_components(force=True)))
        self.assertEqual(2, len(api.component_reports))

        # And the forced report resets the clock, so the next tick stays quiet.
        self.tick(coordinator, seconds=60)
        self.assertEqual(2, len(api.component_reports))

    def test_a_failed_report_is_retried_on_the_next_tick(self):
        class Failing(FakeApiClient):
            def __init__(self):
                super().__init__()
                self.attempts = 0

            async def report_components(self, **kwargs):
                self.attempts += 1
                if self.attempts == 1:
                    raise TimeoutError("server took too long")
                return await FakeApiClient.report_components(self, **kwargs)

        api = Failing()
        coordinator = make_coordinator(api, self.hass)

        self.tick(coordinator)
        self.assertEqual(0, len(api.component_reports))

        # Not half an hour later: a transport blip must not cost a whole cycle.
        self.tick(coordinator, seconds=60)
        self.assertEqual(1, len(api.component_reports))

    def test_a_broken_inventory_never_takes_metrics_down_with_it(self):
        api = FakeApiClient()
        coordinator = make_coordinator(api, self.hass)

        def _explode(hass):
            raise KeyError("hassio moved house again")

        hassio.get_addons_info = _explode

        result = self.tick(coordinator)

        self.assertEqual("online", result["status"])
        self.assertEqual(1, len(api.submitted))


# --- reporting promptly after an update --------------------------------------


class PostUpdateReportTest(unittest.TestCase):
    """An update is the one moment a late inventory actively misleads.

    The transition is what opens the fleet's two-hour observation window, so a
    version discovered half an hour after the fact gets credited with half an
    hour of unrelated faults.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.forced = []

        test = self

        class RecordingCoordinator:
            api_client = None
            installation_id = 7
            restart_after_update = False

            async def async_report_components(self, *, force=False):
                test.forced.append(force)
                return True

        self.coordinator = RecordingCoordinator()
        self.hass = types.SimpleNamespace(
            config=types.SimpleNamespace(path=lambda *parts: os.path.join(
                self.tmp, *parts
            )),
            async_add_executor_job=self._executor,
        )
        self.updater = updater_mod.ClientUpdater(self.hass, self.coordinator)

    async def _executor(self, func, *args):
        return func(*args)

    def test_a_confirmed_update_reports_the_new_inventory_immediately(self):
        self.updater._store.data = {"from_version": "1.6.0", "to_version": "1.6.1"}

        async def _installed():
            return "1.6.1"

        self.updater.async_installed_version = _installed

        asyncio.run(self.updater.async_confirm_pending())

        self.assertEqual([True], self.forced)

    def test_an_update_that_did_not_take_reports_nothing(self):
        # Still running the old code, so the inventory has not changed and
        # claiming it had would put a phantom transition in the ledger.
        self.updater._store.data = {"from_version": "1.6.0", "to_version": "1.6.1"}

        async def _installed():
            return "1.6.0"

        self.updater.async_installed_version = _installed

        asyncio.run(self.updater.async_confirm_pending())

        self.assertEqual([], self.forced)

    def test_an_update_without_a_restart_still_reports(self):
        # Nothing is going to restart, so nothing else notices the new version
        # until the next cadence tick half an hour later.
        asyncio.run(
            self.updater._async_finish({"version": "1.6.1"}, "1.6.0", "1.6.1")
        )

        self.assertEqual([True], self.forced)


if __name__ == "__main__":
    unittest.main()
