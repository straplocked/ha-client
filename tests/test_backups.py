"""Tests for backup reporting.

What these cover: which of Home Assistant's backups are reported and under
which slug, that each one is reported once (across restarts too), that a first
scan sends a bounded history oldest first, that only copies on this machine are
hashed, and that nothing here can raise into the coordinator's poll.

Home Assistant is not installed here; tests/conftest.py stubs the symbols the
integration imports. The backup manager is a fake shaped like Home Assistant
2025.1's: async_get_backups() returns (backups, agent_errors), and each backup
carries per-agent sizes.
"""
import asyncio
import hashlib
import importlib
import os
import sys
import tempfile
import types
import unittest
from dataclasses import dataclass, field
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


api_client = _load("api_client")
const = _load("const")
backups_mod = _load("backups")

HADispatchBackups = backups_mod.HADispatchBackups
classify = backups_mod.classify
BackupReportingUnavailable = api_client.BackupReportingUnavailable


# --- fakes -------------------------------------------------------------------


@dataclass
class AgentStatus:
    size: int
    protected: bool = False


@dataclass
class Backup:
    backup_id: str
    date: str
    name: str = "Automatic backup 2026.10.0"
    agents: dict = field(default_factory=lambda: {"backup.local": AgentStatus(1000)})
    homeassistant_included: bool = True
    database_included: bool = True
    addons: list = field(default_factory=list)
    folders: list = field(default_factory=list)
    with_automatic_settings: bool = True


class FakeLocalAgent:
    def __init__(self, paths):
        self.paths = paths

    def get_backup_path(self, backup_id):
        return self.paths[backup_id]


class FakeSupervisorAgent:
    def __init__(self, blobs):
        self.blobs = blobs
        self.downloaded = []

    async def async_download_backup(self, backup_id):
        self.downloaded.append(backup_id)
        data = self.blobs[backup_id]

        async def stream():
            for start in range(0, len(data), 3):
                yield data[start:start + 3]

        return stream()


class FakeManager:
    def __init__(self, backups=(), local=None, supervisor=None, legacy=False):
        self.backups = {b.backup_id: b for b in backups}
        self.local_backup_agents = {"backup.local": local} if local else {}
        self.backup_agents = {"hassio.local": supervisor} if supervisor else {}
        self.legacy = legacy
        self.subscribers = []

    def add(self, backup):
        self.backups[backup.backup_id] = backup

    async def async_get_backups(self):
        if self.legacy:
            return dict(self.backups)
        return dict(self.backups), {}

    def async_subscribe_events(self, on_event):
        self.subscribers.append(on_event)
        return lambda: self.subscribers.remove(on_event)


class FakeApi:
    def __init__(self, error=None):
        self.batches = []
        self.error = error

    async def report_backups(self, installation_id, backups):
        if self.error is not None:
            raise self.error
        self.batches.append((installation_id, list(backups)))
        return {"status": "ok", "recorded": len(backups)}

    @property
    def sent(self):
        return [entry for _, batch in self.batches for entry in batch]


class FakeCoordinator:
    def __init__(self, api, installation_id="inst-1"):
        self.api_client = api
        self.installation_id = installation_id


class FakeHass:
    def __init__(self, manager=None):
        self.data = {}
        if manager is not None:
            self.data["backup"] = manager
        self.scheduled = []

    async def async_add_executor_job(self, func, *args):
        return func(*args)

    def async_create_background_task(self, coro, name):
        coro.close()
        self.scheduled.append(name)
        return object()


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _at(hours_ago: float) -> str:
    base = datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc)
    return (base - timedelta(hours=hours_ago)).isoformat()


def _reporter(manager, api=None, installation_id="inst-1"):
    api = api or FakeApi()
    hass = FakeHass(manager)
    return HADispatchBackups(hass, FakeCoordinator(api, installation_id)), api, hass


# --- classification ----------------------------------------------------------


class ClassifyTests(unittest.TestCase):
    def test_the_automatic_schedule_has_its_own_slug(self):
        self.assertEqual(("automatic", "full"), classify(Backup("a", _at(0))))

    def test_a_manual_full_backup_is_reported_as_manual(self):
        backup = Backup("a", _at(0), name="Nightly", with_automatic_settings=False)
        self.assertEqual(("manual", "full"), classify(backup))

    def test_a_manual_partial_backup_is_left_out(self):
        backup = Backup(
            "a", _at(0), with_automatic_settings=False, database_included=False
        )
        self.assertIsNone(classify(backup))

    def test_on_a_supervised_install_config_only_is_not_full(self):
        # What Core's own pre-update backup looks like on HAOS.
        backup = Backup(
            "a", _at(0),
            name="Home Assistant Core 2026.10.1",
            agents={"hassio.local": AgentStatus(5000)},
            with_automatic_settings=False,
        )
        self.assertIsNone(classify(backup))

        backup.addons = [{"slug": "core_mosquitto"}]
        backup.folders = ["share"]
        self.assertEqual(("manual", "full"), classify(backup))

    def test_an_automatic_partial_keeps_its_slug_and_says_partial(self):
        backup = Backup("a", _at(0), database_included=False)
        self.assertEqual(("automatic", "partial"), classify(backup))

    def test_a_remote_update_runs_own_backup_is_never_reported(self):
        backup = Backup(
            "a", _at(0),
            name=f"{const.UPDATE_RUN_BACKUP_PREFIX} core 2026.10.1",
            with_automatic_settings=False,
        )
        self.assertIsNone(classify(backup))


# --- scanning ----------------------------------------------------------------


class ScanTests(unittest.TestCase):
    def test_reports_oldest_first_with_slug_size_and_time(self):
        manager = FakeManager([
            Backup("new", _at(0), agents={"backup.local": AgentStatus(2000)}),
            Backup("old", _at(24), agents={"backup.local": AgentStatus(1900)}),
        ])
        reporter, api, _ = _reporter(manager)

        self.assertEqual(2, _run(reporter.async_scan()))

        sent = api.sent
        self.assertEqual(["inst-1"], [inst for inst, _ in api.batches])
        self.assertEqual([1900, 2000], [entry["size_bytes"] for entry in sent])
        self.assertEqual({"automatic"}, {entry["slug"] for entry in sent})
        self.assertEqual({"full"}, {entry["type"] for entry in sent})
        self.assertEqual(_at(24), sent[0]["completed_at"])

    def test_the_local_copy_decides_the_size_over_a_cloud_copy(self):
        manager = FakeManager([
            Backup("a", _at(0), agents={
                "cloud.cloud": AgentStatus(999_999),
                "hassio.local": AgentStatus(1234),
            }, addons=[{"slug": "x"}]),
        ])
        reporter, api, _ = _reporter(manager)
        _run(reporter.async_scan())
        self.assertEqual(1234, api.sent[0]["size_bytes"])

    def test_each_backup_is_reported_once(self):
        manager = FakeManager([Backup("a", _at(24))])
        reporter, api, _ = _reporter(manager)

        _run(reporter.async_scan())
        self.assertEqual(0, _run(reporter.async_scan()))

        manager.add(Backup("b", _at(0)))
        self.assertEqual(1, _run(reporter.async_scan()))
        self.assertEqual(2, len(api.sent))

    def test_a_restart_does_not_resend_history(self):
        manager = FakeManager([Backup("a", _at(24)), Backup("b", _at(0))])
        reporter, api, hass = _reporter(manager)
        _run(reporter.async_scan())

        again = HADispatchBackups(hass, FakeCoordinator(api))
        again._store = reporter._store  # the same file on disk

        self.assertEqual(0, _run(again.async_scan()))

    def test_a_re_enrolled_installation_gets_its_history_again(self):
        manager = FakeManager([Backup("a", _at(24))])
        reporter, api, _ = _reporter(manager)
        _run(reporter.async_scan())

        reporter.coordinator.installation_id = "inst-2"

        self.assertEqual(1, _run(reporter.async_scan()))
        self.assertEqual("inst-2", api.batches[-1][0])

    def test_the_first_scan_sends_a_bounded_history_and_never_the_rest(self):
        cap = const.BACKUP_BACKFILL_PER_SLUG
        manager = FakeManager([Backup(f"b{i}", _at(24 * i)) for i in range(cap + 5)])
        reporter, api, _ = _reporter(manager)

        self.assertEqual(cap, _run(reporter.async_scan()))
        # The newest ten, still oldest first.
        self.assertEqual(_at(24 * (cap - 1)), api.sent[0]["completed_at"])
        self.assertEqual(_at(0), api.sent[-1]["completed_at"])

        self.assertEqual(0, _run(reporter.async_scan()))

    def test_more_than_a_batch_goes_out_in_several_requests(self):
        count = const.BACKUP_BATCH_MAX + 20
        manager = FakeManager([Backup("seed", _at(10_000))])
        reporter, api, _ = _reporter(manager)
        _run(reporter.async_scan())  # past the first-scan cap

        for i in range(count):
            manager.add(Backup(f"b{i}", _at(i)))
        self.assertEqual(count, _run(reporter.async_scan()))
        self.assertEqual([1, const.BACKUP_BATCH_MAX, 20], [len(b) for _, b in api.batches])

    def test_a_transport_failure_is_retried_on_the_next_tick(self):
        manager = FakeManager([Backup("a", _at(0))])
        api = FakeApi(error=OSError("connection reset"))
        reporter, _, hass = _reporter(manager, api)

        self.assertEqual(0, _run(reporter.async_scan()))
        self.assertTrue(reporter._is_due())

        api.error = None
        self.assertEqual(1, _run(reporter.async_scan()))

    def test_a_server_without_backup_reporting_switches_it_off(self):
        manager = FakeManager([Backup("a", _at(0))])
        api = FakeApi(error=BackupReportingUnavailable("404"))
        reporter, _, hass = _reporter(manager, api)

        _run(reporter.async_scan())
        self.assertFalse(reporter.available)

        reporter.async_tick()
        self.assertEqual([], hass.scheduled)

    def test_a_backup_manager_before_2025_1_is_left_alone(self):
        manager = FakeManager([Backup("a", _at(0))], legacy=True)
        reporter, api, _ = _reporter(manager)

        self.assertEqual(0, _run(reporter.async_scan()))
        self.assertEqual([], api.batches)
        self.assertFalse(reporter.available)

    def test_no_backup_integration_reports_nothing(self):
        reporter, api, _ = _reporter(None)
        self.assertEqual(0, _run(reporter.async_scan()))
        self.assertEqual([], api.batches)
        self.assertTrue(reporter.available)


# --- checksums ---------------------------------------------------------------


class ChecksumTests(unittest.TestCase):
    def test_a_core_local_archive_is_hashed_from_disk(self):
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(b"tar archive bytes")
            path = handle.name
        self.addCleanup(os.unlink, path)

        manager = FakeManager(
            [Backup("a", _at(0))], local=FakeLocalAgent({"a": path})
        )
        reporter, api, _ = _reporter(manager)
        _run(reporter.async_scan())

        expected = "sha256:" + hashlib.sha256(b"tar archive bytes").hexdigest()
        self.assertEqual(expected, api.sent[0]["checksum"])

    def test_a_supervisor_archive_is_hashed_from_its_stream(self):
        supervisor = FakeSupervisorAgent({"a": b"supervisor archive"})
        manager = FakeManager(
            [Backup("a", _at(0), agents={"hassio.local": AgentStatus(18)},
                    folders=["share"])],
            supervisor=supervisor,
        )
        reporter, api, _ = _reporter(manager)
        _run(reporter.async_scan())

        expected = "sha256:" + hashlib.sha256(b"supervisor archive").hexdigest()
        self.assertEqual(expected, api.sent[0]["checksum"])

    def test_only_the_newest_per_slug_is_hashed_in_one_scan(self):
        supervisor = FakeSupervisorAgent({"old": b"1", "new": b"2", "man": b"3"})

        def backup(backup_id, hours, automatic=True):
            return Backup(
                backup_id, _at(hours), agents={"hassio.local": AgentStatus(1)},
                folders=["share"], with_automatic_settings=automatic,
            )

        manager = FakeManager(
            [backup("old", 24), backup("new", 0), backup("man", 30, automatic=False)],
            supervisor=supervisor,
        )
        reporter, api, _ = _reporter(manager)
        _run(reporter.async_scan())

        self.assertEqual({"new", "man"}, set(supervisor.downloaded))
        by_slug_time = {(e["slug"], e["completed_at"]): e for e in api.sent}
        self.assertNotIn("checksum", by_slug_time[("automatic", _at(24))])
        self.assertIn("checksum", by_slug_time[("automatic", _at(0))])

    def test_a_cloud_only_backup_is_never_downloaded_to_hash_it(self):
        supervisor = FakeSupervisorAgent({})
        manager = FakeManager(
            [Backup("a", _at(0), agents={"cloud.cloud": AgentStatus(5)})],
            supervisor=supervisor,
        )
        reporter, api, _ = _reporter(manager)
        _run(reporter.async_scan())

        self.assertEqual([], supervisor.downloaded)
        self.assertNotIn("checksum", api.sent[0])
        self.assertEqual(5, api.sent[0]["size_bytes"])

    def test_a_hashing_failure_still_reports_the_backup(self):
        manager = FakeManager(
            [Backup("a", _at(0))], local=FakeLocalAgent({"a": "/nonexistent/a.tar"})
        )
        reporter, api, _ = _reporter(manager)
        self.assertEqual(1, _run(reporter.async_scan()))
        self.assertNotIn("checksum", api.sent[0])


# --- scheduling --------------------------------------------------------------


class ScheduleTests(unittest.TestCase):
    def test_the_first_tick_scans_in_the_background_and_only_once(self):
        reporter, _, hass = _reporter(FakeManager())
        reporter.async_tick()
        reporter.async_tick()
        self.assertEqual(1, len(hass.scheduled))

    def test_a_finished_backup_makes_the_next_tick_scan(self):
        manager = FakeManager()
        reporter, _, hass = _reporter(manager)
        _run(reporter.async_scan())
        reporter.async_tick()
        self.assertEqual([], hass.scheduled)  # not due yet

        event = types.SimpleNamespace(manager_state="create_backup", state="completed")
        for subscriber in manager.subscribers:
            subscriber(event)

        reporter.async_tick()
        self.assertEqual(1, len(hass.scheduled))

    def test_an_in_progress_event_does_not_trigger_a_scan(self):
        manager = FakeManager()
        reporter, _, hass = _reporter(manager)
        _run(reporter.async_scan())
        reporter.async_tick()

        event = types.SimpleNamespace(manager_state="create_backup", state="in_progress")
        for subscriber in manager.subscribers:
            subscriber(event)

        reporter.async_tick()
        self.assertEqual([], hass.scheduled)

    def test_unload_stops_listening(self):
        manager = FakeManager()
        reporter, _, _ = _reporter(manager)
        reporter.async_tick()
        self.assertEqual(1, len(manager.subscribers))

        reporter.async_unload()
        self.assertEqual([], manager.subscribers)


if __name__ == "__main__":
    unittest.main()
