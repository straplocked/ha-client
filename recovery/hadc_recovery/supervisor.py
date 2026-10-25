"""The Supervisor calls this add-on is allowed to make -- and no others.

There is deliberately no public "call any path" method. Every call the add-on
can make is a named method below, so the allowlist is the module itself and a
test can read it. The rule it enforces: Core and this add-on only. Nothing here
can reach another add-on, the store, backups, or an update of anything.

Phase 0 facts this relies on (docs/technical/core-watchdog-addon.md in the
Dispatch repo):

- `/core/*` needs the `homeassistant` role; every `*/info` needs `default`;
  `/host/logs/*` needs `manager`. This add-on ships with `homeassistant`, so
  `host_journal()` is expected to come back 403 and that is reported, not
  treated as a fault.
- `start`/`restart`/`rebuild` are refused while the Supervisor is not
  `running`, and `restart`/`rebuild` during an offline database migration
  unless sent with `force`. We never send `force`; a refusal means "wait".
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

_LOGGER = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor"

# Words the Supervisor uses when it refuses for now rather than failing.
_BUSY_MARKERS = (
    "migration",
    "not running",
    "freeze",
    "frozen",
    "startup",
    "setup",
    "in progress",
    "jobcondition",
    "system is not",
)

# Supervisor jobs that mean "Core is being worked on on purpose: stand down".
# An allowlist, never "any job touching Core": in the 2026-10-24 failure state
# `home_assistant_core_restart_after_problem` sits done:false for ever.
STAND_DOWN_JOBS = frozenset(
    {
        "home_assistant_core_update",
        "backup_manager_full_backup",
        "backup_manager_partial_backup",
        "backup_manager_full_restore",
        "backup_manager_partial_restore",
    }
)


@dataclass
class Reply:
    status: int
    body: Any
    text: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def message(self) -> str:
        if isinstance(self.body, dict):
            return str(self.body.get("message") or "")
        return self.text[:300]

    @property
    def busy(self) -> bool:
        """Refused for now (try the same rung again later), not failed."""
        if self.ok:
            return False
        lowered = self.message.lower()
        return any(marker in lowered for marker in _BUSY_MARKERS)


class SupervisorClient:
    def __init__(self, token: str, base_url: str = SUPERVISOR_URL, opener=None) -> None:
        self._token = token
        self._base = base_url.rstrip("/")
        self._open = opener or urllib.request.urlopen

    # -- reads

    def info(self) -> Reply:
        return self._call("GET", "/info")

    def core_info(self) -> Reply:
        return self._call("GET", "/core/info")

    def core_stats(self) -> Reply:
        return self._call("GET", "/core/stats")

    def jobs_info(self) -> Reply:
        return self._call("GET", "/jobs/info")

    def host_info(self) -> Reply:
        return self._call("GET", "/host/info")

    def self_info(self) -> Reply:
        return self._call("GET", "/addons/self/info")

    def core_logs(self, lines: int = 200) -> Reply:
        return self._call("GET", f"/core/logs/latest?lines={_lines(lines)}", text=True)

    def host_journal(self, lines: int = 200) -> Reply:
        """Core's container journal; where a pre-Python segfault is recorded."""
        return self._call(
            "GET", f"/host/logs/identifiers/homeassistant?lines={_lines(lines)}", text=True
        )

    # -- the ladder

    def core_start(self) -> Reply:
        return self._call("POST", "/core/start", timeout=330)

    def core_restart(self) -> Reply:
        return self._call("POST", "/core/restart", timeout=330)

    def core_rebuild(self) -> Reply:
        return self._call("POST", "/core/rebuild", timeout=900)

    # -- itself

    def enable_own_watchdog(self) -> Reply:
        """The Supervisor's per-add-on watchdog is off by default; switch ours on."""
        return self._call("POST", "/addons/self/options", payload={"watchdog": True})

    # -- derived answers

    def supervisor_running(self) -> Optional[bool]:
        reply = self.info()
        if not reply.ok or not isinstance(reply.body, dict):
            return None
        data = reply.body.get("data") or {}
        state = data.get("state")
        return None if state is None else state == "running"

    def container_running(self) -> Optional[bool]:
        """True/False when the Supervisor can say; None when it cannot."""
        reply = self.core_stats()
        if reply.ok:
            return True
        if reply.status in (400, 404, 500):
            return False
        return None

    def blocking_job(self) -> Optional[str]:
        reply = self.jobs_info()
        if not reply.ok or not isinstance(reply.body, dict):
            return None
        return find_blocking_job((reply.body.get("data") or {}).get("jobs") or [])

    def disk_free_gb(self) -> Optional[float]:
        reply = self.host_info()
        if not reply.ok or not isinstance(reply.body, dict):
            return None
        value = (reply.body.get("data") or {}).get("disk_free")
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # -- the one private transport

    def _call(
        self,
        method: str,
        path: str,
        payload: Optional[dict] = None,
        timeout: float = 20,
        text: bool = False,
    ) -> Reply:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self._base + path, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if text:
            request.add_header("Accept", "text/plain")
        try:
            with self._open(request, timeout=timeout) as response:
                raw = response.read()
                return _reply(response.status, raw, text)
        except urllib.error.HTTPError as err:
            return _reply(err.code, err.read() or b"", text)
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            # A timeout on start/rebuild is normal: the Supervisor holds the
            # request until Core is up. The caller treats it as "issued".
            return Reply(status=0, body=None, text=str(err))


def find_blocking_job(jobs: list) -> Optional[str]:
    """The first allowlisted job not yet done, searching child jobs too."""
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if job.get("name") in STAND_DOWN_JOBS and job.get("done") is not True:
            return job["name"]
        child = find_blocking_job(job.get("child_jobs") or [])
        if child:
            return child
    return None


def _reply(status: int, raw: bytes, text: bool) -> Reply:
    decoded = raw.decode("utf-8", errors="replace")
    if text:
        return Reply(status=status, body=None, text=decoded)
    try:
        return Reply(status=status, body=json.loads(decoded) if decoded else None, text=decoded)
    except ValueError:
        return Reply(status=status, body=None, text=decoded)


def _lines(value: int) -> int:
    return max(1, min(int(value), 1000))
