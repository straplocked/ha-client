"""Tests for consent-gated remote updates.

The feature these cover: the server tells this agent which Home Assistant update
the homeowner consented to, and the agent takes a backup, applies it, and reports
each phase -- reporting *before* it touches anything, so an update that bricks the
box still leaves a trail, and confirming on the next boot when the update was one
that restarted Home Assistant out from under the run.

Home Assistant is not installed here; tests/conftest.py stubs the symbols the
integration imports, including an in-memory Store and a Core-only Supervisor.
The Supervisor calls themselves are behind SupervisorUpdater, replaced by a fake
that records what it was asked to do.
"""
import asyncio
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


api_client = _load("api_client")
const = _load("const")
updates_mod = _load("updates")

HADispatchUpdates = updates_mod.HADispatchUpdates
UpdateExecutionError = updates_mod.UpdateExecutionError
versions_equal = updates_mod.versions_equal
RemoteUpdatesUnavailable = api_client.RemoteUpdatesUnavailable

# The stubbed Home Assistant core version -- see conftest. A run targeting this
# confirms as installed; any other version confirms as "did not land".
INSTALLED_CORE = "2026.6.4"


# --- fakes -------------------------------------------------------------------


class FakeApi:
    """Records every report, and hands back whatever pending list a test sets."""

    def __init__(self, events, pending=None, pending_error=None):
        self.events = events
        self._pending = pending or []
        self._pending_error = pending_error
        self.reports = []

    async def fetch_pending_updates(self, installation_id):
        if self._pending_error is not None:
            raise self._pending_error
        return list(self._pending)

    async def report_update(
        self, installation_id, run_id, status, phase=None,
        backup_reference=None, error=None, detail=None,
    ):
        self.reports.append(
            {
                "run_id": run_id,
                "status": status,
                "phase": phase,
                "backup_reference": backup_reference,
                "error": error,
            }
        )
        self.events.append(("report", status, phase))
        return {"status": "ok"}


class FakeExecutor:
    """Stands in for SupervisorUpdater, recording calls in execution order."""

    def __init__(self, events, backup_ref="pre-update-backup", apply_error=None):
        self.events = events
        self.backup_ref = backup_ref
        self.apply_error = apply_error
        self.backups = []
        self.applies = []

    async def async_backup(self, kind, slug, name):
        self.events.append(("backup", kind, slug))
        self.backups.append((kind, slug, name))
        return self.backup_ref

    async def async_apply(self, kind, slug, target):
        self.events.append(("apply", kind, slug))
        self.applies.append((kind, slug, target))
        if self.apply_error is not None:
            raise self.apply_error


class FakeCoordinator:
    def __init__(self, api):
        self.api_client = api
        self.installation_id = "inst-1"


class FakeHass:
    """Records scheduled tasks without running them, for the poll-loop tests."""

    def __init__(self):
        self.data = {}
        self.scheduled = []

    def async_create_task(self, coro, name=None):
        # Closing the coroutine keeps the scheduling decision testable without
        # actually running the run.
        coro.close()
        self.scheduled.append(name)
        return object()


def _manager(events, api, executor=None, hass=None):
    hass = hass or FakeHass()
    coordinator = FakeCoordinator(api)
    manager = HADispatchUpdates(hass, coordinator, executor=executor)
    return manager


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _core_run(target=INSTALLED_CORE, backup=True, run_id=7):
    return {
        "run_id": run_id,
        "kind": "core",
        "slug": "core",
        "target_version": target,
        "backup": backup,
    }


# --- version helper ----------------------------------------------------------


class VersionEqualityTest(unittest.TestCase):
    def test_a_leading_v_and_whitespace_do_not_matter(self):
        self.assertTrue(versions_equal("v2026.9.1", " 2026.9.1 "))

    def test_different_versions_are_not_equal(self):
        self.assertFalse(versions_equal("2026.9.1", "2026.9.2"))


# --- the run pipeline --------------------------------------------------------


class RunPipelineTest(unittest.TestCase):
    def test_a_run_backs_up_before_it_applies_and_reports_started_first(self):
        events = []
        api = FakeApi(events)
        executor = FakeExecutor(events)
        manager = _manager(events, api, executor)

        _run(manager._async_run(_core_run()))

        self.assertEqual(
            events,
            [
                ("report", "started", "backup"),
                ("backup", "core", "core"),
                ("report", "progress", "backup"),
                ("report", "progress", "apply"),
                ("apply", "core", "core"),
                ("report", "success", "confirm"),
            ],
        )

    def test_the_backup_reference_reaches_the_server(self):
        events = []
        api = FakeApi(events)
        manager = _manager(events, api, FakeExecutor(events, backup_ref="backup-abc"))

        _run(manager._async_run(_core_run()))

        backup_reports = [r for r in api.reports if r["phase"] == "backup" and r["status"] == "progress"]
        self.assertEqual(len(backup_reports), 1)
        self.assertEqual(backup_reports[0]["backup_reference"], "backup-abc")

    def test_skipping_the_backup_skips_the_backup(self):
        events = []
        api = FakeApi(events)
        executor = FakeExecutor(events)
        manager = _manager(events, api, executor)

        _run(manager._async_run(_core_run(backup=False)))

        self.assertEqual(executor.backups, [])
        self.assertEqual(
            events,
            [
                ("report", "started", "apply"),
                ("report", "progress", "apply"),
                ("apply", "core", "core"),
                ("report", "success", "confirm"),
            ],
        )

    def test_a_version_that_does_not_land_is_reported_as_a_failure(self):
        events = []
        api = FakeApi(events)
        manager = _manager(events, api, FakeExecutor(events))

        # The fake apply does not change what the installation reports, so a
        # target other than the installed core version reads as "did not land".
        _run(manager._async_run(_core_run(target="2099.1.0")))

        last = api.reports[-1]
        self.assertEqual(last["status"], "failed")
        self.assertEqual(last["phase"], "confirm")
        self.assertIn("2099.1.0", last["error"])

    def test_a_failed_apply_is_reported_with_its_phase(self):
        events = []
        api = FakeApi(events)
        executor = FakeExecutor(events, apply_error=UpdateExecutionError("supervisor said no"))
        manager = _manager(events, api, executor)

        _run(manager._async_run(_core_run()))

        last = api.reports[-1]
        self.assertEqual(last["status"], "failed")
        self.assertEqual(last["phase"], "apply")
        self.assertIn("supervisor said no", last["error"])

    def test_a_malformed_run_is_ignored(self):
        events = []
        api = FakeApi(events)
        manager = _manager(events, api, FakeExecutor(events))

        _run(manager._async_run({"run_id": 1, "kind": "integration", "slug": "zha", "target_version": "1"}))

        self.assertEqual(api.reports, [])

    def test_the_pending_record_is_cleared_after_a_run(self):
        events = []
        api = FakeApi(events)
        manager = _manager(events, api, FakeExecutor(events))

        _run(manager._async_run(_core_run()))

        # Persisted before the apply, cleared once the run settled: a Core update
        # that restarted the box would otherwise leave it to confirm forever.
        self.assertEqual(manager._store.data, {})


# --- confirming on the next boot ---------------------------------------------


class BootConfirmTest(unittest.TestCase):
    def test_a_pending_record_is_confirmed_from_what_is_installed(self):
        events = []
        api = FakeApi(events)
        manager = _manager(events, api, FakeExecutor(events))

        manager._store.data = {
            "run": {"run_id": 9, "kind": "core", "slug": "core", "target_version": INSTALLED_CORE}
        }

        _run(manager.async_confirm_pending())

        self.assertEqual(api.reports[-1]["status"], "success")
        self.assertEqual(api.reports[-1]["phase"], "confirm")
        self.assertEqual(api.reports[-1]["run_id"], "9")
        # Cleared first, so a crash confirming cannot re-confirm every boot.
        self.assertEqual(manager._store.data, {})

    def test_a_pending_record_that_did_not_land_confirms_as_failed(self):
        events = []
        api = FakeApi(events)
        manager = _manager(events, api, FakeExecutor(events))

        manager._store.data = {
            "run": {"run_id": 9, "kind": "core", "slug": "core", "target_version": "2099.1.0"}
        }

        _run(manager.async_confirm_pending())

        self.assertEqual(api.reports[-1]["status"], "failed")
        self.assertEqual(api.reports[-1]["phase"], "confirm")

    def test_no_pending_record_reports_nothing(self):
        events = []
        api = FakeApi(events)
        manager = _manager(events, api, FakeExecutor(events))

        _run(manager.async_confirm_pending())

        self.assertEqual(api.reports, [])


# --- the poll loop -----------------------------------------------------------


class PollLoopTest(unittest.TestCase):
    def test_a_consented_update_is_scheduled(self):
        events = []
        hass = FakeHass()
        api = FakeApi(events, pending=[_core_run()])
        manager = _manager(events, api, FakeExecutor(events), hass=hass)

        _run(manager.async_poll_pending())

        self.assertEqual(len(hass.scheduled), 1)
        self.assertEqual(manager._running, {"7"})

    def test_only_one_run_starts_at_a_time(self):
        events = []
        hass = FakeHass()
        api = FakeApi(events, pending=[_core_run(run_id=7), _core_run(run_id=8)])
        manager = _manager(events, api, FakeExecutor(events), hass=hass)

        _run(manager.async_poll_pending())

        self.assertEqual(len(hass.scheduled), 1)
        self.assertEqual(len(manager._running), 1)

    def test_a_run_already_in_flight_is_not_restarted(self):
        events = []
        hass = FakeHass()
        api = FakeApi(events, pending=[_core_run(run_id=7)])
        manager = _manager(events, api, FakeExecutor(events), hass=hass)
        manager._running.add("7")

        _run(manager.async_poll_pending())

        self.assertEqual(hass.scheduled, [])

    def test_a_server_without_remote_updates_stops_being_asked(self):
        events = []
        api = FakeApi(events, pending_error=RemoteUpdatesUnavailable("no endpoint"))
        manager = _manager(events, api, FakeExecutor(events))

        _run(manager.async_poll_pending())

        self.assertFalse(manager.available)

    def test_a_transient_fetch_failure_leaves_the_manager_enabled(self):
        events = []
        api = FakeApi(events, pending_error=TimeoutError())
        manager = _manager(events, api, FakeExecutor(events))

        _run(manager.async_poll_pending())

        self.assertTrue(manager.available)
        self.assertEqual(manager._running, set())


# --- resolving what to actually call ----------------------------------------
#
# The attribute shapes below are copied from a real supervised Home Assistant
# 2026.6.4, including the supported_features bitmasks: Core advertises
# SPECIFIC_VERSION and an add-on does not, which is the whole reason the version
# parameter cannot be sent unconditionally.


class FakeState:
    def __init__(self, entity_id, **attributes):
        self.entity_id = entity_id
        self.attributes = attributes


class FakeStates:
    def __init__(self, states):
        self._states = states

    def async_all(self, domain=None):
        return list(self._states)


class RecordingServices:
    def __init__(self, response=None):
        self.calls = []
        self.response = response

    async def async_call(self, domain, service, data, blocking=False, return_response=False):
        self.calls.append(
            {"domain": domain, "service": service, "data": dict(data),
             "return_response": return_response}
        )
        if return_response:
            if self.response is None:
                raise TypeError("service does not support response data")
            return self.response
        return None


def _supervised_hass(services=None):
    hass = FakeHass()
    hass.states = FakeStates(
        [
            FakeState(
                "update.home_assistant_core_update",
                supported_features=15,
                entity_picture="/api/brands/integration/homeassistant/icon.png",
            ),
            FakeState(
                "update.home_assistant_operating_system_update",
                supported_features=11,
            ),
            FakeState(
                "update.mosquitto_broker_update",
                supported_features=29,
                entity_picture="/api/hassio/addons/core_mosquitto/icon",
            ),
        ]
    )
    hass.services = services or RecordingServices()
    return hass


def _executor(hass):
    return updates_mod.SupervisorUpdater(hass)


class SupervisorCallTest(unittest.TestCase):
    def test_add_ons_are_updated_through_the_update_entity_not_the_deprecated_service(self):
        # hassio.addon_update was deprecated in Home Assistant 2024.11; calling
        # it on a recent core fails outright.
        services = RecordingServices()
        hass = _supervised_hass(services)

        _run(_executor(hass).async_apply("addon", "core_mosquitto", "7.1.1"))

        self.assertEqual(len(services.calls), 1)
        call = services.calls[0]
        self.assertEqual((call["domain"], call["service"]), ("update", "install"))
        self.assertEqual(call["data"]["entity_id"], "update.mosquitto_broker_update")

    def test_an_add_on_is_never_sent_a_specific_version(self):
        # Its update entity does not advertise SPECIFIC_VERSION, and sending the
        # parameter anyway fails the run.
        services = RecordingServices()
        hass = _supervised_hass(services)

        _run(_executor(hass).async_apply("addon", "core_mosquitto", "7.1.1"))

        self.assertNotIn("version", services.calls[0]["data"])

    def test_core_is_sent_the_target_version(self):
        services = RecordingServices()
        hass = _supervised_hass(services)

        _run(_executor(hass).async_apply("core", "core", "2026.9.3"))

        call = services.calls[0]
        self.assertEqual(call["data"]["entity_id"], "update.home_assistant_core_update")
        self.assertEqual(call["data"]["version"], "2026.9.3")

    def test_the_update_entity_is_not_asked_for_a_second_backup(self):
        services = RecordingServices()
        hass = _supervised_hass(services)

        _run(_executor(hass).async_apply("core", "core", "2026.9.3"))

        self.assertIs(services.calls[0]["data"]["backup"], False)

    def test_an_add_on_that_is_not_installed_fails_clearly(self):
        hass = _supervised_hass()

        with self.assertRaises(UpdateExecutionError) as caught:
            _run(_executor(hass).async_apply("addon", "core_nonexistent", "1.0"))

        self.assertIn("core_nonexistent", str(caught.exception))

    def test_a_backup_reports_the_slug_the_supervisor_returned(self):
        services = RecordingServices(response={"slug": "a1b2c3d4"})
        hass = _supervised_hass(services)

        reference = _run(_executor(hass).async_backup("addon", "core_mosquitto", "pre-update"))

        self.assertEqual(reference, "a1b2c3d4")
        self.assertEqual(services.calls[0]["service"], "backup_partial")
        self.assertEqual(services.calls[0]["data"]["addons"], ["core_mosquitto"])

    def test_a_backup_falls_back_to_its_name_without_response_data(self):
        # Some cores refuse return_response; a backup that ran is not worth
        # failing over the shape of its reply.
        services = RecordingServices(response=None)
        hass = _supervised_hass(services)

        reference = _run(_executor(hass).async_backup("core", "core", "pre-update"))

        self.assertEqual(reference, "pre-update")
        self.assertEqual(services.calls[-1]["service"], "backup_full")


if __name__ == "__main__":
    unittest.main()
