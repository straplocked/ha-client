"""Putting the previous client back -- the only file write this add-on makes.

Never touched: configuration.yaml, .storage (read one file, write none),
secrets, any other integration. Writes are confined to two places, checked on
every write by `_writable()`:

- `custom_components/ha_dispatch_client/` -- only the swap itself
- `.ha_dispatch_client/recovery/` -- staging, the one displaced tree, notes

No backup, no restore. The backup is copied, never moved, so the updater's
own backups survive a restore and a failed swap can be retried.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

DOMAIN = "ha_dispatch_client"
PENDING_RECORD = Path(".storage") / "ha_dispatch_client.update"
WORK_DIR = Path(".ha_dispatch_client")
BACKUPS_DIR = WORK_DIR / "backups"
CONTRACT_FILE = WORK_DIR / "contract.json"
RECOVERY_DIR = WORK_DIR / "recovery"
LIVE_DIR = Path("custom_components") / DOMAIN

# The file layout this add-on knows how to move around in. An integration that
# writes no contract.json predates the file (1.9.x); its layout is contract 1,
# and the eligibility checks below verify that layout rather than assume it.
KNOWN_CONTRACTS = frozenset({1})
RESTORE_MAX_AGE = timedelta(hours=6)

_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}$")


class RefusedWrite(RuntimeError):
    """A write outside the allowlist was attempted. Always a bug; never caught."""


@dataclass(frozen=True)
class RestoreCheck:
    eligible: bool
    reason: str
    from_version: Optional[str] = None
    to_version: Optional[str] = None
    started_at: Optional[datetime] = None


def read_pending(config_root: Path) -> Optional[dict]:
    """The client updater's pending record, or None when there is none."""
    path = Path(config_root) / PENDING_RECORD
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"_unreadable": True}
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else {"_unreadable": True}


def pending_present(config_root: Path) -> bool:
    return (Path(config_root) / PENDING_RECORD).is_file()


def check(config_root: Path, now: datetime, max_age: timedelta = RESTORE_MAX_AGE) -> RestoreCheck:
    """May the previous client be put back? Every condition must hold."""
    root = Path(config_root)
    record = read_pending(root)
    if record is None:
        return RestoreCheck(False, "no_pending_record")
    if record.get("_unreadable"):
        return RestoreCheck(False, "pending_record_unreadable")

    from_version = record.get("from_version")
    to_version = record.get("to_version")
    if not _safe_version(from_version) or not _safe_version(to_version):
        return RestoreCheck(False, "pending_record_bad_version")

    started_at = _parse_time(record.get("started_at"))
    if started_at is None:
        return RestoreCheck(False, "pending_record_no_start_time", from_version, to_version)
    if now - started_at > max_age:
        return RestoreCheck(False, "update_too_old", from_version, to_version, started_at)
    if started_at - now > timedelta(minutes=5):
        return RestoreCheck(False, "update_in_future", from_version, to_version, started_at)

    contract = _read_contract(root)
    if contract not in KNOWN_CONTRACTS:
        return RestoreCheck(False, "contract_unknown", from_version, to_version, started_at)

    backup = root / BACKUPS_DIR / from_version
    reason = _manifest_problem(backup, from_version)
    if reason:
        return RestoreCheck(False, reason, from_version, to_version, started_at)

    return RestoreCheck(True, "eligible", from_version, to_version, started_at)


def swap(config_root: Path, from_version: str, to_version: str) -> dict:
    """Copy backups/<from> into place; keep the displaced <to> tree aside.

    Raises on any failure, after trying to put the displaced tree back.
    """
    root = Path(config_root)
    if not _safe_version(from_version) or not _safe_version(to_version):
        raise ValueError("unsafe version string")

    backup = root / BACKUPS_DIR / from_version
    problem = _manifest_problem(backup, from_version)
    if problem:
        raise FileNotFoundError(f"no usable backup of {from_version}: {problem}")

    live = root / LIVE_DIR
    staging = root / RECOVERY_DIR / "staging" / from_version
    displaced_root = root / RECOVERY_DIR / "displaced"
    displaced = displaced_root / to_version

    _rmtree(root, staging)
    _writable(root, staging.parent).mkdir(parents=True, exist_ok=True)
    shutil.copytree(backup, _writable(root, staging))
    problem = _manifest_problem(staging, from_version)
    if problem:
        _rmtree(root, staging)
        raise RuntimeError(f"staged copy failed verification: {problem}")

    # Exactly one displaced tree is kept, for forensics.
    _rmtree(root, displaced_root)
    _writable(root, displaced_root).mkdir(parents=True, exist_ok=True)
    moved_live = False
    if live.exists():
        shutil.move(str(_writable(root, live)), str(_writable(root, displaced)))
        moved_live = True
    try:
        shutil.move(str(_writable(root, staging)), str(_writable(root, live)))
    except OSError:
        if moved_live and not live.exists():
            shutil.move(str(displaced), str(live))
        raise
    return {
        "restored": from_version,
        "displaced": to_version if moved_live else None,
        "displaced_path": str(displaced) if moved_live else None,
    }


def write_note(config_root: Path, incident_id: str, note: dict) -> Path:
    """An incident note for the integration to announce once Core is back."""
    root = Path(config_root)
    if not re.fullmatch(r"[0-9a-f-]{8,40}", incident_id):
        raise ValueError("unsafe incident id")
    folder = _writable(root, root / RECOVERY_DIR / "incidents")
    folder.mkdir(parents=True, exist_ok=True)
    path = _writable(root, folder / f"{incident_id}.json")
    tmp = _writable(root, folder / f".{incident_id}.json.tmp")
    tmp.write_text(json.dumps(note, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)
    return path


# -- guards


def _writable(root: Path, path: Path) -> Path:
    """Return `path` if it is inside a writable area; raise RefusedWrite if not."""
    resolved = Path(path).resolve()
    allowed = [(Path(root) / LIVE_DIR).resolve(), (Path(root) / RECOVERY_DIR).resolve()]
    for area in allowed:
        if resolved == area or area in resolved.parents:
            return Path(path)
    raise RefusedWrite(f"refusing to write outside the allowlist: {path}")


def _rmtree(root: Path, path: Path) -> None:
    if Path(path).exists():
        shutil.rmtree(_writable(root, path))


def _manifest_problem(tree: Path, version: str) -> Optional[str]:
    manifest = Path(tree) / "manifest.json"
    if not Path(tree).is_dir():
        return "no_backup"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "backup_manifest_unreadable"
    if data.get("domain") != DOMAIN:
        return "backup_manifest_wrong_domain"
    if str(data.get("version")) != str(version):
        return "backup_manifest_version_mismatch"
    return None


def _read_contract(root: Path) -> Optional[int]:
    path = root / CONTRACT_FILE
    if not path.is_file():
        return 1
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("watchdog_contract")
        return int(value)
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _safe_version(value) -> bool:
    return isinstance(value, str) and bool(_VERSION.match(value)) and ".." not in value


def _parse_time(value) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
