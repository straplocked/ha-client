"""The recovery ladder, as a pure state machine.

No I/O here. `Ladder.step()` takes what the runner observed and returns what
to do; the runner does it and reports back with `accepted()` or `busy()`.
That split is what lets every rule below be tested without a Supervisor.

Timings are the owner's (D2): act after Core has been unreachable for 3
minutes; then 5 minutes after start, 10 after rebuild, 10 after restore. Each
rung runs once per incident. A Core that answers again counts as recovered only
after 2 minutes of answering.
"""
from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Deque, Optional

from .restore import RestoreCheck

WATCHING = "watching"
SUSPECT = "suspect"
ACTING = "acting"
STABILISING = "stabilising"
EXHAUSTED = "exhausted"

START = "start"
REBUILD = "rebuild"
RESTORE = "restore"

CORE_DOWN = "core_down"
AGENT_SILENT = "agent_silent"

AFTER_CLIENT_UPDATE = "after_client_update"
UNRELATED = "unrelated"


@dataclass(frozen=True)
class Timings:
    detect: float = 180
    start_wait: float = 300
    rebuild_wait: float = 600
    restore_wait: float = 600
    stable: float = 120
    agent_grace: float = 600      # D12: Core serving this long with our update unconfirmed
    rearm: float = 3600           # a re-crash this soon after a recovery is not re-laddered
    max_ladders: int = 3          # automatic ladders per window
    window: float = 86400
    min_disk_gb: float = 2.0      # a rebuild pulls an image

    def wait_for(self, rung: str) -> float:
        return {START: self.start_wait, REBUILD: self.rebuild_wait, RESTORE: self.restore_wait}[rung]


@dataclass(frozen=True)
class Observation:
    now: float
    serving: bool
    restore: RestoreCheck
    pending_present: bool = False
    supervisor_running: Optional[bool] = True
    blocking_job: Optional[str] = None
    disk_free_gb: Optional[float] = None


@dataclass
class Decision:
    action: Optional[str] = None          # core_start | core_rebuild | restore_start | restore_restart
    log: Optional[str] = None
    note: bool = False                    # the incident changed: write its note
    escalate: bool = False                # "needs a human"


@dataclass
class Ladder:
    timings: Timings = field(default_factory=Timings)
    state: str = WATCHING
    down_since: Optional[float] = None
    serving_since: Optional[float] = None
    rung: Optional[str] = None
    rung_issued_at: Optional[float] = None
    stable_since: Optional[float] = None
    last_recovered_at: Optional[float] = None
    last_restore_at: Optional[float] = None
    ladder_starts: Deque[float] = field(default_factory=deque)
    incident: Optional[dict] = None
    _waiting_logged: bool = False
    _reported_unrestorable: Optional[str] = None

    # -- the runner's reports

    def accepted(self, now: float, sent_at: Optional[float] = None) -> None:
        """The rung's call went out (or timed out waiting for Core): start its clock.

        `now` is when the call returned -- the Supervisor holds a start until
        Core is up -- and starts the rung's wait. `sent_at` is when it went out.
        """
        self.rung_issued_at = now
        self._waiting_logged = False
        if self.incident is not None:
            self.incident["rungs"].append(
                {"rung": self.rung, "sent_at": _iso(sent_at if sent_at is not None else now),
                 "at": _iso(now), "result": None}
            )

    def busy(self) -> None:
        """The Supervisor refused for now. Retry the same rung; it has not run."""
        self.rung_issued_at = None

    def failed(self, obs: "Observation", why: str) -> "Decision":
        """The rung could not be carried out at all (a restore swap that failed).

        Not "busy": retrying would fail the same way. Move on.
        """
        if self.incident is not None:
            self.incident["rungs"].append({"rung": self.rung, "at": _iso(obs.now), "result": f"failed: {why}"})
        return self._next_rung(obs, after=self.rung)

    # -- the decision

    def step(self, obs: Observation) -> Decision:
        if not obs.serving:
            self.serving_since = None
        elif self.serving_since is None:
            self.serving_since = obs.now
        handler = {
            WATCHING: self._watching,
            SUSPECT: self._suspect,
            ACTING: self._acting,
            STABILISING: self._stabilising,
            EXHAUSTED: self._exhausted,
        }[self.state]
        return handler(obs)

    # -- states

    def _watching(self, obs: Observation) -> Decision:
        if not obs.serving:
            self.state = SUSPECT
            self.down_since = obs.now
            return Decision(log="Home Assistant stopped answering; watching for 3 minutes")

        # D12: Core is serving, but our client update never confirmed. The
        # integration clears the pending record within a minute of loading, so
        # a record that outlives `agent_grace` of serving means it did not load.
        if (
            obs.pending_present
            and obs.restore.eligible
            and obs.now - self.serving_since >= self.timings.agent_grace
            and not self._restored_recently(obs.now)
        ):
            if self._budget_spent(obs.now):
                return Decision(
                    log="Client update never confirmed, but the automatic budget is spent; needs a human",
                    escalate=True,
                )
            self._open(obs, AGENT_SILENT)
            self.state = ACTING
            self.rung = RESTORE
            self.rung_issued_at = None
            return Decision(
                action="restore_restart",
                log=(
                    f"Home Assistant is up but client {obs.restore.to_version} never loaded; "
                    f"putting {obs.restore.from_version} back and restarting once"
                ),
                note=True,
            )

        # D12, the other half: our update never confirmed but restore is not
        # possible (no backup, unknown contract...). Report it once per
        # pending record, and leave the working Core alone.
        if (
            obs.pending_present
            and not obs.restore.eligible
            and obs.now - self.serving_since >= self.timings.agent_grace
        ):
            key = f"{obs.restore.to_version}@{obs.restore.started_at}"
            if self._reported_unrestorable != key:
                self._reported_unrestorable = key
                return Decision(
                    log=(
                        f"Client update {obs.restore.from_version} -> {obs.restore.to_version} never "
                        f"confirmed, and restore is not possible ({obs.restore.reason}); "
                        "leaving Home Assistant running; needs a human"
                    ),
                    escalate=True,
                )
        return Decision()

    def _suspect(self, obs: Observation) -> Decision:
        if obs.serving:
            self.state = WATCHING
            self.down_since = None
            return Decision(log="Home Assistant is answering again; nothing done")
        stand_down = self._stand_down_reason(obs)
        if stand_down:
            # The 3 minutes start again from zero once the Supervisor is done.
            self.down_since = obs.now
            return self._log_once(f"Standing down: {stand_down}")
        self._waiting_logged = False
        if obs.now - self.down_since < self.timings.detect:
            return Decision()

        if self._recrashed(obs.now):
            self._open(obs, CORE_DOWN)
            return self._exhaust(obs, "Home Assistant stopped again within an hour of being brought back")
        if self._budget_spent(obs.now):
            self._open(obs, CORE_DOWN)
            return self._exhaust(obs, "Three automatic recoveries in 24 hours already")

        self._open(obs, CORE_DOWN)
        return self._next_rung(obs, after=None)

    def _acting(self, obs: Observation) -> Decision:
        if self.rung_issued_at is None:
            stand_down = self._stand_down_reason(obs)
            if stand_down:
                return self._log_once(f"Waiting to {self.rung}: {stand_down}")
            return Decision(action=self._action_for(self.rung, obs), log=f"Retrying {self.rung}")

        if self._healthy(obs):
            self.state = STABILISING
            self.stable_since = obs.now
            return Decision(log=f"Home Assistant answering after {self.rung}; confirming for 2 minutes")
        if obs.now - self.rung_issued_at >= self.timings.wait_for(self.rung):
            self._result("still_down")
            return self._next_rung(obs, after=self.rung)
        return Decision()

    def _stabilising(self, obs: Observation) -> Decision:
        if not self._healthy(obs):
            self.state = ACTING
            self.stable_since = None
            return Decision(log=f"Home Assistant fell over again during the 2 minutes after {self.rung}")
        if obs.now - self.stable_since < self.timings.stable:
            return Decision()
        self._result("recovered")
        return self._close(obs, recovered_by=self.rung)

    def _exhausted(self, obs: Observation) -> Decision:
        if self._healthy(obs) and self.serving_since is not None and obs.now - self.serving_since >= self.timings.stable:
            return self._close(obs, recovered_by="other")
        return Decision()

    # -- rungs

    def _next_rung(self, obs: Observation, after: Optional[str]) -> Decision:
        order = [START, REBUILD, RESTORE]
        candidates = order if after is None else order[order.index(after) + 1:]
        skipped = []
        for rung in candidates:
            if rung == REBUILD and obs.disk_free_gb is not None and obs.disk_free_gb < self.timings.min_disk_gb:
                skipped.append(f"rebuild (only {obs.disk_free_gb:.1f} GB free)")
                continue
            if rung == RESTORE:
                if self.incident and self.incident.get("cause") != AFTER_CLIENT_UPDATE:
                    skipped.append("restore (not after our update)")
                    continue
                if not obs.restore.eligible:
                    skipped.append(f"restore ({obs.restore.reason})")
                    continue
            self.state = ACTING
            self.rung = rung
            self.rung_issued_at = None
            message = {
                START: "Starting Home Assistant",
                REBUILD: "Rebuilding Home Assistant",
                RESTORE: f"Putting back client {obs.restore.from_version} and starting Home Assistant",
            }[rung]
            if skipped:
                message += f" (skipped: {', '.join(skipped)})"
            return Decision(action=self._action_for(rung, obs), log=message, note=True)
        reason = "Every rung tried"
        if skipped:
            reason += f"; skipped: {', '.join(skipped)}"
        return self._exhaust(obs, reason)

    def _action_for(self, rung: str, obs: Observation) -> str:
        if rung == START:
            return "core_start"
        if rung == REBUILD:
            return "core_rebuild"
        if self.incident and self.incident.get("kind") == AGENT_SILENT:
            return "restore_restart"
        return "restore_start"

    # -- incident bookkeeping

    def _open(self, obs: Observation, kind: str) -> None:
        self.ladder_starts.append(obs.now)
        cause = AFTER_CLIENT_UPDATE if _after_our_update(obs.restore) else UNRELATED
        self.incident = {
            "id": str(uuid.uuid4()),
            "kind": kind,
            "cause": cause,
            "opened_at": _iso(obs.now),
            "down_since": _iso(self.down_since) if self.down_since is not None else None,
            "client_update": (
                {"from": obs.restore.from_version, "to": obs.restore.to_version}
                if cause == AFTER_CLIENT_UPDATE
                else None
            ),
            "rungs": [],
            "state": "acting",
            "closed_at": None,
            "recovered_by": None,
            "announced": False,
        }

    def _result(self, result: str) -> None:
        if self.incident and self.incident["rungs"]:
            self.incident["rungs"][-1]["result"] = result

    def _close(self, obs: Observation, recovered_by: str) -> Decision:
        self.state = WATCHING
        self.last_recovered_at = obs.now
        if recovered_by == RESTORE:
            self.last_restore_at = obs.now
        self.down_since = None
        self.rung = None
        self.rung_issued_at = None
        if self.incident is not None:
            self.incident.update(state="recovered", closed_at=_iso(obs.now), recovered_by=recovered_by)
        return Decision(log=f"Home Assistant is back (recovered by {recovered_by})", note=True)

    def _exhaust(self, obs: Observation, reason: str) -> Decision:
        self.state = EXHAUSTED
        if self.rung == RESTORE and self.rung_issued_at is not None:
            self.last_restore_at = obs.now
        self.rung = None
        self.rung_issued_at = None
        if self.incident is not None:
            self.incident.update(state="exhausted", exhausted_reason=reason)
        return Decision(log=f"Needs a human: {reason}", note=True, escalate=True)

    # -- rules

    def _healthy(self, obs: Observation) -> bool:
        if not obs.serving:
            return False
        if self.incident and self.incident.get("kind") == AGENT_SILENT:
            # Back means the integration loaded and cleared its pending record.
            return not obs.pending_present
        return True

    def _stand_down_reason(self, obs: Observation) -> Optional[str]:
        if obs.blocking_job:
            return f"the Supervisor is running {obs.blocking_job}"
        if obs.supervisor_running is False:
            return "the Supervisor is not in its running state"
        return None

    def _recrashed(self, now: float) -> bool:
        return self.last_recovered_at is not None and now - self.last_recovered_at < self.timings.rearm

    def _restored_recently(self, now: float) -> bool:
        return self.last_restore_at is not None and now - self.last_restore_at < self.timings.rearm

    def _budget_spent(self, now: float) -> bool:
        while self.ladder_starts and now - self.ladder_starts[0] >= self.timings.window:
            self.ladder_starts.popleft()
        return len(self.ladder_starts) >= self.timings.max_ladders

    def _log_once(self, message: str) -> Decision:
        if self._waiting_logged:
            return Decision()
        self._waiting_logged = True
        return Decision(log=message)


def _after_our_update(check: RestoreCheck) -> bool:
    """Our update is the suspect: a recent pending record, eligible or not."""
    return check.started_at is not None and check.reason not in ("update_too_old", "update_in_future")


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
