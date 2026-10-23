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

Slice 1 (the home lab) places the files by hand: the certificate from a manual
ACME run, ``remote_ui.json`` from the relay's ``mint_token.py``. Slice 2 will
have the agent generate its key and ask Dispatch for the certificate and the
token; the file shapes are the contract that endpoint will return.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import ssl
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from homeassistant.core import HomeAssistant

from .const import (
    REMOTE_UI_CERT_FILE,
    REMOTE_UI_CONFIG_FILE,
    REMOTE_UI_CONNECT_TIMEOUT,
    REMOTE_UI_DEFAULT_PORT,
    REMOTE_UI_DEFAULT_PROTOCOL_VERSION,
    REMOTE_UI_DIR,
    REMOTE_UI_KEY_FILE,
    REMOTE_UI_RECONNECT_MAX,
    REMOTE_UI_RECONNECT_MIN,
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
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self._client_factory = client_factory or _default_client_factory
        self._delay = delay
        self._client = None
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self.config: Optional[Dict[str, Any]] = None
        # Counted for slice 1's fourth pass condition, and for the day the
        # coordinator reports them.
        self.dials = 0
        self.drops = 0
        self.last_error: Optional[str] = None

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
                _LOGGER.info("Remote UI: dialled %s:%s as %s", config["server"], config["port"], config["hostname"])
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


def _when(timestamp: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(timestamp))
