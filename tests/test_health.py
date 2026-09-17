"""Tests for Home Assistant health signal collection.

Home Assistant itself is not installed in this environment, and pulling it in
just to exercise pure classification logic would be disproportionate. The few
symbols health.py touches are stubbed below, which keeps these tests fast and
dependency-free while still covering the part that actually makes decisions.
"""
import importlib
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone


def _install_homeassistant_stubs() -> None:
    """Register the minimal homeassistant modules health.py imports."""
    if "homeassistant" in sys.modules:
        return

    ha = types.ModuleType("homeassistant")

    config_entries = types.ModuleType("homeassistant.config_entries")

    class ConfigEntryState:
        LOADED = "loaded"
        SETUP_ERROR = "setup_error"
        SETUP_RETRY = "setup_retry"
        MIGRATION_ERROR = "migration_error"
        FAILED_UNLOAD = "failed_unload"
        NOT_LOADED = "not_loaded"

    config_entries.ConfigEntryState = ConfigEntryState

    const = types.ModuleType("homeassistant.const")
    const.STATE_OFF = "off"
    const.STATE_UNAVAILABLE = "unavailable"
    const.STATE_UNKNOWN = "unknown"

    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:
        pass

    class State:
        pass

    core.HomeAssistant = HomeAssistant
    core.State = State

    util = types.ModuleType("homeassistant.util")
    dt_module = types.ModuleType("homeassistant.util.dt")
    dt_module.utcnow = lambda: datetime.now(timezone.utc)
    util.dt = dt_module

    sys.modules.update(
        {
            "homeassistant": ha,
            "homeassistant.config_entries": config_entries,
            "homeassistant.const": const,
            "homeassistant.core": core,
            "homeassistant.util": util,
            "homeassistant.util.dt": dt_module,
        }
    )


def _load_health_module():
    """Import health.py without executing the package __init__.

    __init__.py wires up the whole integration and imports far more of Home
    Assistant than health.py needs. Registering a bare package module first lets
    the relative `from .const import ...` resolve while skipping that entirely.
    """
    component_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "custom_components",
        "ha_dispatch_client",
    )

    package = types.ModuleType("ha_dispatch_client")
    package.__path__ = [component_dir]
    sys.modules["ha_dispatch_client"] = package

    return importlib.import_module("ha_dispatch_client.health")


_install_homeassistant_stubs()
collect_health = _load_health_module().collect_health


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FakeState:
    """Stand-in for homeassistant.core.State."""

    def __init__(
        self,
        entity_id: str,
        state: str,
        attributes: dict | None = None,
        last_reported: datetime | None = None,
        name: str | None = None,
    ):
        self.entity_id = entity_id
        self.state = state
        self.attributes = attributes or {}
        self.last_reported = last_reported or _now()
        self.last_updated = self.last_reported
        self.name = name or entity_id.split(".", 1)[1]

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]


class FakeEntry:
    def __init__(self, entry_id, domain, state, title=None, disabled_by=None, reason=None):
        self.entry_id = entry_id
        self.domain = domain
        self.state = state
        self.title = title or domain
        self.disabled_by = disabled_by
        self.reason = reason


class FakeHass:
    def __init__(self, states, entries=()):
        self.states = types.SimpleNamespace(async_all=lambda: list(states))
        self.config_entries = types.SimpleNamespace(async_entries=lambda: list(entries))


class HealthCollectionTest(unittest.TestCase):
    def test_it_counts_unavailable_entities(self):
        hass = FakeHass([
            FakeState("sensor.ok", "21.5"),
            FakeState("binary_sensor.front_door", "unavailable"),
            FakeState("light.hall", "unavailable"),
        ])

        health = collect_health(hass)

        self.assertEqual(3, health["entities"]["total"])
        self.assertEqual(2, health["entities"]["unavailable"])

        keys = {i["key"] for i in health["items"] if i["type"] == "unavailable_entity"}
        self.assertEqual({"binary_sensor.front_door", "light.hall"}, keys)

    def test_it_flags_entities_that_have_gone_quiet(self):
        hass = FakeHass([
            FakeState("sensor.fresh", "21.5", last_reported=_now() - timedelta(hours=1)),
            FakeState("sensor.quiet", "18.0", last_reported=_now() - timedelta(hours=30)),
        ])

        health = collect_health(hass, stale_hours=24)

        self.assertEqual(1, health["entities"]["stale"])
        stale = [i for i in health["items"] if i["type"] == "stale_entity"]
        self.assertEqual("sensor.quiet", stale[0]["key"])

    def test_staleness_only_applies_to_reporting_domains(self):
        # A scene or input_boolean legitimately never changes on its own.
        hass = FakeHass([
            FakeState("scene.movie_night", "2020-01-01", last_reported=_now() - timedelta(days=400)),
            FakeState("input_boolean.guest", "off", last_reported=_now() - timedelta(days=400)),
        ])

        health = collect_health(hass, stale_hours=24)

        self.assertEqual(0, health["entities"]["stale"])

    def test_it_classifies_battery_levels(self):
        hass = FakeHass([
            FakeState("sensor.a_battery", "80", {"device_class": "battery"}),
            FakeState("sensor.b_battery", "15", {"device_class": "battery"}),
            FakeState("sensor.c_battery", "3", {"device_class": "battery"}),
        ])

        health = collect_health(hass, battery_low=20, battery_critical=5)

        self.assertEqual(1, health["batteries"]["low"])
        self.assertEqual(1, health["batteries"]["critical"])

        critical = [
            i for i in health["items"]
            if i["type"] == "low_battery" and i["severity"] == "critical"
        ]
        self.assertEqual("sensor.c_battery", critical[0]["key"])

    def test_it_reads_battery_level_attribute(self):
        hass = FakeHass([
            FakeState("lock.front", "locked", {"battery_level": 4}),
        ])

        health = collect_health(hass, battery_low=20, battery_critical=5)

        self.assertEqual(1, health["batteries"]["critical"])

    def test_an_unavailable_entity_is_not_also_counted_as_stale_or_flat(self):
        # A dead device would otherwise generate three separate items for one fault.
        hass = FakeHass([
            FakeState(
                "sensor.dead_battery",
                "unavailable",
                {"device_class": "battery"},
                last_reported=_now() - timedelta(days=5),
            ),
        ])

        health = collect_health(hass)

        self.assertEqual(1, health["entities"]["unavailable"])
        self.assertEqual(0, health["entities"]["stale"])
        self.assertEqual(0, health["batteries"]["low"])
        self.assertEqual(0, health["batteries"]["critical"])
        self.assertEqual(1, len(health["items"]))

    def test_it_reports_automation_problems(self):
        hass = FakeHass([
            FakeState("automation.working", "on"),
            FakeState("automation.turned_off", "off"),
            FakeState("automation.broken", "unavailable"),
        ])

        health = collect_health(hass)

        self.assertEqual(3, health["automations"]["total"])
        self.assertEqual(2, health["automations"]["problem"])

        types_ = {i["type"] for i in health["items"]}
        self.assertIn("disabled_automation", types_)
        self.assertIn("unavailable_automation", types_)

        # Automations must not inflate the entity count.
        self.assertEqual(0, health["entities"]["total"])

    def test_it_reports_failing_integrations(self):
        entries = [
            FakeEntry("e1", "hue", "loaded"),
            FakeEntry("e2", "zwave_js", "setup_retry", reason="Connection refused"),
            FakeEntry("e3", "old_thing", "loaded", disabled_by="user"),
        ]
        hass = FakeHass([], entries)

        health = collect_health(hass)

        # The disabled entry counts toward neither total nor failures.
        self.assertEqual(2, health["integrations"]["total"])
        self.assertEqual(1, health["integrations"]["failed"])

        failed = [i for i in health["items"] if i["type"] == "failed_integration"]
        self.assertEqual("e2", failed[0]["key"])
        self.assertEqual("Connection refused", failed[0]["detail"]["reason"])

    def test_a_disabled_integration_is_not_a_failure(self):
        # NOT_LOADED is the resting state of a deliberately disabled entry.
        hass = FakeHass([], [FakeEntry("e1", "spotify", "not_loaded")])

        health = collect_health(hass)

        self.assertEqual(0, health["integrations"]["failed"])

    def test_item_detail_is_capped_but_counts_are_not(self):
        states = [
            FakeState(f"sensor.entity_{i}", "unavailable") for i in range(700)
        ]
        hass = FakeHass(states)

        health = collect_health(hass)

        self.assertEqual(700, health["entities"]["unavailable"])
        self.assertEqual(500, len(health["items"]))

    def test_most_actionable_items_survive_truncation(self):
        states = [FakeState(f"sensor.e{i}", "unavailable") for i in range(600)]
        states.append(FakeState("automation.broken", "unavailable"))
        entries = [FakeEntry("e1", "zwave_js", "setup_error")]
        hass = FakeHass(states, entries)

        health = collect_health(hass)

        kept = {i["type"] for i in health["items"]}
        self.assertIn("failed_integration", kept)
        self.assertIn("unavailable_automation", kept)


if __name__ == "__main__":
    unittest.main()
