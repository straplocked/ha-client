"""HA Dispatch Client -- Recovery: the add-on's own logic.

The add-on is not Home Assistant code; it lives in recovery/hadc_recovery and
imports nothing from homeassistant. These tests run it against a temporary
config directory and a fake Supervisor.
"""
import inspect
import json
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "recovery"))

from hadc_recovery import ladder as L  # noqa: E402
from hadc_recovery import probe, restore, supervisor  # noqa: E402
from hadc_recovery.__main__ import Runner, redact  # noqa: E402
from hadc_recovery.restore import RestoreCheck  # noqa: E402

PACKAGE = Path(__file__).resolve().parents[1] / "recovery" / "hadc_recovery"
NOW = datetime(2026, 10, 24, 9, 13, 30, tzinfo=timezone.utc)


def make_house(root: Path, *, from_v="1.9.0", to_v="1.9.1", started=NOW, backup=True, manifest_version=None):
    (root / "custom_components" / "ha_dispatch_client").mkdir(parents=True)
    (root / "custom_components" / "ha_dispatch_client" / "manifest.json").write_text(
        json.dumps({"domain": "ha_dispatch_client", "version": to_v})
    )
    (root / "custom_components" / "other").mkdir(parents=True)
    (root / "configuration.yaml").write_text("default_config:\n")
    (root / ".storage").mkdir()
    if started is not None:
        (root / ".storage" / "ha_dispatch_client.update").write_text(
            json.dumps(
                {
                    "version": 1,
                    "key": "ha_dispatch_client.update",
                    "data": {
                        "from_version": from_v,
                        "to_version": to_v,
                        "started_at": started.isoformat(),
                        "phase": "restart",
                    },
                }
            )
        )
    if backup:
        b = root / ".ha_dispatch_client" / "backups" / from_v
        b.mkdir(parents=True)
        (b / "manifest.json").write_text(
            json.dumps({"domain": "ha_dispatch_client", "version": manifest_version or from_v})
        )
        (b / "__init__.py").write_text("# old client\n")


def ok_check(from_v="1.9.0", to_v="1.9.1"):
    return RestoreCheck(True, "eligible", from_v, to_v, NOW)


NO_RECORD = RestoreCheck(False, "no_pending_record")


def obs(t, serving, check=NO_RECORD, **kw):
    return L.Observation(now=t, serving=serving, restore=check, **kw)


class RestoreEligibilityTest(unittest.TestCase):
    def test_eligible_with_recent_record_and_backup(self):
        with TemporaryDirectory() as d:
            make_house(Path(d))
            result = restore.check(Path(d), NOW + timedelta(minutes=10))
            self.assertTrue(result.eligible, result.reason)
            self.assertEqual((result.from_version, result.to_version), ("1.9.0", "1.9.1"))

    def test_no_record_no_restore(self):
        with TemporaryDirectory() as d:
            make_house(Path(d), started=None)
            self.assertEqual(restore.check(Path(d), NOW).reason, "no_pending_record")

    def test_no_backup_no_restore(self):
        with TemporaryDirectory() as d:
            make_house(Path(d), backup=False)
            self.assertEqual(restore.check(Path(d), NOW).reason, "no_backup")

    def test_backup_of_another_version_is_refused(self):
        with TemporaryDirectory() as d:
            make_house(Path(d), manifest_version="1.8.2")
            self.assertEqual(restore.check(Path(d), NOW).reason, "backup_manifest_version_mismatch")

    def test_an_old_update_is_not_a_suspect(self):
        with TemporaryDirectory() as d:
            make_house(Path(d), started=NOW - timedelta(hours=7))
            self.assertEqual(restore.check(Path(d), NOW).reason, "update_too_old")

    def test_a_version_that_walks_out_of_the_backups_dir_is_refused(self):
        with TemporaryDirectory() as d:
            make_house(Path(d), from_v="../../custom_components/other", backup=False)
            self.assertEqual(restore.check(Path(d), NOW).reason, "pending_record_bad_version")

    def test_an_unknown_contract_refuses_restore(self):
        with TemporaryDirectory() as d:
            make_house(Path(d))
            (Path(d) / ".ha_dispatch_client" / "contract.json").write_text('{"watchdog_contract": 2}')
            self.assertEqual(restore.check(Path(d), NOW).reason, "contract_unknown")

    def test_no_contract_file_is_the_1_9_layout(self):
        with TemporaryDirectory() as d:
            make_house(Path(d))
            self.assertTrue(restore.check(Path(d), NOW).eligible)


class RestoreSwapTest(unittest.TestCase):
    def test_swap_puts_the_backup_in_place_and_keeps_both_copies(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            make_house(root)
            result = restore.swap(root, "1.9.0", "1.9.1")
            live = root / "custom_components" / "ha_dispatch_client"
            self.assertEqual(json.loads((live / "manifest.json").read_text())["version"], "1.9.0")
            # The updater's backup survives; the displaced tree is kept aside.
            self.assertTrue((root / ".ha_dispatch_client" / "backups" / "1.9.0" / "manifest.json").exists())
            displaced = root / ".ha_dispatch_client" / "recovery" / "displaced" / "1.9.1"
            self.assertEqual(json.loads((displaced / "manifest.json").read_text())["version"], "1.9.1")
            self.assertEqual(result["restored"], "1.9.0")
            # Nothing else was touched.
            self.assertEqual((root / "configuration.yaml").read_text(), "default_config:\n")
            self.assertTrue((root / "custom_components" / "other").is_dir())

    def test_only_one_displaced_tree_is_kept(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            make_house(root)
            restore.swap(root, "1.9.0", "1.9.1")
            restore.swap(root, "1.9.0", "1.9.0-again")
            displaced = list((root / ".ha_dispatch_client" / "recovery" / "displaced").iterdir())
            self.assertEqual([p.name for p in displaced], ["1.9.0-again"])

    def test_no_backup_raises_and_leaves_the_live_tree(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            make_house(root, backup=False)
            with self.assertRaises(FileNotFoundError):
                restore.swap(root, "1.9.0", "1.9.1")
            live = root / "custom_components" / "ha_dispatch_client" / "manifest.json"
            self.assertEqual(json.loads(live.read_text())["version"], "1.9.1")

    def test_writes_outside_the_allowlist_are_refused(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            make_house(root)
            for path in (
                root / "configuration.yaml",
                root / ".storage" / "ha_dispatch_client.update",
                root / "secrets.yaml",
                root / "custom_components" / "other",
                root / ".ha_dispatch_client" / "backups" / "1.9.0",
                root / ".ha_dispatch_client" / "recovery" / ".." / "backups",
            ):
                with self.assertRaises(restore.RefusedWrite, msg=str(path)):
                    restore._writable(root, path)

    def test_a_symlink_out_of_the_recovery_dir_is_refused(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            make_house(root)
            rec = root / ".ha_dispatch_client" / "recovery"
            rec.mkdir(parents=True)
            (rec / "incidents").symlink_to(root / ".storage")
            with self.assertRaises(restore.RefusedWrite):
                restore.write_note(root, "0123abcd-0000-0000-0000-000000000000", {})

    def test_the_note_lands_in_the_recovery_dir(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            make_house(root)
            path = restore.write_note(root, "0123abcd-0000-0000-0000-000000000000", {"x": 1})
            self.assertEqual(path.parent, root / ".ha_dispatch_client" / "recovery" / "incidents")


class SupervisorAllowlistTest(unittest.TestCase):
    ALLOWED = {
        "info", "core_info", "core_stats", "jobs_info", "host_info", "self_info",
        "core_logs", "host_journal", "core_start", "core_restart", "core_rebuild",
        "enable_own_watchdog", "supervisor_running", "container_running",
        "blocking_job", "disk_free_gb",
    }

    def test_the_public_surface_is_exactly_the_allowlist(self):
        public = {
            name for name, _ in inspect.getmembers(supervisor.SupervisorClient, inspect.isfunction)
            if not name.startswith("_")
        }
        self.assertEqual(public, self.ALLOWED)

    def test_no_path_reaches_another_add_on_the_store_backups_or_an_update(self):
        source = (PACKAGE / "supervisor.py").read_text()
        paths = set(re.findall(r'"(/[a-z][^"?]*)', source))
        for path in paths:
            self.assertFalse(path.startswith("/store"), path)
            self.assertFalse(path.startswith("/backups"), path)
            self.assertNotIn("update", path)
            self.assertNotIn("reboot", path)
            if path.startswith("/addons"):
                self.assertTrue(path.startswith("/addons/self/"), path)

    def test_nothing_in_the_package_can_run_a_command(self):
        for path in PACKAGE.glob("*.py"):
            source = path.read_text()
            for forbidden in ("subprocess", "os.system", "os.popen", "eval(", "exec(", "pty"):
                self.assertNotIn(forbidden, source, f"{forbidden} in {path.name}")

    def test_force_is_never_sent(self):
        source = (PACKAGE / "supervisor.py").read_text()
        self.assertNotIn('"force"', source)
        self.assertNotIn("force=", source)

    def test_stand_down_is_an_allowlist(self):
        jobs = [{"name": "home_assistant_core_restart_after_problem", "done": False, "child_jobs": []}]
        self.assertIsNone(supervisor.find_blocking_job(jobs))
        jobs.append({"name": "x", "done": False, "child_jobs": [{"name": "home_assistant_core_update", "done": False}]})
        self.assertEqual(supervisor.find_blocking_job(jobs), "home_assistant_core_update")
        self.assertIsNone(supervisor.find_blocking_job([{"name": "backup_manager_full_backup", "done": True}]))

    def test_busy_is_a_refusal_for_now(self):
        busy = supervisor.Reply(400, {"result": "error", "message": "Offline database migration in progress"})
        self.assertTrue(busy.busy)
        crashed = supervisor.Reply(400, {"result": "error", "message": "Home Assistant has crashed"})
        self.assertFalse(crashed.busy)

    def test_the_probe_never_asks_for_api(self):
        self.assertTrue(probe.CORE_PROBE_URL.endswith("/auth/providers"))
        self.assertNotIn('"/api', (PACKAGE / "probe.py").read_text())


class LadderTest(unittest.TestCase):
    def run_until(self, ladder, start, end, serving, check=NO_RECORD, step=15, **kw):
        actions = []
        t = start
        while t <= end:
            decision = ladder.step(obs(t, serving, check, **kw))
            if decision.action:
                actions.append((t - start, decision.action))
                ladder.accepted(t)
            t += step
        return actions

    def test_nothing_happens_inside_the_first_three_minutes(self):
        ladder = L.Ladder()
        self.assertEqual(self.run_until(ladder, 0, 165, serving=False), [])
        self.assertEqual(ladder.state, L.SUSPECT)

    def test_a_blip_is_forgiven(self):
        ladder = L.Ladder()
        ladder.step(obs(0, False))
        ladder.step(obs(60, True))
        self.assertEqual(ladder.state, L.WATCHING)

    def test_the_full_ladder_after_our_update_and_its_timings(self):
        ladder = L.Ladder()
        actions = self.run_until(ladder, 0, 40 * 60, serving=False, check=ok_check())
        self.assertEqual(
            actions,
            [(180, "core_start"), (180 + 300, "core_rebuild"), (180 + 300 + 600, "restore_start")],
        )
        self.assertEqual(ladder.state, L.EXHAUSTED)
        self.assertEqual(ladder.incident["cause"], L.AFTER_CLIENT_UPDATE)
        self.assertEqual(ladder.incident["state"], "exhausted")

    def test_no_restore_when_not_after_our_update(self):
        ladder = L.Ladder()
        actions = self.run_until(ladder, 0, 40 * 60, serving=False)
        self.assertEqual([a for _, a in actions], ["core_start", "core_rebuild"])
        self.assertEqual(ladder.state, L.EXHAUSTED)
        self.assertIn("not after our update", ladder.incident["exhausted_reason"])

    def test_recovered_only_after_two_minutes_of_answering(self):
        ladder = L.Ladder()
        self.run_until(ladder, 0, 180, serving=False)
        ladder.step(obs(200, True))
        self.assertEqual(ladder.state, L.STABILISING)
        ladder.step(obs(300, True))
        self.assertEqual(ladder.state, L.STABILISING)
        decision = ladder.step(obs(321, True))
        self.assertEqual(ladder.state, L.WATCHING)
        self.assertIn("recovered by start", decision.log)
        self.assertEqual(ladder.incident["recovered_by"], "start")

    def test_falling_over_while_stabilising_keeps_the_rung_clock(self):
        ladder = L.Ladder()
        self.run_until(ladder, 0, 180, serving=False)
        ladder.step(obs(200, True))
        ladder.step(obs(260, False))
        self.assertEqual(ladder.state, L.ACTING)
        self.assertEqual(ladder.step(obs(470, False)).action, None)
        self.assertEqual(ladder.step(obs(480, False)).action, "core_rebuild")

    def test_stand_down_while_the_supervisor_works_on_core(self):
        ladder = L.Ladder()
        self.assertEqual(self.run_until(ladder, 0, 600, serving=False, blocking_job="home_assistant_core_update"), [])
        actions = self.run_until(ladder, 615, 615 + 200, serving=False)
        # A fresh 3 minutes from the last time the job was seen (t=600).
        self.assertEqual(actions, [(165, "core_start")])

    def test_a_busy_refusal_retries_the_same_rung(self):
        ladder = L.Ladder()
        for t in range(0, 181, 15):
            decision = ladder.step(obs(t, False))
        self.assertEqual(decision.action, "core_start")
        ladder.busy()
        decision = ladder.step(obs(195, False, supervisor_running=False))
        self.assertIsNone(decision.action)
        decision = ladder.step(obs(210, False))
        self.assertEqual(decision.action, "core_start")
        self.assertEqual(ladder.rung, L.START)

    def test_a_re_crash_within_the_hour_is_not_re_laddered(self):
        ladder = L.Ladder()
        ladder.last_recovered_at = 0
        actions = self.run_until(ladder, 1000, 1000 + 400, serving=False)
        self.assertEqual(actions, [])
        self.assertEqual(ladder.state, L.EXHAUSTED)

    def test_three_ladders_a_day_at_most(self):
        ladder = L.Ladder()
        ladder.ladder_starts.extend([0, 10_000, 20_000])
        actions = self.run_until(ladder, 30_000, 30_400, serving=False)
        self.assertEqual(actions, [])
        self.assertEqual(ladder.state, L.EXHAUSTED)
        fresh = L.Ladder()
        fresh.ladder_starts.extend([0, 10_000, 20_000])
        self.assertTrue(self.run_until(fresh, 90_000, 90_400, serving=False))

    def test_a_full_disk_skips_the_rebuild(self):
        ladder = L.Ladder()
        actions = self.run_until(ladder, 0, 40 * 60, serving=False, check=ok_check(), disk_free_gb=0.5)
        self.assertEqual([a for _, a in actions], ["core_start", "restore_start"])

    def test_core_up_but_our_update_never_confirmed(self):
        ladder = L.Ladder()
        actions = self.run_until(ladder, 0, 900, serving=True, check=ok_check(), pending_present=True)
        self.assertEqual(actions, [(600, "restore_restart")])
        self.assertEqual(ladder.incident["kind"], L.AGENT_SILENT)
        # Healthy means the integration loaded and cleared the record.
        ladder.step(obs(1000, True, ok_check(), pending_present=True))
        self.assertEqual(ladder.state, L.ACTING)
        ladder.step(obs(1015, True, NO_RECORD, pending_present=False))
        ladder.step(obs(1140, True, NO_RECORD, pending_present=False))
        self.assertEqual(ladder.state, L.WATCHING)
        self.assertEqual(ladder.incident["recovered_by"], "restore")

    def test_core_up_unconfirmed_but_unrestorable_is_reported_once_and_left_alone(self):
        ladder = L.Ladder()
        no_backup = RestoreCheck(False, "no_backup", "1.9.1", "1.9.2", NOW)
        decisions = []
        for t in range(0, 1800, 15):
            d = ladder.step(obs(t, True, no_backup, pending_present=True))
            decisions.append(d)
        self.assertFalse(any(d.action for d in decisions))
        escalations = [d for d in decisions if d.escalate]
        self.assertEqual(len(escalations), 1)
        self.assertIn("no_backup", escalations[0].log)
        self.assertEqual(ladder.state, L.WATCHING)

    def test_core_up_without_our_update_is_left_alone(self):
        ladder = L.Ladder()
        self.assertEqual(self.run_until(ladder, 0, 3600, serving=True, pending_present=False), [])

    def test_a_failed_restore_moves_on(self):
        ladder = L.Ladder()
        actions = self.run_until(ladder, 0, 180 + 300 + 600, serving=False, check=ok_check())
        self.assertEqual(actions[-1][1], "restore_start")
        decision = ladder.failed(obs(1100, False, ok_check()), "disk full")
        self.assertTrue(decision.escalate)
        self.assertEqual(ladder.state, L.EXHAUSTED)


class FakeReply(supervisor.Reply):
    pass


class FakeSupervisor:
    def __init__(self, busy=0):
        self.calls = []
        self._busy = busy

    def _reply(self, name):
        self.calls.append(name)
        if self._busy:
            self._busy -= 1
            return supervisor.Reply(400, {"message": "Supervisor is in startup state"})
        return supervisor.Reply(200, {"result": "ok"})

    def core_start(self):
        return self._reply("core_start")

    def core_restart(self):
        return self._reply("core_restart")

    def core_rebuild(self):
        return self._reply("core_rebuild")

    def core_logs(self, lines):
        return supervisor.Reply(200, None, "log Bearer abc.def token: hunter2")

    def host_journal(self, lines):
        return supervisor.Reply(403, None, "")


class RunnerTest(unittest.TestCase):
    def test_a_busy_restore_swaps_once_and_retries_only_the_start(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            make_house(root)
            sup = FakeSupervisor(busy=1)
            runner = Runner(sup, root, {}, clock=lambda: 0, state_file=root / 'state.json')
            runner.ladder.state = L.ACTING
            runner.ladder.rung = L.RESTORE
            runner.ladder.incident = {"id": "0123abcd-0000-0000-0000-000000000000", "kind": L.CORE_DOWN,
                                      "cause": L.AFTER_CLIENT_UPDATE, "rungs": []}
            o = obs(0, False, restore.check(root, NOW))
            runner.handle(L.Decision(action="restore_start"), o)
            self.assertIsNone(runner.ladder.rung_issued_at)  # refused: retry
            runner.handle(L.Decision(action="restore_start"), o)
            self.assertEqual(sup.calls, ["core_start", "core_start"])
            displaced = list((root / ".ha_dispatch_client" / "recovery" / "displaced").iterdir())
            self.assertEqual(len(displaced), 1)
            self.assertEqual(runner.ladder.rung_issued_at, 0)
            self.assertEqual(runner.ladder.incident["rungs"][-1]["sent_at"], runner.ladder.incident["rungs"][-1]["at"])

    def test_the_lab_option_skips_only_the_first_start(self):
        with TemporaryDirectory() as d:
            sup = FakeSupervisor()
            runner = Runner(sup, Path(d), {"lab_simulate_start_failure": True}, clock=lambda: 0, state_file=Path(d) / "state.json")
            runner.ladder.rung = L.START
            runner.ladder.incident = {"id": "0123abcd-0000-0000-0000-000000000000", "rungs": []}
            runner.handle(L.Decision(action="core_start"), obs(0, False))
            self.assertEqual(sup.calls, [])
            runner.handle(L.Decision(action="core_start"), obs(0, False))
            self.assertEqual(sup.calls, ["core_start"])

    def test_evidence_is_redacted(self):
        self.assertNotIn("hunter2", redact("token: hunter2"))
        self.assertNotIn("abc.def", redact("Authorization: Bearer abc.def"))
        self.assertNotIn("pw", redact("https://user:pw@example.com/x"))


if __name__ == "__main__":
    unittest.main()
