"""Tests for the offer's `install` flag (client 1.11.0).

The bug these cover: a technician could push a client release to a house
from Dispatch, and nothing happened unless that house also had `auto_update`
switched on. The offer now says so itself -- `client_release.install: true`
-- and the client installs it exactly as auto_update would, every safeguard
included. An older server sends no key at all, and that must mean "no".

Home Assistant is not installed here; tests/conftest.py stubs the symbols the
coordinator imports.
"""
import importlib
import os
import sys
import types
import unittest


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


coordinator_mod = _load("coordinator")


class FakeHass:
    def __init__(self):
        self.tasks = []
        self.config = types.SimpleNamespace(location_name="Test Home")

    def async_create_task(self, coro):
        # Closed rather than run: the install itself is the updater's business
        # and is covered in test_updater. What matters here is whether one was
        # started at all.
        self.tasks.append(coro)
        coro.close()


class FakeUpdater:
    in_progress = False

    async def async_install(self, release):  # pragma: no cover - never awaited
        raise AssertionError("the install must not run inside the refresh")


def make_coordinator(*, auto_update=False):
    hass = FakeHass()
    coordinator = coordinator_mod.HADispatchCoordinator(
        hass=hass, api_client=None, installation_id=7, entry=None
    )
    coordinator.updater = FakeUpdater()
    coordinator.client_version = "1.10.0"
    coordinator.auto_update = auto_update
    return coordinator, hass


# Hand-written offers, in the shape ClientUpdateService::offerFor sends.
PUSHED = {"version": "1.11.0", "url": "https://example.invalid/r.zip", "install": True}
OFFERED = {"version": "1.11.0", "url": "https://example.invalid/r.zip"}


class InstallFlagTest(unittest.TestCase):
    def test_an_offer_marked_install_is_installed_with_auto_update_off(self):
        coordinator, hass = make_coordinator(auto_update=False)
        coordinator._schedule_auto_update(dict(PUSHED))
        self.assertEqual(len(hass.tasks), 1)

    def test_the_same_offer_without_the_key_is_not(self):
        # An older server: no `install` key. Missing means false.
        coordinator, hass = make_coordinator(auto_update=False)
        coordinator._schedule_auto_update(dict(OFFERED))
        self.assertEqual(hass.tasks, [])

    def test_install_false_is_not_install(self):
        coordinator, hass = make_coordinator(auto_update=False)
        coordinator._schedule_auto_update(dict(OFFERED, install=False))
        self.assertEqual(hass.tasks, [])

    def test_only_a_literal_true_counts(self):
        # A truthy string is a malformed offer, not a push.
        coordinator, hass = make_coordinator(auto_update=False)
        coordinator._schedule_auto_update(dict(OFFERED, install="false"))
        self.assertEqual(hass.tasks, [])

    def test_auto_update_alone_still_installs(self):
        coordinator, hass = make_coordinator(auto_update=True)
        coordinator._schedule_auto_update(dict(OFFERED))
        self.assertEqual(len(hass.tasks), 1)


class InstallFlagKeepsEverySafeguardTest(unittest.TestCase):
    """The flag decides whether to install, never what or when."""

    def test_the_same_version_is_not_reinstalled(self):
        coordinator, hass = make_coordinator()
        coordinator.client_version = "1.11.0"
        coordinator._schedule_auto_update(dict(PUSHED))
        self.assertEqual(hass.tasks, [])

    def test_an_older_version_is_not_installed(self):
        coordinator, hass = make_coordinator()
        coordinator.client_version = "1.12.0"
        coordinator._schedule_auto_update(dict(PUSHED))
        self.assertEqual(hass.tasks, [])

    def test_a_pin_to_another_version_still_holds(self):
        coordinator, hass = make_coordinator()
        coordinator.target_version = "1.10.5"
        coordinator._schedule_auto_update(dict(PUSHED))
        self.assertEqual(hass.tasks, [])

    def test_the_update_window_still_holds(self):
        coordinator, hass = make_coordinator()
        saved = coordinator_mod.in_update_window
        coordinator_mod.in_update_window = lambda now, window: False
        coordinator.update_window = "02:00-04:00"
        try:
            coordinator._schedule_auto_update(dict(PUSHED))
        finally:
            coordinator_mod.in_update_window = saved
        self.assertEqual(hass.tasks, [])

    def test_an_install_already_running_is_not_started_twice(self):
        coordinator, hass = make_coordinator()
        coordinator.updater.in_progress = True
        coordinator._schedule_auto_update(dict(PUSHED))
        self.assertEqual(hass.tasks, [])

    def test_no_updater_means_no_install(self):
        coordinator, hass = make_coordinator()
        coordinator.updater = None
        coordinator._schedule_auto_update(dict(PUSHED))
        self.assertEqual(hass.tasks, [])


if __name__ == "__main__":
    unittest.main()
