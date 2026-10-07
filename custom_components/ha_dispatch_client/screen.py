"""The Home Assistant end of a Remote Screen's WebSocket.

Dispatch renders this house's own frontend in a headless browser on its side,
so a technician can see and use it the way the homeowner does. That frontend
lives on one WebSocket. Its browser end is intercepted on the Dispatch side;
this module is the other end: one loopback WebSocket to this Home Assistant
per screen session, with frames pumped both ways over the tunnel's existing
outbound long-poll. Nothing listens on a port.

Two things here are load-bearing:

  - **The credential is ours, and stays here.** The rendered browser is handed
    a placeholder token. When its `auth` frame arrives we replace the token
    with one minted from this integration's admin system user, so a real
    Home Assistant credential never crosses to Dispatch or sits in its
    database. Every other frame is passed through untouched.
  - **Only screen sessions get a socket.** A frame for a session we have no
    `screen` consent on record for is dropped, the same fail-closed rule the
    HTTP side applies.

Spec: ha-dispatch docs/technical/remote-screen.md
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import aiohttp

from homeassistant.helpers import aiohttp_client

from .api_client import RemoteAccessUnavailable
from .const import ACCESS_SCOPE_SCREEN, ACCESS_SCREEN_FRAME_FLUSH, SCREEN_DEVICE_SOURCES

_LOGGER = logging.getLogger(__name__)


def substitute_auth(payload: str, token: str) -> str:
    """Put our credential into the browser's auth frame; leave all else alone.

    Returns the payload unchanged unless it is exactly an auth message, so a
    frame we do not understand is never rewritten by accident.
    """
    try:
        message = json.loads(payload)
    except (TypeError, ValueError):
        return payload
    if isinstance(message, dict) and message.get("type") == "auth":
        return json.dumps({"type": "auth", "access_token": token})
    return payload


def collect_screen_devices(hass) -> List[Dict[str, Any]]:
    """The screens this house looks at Home Assistant on.

    Companion-app phones and tablets, and Fully Kiosk wall panels, from the
    device registry. Only what sizes a render: the integration, the
    manufacturer and the model. A wall panel's name is included, because
    "Kitchen panel" is how a technician knows which one it is; a phone's is
    not, because it is usually somebody's name.
    """
    from homeassistant.helpers import device_registry

    registry = device_registry.async_get(hass)
    found: List[Dict[str, Any]] = []
    seen = set()

    # Per config entry, through the registry's own lookup: reading
    # registry.devices as a mapping is deprecated and stops working in
    # Home Assistant 2027.9.
    pairs = [
        (source, device)
        for source in SCREEN_DEVICE_SOURCES
        for entry in hass.config_entries.async_entries(source)
        for device in device_registry.async_entries_for_config_entry(registry, entry.entry_id)
    ]

    for source, device in pairs:
        if getattr(device, "id", id(device)) in seen:
            continue
        seen.add(getattr(device, "id", id(device)))

        record: Dict[str, Any] = {
            "source": source,
            "manufacturer": (getattr(device, "manufacturer", None) or "")[:128],
            "model": (getattr(device, "model", None) or "")[:128],
        }
        if source == "fully_kiosk":
            name = getattr(device, "name_by_user", None) or getattr(device, "name", None)
            if name:
                record["name"] = str(name)[:128]
        if record["manufacturer"] or record["model"]:
            found.append(record)

    # Stable order, so an unchanged house produces an unchanged report.
    return sorted(found, key=lambda d: (d["source"], d.get("name", ""), d["manufacturer"], d["model"]))


class _LocalSocket:
    """One loopback WebSocket for one screen session."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.reader: Optional[asyncio.Task] = None
        self.lock = asyncio.Lock()


class ScreenSockets:
    """Every screen session's local socket, owned by the tunnel."""

    def __init__(self, hass, tunnel) -> None:
        self.hass = hass
        self.tunnel = tunnel
        self._sockets: Dict[str, _LocalSocket] = {}
        self._outbox: List[Dict[str, Any]] = []
        self._flusher: Optional[asyncio.Task] = None

    # -- frames from the browser -------------------------------------------

    async def async_handle(self, frames: List[Dict[str, Any]]) -> None:
        """Apply a batch of browser frames, in order, per session."""
        for frame in frames:
            session_id = str(frame.get("session_id"))
            if self.tunnel.access.scope_for(session_id) != ACCESS_SCOPE_SCREEN:
                _LOGGER.debug("Dropping a frame for session %s: no screen consent here", session_id)
                continue

            kind = frame.get("kind") or "frame"
            try:
                if kind == "open":
                    await self._async_open(session_id)
                elif kind == "close":
                    await self._async_close(session_id)
                else:
                    await self._async_send(session_id, frame.get("payload"))
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
                _LOGGER.warning("Screen socket for session %s failed: %s", session_id, err)
                await self._async_close(session_id, tell_browser=True)

    async def _async_open(self, session_id: str) -> None:
        # The frontend reconnects with a fresh socket; the old one is done.
        await self._async_close(session_id)

        local = _LocalSocket(session_id)
        self._sockets[session_id] = local

        url = self.tunnel._local_base_url().replace("http", "ws", 1) + "/api/websocket"
        session = aiohttp_client.async_get_clientsession(self.hass)
        local.ws = await session.ws_connect(url, max_msg_size=0, heartbeat=None)
        local.reader = self._create_task(self._async_read(local))
        _LOGGER.info("Remote Screen connected to the local WebSocket for session %s", session_id)

    async def _async_send(self, session_id: str, payload: Any) -> None:
        local = self._sockets.get(session_id)
        if local is None or local.ws is None or local.ws.closed or payload is None:
            return

        payload = str(payload)
        if '"auth"' in payload:
            token = await self.tunnel._async_access_token(ACCESS_SCOPE_SCREEN)
            if token is None:
                _LOGGER.error("No local credential for the Remote Screen; closing it")
                await self._async_close(session_id, tell_browser=True)
                return
            payload = substitute_auth(payload, token)

        async with local.lock:
            await local.ws.send_str(payload)

    async def _async_close(self, session_id: str, tell_browser: bool = False) -> None:
        local = self._sockets.pop(session_id, None)
        if local is None:
            return
        if local.reader is not None:
            local.reader.cancel()
        if local.ws is not None and not local.ws.closed:
            await local.ws.close()
        if tell_browser:
            self._queue(session_id, "close", None)

    # -- frames from Home Assistant ----------------------------------------

    async def _async_read(self, local: _LocalSocket) -> None:
        try:
            async for message in local.ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    self._queue(local.session_id, "frame", message.data)
                elif message.type == aiohttp.WSMsgType.BINARY:
                    self._queue(local.session_id, "frame", message.data.decode("utf-8", "replace"))
                elif message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                                      aiohttp.WSMsgType.ERROR):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - the browser is told either way
            _LOGGER.debug("Local WebSocket for session %s ended: %s", local.session_id, err)
        # Home Assistant closed it (a restart, say). Tell the browser, which
        # will reconnect -- that is how the frontend survives a restart.
        if self._sockets.get(local.session_id) is local:
            self._sockets.pop(local.session_id, None)
            self._queue(local.session_id, "close", None)

    def _queue(self, session_id: str, kind: str, payload: Optional[str]) -> None:
        frame: Dict[str, Any] = {"session_id": int(session_id) if session_id.isdigit() else session_id,
                                 "kind": kind}
        if payload is not None:
            frame["payload"] = payload
        self._outbox.append(frame)
        if self._flusher is None or self._flusher.done():
            self._flusher = self._create_task(self._async_flush())

    async def _async_flush(self) -> None:
        # A burst of state changes becomes one POST.
        await asyncio.sleep(ACCESS_SCREEN_FRAME_FLUSH)
        while self._outbox:
            batch, self._outbox = self._outbox[:400], self._outbox[400:]
            try:
                await self.tunnel.access.api.post_access_frames(
                    self.tunnel.access.installation_id, batch
                )
            except RemoteAccessUnavailable:
                self._outbox.clear()
                return
            except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError, ValueError) as err:
                # The browser's frontend will see a stalled socket and
                # reconnect; dropping beats replaying stale state later.
                _LOGGER.warning("Could not post Remote Screen frames: %s", err)
                return

    # -- lifecycle ---------------------------------------------------------

    async def async_prune(self) -> None:
        """Close sockets whose session is no longer a live screen session."""
        for session_id in list(self._sockets):
            if self.tunnel.access.scope_for(session_id) != ACCESS_SCOPE_SCREEN:
                await self._async_close(session_id)

    async def async_close_all(self) -> None:
        for session_id in list(self._sockets):
            await self._async_close(session_id)

    def _create_task(self, coro):
        creator = getattr(self.hass, "async_create_background_task", None)
        if creator is not None:
            return creator(coro, name="ha_dispatch_client_screen")
        return self.hass.async_create_task(coro)
