"""Remote UI: the homeowner's own way into their Home Assistant, through Dispatch.

This is the agent half of homeowner remote access (ha-dispatch
``docs/technical/homeowner-remote-access.md``). It is **not** remote access for
a technician -- that is `remote_access.py` and `tunnel.py`, where every request
is authorised by the server and written on a receipt. Here nobody of ours is
looking, and the design depends on nobody of ours being *able* to look.

How it works: this module dials out to the Dispatch relay and holds one
multiplexed connection open. When the homeowner's phone connects to
``https://<label>.remote.hadispatch.app``, the relay matches the TLS SNI to
this connection and copies bytes. The TLS session terminates **here**, inside
Home Assistant's own aiohttp server, with a certificate whose private key sits
on this disk and has never left it. Home Assistant then sees an ordinary
request from an ordinary client and applies its own login, its own MFA and its
own permissions, exactly as it does on the LAN. Nothing is minted, substituted
or relayed on Home Assistant's behalf. There is no credential of ours in the
path at all.

Two rules this module keeps:

  - **Off until a house is given the files.** The config, certificate and key
    live under ``<config>/ha_dispatch_client/remote_ui/``. No directory, no
    attempt. A server that wants a house reachable cannot make it so.
  - **Nothing about the stream is ours to see.** The transport is snitun's
    (the library Nabu Casa runs for HA Cloud), driving Home Assistant's own
    aiohttp runner. This module never reads a request.

Where the files come from: slice 1 (the home lab) placed them by hand, the
certificate from a manual ACME run and ``remote_ui.json`` from the relay's
``mint_token.py``. Slice 2 adds the **provisioner** at the bottom of this
module: on each coordinator cycle it asks Dispatch for the desired state and,
when that state says so, generates the key here (it is never sent), has a
certificate signing request signed, mints the relay token for a keyset made
here, and writes the same three files. The file shapes are the contract
between the two: a house with hand-placed files and a server that predates
the feature (404) keeps working untouched, and the dial loop neither knows nor
cares which slice wrote what it reads.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import random
import ssl
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import aiohttp
from homeassistant.core import HomeAssistant

from .api_client import RemoteUiRefused, RemoteUiUnavailable
from .const import (
    REMOTE_UI_CERT_FILE,
    REMOTE_UI_CERT_RENEW_DAYS,
    REMOTE_UI_CONFIG_FILE,
    REMOTE_UI_CONNECT_TIMEOUT,
    REMOTE_UI_DEFAULT_PORT,
    REMOTE_UI_DEFAULT_PROTOCOL_VERSION,
    REMOTE_UI_DIR,
    REMOTE_UI_HTTP_FILE,
    REMOTE_UI_KEY_FILE,
    REMOTE_UI_MANAGED_KEY,
    REMOTE_UI_MANAGED_VALUE,
    REMOTE_UI_RECONNECT_MAX,
    REMOTE_UI_RECONNECT_MIN,
    REMOTE_UI_RETRY_SECONDS,
    REMOTE_UI_SECRET_FILE_MODE,
    REMOTE_UI_STATUS_ERROR_MAX_CHARS,
    REMOTE_UI_STATUS_INTERVAL_SECONDS,
    REMOTE_UI_TOKEN_REFRESH_SECONDS,
)
from .http_config import (
    HTTP_BLOCK_MANAGED,
    HTTP_BLOCK_MISSING,
    HTTP_BLOCK_UNMANAGED,
    apply_http_config,
    clean_trusted_proxies,
    restart_is_owed,
    write_atomic,
)

_LOGGER = logging.getLogger(__name__)


class RemoteUIConfigError(ValueError):
    """The files are there but cannot be used. Said once, loudly, then nothing is tried."""


def parse_remote_ui_config(raw: Any) -> Dict[str, Any]:
    """Validate and normalise ``remote_ui.json``.

    The shape is what the relay's ``mint_token.py mint`` writes, and what the
    slice-2 endpoint will return. Everything the dial needs is checked here so
    a typo fails at load with a reason, not at connect with a traceback.
    """
    if not isinstance(raw, dict):
        raise RemoteUIConfigError("remote_ui.json must hold a JSON object")

    def text(key: str) -> str:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise RemoteUIConfigError(f"remote_ui.json: '{key}' must be a non-empty string")
        return value.strip()

    def hexbytes(key: str, length: int) -> str:
        value = text(key)
        try:
            decoded = bytes.fromhex(value)
        except ValueError as err:
            raise RemoteUIConfigError(f"remote_ui.json: '{key}' is not hex") from err
        if len(decoded) != length:
            raise RemoteUIConfigError(f"remote_ui.json: '{key}' must be {length} bytes, got {len(decoded)}")
        return value.lower()

    port = raw.get("port", REMOTE_UI_DEFAULT_PORT)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise RemoteUIConfigError("remote_ui.json: 'port' must be an integer between 1 and 65535")

    valid = raw.get("valid")
    if isinstance(valid, bool) or not isinstance(valid, (int, float)):
        raise RemoteUIConfigError("remote_ui.json: 'valid' must be a Unix timestamp")

    protocol_version = raw.get("protocol_version", REMOTE_UI_DEFAULT_PROTOCOL_VERSION)
    if isinstance(protocol_version, bool) or not isinstance(protocol_version, int):
        raise RemoteUIConfigError("remote_ui.json: 'protocol_version' must be an integer")

    return {
        "hostname": text("hostname"),
        "server": text("server"),
        "port": port,
        "token": text("token"),
        "aes_key": hexbytes("aes_key", 32),
        "aes_iv": hexbytes("aes_iv", 16),
        "valid": int(valid),
        "protocol_version": protocol_version,
    }


def token_is_valid(config: Dict[str, Any], now: Optional[float] = None) -> bool:
    """Whether the token can still be presented. The relay refuses an expired one anyway."""
    return (now if now is not None else time.time()) < config["valid"]


def process_start_time() -> Optional[float]:
    """When this Home Assistant process started: the boot marker for 'restart owed'.

    A restart is a new process, an integration reload is not, which is exactly
    the distinction that matters. ``None`` if it cannot be read.
    """
    try:
        import psutil

        return psutil.Process().create_time()
    except Exception:  # noqa: BLE001 - psutil missing or the process unreadable: unknown, not fatal
        return None


def reconnect_delay(rng: random.Random = random) -> float:
    """How long to wait before redialling after a drop."""
    return rng.uniform(REMOTE_UI_RECONNECT_MIN, REMOTE_UI_RECONNECT_MAX)


def load_remote_ui_files(directory: Path) -> Optional[Tuple[Dict[str, Any], Path, Path]]:
    """Read the directory. ``None`` means the feature is off; an error means it is broken.

    Blocking file I/O: call through the executor.
    """
    config_path = directory / REMOTE_UI_CONFIG_FILE
    if not config_path.is_file():
        return None

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise RemoteUIConfigError(f"{config_path} could not be read as JSON: {err}") from err
    config = parse_remote_ui_config(raw)

    cert_path = directory / REMOTE_UI_CERT_FILE
    key_path = directory / REMOTE_UI_KEY_FILE
    for path in (cert_path, key_path):
        if not path.is_file():
            raise RemoteUIConfigError(
                f"{config_path.name} is present but {path.name} is missing from {directory}"
            )
    return config, cert_path, key_path


def build_server_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    """The TLS server context the house answers the phone with.

    Blocking file I/O: call through the executor. A certificate that does not
    match its key, or cannot be parsed, raises ``RemoteUIConfigError``.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_cert_chain(str(cert_path), str(key_path))
    except (ssl.SSLError, OSError) as err:
        raise RemoteUIConfigError(f"Could not load {cert_path.name} with {key_path.name}: {err}") from err
    return context


def installed_relay_protocol() -> Tuple[Optional[str], Optional[int]]:
    """The snitun this Home Assistant has, and the newest relay protocol it speaks.

    The agent imports whatever snitun Core bundles (through hass-nabucasa), and
    that lags the relay: Home Assistant 2026.6 ships snitun 0.45.1, protocol 1,
    while the relay image speaks 2. A token minted for a newer protocol than
    the house speaks connects and then every channel hangs, silently, so the
    version is checked here and sent to Dispatch at mint time. Returns
    ``(None, None)`` when snitun is not importable at all.
    """
    try:
        import importlib.metadata as metadata
        from snitun.utils import server as snitun_server
    except Exception:  # noqa: BLE001 - absent or broken snitun is answered the same way
        return None, None
    try:
        version = metadata.version("snitun")
    except Exception:  # noqa: BLE001
        version = "unknown"
    protocol = getattr(snitun_server, "PROTOCOL_VERSION", None)
    return version, protocol if isinstance(protocol, int) else None


def _default_client_factory(runner, context: ssl.SSLContext, server: str, port: int):
    # Imported here so the integration loads on an install without snitun
    # (every current Home Assistant has it, via hass-nabucasa) and so the
    # test suite can exercise the lifecycle with a stand-in.
    from snitun.utils.aiohttp_client import SniTunClientAioHttp

    return SniTunClientAioHttp(runner, context, server, port)


class HADispatchRemoteUI:
    """Holds the house's connection to the relay, for as long as the files say to."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator,
        client_factory: Optional[Callable[..., Any]] = None,
        delay: Callable[[], float] = reconnect_delay,
        started_at: Callable[[], Optional[float]] = process_start_time,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self._client_factory = client_factory or _default_client_factory
        self._delay = delay
        self._started_at = started_at
        self._clock = clock
        self._client = None
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self.config: Optional[Dict[str, Any]] = None
        # Counted for slice 1's fourth pass condition, and for the day the
        # coordinator reports them.
        self.dials = 0
        self.drops = 0
        self.last_error: Optional[str] = None
        # Provisioner state. desired is the last state the server sent;
        # _applied_version the config_version whose files are on disk and
        # dialled; _unavailable remembers a 404 so a server predating the
        # feature is asked once per run, not once a minute.
        self.desired: Optional[Dict[str, Any]] = None
        self._applied_version: Optional[int] = None
        self._unavailable = False
        self._kept_hand_placed = False
        self._retry_after: float = 0.0
        self._provisioning = False
        # What the house reports (POST .../remote-ui/status). http_block and
        # restart_required are the outcome of the last http.yaml application;
        # _http_applied is the proxy list it was done for, so the two files
        # are read once per change, not once a minute (and a homeowner who
        # later removes the include line is not fought on every poll).
        self.http_block: str = HTTP_BLOCK_MISSING
        self.restart_required = False
        self.certificate_expires_at: Optional[str] = None
        self._http_applied: Optional[Tuple[str, ...]] = None
        self._status_sent: Optional[Dict[str, Any]] = None
        self._status_sent_at: float = 0.0

    @property
    def enabled(self) -> bool:
        return self.config is not None

    @property
    def connected(self) -> bool:
        return bool(self._client is not None and getattr(self._client, "is_connected", False))

    def directory(self) -> Path:
        return Path(self.hass.config.path(*REMOTE_UI_DIR))

    async def async_load(self) -> None:
        """Read the files and, if they are all there, start dialling. Cannot raise."""
        directory = self.directory()
        try:
            loaded = await self.hass.async_add_executor_job(load_remote_ui_files, directory)
        except RemoteUIConfigError as err:
            _LOGGER.error("Remote UI is configured but unusable: %s", err)
            return
        except OSError as err:
            _LOGGER.error("Remote UI: could not read %s: %s", directory, err)
            return

        if loaded is None:
            _LOGGER.debug("Remote UI: no %s in %s, so nothing is dialled", REMOTE_UI_CONFIG_FILE, directory)
            return

        config, cert_path, key_path = loaded
        if not token_is_valid(config):
            _LOGGER.error(
                "Remote UI: the token in %s expired at %s; mint a new one. Not dialling",
                REMOTE_UI_CONFIG_FILE, _when(config["valid"]),
            )
            return

        runner = getattr(getattr(self.hass, "http", None), "runner", None)
        if runner is None:
            _LOGGER.error("Remote UI: Home Assistant's HTTP server is not running, so there is nothing to serve")
            return

        try:
            context = await self.hass.async_add_executor_job(build_server_context, cert_path, key_path)
        except RemoteUIConfigError as err:
            _LOGGER.error("Remote UI is configured but unusable: %s", err)
            return

        self.config = config
        self.certificate_expires_at = await self.hass.async_add_executor_job(certificate_expires_at, cert_path)
        self._client = self._client_factory(runner, context, config["server"], config["port"])
        await self._client.start()
        self._stopping = False
        self._task = self._create_task(self._async_run())
        _LOGGER.info(
            "Remote UI: serving %s through %s:%s with the certificate in %s",
            config["hostname"], config["server"], config["port"], directory,
        )

    async def _async_run(self) -> None:
        """Dial, hold, redial. Ends only on unload or an expired token."""
        config = self.config
        assert config is not None
        while not self._stopping:
            if not token_is_valid(config):
                _LOGGER.error("Remote UI: the token expired at %s; the house is unreachable until a new one is placed",
                              _when(config["valid"]))
                return
            snitun_version, supported = installed_relay_protocol()
            if supported is not None and config["protocol_version"] > supported:
                self.last_error = (
                    f"token minted for relay protocol {config['protocol_version']}, "
                    f"but this Home Assistant's snitun {snitun_version} speaks up to {supported}"
                )
                _LOGGER.error(
                    "Remote UI: %s. Not dialling: the connection would open and every channel would hang. "
                    "Mint the token for protocol %s (Dispatch does this itself once it is asked)",
                    self.last_error, supported,
                )
                return
            try:
                async with asyncio.timeout(REMOTE_UI_CONNECT_TIMEOUT):
                    await self._client.connect(
                        config["token"].encode(),
                        bytes.fromhex(config["aes_key"]),
                        bytes.fromhex(config["aes_iv"]),
                        protocol_version=config["protocol_version"],
                    )
                self.dials += 1
                self.last_error = None
                _LOGGER.info(
                    "Remote UI: dialled %s:%s as %s (snitun %s, relay protocol %s)",
                    config["server"], config["port"], config["hostname"], snitun_version, config["protocol_version"],
                )
                await self._client.wait()
                if self._stopping:
                    return
                self.drops += 1
                _LOGGER.warning("Remote UI: the connection to the relay ended; redialling shortly")
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - whatever the dial raised, the answer is the same: wait, try again
                self.last_error = f"{type(err).__name__}: {err}" if str(err) else type(err).__name__
                _LOGGER.warning("Remote UI: could not reach the relay at %s:%s (%s); retrying shortly",
                                config["server"], config["port"], self.last_error)
            if self._stopping:
                return
            await asyncio.sleep(self._delay())

    async def async_unload(self) -> None:
        """Hang up. An integration reload is not the homeowner turning this off; the files stay."""
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        if self._client is not None:
            try:
                await self._client.stop(wait=True)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Remote UI: stopping the relay client raised %s", err)
            self._client = None

    def _create_task(self, coro):
        creator = getattr(self.hass, "async_create_background_task", None)
        if creator is not None:
            return creator(coro, name="ha_dispatch_client_remote_ui")
        return self.hass.async_create_task(coro)

    # ------------------------------------------------------------ provisioner --
    #
    # Slice 2. Called on the coordinator's cadence, like backups.async_tick():
    # absent unless setup wired a coordinator, and incapable of raising, because
    # a house must keep reporting metrics even when its remote access is broken.

    async def async_poll(self) -> None:
        """Ask Dispatch what this house should be, and make it so. Cannot raise."""
        if self._provisioning:
            # A slow step (a CSR the server is slow to sign) outlived the
            # 60 s cadence; the next poll picks up where this one ends.
            return
        self._provisioning = True
        try:
            await self._async_poll()
        except Exception as err:  # noqa: BLE001 - see the docstring
            _LOGGER.error("Remote UI: unexpected error while provisioning: %s", err)
            self._retry_after = time.time() + REMOTE_UI_RETRY_SECONDS
        finally:
            self._provisioning = False
        await self.async_report_status()

    async def _async_poll(self) -> None:
        if self._unavailable:
            return
        api = getattr(self.coordinator, "api_client", None)
        installation_id = getattr(self.coordinator, "installation_id", None)
        if api is None or installation_id is None:
            return

        try:
            desired = await api.fetch_remote_ui(installation_id)
        except RemoteUiUnavailable:
            self._unavailable = True
            _LOGGER.info(
                "This Dispatch server has no Remote UI endpoint; not asking again. "
                "Files placed by hand in %s keep working as they are", self.directory(),
            )
            return
        except (aiohttp.ClientError, TimeoutError, KeyError, ValueError) as err:
            _LOGGER.debug("Remote UI: could not fetch the desired state: %s", err)
            return

        if not isinstance(desired, dict):
            return
        self.desired = desired

        if not desired.get("enabled"):
            await self._async_disable()
            return

        # The proxy list does not depend on the certificate or the token, so a
        # refusal further down must not stop Home Assistant being told about
        # the relay.
        await self._async_apply_http(desired)

        if time.time() < self._retry_after:
            return
        await self._async_provision(desired)

    async def _async_apply_http(self, desired: Dict[str, Any]) -> None:
        """Write http.yaml and, if the homeowner has no http: key, the include line.

        Runs when the proxy list is first seen or changes -- not every poll.
        The agent never restarts Home Assistant: a change is reported as
        ``restart_required`` and the homeowner (or a technician, with their
        say-so) restarts when it suits them.
        """
        proxies = clean_trusted_proxies(desired.get("trusted_proxies"))
        if not proxies:
            return
        key = tuple(proxies)
        if key == self._http_applied:
            return

        http_yaml = Path(self.hass.config.path(*REMOTE_UI_HTTP_FILE))
        configuration = Path(self.hass.config.path("configuration.yaml"))
        try:
            block, changed = await self.hass.async_add_executor_job(
                apply_http_config, http_yaml, configuration, proxies
            )
            owed = await self.hass.async_add_executor_job(restart_is_owed, http_yaml, self._started_at())
        except OSError as err:
            _LOGGER.warning("Remote UI: could not write the http configuration (%s); trying again later", err)
            self._retry_after = time.time() + REMOTE_UI_RETRY_SECONDS
            return

        self._http_applied = key
        self.http_block = block
        # restart_is_owed knows from the file's age; changed knows this write
        # happened (and covers a process start time we could not read).
        self.restart_required = bool(changed or owed) and block == HTTP_BLOCK_MANAGED
        if changed:
            _LOGGER.info("Remote UI: wrote %s (%d trusted proxy ranges); Home Assistant needs a restart to use it",
                         http_yaml, len(proxies))
        if block != HTTP_BLOCK_MANAGED:
            _LOGGER.warning(
                "Remote UI: configuration.yaml %s; the trusted proxy list is in %s for a technician to merge",
                "already has its own http: block" if block == HTTP_BLOCK_UNMANAGED else "was not found",
                http_yaml,
            )

    # ----------------------------------------------------------------- status --

    def status_payload(self) -> Dict[str, Any]:
        """The body of POST .../remote-ui/status, from what this manager knows."""
        desired = self.desired or {}
        # The server accepts at most REMOTE_UI_STATUS_ERROR_MAX_CHARS here and
        # answers 422 to more; a verbose dial error must not cost the report.
        last_error = self.last_error
        if isinstance(last_error, str) and len(last_error) > REMOTE_UI_STATUS_ERROR_MAX_CHARS:
            last_error = last_error[:REMOTE_UI_STATUS_ERROR_MAX_CHARS]
        return {
            "connected": self.connected,
            "dials": self.dials,
            "drops": self.drops,
            "last_error": last_error,
            "http_block": self.http_block,
            "ranges_version": desired.get("ranges_version"),
            "restart_required": self.restart_required,
            "certificate_expires_at": self.certificate_expires_at,
            "token_valid": self.config["valid"] if self.config else None,
        }

    async def async_report_status(self) -> None:
        """Tell Dispatch how this house is doing. Cannot raise.

        Sent when the payload differs from the last one that went, and at
        least every ten minutes whether or not it does -- the heartbeat that
        lets the server tell a quiet house from a silent one. Nothing is sent
        for a house that is off or a server that predates the feature.
        """
        try:
            if self._unavailable or not (self.desired or {}).get("enabled"):
                return
            api = getattr(self.coordinator, "api_client", None)
            installation_id = getattr(self.coordinator, "installation_id", None)
            if api is None or installation_id is None:
                return
            payload = self.status_payload()
            now = self._clock()
            if payload == self._status_sent and now - self._status_sent_at < REMOTE_UI_STATUS_INTERVAL_SECONDS:
                return
            try:
                await api.report_remote_ui_status(installation_id, payload)
            except RemoteUiUnavailable:
                self._unavailable = True
                return
            except RemoteUiRefused as err:
                # 409: not switched on yet. Not retried every minute; the next
                # report is a change or the ten-minute mark.
                _LOGGER.debug("Remote UI: status report declined (%s)", err)
            except (aiohttp.ClientError, TimeoutError, KeyError, ValueError) as err:
                _LOGGER.debug("Remote UI: could not report status: %s", err)
                return
            self._status_sent = payload
            self._status_sent_at = now
        except Exception as err:  # noqa: BLE001 - reporting must never cost the house its metrics
            _LOGGER.debug("Remote UI: status report failed unexpectedly: %s", err)

    async def _async_disable(self) -> None:
        """The server says off: hang up and remove the three files, nothing else.

        ``http.yaml`` is left alone: a stale trusted-proxies list is harmless
        and removing the include line is a config edit the homeowner did not
        ask for. Idempotent -- a house that is already off writes and logs
        nothing.
        """
        was_dialling = self._client is not None
        if was_dialling:
            await self.async_unload()
        self.config = None
        self._applied_version = None
        self.certificate_expires_at = None
        self._http_applied = None
        self._status_sent = None
        removed = await self.hass.async_add_executor_job(remove_remote_ui_files, self.directory())
        if removed is None:
            # Hand-placed files (slice 1, or an operator's own set) are not
            # ours to delete. Say so once and leave them; the dial stops
            # because the server said off, which is the part that is ours.
            if was_dialling or not self._kept_hand_placed:
                _LOGGER.info(
                    "Remote UI: the server says off, but the files in %s were placed by hand, "
                    "not by this client, so they are left in place. Remove them yourself to finish turning it off",
                    self.directory(),
                )
            self._kept_hand_placed = True
            return
        self._kept_hand_placed = False
        if was_dialling or removed:
            _LOGGER.info(
                "Remote UI: switched off by the homeowner; hung up and removed %s",
                ", ".join(removed) if removed else "nothing (no files were present)",
            )

    async def _async_provision(self, desired: Dict[str, Any]) -> None:
        installation_id = self.coordinator.installation_id
        api = self.coordinator.api_client
        directory = self.directory()
        now = time.time()

        try:
            hostname, server, port = desired_endpoint(desired)
        except RemoteUIConfigError as err:
            _LOGGER.error("Remote UI: the server's desired state is unusable: %s", err)
            self._retry_after = now + REMOTE_UI_RETRY_SECONDS
            return

        files: RemoteUiFiles = await self.hass.async_add_executor_job(inspect_remote_ui_files, directory)
        plan = plan_provisioning(desired, files, self._applied_version, now)
        written: List[str] = []

        try:
            key = files.key
            if plan["key"]:
                key = await self.hass.async_add_executor_job(generate_private_key)
                await self.hass.async_add_executor_job(
                    write_atomic, directory / REMOTE_UI_KEY_FILE, private_key_pem(key), REMOTE_UI_SECRET_FILE_MODE,
                )
                written.append(REMOTE_UI_KEY_FILE)
                _LOGGER.info("Remote UI: generated this house's private key in %s; it will not leave", directory)

            if plan["certificate"]:
                certificate = desired.get("certificate")
                offered = certificate.get("fullchain_pem") if isinstance(certificate, dict) else None
                if isinstance(offered, str) and certificate_is_usable(offered, key, hostname, now)[0]:
                    # The server already holds a certificate for this very key
                    # (a lost or hand-replaced file); no need to spend a signing.
                    cert_pem = offered
                else:
                    csr_pem = build_csr(key, hostname)
                    signed = await api.sign_remote_ui_csr(installation_id, csr_pem)
                    cert_pem = signed.get("fullchain_pem") if isinstance(signed, dict) else None
                    usable, why = certificate_is_usable(cert_pem, key, hostname, now)
                    if not usable:
                        raise RemoteUIConfigError(f"the certificate Dispatch signed cannot be used: {why}")
                await self.hass.async_add_executor_job(
                    write_atomic, directory / REMOTE_UI_CERT_FILE, cert_pem.encode("utf-8"), 0o644,
                )
                written.append(REMOTE_UI_CERT_FILE)
                _LOGGER.info("Remote UI: certificate for %s written to %s", hostname, directory)

            if plan["token"]:
                aes_key = os.urandom(32).hex()
                aes_iv = os.urandom(16).hex()
                snitun_version, supported = installed_relay_protocol()
                minted = await api.mint_remote_ui_token(
                    installation_id, aes_key, aes_iv, protocol_version=supported
                )
                if not isinstance(minted, dict):
                    raise RemoteUIConfigError("the token endpoint did not return an object")
                protocol_version = minted.get(
                    "protocol_version", desired.get("protocol_version", REMOTE_UI_DEFAULT_PROTOCOL_VERSION)
                )
                if supported is not None and isinstance(protocol_version, int) and protocol_version > supported:
                    raise RemoteUIConfigError(
                        f"Dispatch minted a token for relay protocol {protocol_version}, but this Home "
                        f"Assistant's snitun {snitun_version} speaks up to {supported}; the server ignored the cap"
                    )
                # Exactly the slice-1 shape, through the same validator the
                # loader uses, so what is written is what will load.
                config = parse_remote_ui_config({
                    "hostname": hostname,
                    "server": server,
                    "port": port,
                    "token": minted.get("token"),
                    "aes_key": aes_key,
                    "aes_iv": aes_iv,
                    "valid": minted.get("valid"),
                    "protocol_version": protocol_version,
                })
                # Marked as ours, so a later "off" from the server removes only
                # files this provisioner wrote and never a hand-placed set.
                on_disk = dict(config)
                on_disk[REMOTE_UI_MANAGED_KEY] = REMOTE_UI_MANAGED_VALUE
                await self.hass.async_add_executor_job(
                    write_atomic, directory / REMOTE_UI_CONFIG_FILE,
                    (json.dumps(on_disk, indent=2) + "\n").encode("utf-8"), REMOTE_UI_SECRET_FILE_MODE,
                )
                written.append(REMOTE_UI_CONFIG_FILE)
                _LOGGER.info("Remote UI: relay token for %s minted, valid until %s", hostname, _when(config["valid"]))
        except RemoteUiUnavailable:
            self._unavailable = True
            _LOGGER.info("This Dispatch server has no Remote UI endpoint; not asking again")
            return
        except RemoteUiRefused as err:
            _LOGGER.warning("Remote UI: Dispatch declined a provisioning step (%s); trying again later", err)
            self._retry_after = time.time() + REMOTE_UI_RETRY_SECONDS
            return
        except RemoteUIConfigError as err:
            _LOGGER.error("Remote UI: %s; trying again later", err)
            self._retry_after = time.time() + REMOTE_UI_RETRY_SECONDS
            return
        except (aiohttp.ClientError, TimeoutError, OSError, KeyError, ValueError) as err:
            _LOGGER.warning("Remote UI: provisioning did not complete (%s); trying again later", err)
            self._retry_after = time.time() + REMOTE_UI_RETRY_SECONDS
            return

        if written or self.config is None:
            # Something new on disk, or files present but not yet dialled:
            # (re)load through the one path that knows how to dial.
            if self._client is not None:
                await self.async_unload()
            await self.async_load()
            if self.config is None:
                # async_load has said why. Do not record the version, so the
                # next poll tries again once the backoff has passed.
                self._retry_after = time.time() + REMOTE_UI_RETRY_SECONDS
                return

        self._applied_version = desired.get("config_version")
        self._retry_after = 0.0
        if plan["reasons"]:
            _LOGGER.debug("Remote UI: provisioned (%s); wrote %s", ", ".join(plan["reasons"]), written or "nothing")


class RemoteUiFiles:
    """What is on disk right now, as far as the provisioner needs to know."""

    def __init__(self, key=None, cert_pem: Optional[str] = None, config: Optional[Dict[str, Any]] = None):
        self.key = key
        self.cert_pem = cert_pem
        self.config = config


def desired_endpoint(desired: Dict[str, Any]) -> Tuple[str, str, int]:
    """The hostname, relay server and relay port from a desired state, checked."""
    hostname = desired.get("hostname")
    relay = desired.get("relay") if isinstance(desired.get("relay"), dict) else {}
    server = relay.get("server")
    port = relay.get("port", REMOTE_UI_DEFAULT_PORT)
    if not isinstance(hostname, str) or not hostname.strip():
        raise RemoteUIConfigError("'hostname' is missing")
    if not isinstance(server, str) or not server.strip():
        raise RemoteUIConfigError("'relay.server' is missing")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise RemoteUIConfigError("'relay.port' is not a port number")
    return hostname.strip(), server.strip(), port


def plan_provisioning(
    desired: Dict[str, Any],
    files: RemoteUiFiles,
    applied_version: Optional[int],
    now: float,
) -> Dict[str, Any]:
    """Decide which of the three files need (re)writing. Pure; tested directly.

    The rule from the contract: act on the first poll, when config_version
    moves, when the token is within 48 h of expiry, when the certificate is
    within 60 days of expiry, or when a renewal is requested. Everything else
    is a no-op, so an unchanged desired state writes nothing. A moved version
    re-mints the token (it names the hostname and the relay) but does not
    re-sign a certificate that is still good: signings are rationed.
    """
    hostname, server, port = desired_endpoint(desired)
    version = desired.get("config_version")
    first = applied_version is None
    moved = not first and version != applied_version
    reasons: List[str] = []

    need_key = files.key is None
    if need_key:
        reasons.append("no private key")

    usable, why = certificate_is_usable(files.cert_pem, files.key, hostname, now) if not need_key else (False, "new key")
    need_cert = need_key or not usable
    if need_cert and not need_key:
        reasons.append(f"certificate: {why}")
    if (first or moved) and desired.get("renew_requested") and not need_cert:
        need_cert = True
        reasons.append("renewal requested")

    config = files.config
    need_token = (
        config is None
        or config["hostname"] != hostname
        or config["server"] != server
        or config["port"] != port
        or config["valid"] - now < REMOTE_UI_TOKEN_REFRESH_SECONDS
    )
    if need_token:
        reasons.append("token: missing, stale or within 48 h of expiry")
    elif moved:
        need_token = True
        reasons.append(f"config_version moved {applied_version} -> {version}")

    return {"key": need_key, "certificate": need_cert, "token": need_token, "reasons": reasons}


def generate_private_key():
    """A fresh EC P-256 key. Blocking (briefly): call through the executor."""
    from cryptography.hazmat.primitives.asymmetric import ec

    return ec.generate_private_key(ec.SECP256R1())


def private_key_pem(key) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def build_csr(key, hostname: str) -> str:
    """A certificate signing request with CN and SAN = hostname, as PEM text.

    This -- and only this -- is what leaves the house. It carries the public
    key and a signature made with the private one; the private key itself is
    not in it and cannot be recovered from it.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509.oid import NameOID

    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _not_after(cert) -> dt.datetime:
    expires = getattr(cert, "not_valid_after_utc", None)
    if expires is None:  # cryptography < 42
        expires = cert.not_valid_after.replace(tzinfo=dt.timezone.utc)
    return expires


def certificate_expires_at(cert_path: Path) -> Optional[str]:
    """When the certificate on disk stops being valid, as ISO 8601 UTC. Blocking I/O.

    ``None`` if it cannot be read: the status report says "unknown", it does
    not stop the dial that the TLS context load has already vouched for.
    """
    from cryptography import x509

    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except (OSError, ValueError, TypeError):
        return None
    return _not_after(cert).strftime("%Y-%m-%dT%H:%M:%SZ")


def certificate_is_usable(cert_pem: Optional[str], key, hostname: str, now: float) -> Tuple[bool, str]:
    """Whether a certificate is this key's, names this hostname, and has 60+ days left."""
    if not cert_pem or key is None:
        return False, "missing"
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.x509.oid import ExtensionOID, NameOID

    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
    except (ValueError, TypeError) as err:
        return False, f"unreadable ({err})"

    def spki(public_key) -> bytes:
        return public_key.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )

    if spki(cert.public_key()) != spki(key.public_key()):
        return False, "does not match the private key"

    names: List[str] = []
    try:
        san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        names = san.value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        pass
    names += [attr.value for attr in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)]
    if hostname not in names:
        return False, f"issued for {', '.join(names) or 'no name'}, not {hostname}"

    expires = _not_after(cert)
    left = expires.timestamp() - now
    if left < REMOTE_UI_CERT_RENEW_DAYS * 86400:
        return False, f"expires {expires.date().isoformat()}, within {REMOTE_UI_CERT_RENEW_DAYS} days"
    return True, "usable"


def inspect_remote_ui_files(directory: Path) -> RemoteUiFiles:
    """Read whatever is in the directory without judging it. Blocking I/O."""
    from cryptography.hazmat.primitives import serialization

    files = RemoteUiFiles()
    key_path = directory / REMOTE_UI_KEY_FILE
    if key_path.is_file():
        try:
            files.key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        except (OSError, ValueError, TypeError) as err:
            _LOGGER.warning("Remote UI: %s is unreadable (%s); a new key will be generated", key_path, err)
    cert_path = directory / REMOTE_UI_CERT_FILE
    if cert_path.is_file():
        try:
            files.cert_pem = cert_path.read_text(encoding="utf-8")
        except OSError:
            pass
    config_path = directory / REMOTE_UI_CONFIG_FILE
    if config_path.is_file():
        try:
            files.config = parse_remote_ui_config(json.loads(config_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, RemoteUIConfigError):
            files.config = None
    return files


def remote_ui_files_are_managed(directory: Path) -> bool:
    """Whether the files in the directory were written by this client.

    The provisioner stamps ``remote_ui.json`` with ``"managed_by": "dispatch"``.
    A file without the stamp was placed by hand (slice 1, or an operator) and
    is not ours to delete. No config file at all counts as managed: there is
    nothing hand-placed to protect, and a stray certificate or key is ours.
    """
    config_path = directory / REMOTE_UI_CONFIG_FILE
    if not config_path.is_file():
        return True
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return isinstance(raw, dict) and raw.get(REMOTE_UI_MANAGED_KEY) == REMOTE_UI_MANAGED_VALUE


def remove_remote_ui_files(directory: Path) -> Optional[List[str]]:
    """Delete the three files, and only those, and only if we wrote them.

    Blocking I/O. Returns what went, or ``None`` when the files were placed by
    hand and were therefore left alone.
    """
    if not remote_ui_files_are_managed(directory):
        return None
    removed: List[str] = []
    for name in (REMOTE_UI_CONFIG_FILE, REMOTE_UI_CERT_FILE, REMOTE_UI_KEY_FILE):
        path = directory / name
        try:
            path.unlink()
            removed.append(name)
        except FileNotFoundError:
            continue
    return removed


def _when(timestamp: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(timestamp))
