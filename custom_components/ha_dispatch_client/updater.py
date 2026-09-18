"""Signed self-update installer for the HA Dispatch client.

This module replaces the integration's own files on disk and then restarts Home
Assistant. That is, by construction, remote code execution on this machine, so
every step here assumes the network -- and the Dispatch server itself -- may be
hostile. The only thing trusted is an Ed25519 signature checked against a key
pinned in const.py. The server decides whether and when an update is offered;
it never decides what code runs.

Design and threat model: docs/technical/self-update.md
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import zipfile
from datetime import datetime, time, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration

from .const import (
    DOMAIN,
    RELEASE_ALLOWED_HOSTS,
    RELEASE_SIGNING_KEYS,
    UPDATE_BACKUP_KEEP,
    UPDATE_BACKUP_SUBDIR,
    UPDATE_DISK_HEADROOM_FACTOR,
    UPDATE_MAX_ARCHIVE_BYTES,
    UPDATE_MAX_UNCOMPRESSED_BYTES,
    UPDATE_STAGING_SUBDIR,
    UPDATE_STORAGE_KEY,
    UPDATE_STORAGE_VERSION,
    UPDATE_WORK_DIR,
)

_LOGGER = logging.getLogger(__name__)

PHASE_PREFLIGHT = "preflight"
PHASE_DOWNLOAD = "download"
PHASE_VERIFY = "verify"
PHASE_VALIDATE = "validate"
PHASE_SWAP = "swap"
PHASE_RESTART = "restart"
PHASE_CONFIRM = "confirm"

ARCHIVE_NAME = "ha_dispatch_client.zip"
MANIFEST_NAME = "manifest.json"


class UpdateError(HomeAssistantError):
    """Raised when an update cannot be installed.

    Carries the phase it failed in so the server-side rollout can distinguish a
    flaky download from a signature that did not verify -- the first is worth
    retrying, the second means stop the rollout now.
    """

    def __init__(self, message: str, phase: str = PHASE_PREFLIGHT):
        """Initialize with a human-readable message and the failing phase."""
        super().__init__(message)
        self.phase = phase


# --- Version handling -------------------------------------------------------

# Deliberately not awesomeversion (which Home Assistant bundles): keeping this
# module importable with nothing but the standard library and cryptography is
# what lets the update path be tested without a Home Assistant install, in
# keeping with the rest of tests/.
_VERSION_RE = re.compile(
    r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?"
    r"(?:[.\-]?(a|alpha|b|beta|rc)\.?(\d+)?)?$",
    re.IGNORECASE,
)

_PRERELEASE_RANK = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "rc": 2}


def parse_version(value: Any) -> Optional[tuple]:
    """Parse a dotted version into a comparable tuple, or None if unparseable.

    Returns (major, minor, patch, stage, stage_number) where a final release
    sorts above any of its prereleases.
    """
    if value is None:
        return None
    match = _VERSION_RE.match(str(value).strip())
    if not match:
        return None

    major, minor, patch, stage, stage_num = match.groups()
    if stage is None:
        # 3 sorts above every prerelease rank, so 1.5.0 > 1.5.0rc9.
        return (int(major), int(minor or 0), int(patch or 0), 3, 0)
    return (
        int(major),
        int(minor or 0),
        int(patch or 0),
        _PRERELEASE_RANK[stage.lower()],
        int(stage_num or 0),
    )


def is_newer(candidate: Any, installed: Any) -> bool:
    """Return True when candidate is a strictly newer version than installed.

    Unparseable input returns False. Refusing to act on a version we cannot
    read is the safe direction: the worst case is an update that needs doing by
    hand, rather than an unintended downgrade.
    """
    left, right = parse_version(candidate), parse_version(installed)
    if left is None or right is None:
        return False
    return left > right


def in_update_window(now: datetime, window: Optional[str]) -> bool:
    """Return True when now falls inside a "HH:MM-HH:MM" local-time window.

    No window means always. A window that wraps past midnight ("23:00-02:00")
    is treated as spanning the boundary. An unparseable window returns True --
    a malformed value pushed from the server should not silently pin an
    installation to a version forever.
    """
    if not window:
        return True

    try:
        start_raw, end_raw = str(window).split("-", 1)
        start_h, start_m = (int(part) for part in start_raw.strip().split(":", 1))
        end_h, end_m = (int(part) for part in end_raw.strip().split(":", 1))
        start = time(start_h, start_m)
        end = time(end_h, end_m)
    except (AttributeError, TypeError, ValueError):
        _LOGGER.warning("Ignoring unparseable update_window: %r", window)
        return True

    current = now.time()
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


# --- Verification -----------------------------------------------------------


def verify_digest(payload: bytes, expected_sha256: Optional[str]) -> None:
    """Check the archive digest. Raises UpdateError on mismatch."""
    if not expected_sha256:
        raise UpdateError("Release payload carried no sha256", PHASE_VERIFY)

    actual = hashlib.sha256(payload).hexdigest()
    if not _constant_time_equal(actual, str(expected_sha256).strip().lower()):
        raise UpdateError(
            f"Archive digest mismatch: expected {expected_sha256}, got {actual}",
            PHASE_VERIFY,
        )


def _constant_time_equal(left: str, right: str) -> bool:
    """Compare two hex digests without an early exit."""
    return hmac.compare_digest(left, right)


def verify_signature(
    payload: bytes,
    signature_b64: Optional[str],
    key_id: Optional[str],
    keys: Optional[Dict[str, str]] = None,
) -> None:
    """Verify an Ed25519 signature over the raw archive bytes.

    This is the load-bearing check in the whole feature. There is deliberately
    no bypass, no "unsigned but trusted server" path, and no fallback to
    digest-only verification: the digest arrives over the same channel as the
    archive URL, so on its own it proves nothing about origin.
    """
    trusted = RELEASE_SIGNING_KEYS if keys is None else keys

    if not trusted:
        raise UpdateError(
            "No release signing keys are pinned in this build, so no update can "
            "be verified. This is the safe default -- see "
            "docs/technical/self-update.md",
            PHASE_VERIFY,
        )
    if not signature_b64:
        raise UpdateError("Release payload carried no signature", PHASE_VERIFY)
    if not key_id:
        raise UpdateError("Release payload carried no key_id", PHASE_VERIFY)
    if key_id not in trusted:
        raise UpdateError(
            f"Release signed by unknown key_id {key_id!r}; this client trusts "
            f"{sorted(trusted)}",
            PHASE_VERIFY,
        )

    # Imported here rather than at module scope so a missing or broken
    # cryptography install degrades to "updates do not work" instead of "the
    # integration does not load at all".
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except ImportError as err:
        raise UpdateError(
            f"cryptography is unavailable, cannot verify the release: {err}",
            PHASE_VERIFY,
        ) from err

    try:
        public_bytes = base64.b64decode(trusted[key_id], validate=True)
        signature = base64.b64decode(signature_b64, validate=True)
    except (ValueError, TypeError) as err:
        raise UpdateError(f"Malformed key or signature encoding: {err}", PHASE_VERIFY) from err

    try:
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, payload)
    except InvalidSignature as err:
        raise UpdateError(
            f"Signature verification FAILED for key_id {key_id!r}. The archive "
            "was not signed by a trusted key -- refusing to install.",
            PHASE_VERIFY,
        ) from err
    except ValueError as err:
        raise UpdateError(f"Invalid Ed25519 key material: {err}", PHASE_VERIFY) from err

    _LOGGER.debug("Release signature verified against key_id %s", key_id)


# --- Archive validation -----------------------------------------------------


def _unsafe_member_reason(info: zipfile.ZipInfo) -> Optional[str]:
    """Return why this archive member must not be extracted, or None."""
    name = info.filename
    if not name:
        return "empty member name"

    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        return f"absolute path: {name}"
    if re.match(r"^[A-Za-z]:", normalized):
        return f"drive-qualified path: {name}"
    if any(part == ".." for part in PurePosixPath(normalized).parts):
        return f"path traversal: {name}"
    if (info.external_attr >> 16) & 0o170000 == 0o120000:
        return f"symlink: {name}"
    return None


def validate_archive(
    archive_path: os.PathLike,
    *,
    expected_version: Optional[str] = None,
    expected_domain: str = DOMAIN,
    max_uncompressed: int = UPDATE_MAX_UNCOMPRESSED_BYTES,
) -> Dict[str, Any]:
    """Validate a release archive and return its parsed manifest.

    Runs entirely against the downloaded file, before anything is written to
    custom_components/ -- the working copy is still live and untouched while
    this runs. Python's zipfile does not recreate symlinks and strips leading
    separators on extract, so some of these checks duplicate its behaviour;
    they are worth keeping because they fail the update early with a clear
    reason, and because they do not depend on that behaviour holding.
    """
    path = Path(archive_path)
    if not zipfile.is_zipfile(path):
        raise UpdateError("Downloaded file is not a zip archive", PHASE_VALIDATE)

    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if not members:
            raise UpdateError("Release archive is empty", PHASE_VALIDATE)

        total = 0
        for info in members:
            reason = _unsafe_member_reason(info)
            if reason is not None:
                raise UpdateError(f"Refusing unsafe archive member -- {reason}", PHASE_VALIDATE)
            total += info.file_size
            if total > max_uncompressed:
                raise UpdateError(
                    f"Archive expands to more than {max_uncompressed} bytes", PHASE_VALIDATE
                )

        names = {info.filename.replace("\\", "/") for info in members}
        if MANIFEST_NAME not in names:
            # The zip root holds the integration files directly -- the layout
            # HACS expects for a release asset, so one artifact serves both
            # install paths. A nested custom_components/ tree is the common
            # packaging mistake, so name it explicitly.
            hint = ""
            if any(name.startswith("custom_components/") for name in names):
                hint = (
                    " (found a nested custom_components/ tree; the integration "
                    "files must sit at the archive root)"
                )
            raise UpdateError(f"Archive has no {MANIFEST_NAME} at its root{hint}", PHASE_VALIDATE)

        try:
            manifest = json.loads(archive.read(MANIFEST_NAME))
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError) as err:
            raise UpdateError(f"Archive manifest is unreadable: {err}", PHASE_VALIDATE) from err

    if not isinstance(manifest, dict):
        raise UpdateError("Archive manifest is not a JSON object", PHASE_VALIDATE)

    domain = manifest.get("domain")
    if domain != expected_domain:
        raise UpdateError(
            f"Archive is for domain {domain!r}, expected {expected_domain!r}", PHASE_VALIDATE
        )

    version = manifest.get("version")
    if expected_version is not None and str(version) != str(expected_version):
        raise UpdateError(
            f"Archive contains version {version!r}, but {expected_version!r} was offered",
            PHASE_VALIDATE,
        )

    return manifest


# --- Filesystem swap --------------------------------------------------------


def swap_integration_dir(
    live_dir: os.PathLike,
    staged_dir: os.PathLike,
    backup_dir: os.PathLike,
) -> None:
    """Move the staged copy into place, keeping the old one as a backup.

    Synchronous and blocking; callers run it in an executor. On failure the
    previous directory is moved back, so a swap that goes wrong leaves the
    working version running rather than no version at all.
    """
    live, staged, backup = Path(live_dir), Path(staged_dir), Path(backup_dir)

    if not staged.is_dir():
        raise UpdateError(f"Staged directory {staged} is missing", PHASE_SWAP)

    backup.parent.mkdir(parents=True, exist_ok=True)
    if backup.exists():
        shutil.rmtree(backup)

    backed_up = False
    if live.exists():
        try:
            shutil.move(str(live), str(backup))
            backed_up = True
        except OSError as err:
            raise UpdateError(f"Could not back up the current version: {err}", PHASE_SWAP) from err

    try:
        shutil.move(str(staged), str(live))
    except OSError as err:
        if backed_up:
            try:
                shutil.move(str(backup), str(live))
            except OSError as restore_err:
                # Both moves failed. Say exactly where the files are, because
                # the recovery from here is a human with shell access.
                raise UpdateError(
                    f"Update failed AND the previous version could not be "
                    f"restored ({restore_err}). The working copy is at "
                    f"{backup} -- move it back to {live} and restart Home "
                    f"Assistant.",
                    PHASE_SWAP,
                ) from restore_err
        raise UpdateError(f"Could not move the new version into place: {err}", PHASE_SWAP) from err


def prune_backups(backup_root: os.PathLike, keep: int = UPDATE_BACKUP_KEEP) -> None:
    """Keep the newest backups and delete the rest. Never raises."""
    root = Path(backup_root)
    if not root.is_dir():
        return

    try:
        entries = sorted(
            (child for child in root.iterdir() if child.is_dir()),
            key=lambda child: child.stat().st_mtime,
            reverse=True,
        )
        for stale in entries[keep:]:
            shutil.rmtree(stale, ignore_errors=True)
    except OSError as err:
        _LOGGER.warning("Could not prune update backups in %s: %s", root, err)


# --- Orchestration ----------------------------------------------------------


class ClientUpdater:
    """Downloads, verifies, and installs signed client releases."""

    def __init__(self, hass: HomeAssistant, coordinator) -> None:
        """Initialize the updater for one config entry's coordinator."""
        self.hass = hass
        self.coordinator = coordinator
        self._lock = asyncio.Lock()
        self._store = Store(hass, UPDATE_STORAGE_VERSION, UPDATE_STORAGE_KEY)
        self._listeners: list[Callable[[], None]] = []
        self.in_progress = False
        self.progress: Optional[float] = None
        self.last_error: Optional[str] = None

    # -- paths

    @property
    def work_dir(self) -> Path:
        return Path(self.hass.config.path(UPDATE_WORK_DIR))

    @property
    def backup_root(self) -> Path:
        return self.work_dir / UPDATE_BACKUP_SUBDIR

    @property
    def staging_root(self) -> Path:
        return self.work_dir / UPDATE_STAGING_SUBDIR

    @property
    def live_dir(self) -> Path:
        return Path(self.hass.config.path("custom_components")) / DOMAIN

    # -- progress plumbing

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired whenever install progress changes."""
        self._listeners.append(callback)

        def _remove() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return _remove

    def _set_progress(self, in_progress: bool, progress: Optional[float] = None) -> None:
        self.in_progress = in_progress
        self.progress = progress
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:  # noqa: BLE001 - a bad listener must not abort an update
                _LOGGER.exception("Update progress listener raised")

    # -- version

    async def async_installed_version(self) -> Optional[str]:
        """Return the version of the integration actually on disk.

        Read from the loaded manifest rather than a constant so it stays true
        after a swap, which is the one moment it matters.
        """
        try:
            integration = await async_get_integration(self.hass, DOMAIN)
        except Exception as err:  # noqa: BLE001 - loader raises several types
            _LOGGER.warning("Could not determine the installed client version: %s", err)
            return None
        return None if integration.version is None else str(integration.version)

    # -- install

    async def async_install(self, release: Dict[str, Any], *, force: bool = False) -> None:
        """Install a release. Raises UpdateError with a phase on failure."""
        if self._lock.locked():
            raise UpdateError("An update is already in progress", PHASE_PREFLIGHT)

        async with self._lock:
            self.last_error = None
            self._set_progress(True, 0.0)
            try:
                await self._async_install_locked(release, force=force)
            except UpdateError as err:
                self.last_error = str(err)
                _LOGGER.error("Client update failed during %s: %s", err.phase, err)
                await self._async_report("failed", phase=err.phase, error=str(err), release=release)
                raise
            except Exception as err:  # noqa: BLE001
                # Anything unforeseen -- an OSError out of the executor, say --
                # still has to reach the server. A rollout that halts on
                # failures cannot halt on a failure it never hears about.
                self.last_error = str(err)
                _LOGGER.exception("Client update failed unexpectedly")
                await self._async_report(
                    "failed", phase=PHASE_SWAP, error=f"{type(err).__name__}: {err}",
                    release=release,
                )
                raise UpdateError(f"Unexpected failure during update: {err}", PHASE_SWAP) from err
            finally:
                self._set_progress(False, None)

    async def _async_install_locked(self, release: Dict[str, Any], *, force: bool) -> None:
        version = release.get("version")
        if not version:
            raise UpdateError("Release payload carried no version", PHASE_PREFLIGHT)

        installed = await self.async_installed_version()
        await self._async_preflight(release, installed, force=force)

        self._set_progress(True, 5.0)
        payload = await self._async_download(release)

        self._set_progress(True, 60.0)
        verify_digest(payload, release.get("sha256"))
        verify_signature(payload, release.get("signature"), release.get("key_id"))
        _LOGGER.info("Release %s verified; staging", version)

        self._set_progress(True, 70.0)
        staged = await self.hass.async_add_executor_job(
            self._stage_archive, payload, str(version)
        )

        # Announced before the swap on purpose. If this instance never checks in
        # again, the server's record of a started-but-unconfirmed update is what
        # tells a fleet rollout to halt -- and it needs no cooperation from a
        # client that may be about to stop working.
        await self._async_report("started", release=release, installed=installed)

        self._set_progress(True, 85.0)
        try:
            await self.hass.async_add_executor_job(
                swap_integration_dir,
                self.live_dir,
                staged,
                self.backup_root / str(installed or "unknown"),
            )
        finally:
            await self.hass.async_add_executor_job(
                shutil.rmtree, str(self.staging_root), True
            )

        await self._store.async_save(
            {
                "from_version": installed,
                "to_version": str(version),
                "started_at": datetime.now(timezone.utc).isoformat(),
                "phase": PHASE_RESTART,
            }
        )

        _LOGGER.warning(
            "Client updated from %s to %s. Home Assistant must restart for it to take effect.",
            installed,
            version,
        )
        self._set_progress(True, 95.0)
        await self._async_finish(release, installed, str(version))

    async def _async_preflight(
        self, release: Dict[str, Any], installed: Optional[str], *, force: bool
    ) -> None:
        version = release["version"]

        min_ha = release.get("min_ha_version")
        if min_ha and is_newer(min_ha, HA_VERSION):
            raise UpdateError(
                f"Release {version} needs Home Assistant {min_ha} or newer; this is {HA_VERSION}",
                PHASE_PREFLIGHT,
            )

        if installed is not None and str(version) == str(installed):
            raise UpdateError(f"Version {version} is already installed", PHASE_PREFLIGHT)

        if not force and installed is not None and not is_newer(version, installed):
            raise UpdateError(
                f"Refusing to move from {installed} to {version}: not an upgrade. "
                "Reinstall by hand if this is intended.",
                PHASE_PREFLIGHT,
            )

        size = int(release.get("size_bytes") or 0)
        if size > UPDATE_MAX_ARCHIVE_BYTES:
            raise UpdateError(
                f"Release archive is {size} bytes, over the {UPDATE_MAX_ARCHIVE_BYTES} cap",
                PHASE_PREFLIGHT,
            )

        needed = max(size, 1024 * 1024) * UPDATE_DISK_HEADROOM_FACTOR
        try:
            free = await self.hass.async_add_executor_job(_free_bytes, self.hass.config.path())
        except OSError as err:
            _LOGGER.warning("Could not check free disk space: %s", err)
            free = None
        if free is not None and free < needed:
            raise UpdateError(
                f"Not enough free disk space: {free} bytes available, {needed} needed",
                PHASE_PREFLIGHT,
            )

    async def _async_download(self, release: Dict[str, Any]) -> bytes:
        url = release.get("url")
        if not url:
            raise UpdateError("Release payload carried no url", PHASE_DOWNLOAD)

        parsed = urlparse(str(url))
        if parsed.scheme != "https":
            raise UpdateError(f"Refusing a non-HTTPS release URL: {url}", PHASE_DOWNLOAD)
        if parsed.hostname not in RELEASE_ALLOWED_HOSTS:
            raise UpdateError(
                f"Refusing a release URL on untrusted host {parsed.hostname!r}; "
                f"allowed: {sorted(RELEASE_ALLOWED_HOSTS)}",
                PHASE_DOWNLOAD,
            )

        declared = int(release.get("size_bytes") or 0)
        cap = min(UPDATE_MAX_ARCHIVE_BYTES, declared * 2) if declared else UPDATE_MAX_ARCHIVE_BYTES

        session = aiohttp_client.async_get_clientsession(self.hass)
        _LOGGER.info("Downloading release %s from %s", release.get("version"), url)

        # No Authorization header: this request goes to the artifact host, not
        # to Dispatch, and the installation token has no business being sent
        # anywhere else.
        try:
            async with session.get(str(url)) as response:
                if response.status != 200:
                    raise UpdateError(
                        f"Release download returned HTTP {response.status}", PHASE_DOWNLOAD
                    )
                buffer = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    buffer.extend(chunk)
                    if len(buffer) > cap:
                        raise UpdateError(
                            f"Release download exceeded {cap} bytes; aborted", PHASE_DOWNLOAD
                        )
        except UpdateError:
            raise
        except (OSError, TimeoutError) as err:
            raise UpdateError(f"Release download failed: {err}", PHASE_DOWNLOAD) from err
        except Exception as err:  # noqa: BLE001 - aiohttp raises a wide family here
            raise UpdateError(f"Release download failed: {err}", PHASE_DOWNLOAD) from err

        if declared and len(buffer) != declared:
            raise UpdateError(
                f"Release is {len(buffer)} bytes, server advertised {declared}", PHASE_DOWNLOAD
            )

        return bytes(buffer)

    def _stage_archive(self, payload: bytes, version: str) -> Path:
        """Write, validate, and extract the archive. Blocking; runs in executor."""
        staging = self.staging_root
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)

        archive_path = staging / ARCHIVE_NAME
        archive_path.write_bytes(payload)

        validate_archive(archive_path, expected_version=version)

        target = staging / version
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(target)

        # Extraction can only be trusted as far as the validation above, so
        # confirm the result rather than assuming it.
        if not (target / MANIFEST_NAME).is_file():
            raise UpdateError("Extracted copy has no manifest.json", PHASE_VALIDATE)

        archive_path.unlink(missing_ok=True)
        return target

    async def _async_finish(
        self, release: Dict[str, Any], installed: Optional[str], version: str
    ) -> None:
        """Restart, or tell the operator to, depending on configuration."""
        if not getattr(self.coordinator, "restart_after_update", True):
            self._notify(
                "HA Dispatch client updated",
                f"Updated from {installed} to {version}. Restart Home Assistant to "
                "load the new version.",
            )
            return

        _LOGGER.warning("Restarting Home Assistant to load client %s", version)
        try:
            await self.hass.services.async_call(
                "homeassistant", "restart", blocking=False
            )
        except HomeAssistantError as err:
            # Most often a failed config check blocking the restart. The files
            # are already swapped, so say plainly what state things are in.
            self._notify(
                "HA Dispatch client updated -- restart needed",
                f"Updated from {installed} to {version}, but the automatic restart "
                f"failed: {err}. Restart Home Assistant to load the new version.",
            )
            _LOGGER.error("Automatic restart after update failed: %s", err)

    # -- boot-time confirmation

    async def async_confirm_pending(self) -> None:
        """Resolve a pending update record left by a previous run.

        Called during setup, before anything else. If the swap worked, the
        version now on disk equals the one we aimed for.
        """
        try:
            record = await self._store.async_load()
        except (OSError, ValueError) as err:
            _LOGGER.warning("Could not read the pending update record: %s", err)
            return

        if not record:
            return

        target = record.get("to_version")
        previous = record.get("from_version")
        running = await self.async_installed_version()

        if running is not None and target is not None and str(running) == str(target):
            _LOGGER.info("Confirmed client update %s -> %s", previous, target)
            await self._async_report(
                "success", release={"version": target}, installed=previous
            )
            await self.hass.async_add_executor_job(prune_backups, self.backup_root)
        else:
            message = (
                f"Update to {target} did not take effect -- running {running}. "
                f"The previous version is kept at "
                f"{self.backup_root / str(previous or 'unknown')}."
            )
            _LOGGER.error(message)
            await self._async_report(
                "failed", phase=PHASE_CONFIRM, error=message,
                release={"version": target}, installed=previous,
            )
            self._notify("HA Dispatch client update did not take effect", message)

        try:
            await self._store.async_remove()
        except OSError as err:
            _LOGGER.warning("Could not clear the pending update record: %s", err)

    # -- helpers

    async def _async_report(
        self,
        status: str,
        *,
        release: Optional[Dict[str, Any]] = None,
        installed: Optional[str] = None,
        phase: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Report an update outcome. Never raises -- reporting is not the job."""
        api_client = getattr(self.coordinator, "api_client", None)
        report = getattr(api_client, "report_client_update", None)
        if report is None:
            return

        try:
            await report(
                installation_id=self.coordinator.installation_id,
                status=status,
                from_version=installed,
                to_version=None if release is None else release.get("version"),
                phase=phase,
                error=error,
            )
        except Exception as err:  # noqa: BLE001 - never let reporting break an update
            _LOGGER.warning("Could not report update %s to the server: %s", status, err)

    def _notify(self, title: str, message: str) -> None:
        """Raise a persistent notification, if the component is available."""
        try:
            from homeassistant.components import persistent_notification

            persistent_notification.async_create(
                self.hass, message, title=title, notification_id=f"{DOMAIN}_update"
            )
        except Exception as err:  # noqa: BLE001 - notification is best-effort
            _LOGGER.warning("Could not raise notification %r: %s", title, err)


def _free_bytes(path: str) -> int:
    """Return free bytes on the filesystem holding path."""
    return shutil.disk_usage(path).free
