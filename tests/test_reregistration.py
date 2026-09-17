"""Tests for unattended re-enrolment when the server rejects our token.

The bug these cover: a rejected bearer token was caught as a generic
aiohttp.ClientError and turned into UpdateFailed, which the coordinator retries
forever. Since the client had no way to obtain a new token, the only fix was
deleting the integration in Home Assistant and adding it again by hand -- needed
every time the server's database was rebuilt.

Home Assistant is not installed here, so the handful of symbols the coordinator
imports are stubbed. That keeps these tests fast and dependency-free.
"""
import asyncio
import importlib
import os
import sys
import types
import unittest


def _module(name: str):
    """Fetch or create a stub module, registering it in sys.modules."""
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)
    return sys.modules[name]


def _install_stubs() -> None:
    """Add any stub symbols that are missing.

    Deliberately additive rather than all-or-nothing: test_health installs its
    own narrower set of stubs, and whichever module the test runner imports
    first would otherwise leave the other's symbols absent.
    """
    _module("homeassistant")

    config_entries = _module("homeassistant.config_entries")

    class ConfigEntry:
        def __init__(self, data):
            self.data = dict(data)

    class ConfigEntryState:
        LOADED = "loaded"
        SETUP_ERROR = "setup_error"
        SETUP_RETRY = "setup_retry"
        MIGRATION_ERROR = "migration_error"
        FAILED_UNLOAD = "failed_unload"
        NOT_LOADED = "not_loaded"

    if not hasattr(config_entries, "ConfigEntry"):
        config_entries.ConfigEntry = ConfigEntry
    if not hasattr(config_entries, "ConfigEntryState"):
        config_entries.ConfigEntryState = ConfigEntryState

    core = _module("homeassistant.core")

    class HomeAssistant:
        pass

    class State:
        pass

    if not hasattr(core, "HomeAssistant"):
        core.HomeAssistant = HomeAssistant
    if not hasattr(core, "State"):
        core.State = State

    const = _module("homeassistant.const")
    for attr, value in [("__version__", "2026.6.4"), ("STATE_OFF", "off"),
                        ("STATE_UNAVAILABLE", "unavailable"), ("STATE_UNKNOWN", "unknown")]:
        if not hasattr(const, attr):
            setattr(const, attr, value)

    helpers = _module("homeassistant.helpers")
    uc = _module("homeassistant.helpers.update_coordinator")

    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator:
        def __init__(self, hass, logger, name=None, update_interval=None):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval

    if not hasattr(uc, "UpdateFailed"):
        uc.UpdateFailed = UpdateFailed
    if not hasattr(uc, "DataUpdateCoordinator"):
        uc.DataUpdateCoordinator = DataUpdateCoordinator
    helpers.update_coordinator = uc

    util = _module("homeassistant.util")
    dt_mod = _module("homeassistant.util.dt")
    from datetime import datetime, timezone

    if not hasattr(dt_mod, "utcnow"):
        dt_mod.utcnow = lambda: datetime.now(timezone.utc)
    util.dt = dt_mod


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


_install_stubs()
api_client = _load("api_client")
coordinator_mod = _load("coordinator")

InstallationAuthError = api_client.InstallationAuthError
InstallationGoneError = api_client.InstallationGoneError
ClientIdTakenError = api_client.ClientIdTakenError
RegistrationSecretError = api_client.RegistrationSecretError
UpdateFailed = sys.modules["homeassistant.helpers.update_coordinator"].UpdateFailed
ConfigEntry = sys.modules["homeassistant.config_entries"].ConfigEntry


class FakeConfigEntries:
    """Captures async_update_entry so tests can assert what was persisted."""

    def __init__(self):
        self.updates = []

    def async_update_entry(self, entry, data):
        entry.data = dict(data)
        self.updates.append(dict(data))

    def async_entries(self):
        # collect_health() enumerates config entries; returning an empty list
        # keeps health collection on its real code path instead of degrading.
        return []


class FakeHass:
    def __init__(self):
        self.config = types.SimpleNamespace(location_name="Test Home")
        self.config_entries = FakeConfigEntries()
        self.states = types.SimpleNamespace(async_all=lambda: [])


class FakeApiClient:
    """Stands in for the HTTP client; records calls and replays scripted outcomes."""

    def __init__(self, *, status_effects=None, register_effects=None):
        self.token = "stale-token"
        self.status_effects = list(status_effects or [])
        self.register_effects = list(register_effects or [])
        self.register_calls = []
        self.submitted = []

    async def report_status(self, **kwargs):
        if self.status_effects:
            effect = self.status_effects.pop(0)
            if isinstance(effect, Exception):
                raise effect
            return effect
        return {"installation_status": "online", "config_version": 0}

    async def register_installation(self, **kwargs):
        self.register_calls.append(kwargs)
        if self.register_effects:
            effect = self.register_effects.pop(0)
            if isinstance(effect, Exception):
                raise effect
            return effect
        return {"installation_id": 99, "access_token": "fresh-token"}

    async def fetch_configuration(self, **kwargs):
        return None

    async def submit_metrics(self, **kwargs):
        self.submitted.append(kwargs)
        return {"status": "ok"}


def make_coordinator(api, entry=None):
    hass = FakeHass()
    if entry is None:
        entry = ConfigEntry({
            "server_url": "https://dispatch.example.com",
            "client_id": "11111111-1111-4111-8111-111111111111",
            "installation_id": 7,
            "access_token": "stale-token",
            "registration_secret": "s3cret",
        })
    coord = coordinator_mod.HADispatchCoordinator(
        hass=hass, api_client=api, installation_id=entry.data["installation_id"], entry=entry
    )
    return coord, hass, entry


class ReregistrationTest(unittest.TestCase):
    def test_a_rejected_token_triggers_reenrolment_and_is_persisted(self):
        api = FakeApiClient(status_effects=[InstallationAuthError("401")])
        coord, hass, entry = make_coordinator(api)

        # The refresh still fails this cycle -- deliberately, so a persistent
        # failure cannot recurse -- but the token is replaced.
        with self.assertRaises(UpdateFailed):
            asyncio.run(coord._async_update_data())

        self.assertEqual(1, len(api.register_calls))
        self.assertEqual("fresh-token", api.token)
        self.assertEqual(99, coord.installation_id)

        self.assertEqual(1, len(hass.config_entries.updates))
        saved = hass.config_entries.updates[0]
        self.assertEqual("fresh-token", saved["access_token"])
        self.assertEqual(99, saved["installation_id"])

    def test_a_vanished_installation_record_also_reenrols(self):
        # The common case in practice: the record was removed or the database was
        # rebuilt, so Laravel's route binding answers 404 before auth runs. This
        # path is what the original fix missed.
        api = FakeApiClient(status_effects=[InstallationGoneError("404")])
        coord, hass, _ = make_coordinator(api)

        with self.assertRaises(UpdateFailed):
            asyncio.run(coord._async_update_data())

        self.assertEqual(1, len(api.register_calls))
        self.assertEqual("fresh-token", api.token)
        self.assertEqual(1, len(hass.config_entries.updates))

    def test_gone_is_treated_as_an_auth_failure(self):
        # Guards the subclass relationship the coordinator's handler depends on.
        self.assertTrue(issubclass(InstallationGoneError, InstallationAuthError))

    def test_it_reuses_the_stored_client_id(self):
        api = FakeApiClient(status_effects=[InstallationAuthError("401")])
        coord, _, entry = make_coordinator(api)

        with self.assertRaises(UpdateFailed):
            asyncio.run(coord._async_update_data())

        self.assertEqual(
            "11111111-1111-4111-8111-111111111111",
            api.register_calls[0]["client_id"],
        )

    def test_it_sends_the_stored_secret_when_reenrolling(self):
        api = FakeApiClient(status_effects=[InstallationAuthError("401")])
        coord, _, _ = make_coordinator(api)

        with self.assertRaises(UpdateFailed):
            asyncio.run(coord._async_update_data())

        self.assertEqual("s3cret", api.register_calls[0]["registration_secret"])

    def test_a_taken_client_id_falls_back_to_a_new_one(self):
        # The server still holds our client_id but our token is stale, so
        # registration is refused. A fresh id is the only way through.
        api = FakeApiClient(
            status_effects=[InstallationAuthError("401")],
            register_effects=[
                ClientIdTakenError("client_id has already been taken"),
                {"installation_id": 123, "access_token": "second-token"},
            ],
        )
        coord, hass, _ = make_coordinator(api)

        with self.assertRaises(UpdateFailed):
            asyncio.run(coord._async_update_data())

        self.assertEqual(2, len(api.register_calls))
        self.assertNotEqual(
            api.register_calls[0]["client_id"], api.register_calls[1]["client_id"]
        )
        self.assertEqual("second-token", api.token)
        self.assertEqual(123, hass.config_entries.updates[0]["installation_id"])

    def test_a_wrong_secret_does_not_persist_anything(self):
        api = FakeApiClient(
            status_effects=[InstallationAuthError("401")],
            register_effects=[RegistrationSecretError("nope")],
        )
        coord, hass, _ = make_coordinator(api)

        with self.assertRaises(UpdateFailed):
            asyncio.run(coord._async_update_data())

        self.assertEqual([], hass.config_entries.updates)
        self.assertEqual("stale-token", api.token)

    def test_normal_operation_never_reenrols(self):
        api = FakeApiClient()
        coord, hass, _ = make_coordinator(api)

        result = asyncio.run(coord._async_update_data())

        self.assertEqual("online", result["status"])
        self.assertEqual([], api.register_calls)
        self.assertEqual([], hass.config_entries.updates)

    def test_without_a_config_entry_it_fails_loudly_rather_than_silently(self):
        api = FakeApiClient(status_effects=[InstallationAuthError("401")])
        hass = FakeHass()
        coord = coordinator_mod.HADispatchCoordinator(
            hass=hass, api_client=api, installation_id=7, entry=None
        )

        with self.assertRaises(UpdateFailed):
            asyncio.run(coord._async_update_data())

        self.assertEqual([], api.register_calls)


if __name__ == "__main__":
    unittest.main()
