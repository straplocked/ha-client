"""HA Dispatch Client -- Recovery: the runner.

Probe every 15 s, feed the ladder, do what it says, write the incident note.
Phase 1 of docs/technical/core-watchdog-addon.md (Dispatch repo): local only,
no Dispatch. Every decision goes to the add-on log in one line.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .ladder import RESTORE, START, Decision, Ladder, Observation, Timings
from .probe import CORE_PROBE_URL, core_serving
from .restore import check, pending_present, swap, write_note
from .supervisor import SupervisorClient

_LOGGER = logging.getLogger("hadc_recovery")

OPTIONS_FILE = Path("/data/options.json")
STATE_FILE = Path("/data/state.json")
PROBE_INTERVAL = 15

_REDACT = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1[redacted]"),
    (re.compile(r"(?i)((?:token|password|secret|api_key)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"), r"\1[redacted]"),
    (re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1[redacted]@"),
    (re.compile(r"\b[A-Za-z0-9_-]{40,}\b"), "[redacted]"),
]


def redact(text: str) -> str:
    for pattern, replacement in _REDACT:
        text = pattern.sub(replacement, text)
    return text


def code_hash() -> str:
    """A hash of this add-on's own code, for Dispatch to compare (Phase 2)."""
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class Runner:
    def __init__(
        self,
        supervisor: SupervisorClient,
        config_root: Path,
        options: dict,
        clock=time.time,
        state_file: Path = STATE_FILE,
    ) -> None:
        self.sup = supervisor
        self.state_file = Path(state_file)
        self.root = config_root
        self.options = options
        self.clock = clock
        self.ladder = Ladder(timings=Timings())
        self._swapped_for = None
        self._simulated_for = None
        self._last_heartbeat = 0.0
        self._load_state()

    # -- one tick

    def observe(self) -> Observation:
        now = self.clock()
        serving = core_serving(self.options.get("probe_url") or CORE_PROBE_URL)
        quiet = serving and self.ladder.state == "watching"
        return Observation(
            now=now,
            serving=serving,
            restore=check(self.root, datetime.fromtimestamp(now, tz=timezone.utc)),
            pending_present=pending_present(self.root),
            # Spare the Supervisor while all is well; ask when it matters.
            supervisor_running=True if quiet else self.sup.supervisor_running(),
            blocking_job=None if quiet else self.sup.blocking_job(),
            disk_free_gb=None if quiet else self.sup.disk_free_gb(),
        )

    def tick(self) -> Decision:
        obs = self.observe()
        decision = self.ladder.step(obs)
        self.handle(decision, obs)
        if obs.now - self._last_heartbeat >= 600:
            self._last_heartbeat = obs.now
            _LOGGER.info("state=%s serving=%s pending_record=%s", self.ladder.state, obs.serving, obs.pending_present)
        return decision

    def handle(self, decision: Decision, obs: Observation, depth: int = 0) -> None:
        if decision.log:
            level = logging.WARNING if decision.escalate else logging.INFO
            _LOGGER.log(level, "decision state=%s rung=%s: %s", self.ladder.state, self.ladder.rung, decision.log)
        if decision.action:
            follow_up = self._do(decision.action, obs)
            if follow_up is not None and depth == 0:
                self.handle(follow_up, obs, depth=1)
                return
        if decision.escalate:
            self._attach_evidence()
        if decision.note:
            self._write_note()

    # -- actions

    def _do(self, action: str, obs: Observation):
        incident_id = (self.ladder.incident or {}).get("id")
        if action == "core_start" and self.ladder.rung == START and self.options.get("lab_simulate_start_failure"):
            if self._simulated_for != incident_id:
                self._simulated_for = incident_id
                _LOGGER.warning("lab: simulating a failed start -- Home Assistant is NOT being started")
                self.ladder.accepted(self.clock())
                return None

        if action in ("restore_start", "restore_restart") and self._swapped_for != incident_id:
            try:
                result = swap(self.root, obs.restore.from_version, obs.restore.to_version)
            except Exception as err:  # noqa: BLE001 - any failure moves the ladder on
                _LOGGER.error("restore failed: %s", err)
                return self.ladder.failed(obs, str(err)[:200])
            self._swapped_for = incident_id
            _LOGGER.info("restored client %s (displaced %s)", result["restored"], result["displaced"])

        call = {
            "core_start": self.sup.core_start,
            "core_rebuild": self.sup.core_rebuild,
            "restore_start": self.sup.core_start,
            "restore_restart": self.sup.core_restart,
        }[action]
        sent_at = self.clock()
        reply = call()
        if reply.busy:
            _LOGGER.info("supervisor refused %s for now (%s): will retry", action, reply.message or reply.status)
            self.ladder.busy()
            return None
        # A timeout or an error after the call went out still started the
        # rung's clock: the probe decides whether Home Assistant came back.
        _LOGGER.info("%s sent: status=%s %s", action, reply.status, (reply.message or "")[:200])
        self.ladder.accepted(self.clock(), sent_at=sent_at)
        return None

    # -- notes and state

    def _attach_evidence(self) -> None:
        if self.ladder.incident is None:
            return
        core = self.sup.core_logs(200)
        journal = self.sup.host_journal(200)
        self.ladder.incident["evidence"] = {
            "core_log_tail": redact(core.text[-20000:]) if core.ok else f"unavailable ({core.status})",
            "core_journal_tail": redact(journal.text[-20000:]) if journal.ok else f"unavailable ({journal.status})",
        }

    def _write_note(self) -> None:
        incident = self.ladder.incident
        if incident is None:
            return
        try:
            write_note(self.root, incident["id"], incident)
        except Exception as err:  # noqa: BLE001 - a note must never stop the ladder
            _LOGGER.error("could not write the incident note: %s", err)
        self._save_state()

    def _load_state(self) -> None:
        try:
            state = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return
        self.ladder.ladder_starts.extend(float(t) for t in state.get("ladder_starts", []))
        self.ladder.last_recovered_at = state.get("last_recovered_at")
        self.ladder.last_restore_at = state.get("last_restore_at")

    def _save_state(self) -> None:
        state = {
            "ladder_starts": list(self.ladder.ladder_starts),
            "last_recovered_at": self.ladder.last_recovered_at,
            "last_restore_at": self.ladder.last_restore_at,
        }
        try:
            self.state_file.write_text(json.dumps(state))
        except OSError as err:
            _LOGGER.error("could not save state: %s", err)


def self_check(sup: SupervisorClient) -> None:
    """Log what this add-on may and may not call, once, at start."""
    checks = {
        "info": sup.info(),
        "core/info": sup.core_info(),
        "core/stats": sup.core_stats(),
        "jobs/info": sup.jobs_info(),
        "host/info": sup.host_info(),
        "core/logs": sup.core_logs(1),
        "host journal (needs manager; 403 expected)": sup.host_journal(1),
    }
    for name, reply in checks.items():
        _LOGGER.info("self-check %s: %s", name, reply.status)


def main() -> int:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        options = json.loads(OPTIONS_FILE.read_text())
    except (OSError, ValueError):
        options = {}
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        _LOGGER.error("no SUPERVISOR_TOKEN: is hassio_api set in config.yaml?")
        return 1
    root = Path(os.environ.get("HADC_CONFIG_ROOT", "/homeassistant"))
    sup = SupervisorClient(token)
    _LOGGER.info("HA Dispatch Client -- Recovery %s starting; code sha256 %s", __version__, code_hash())
    if options.get("lab_simulate_start_failure"):
        _LOGGER.warning("lab option on: the first start in an incident will be skipped on purpose")
    _LOGGER.info("enable own watchdog: %s", sup.enable_own_watchdog().status)
    self_check(sup)
    runner = Runner(sup, root, options)
    while True:
        try:
            runner.tick()
        except Exception:  # noqa: BLE001 - the watchdog must keep watching
            _LOGGER.exception("tick failed")
        time.sleep(PROBE_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
