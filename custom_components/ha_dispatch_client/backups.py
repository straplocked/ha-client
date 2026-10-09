"""Backup reporting: tell the server about this installation's own backups.

Every dashboard can say "last backup 2 days ago". The server goes further --
it notices a schedule that has gone quiet and an archive that has quietly
shrunk -- but only if something tells it what was backed up. This module is
that something.

The source is Home Assistant's own backup manager (2025.1 and later), which
lists every backup on every agent: the local disk, the Supervisor's /backup,
Nabu Casa cloud, and any other target. One backup appears once however many
agents hold a copy of it.

What gets reported, and under which slug:

- A backup made by Home Assistant's automatic backup schedule -> `automatic`.
- Any other *full* backup -> `manual`. People who schedule backups with their
  own automation land here, and their nightly full is as comparable night to
  night as the built-in schedule's.
- Partial manual backups are not reported. A one-off snapshot of a single
  add-on next to a 2 GB full backup would read as a 99.9% size collapse.
- Neither are the pre-update backups a remote update run takes (named with
  UPDATE_RUN_BACKUP_PREFIX). Those belong to the run and are reported on it.

The server does not de-duplicate, so the backup ids already reported are kept
in Home Assistant's storage, per installation id: a restart neither resends
history nor re-hashes archives, and a re-enrolled installation, which is a new
record on the server, gets its history again.

A checksum (`sha256:<hex>`) is sent for the newest new backup of each slug,
read from a copy on this machine. Cloud copies are never downloaded to hash
them. In steady state every backup is the newest when it is first seen, so
every one gets a checksum; only the first scan's backfill goes without for all
but the latest.

Nothing here may break metrics. Every failure is logged and swallowed, and a
scan runs as a background task so hashing a large archive never holds up the
coordinator's 60 s poll.

Full design: docs/technical/backup-reporting.md
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import timezone
from typing import Any, Dict, List, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api_client import BackupReportingUnavailable
from .const import (
    BACKUP_AGENT_CORE_LOCAL,
    BACKUP_AGENT_SUPERVISOR_LOCAL,
    BACKUP_BACKFILL_PER_SLUG,
    BACKUP_BATCH_MAX,
    BACKUP_SCAN_INTERVAL,
    BACKUP_SLUG_AUTOMATIC,
    BACKUP_SLUG_MANUAL,
    BACKUP_STORAGE_KEY,
    BACKUP_STORAGE_VERSION,
    BACKUP_TYPE_FULL,
    BACKUP_TYPE_PARTIAL,
    UPDATE_RUN_BACKUP_PREFIX,
)

_LOGGER = logging.getLogger(__name__)

# Home Assistant keeps its BackupManager under hass.data["backup"] (the
# DATA_MANAGER HassKey is a str subclass). Read by name rather than imported, so
# this module loads on a Home Assistant without the backup integration.
_MANAGER_KEY = "backup"

# Field lengths the server enforces; trimmed here so one long name cannot cost
# the whole batch a 422.
_MAX_NAME = 255

_HASH_CHUNK = 4 * 1024 * 1024


def backup_manager(hass: HomeAssistant):
    """Home Assistant's backup manager, or None where there is not one."""
    manager = hass.data.get(_MANAGER_KEY)
    if manager is None or not hasattr(manager, "async_get_backups"):
        return None
    return manager


def classify(backup) -> Optional[tuple[str, str]]:
    """The (slug, type) a backup is reported under, or None to leave it out."""
    name = getattr(backup, "name", "") or ""
    if name.startswith(UPDATE_RUN_BACKUP_PREFIX):
        return None

    agents = getattr(backup, "agents", None) or {}
    supervised = any(str(agent_id).startswith("hassio.") for agent_id in agents)

    # On a supervised install a full backup also carries add-ons or folders; a
    # backup of just the Home Assistant config is what Core's own pre-update
    # backup looks like there. On Core-only there is nothing else to include.
    full = bool(
        getattr(backup, "homeassistant_included", False)
        and getattr(backup, "database_included", False)
        and (
            not supervised
            or getattr(backup, "addons", None)
            or getattr(backup, "folders", None)
        )
    )
    kind = BACKUP_TYPE_FULL if full else BACKUP_TYPE_PARTIAL

    if getattr(backup, "with_automatic_settings", False):
        return BACKUP_SLUG_AUTOMATIC, kind
    if full:
        return BACKUP_SLUG_MANUAL, kind
    return None


def backup_size(backup) -> Optional[int]:
    """Archive size in bytes: the copy on this machine, else the largest one."""
    agents = getattr(backup, "agents", None) or {}
    for agent_id in (BACKUP_AGENT_SUPERVISOR_LOCAL, BACKUP_AGENT_CORE_LOCAL):
        status = agents.get(agent_id)
        if status is not None and isinstance(getattr(status, "size", None), int):
            return status.size

    sizes = [
        status.size
        for status in agents.values()
        if isinstance(getattr(status, "size", None), int)
    ]
    return max(sizes) if sizes else None


def completed_at(backup):
    """The backup's timestamp as an aware datetime, or None if unreadable."""
    parsed = dt_util.parse_datetime(str(getattr(backup, "date", "") or ""))
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class HADispatchBackups:
    """Finds new backups and reports them, on a slow cadence and on completion."""

    def __init__(self, hass: HomeAssistant, coordinator) -> None:
        """Initialise. Nothing touches the network until the first tick."""
        self.hass = hass
        self.coordinator = coordinator
        self.available = True

        self._store = Store(hass, BACKUP_STORAGE_VERSION, BACKUP_STORAGE_KEY)
        self._state: Optional[Dict[str, Any]] = None

        self._scanned_at = None
        # Set when Home Assistant says a backup just finished, so the next tick
        # scans instead of waiting out the interval.
        self._due = False
        self._running = False
        self._unsubscribe = None

    @property
    def installation_id(self):
        """The installation id, which re-enrolment may have changed."""
        return self.coordinator.installation_id

    # ------------------------------------------------------------ schedule --

    def async_tick(self) -> None:
        """Called on every coordinator poll. Cheap unless a scan is due."""
        if not self.available:
            return

        self._subscribe()

        if self._running or not self._is_due():
            return

        self._running = True
        self._create_task(self._async_scan_guarded())

    def _is_due(self) -> bool:
        if self._due or self._scanned_at is None:
            return True
        elapsed = (dt_util.utcnow() - self._scanned_at).total_seconds()
        return elapsed >= BACKUP_SCAN_INTERVAL

    def _create_task(self, coro):
        creator = getattr(self.hass, "async_create_background_task", None)
        if creator is not None:
            return creator(coro, "ha_dispatch_client backup report")
        return self.hass.async_create_task(coro)

    def _subscribe(self) -> None:
        """Listen for finished backups, once the backup manager exists.

        The backup integration may load after this one, so this is retried on
        every tick until it takes.
        """
        if self._unsubscribe is not None:
            return
        manager = backup_manager(self.hass)
        subscribe = getattr(manager, "async_subscribe_events", None)
        if subscribe is None:
            return
        try:
            self._unsubscribe = subscribe(self._on_backup_event)
        except Exception as err:  # noqa: BLE001 - an HA internal, guard it
            _LOGGER.debug("Could not subscribe to backup events: %s", err)

    def _on_backup_event(self, event) -> None:
        # CreateBackupEvent(state=CreateBackupState.COMPLETED). Both are
        # StrEnums, so comparing as strings needs no import.
        if (
            str(getattr(event, "manager_state", "")) == "create_backup"
            and str(getattr(event, "state", "")) == "completed"
        ):
            self._due = True

    def async_unload(self) -> None:
        """Stop listening for backup events."""
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            except Exception:  # noqa: BLE001 - already gone is fine
                pass
            self._unsubscribe = None

    # ---------------------------------------------------------------- scan --

    async def _async_scan_guarded(self) -> None:
        try:
            await self.async_scan()
        except Exception as err:  # noqa: BLE001 - must never break the poll
            _LOGGER.warning("Backup reporting failed: %s", err)
        finally:
            self._running = False

    async def async_scan(self) -> int:
        """Report every backup the server has not heard about. Returns the count.

        The scan is stamped only once it has finished without a transport
        failure, so a server blip is retried on the next tick.
        """
        self._due = False

        manager = backup_manager(self.hass)
        if manager is None:
            self._scanned_at = dt_util.utcnow()
            return 0

        result = await manager.async_get_backups()
        if not isinstance(result, tuple):
            # Before 2025.1 the manager returned a bare dict of a different
            # shape. Not worth supporting: say nothing, as an older agent would.
            _LOGGER.debug("Backup manager predates 2025.1; not reporting backups")
            self.available = False
            return 0
        backups, _agent_errors = result

        known = await self._async_known()
        first_scan = known is None
        known = known or set()

        fresh = []
        for backup_id, backup in (backups or {}).items():
            if backup_id in known:
                continue
            classified = classify(backup)
            when = completed_at(backup)
            if classified is None or when is None:
                continue
            fresh.append((when, backup_id, backup, classified))

        fresh.sort(key=lambda row: row[0])

        skipped: List[str] = []
        if first_scan:
            fresh, skipped = _limit_backfill(fresh)

        newest = {}
        for row in fresh:
            newest[row[3][0]] = row[1]
        hash_ids = set(newest.values())

        reports = []
        for when, backup_id, backup, (slug, kind) in fresh:
            entry: Dict[str, Any] = {
                "slug": slug,
                "name": (getattr(backup, "name", "") or slug)[:_MAX_NAME],
                "type": kind,
                "completed_at": when.isoformat(),
            }
            size = backup_size(backup)
            if size is not None:
                entry["size_bytes"] = size
            if backup_id in hash_ids:
                checksum = await self._async_checksum(manager, backup_id, backup)
                if checksum:
                    entry["checksum"] = checksum
            reports.append((backup_id, entry))

        reported: List[str] = []
        try:
            for start in range(0, len(reports), BACKUP_BATCH_MAX):
                chunk = reports[start:start + BACKUP_BATCH_MAX]
                await self.coordinator.api_client.report_backups(
                    installation_id=self.installation_id,
                    backups=[entry for _, entry in chunk],
                )
                reported.extend(backup_id for backup_id, _ in chunk)
        except BackupReportingUnavailable as err:
            _LOGGER.info("Server does not accept backup reports; stopping: %s", err)
            self.available = False
            return len(reported)
        except Exception as err:  # noqa: BLE001 - transport; retry next tick
            _LOGGER.warning("Could not report backups: %s", err)
            # Keep what did land, and the backfill's deliberate drops, so the
            # retry sends only the rest.
            await self._async_remember(set(backups), known, reported + skipped)
            return len(reported)

        await self._async_remember(set(backups), known, reported + skipped)
        self._scanned_at = dt_util.utcnow()
        if reported:
            _LOGGER.debug("Reported %s backups", len(reported))
        return len(reported)

    # ------------------------------------------------------------- storage --

    async def _async_known(self) -> Optional[set]:
        """Ids already reported for this installation; None if never scanned."""
        if self._state is None:
            self._state = await self._store.async_load() or {}
        if self._state.get("installation_id") != str(self.installation_id):
            return None
        return set(self._state.get("reported") or [])

    async def _async_remember(self, present: set, known: set, added: List[str]) -> None:
        # Pruned to what Home Assistant still lists: retention deletes old
        # backups, and their ids never come back.
        reported = sorted((known | set(added)) & present)
        self._state = {
            "installation_id": str(self.installation_id),
            "reported": reported,
        }
        await self._store.async_save(self._state)

    # ------------------------------------------------------------ checksum --

    async def _async_checksum(self, manager, backup_id: str, backup) -> Optional[str]:
        """sha256 of a copy on this machine, or None if there is not one."""
        agents = getattr(backup, "agents", None) or {}
        try:
            local_agents = getattr(manager, "local_backup_agents", None) or {}
            core_local = local_agents.get(BACKUP_AGENT_CORE_LOCAL)
            if BACKUP_AGENT_CORE_LOCAL in agents and core_local is not None:
                path = core_local.get_backup_path(backup_id)
                digest = await self.hass.async_add_executor_job(_hash_file, path)
                return f"sha256:{digest}"

            supervisor_local = (getattr(manager, "backup_agents", None) or {}).get(
                BACKUP_AGENT_SUPERVISOR_LOCAL
            )
            if BACKUP_AGENT_SUPERVISOR_LOCAL in agents and supervisor_local is not None:
                stream = await supervisor_local.async_download_backup(backup_id)
                sha = hashlib.sha256()
                async for chunk in stream:
                    # Off the event loop: a few GB of hashing on a Green's
                    # cores is seconds of CPU.
                    await self.hass.async_add_executor_job(sha.update, chunk)
                return f"sha256:{sha.hexdigest()}"
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as err:  # noqa: BLE001 - report without it
            _LOGGER.debug("Could not checksum backup %s: %s", backup_id, err)
        return None


def _limit_backfill(fresh: list) -> tuple[list, List[str]]:
    """Keep the newest BACKUP_BACKFILL_PER_SLUG per slug; return (kept, dropped ids)."""
    counts: Dict[str, int] = {}
    kept = []
    dropped: List[str] = []
    for row in reversed(fresh):
        slug = row[3][0]
        counts[slug] = counts.get(slug, 0) + 1
        if counts[slug] <= BACKUP_BACKFILL_PER_SLUG:
            kept.append(row)
        else:
            dropped.append(row[1])
    kept.reverse()
    return kept, dropped


def _hash_file(path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            sha.update(chunk)
    return sha.hexdigest()
