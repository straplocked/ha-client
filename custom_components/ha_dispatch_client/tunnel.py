"""The transport half of consent-gated remote access.

The agent never listens on a port. It long-polls the Dispatch server for
requests a technician has already been authorised to make, runs them against
this Home Assistant, and posts the responses back. Every connection is
outbound, which is what makes this work behind CGNAT and a router nobody wants
to touch.

This module makes no authorisation decisions. The server rules on scope before
anything is queued, and remote_access.py decides which sessions are live. What
happens here is the one thing neither of those can do: hold a credential for
the local Home Assistant, and refuse the things this installation will not
relay regardless of what the server says.

### Why a Home Assistant system user, and what it can reach

The relay strips `Cookie` and `Authorization` before a request arrives, on
purpose -- the technician's Dispatch session has no business authenticating
against a customer's Home Assistant. So the agent has to supply its own local
credential, and the choice of credential is the whole security story.

We mint Home Assistant *system users* through `hass.auth` and hold a refresh
token for each, which is the mechanism Home Assistant itself sanctions for an
integration that needs to call the local API (it is how Supervisor talks to
Core). Requests then go over loopback to Home Assistant's own REST API, so
every one of them passes through Home Assistant's authentication and
permission checks on the way in. A bug in this integration cannot hand out
more than the token's group already allows -- the enforcement is not ours.

Least privilege is the choice of *which* token:

- `diagnostic` sessions use a user in the read-only group. Home Assistant
  itself refuses any write, so a read-only session cannot change anything even
  if the server were compromised and queued a `POST`.
- `maintenance` and `full` sessions use a user in the admin group, because
  restarting, reloading and running repairs is precisely what those scopes
  were granted for.
- A session whose scope we have no record of gets nothing. Fail closed.

Both users are created lazily. An installation that only ever grants
diagnostic access never has an admin-capable credential on it at all.

On top of that, only Home Assistant's REST API under `/api/` is relayed. The
frontend, the auth endpoints at `/auth/token`, and anything else this process
serves are out of reach no matter what the server queues -- that is the "refuse
anything your own configuration forbids" half of the contract.

Agent-side spec: docs/technical/remote-access.md
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any, Dict, Optional

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client

from .api_client import RemoteAccessUnavailable
from .const import (
    ACCESS_ALLOWED_PATH_PREFIX,
    ACCESS_DENIED_PATH_PREFIXES,
    ACCESS_EXECUTE_TIMEOUT,
    ACCESS_LOCAL_FALLBACK_URL,
    ACCESS_MAX_RESPONSE_BYTES,
    ACCESS_POLL_BACKOFF,
    ACCESS_READ_ONLY_SCOPES,
    ACCESS_SYSTEM_USER_ADMIN,
    ACCESS_SYSTEM_USER_READ_ONLY,
)

_LOGGER = logging.getLogger(__name__)

# Privilege levels, keyed by the Home Assistant group the system user joins.
GROUP_READ_ONLY = "system-read-only"
GROUP_ADMIN = "system-admin"

_SYSTEM_USER_NAMES = {
    GROUP_READ_ONLY: ACCESS_SYSTEM_USER_READ_ONLY,
    GROUP_ADMIN: ACCESS_SYSTEM_USER_ADMIN,
}

# Headers that must not be forwarded to the local Home Assistant even though
# the relay let them through. Authorization is ours to set; the rest are
# hop-by-hop or would misdescribe the body we are about to send.
_DROPPED_REQUEST_HEADERS = frozenset({
    "authorization", "cookie", "host", "connection", "keep-alive",
    "content-length", "transfer-encoding", "upgrade", "te", "trailer",
})

# Response headers worth relaying back. Everything else is noise or leaks
# something about the local instance.
_RELAYED_RESPONSE_HEADERS = ("content-type", "content-disposition")

_TEXTUAL_TYPES = frozenset({
    "application/json", "application/xml", "application/javascript",
    "application/x-www-form-urlencoded",
})


def is_textual(content_type: str) -> bool:
    """Whether a response body can cross the relay as a plain string."""
    base = (content_type or "").split(";")[0].strip().lower()
    if not base:
        return True
    return (
        base.startswith("text/")
        or base in _TEXTUAL_TYPES
        or base.endswith("+json")
        or base.endswith("+xml")
    )


def refusal_reason(method: str, path: str) -> Optional[str]:
    """Local policy, applied after the server has already checked scope.

    Returns the reason to refuse, or None to proceed.
    """
    if not path.startswith(ACCESS_ALLOWED_PATH_PREFIX):
        return "This installation only relays the Home Assistant REST API under /api/."
    for denied in ACCESS_DENIED_PATH_PREFIXES:
        if path.startswith(denied):
            return "Streaming endpoints cannot be relayed."
    return None


class HADispatchTunnel:
    """Long-polls for authorised requests and runs them locally."""

    def __init__(self, hass: HomeAssistant, access) -> None:
        """Initialise the tunnel.

        access is the HADispatchRemoteAccess that owns consent state; the
        tunnel asks it which sessions are live and what scope each was granted,
        and never decides either for itself.
        """
        self.hass = hass
        self.access = access
        self._task: asyncio.Task | None = None
        # group id -> refresh token. Minted on first use, kept for the life of
        # the process; the token itself is short-lived and derived per request.
        self._refresh_tokens: Dict[str, Any] = {}

    # -- lifecycle ----------------------------------------------------------

    @property
    def running(self) -> bool:
        """Whether the poll loop is currently up."""
        return self._task is not None and not self._task.done()

    def async_sync(self) -> None:
        """Start or stop the loop to match the live-session state.

        Called after anything that could have changed which sessions are live.
        Idempotent, so callers do not have to know the current state.
        """
        if self.access.has_live_sessions():
            self.async_start()
        else:
            self.async_stop()

    def async_start(self) -> None:
        """Bring the poll loop up, if it is not already."""
        if self.running:
            return
        _LOGGER.info("Remote access session is live; starting the request tunnel")
        self._task = self._create_task(self._async_run())

    def async_stop(self) -> None:
        """Take the poll loop down, if it is up."""
        if self._task is None:
            return
        if not self._task.done():
            _LOGGER.info("No live remote access session; stopping the request tunnel")
            self._task.cancel()
        self._task = None

    def _create_task(self, coro):
        """Create a background task, tolerating older Home Assistant cores."""
        creator = getattr(self.hass, "async_create_background_task", None)
        if creator is not None:
            return creator(coro, name="ha_dispatch_client_tunnel")
        return self.hass.async_create_task(coro)

    async def _async_run(self) -> None:
        """Poll until no session is live any more.

        The availability check matters as much as the liveness one: a server
        that withdraws the endpoints mid-session would otherwise leave this
        looping against a 404 with sessions still nominally live.
        """
        try:
            while self.access.available and self.access.has_live_sessions():
                if not await self.async_pump_once():
                    await asyncio.sleep(ACCESS_POLL_BACKOFF)
        except asyncio.CancelledError:
            raise
        finally:
            _LOGGER.debug("Request tunnel loop finished")

    # -- one turn of the loop ----------------------------------------------

    async def async_pump_once(self) -> bool:
        """Claim whatever is waiting and answer all of it.

        Returns False when the poll itself failed, which is the caller's cue to
        back off rather than spin.
        """
        try:
            requests = await self.access.api.poll_access(self.access.installation_id)
        except RemoteAccessUnavailable:
            _LOGGER.warning("Server withdrew the remote access endpoints; stopping")
            self.access.async_disable()
            return False
        except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError, ValueError) as err:
            _LOGGER.debug("Remote access poll failed: %s", err)
            return False

        if not requests:
            return True

        # The technician is waiting on the whole batch, not the slowest item in
        # sequence.
        await asyncio.gather(
            *(self._async_answer(request) for request in requests),
            return_exceptions=True,
        )
        return True

    async def _async_answer(self, request: Dict[str, Any]) -> None:
        """Run one request and post whatever came of it."""
        request_id = request.get("request_id")
        if not request_id:
            return

        try:
            result = await self._async_execute(request)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - the technician gets the reason
            _LOGGER.error("Relayed request failed: %s", err)
            result = {"error": str(err)}

        try:
            await self.access.api.respond_to_exchange(
                self.access.installation_id,
                request_id,
                status=result.get("status"),
                headers=result.get("headers"),
                body=result.get("body"),
                error=result.get("error"),
            )
        except (RemoteAccessUnavailable, aiohttp.ClientError, TimeoutError,
                asyncio.TimeoutError, ValueError) as err:
            # Nothing to do about it: the exchange times out on the server and
            # the technician sees a 504, which is the truth.
            _LOGGER.warning("Could not return a relayed response: %s", err)

    async def _async_execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Run a single authorised request against the local Home Assistant."""
        method = str(request.get("method") or "GET").upper()
        path = str(request.get("path") or "")

        scope = self.access.scope_for(request.get("session_id"))
        if scope is None:
            # Fail closed. We have no record of consent for this session, so we
            # have no basis for choosing a credential.
            return {"error": "No consent on record for this session."}

        refused = refusal_reason(method, path)
        if refused:
            _LOGGER.info("Refusing relayed request %s %s: %s", method, path, refused)
            return {"error": refused}

        token = await self._async_access_token(scope)
        if token is None:
            return {"error": "Could not obtain a local Home Assistant credential."}

        headers = {
            key: value
            for key, value in (request.get("headers") or {}).items()
            if key.lower() not in _DROPPED_REQUEST_HEADERS
        }
        headers["Authorization"] = f"Bearer {token}"

        url = self._local_base_url() + path
        query = request.get("query")
        if query:
            url = f"{url}?{query}"

        body = request.get("body")
        session = aiohttp_client.async_get_clientsession(self.hass)

        async def _call():
            async with session.request(
                method,
                url,
                headers=headers,
                data=body.encode("utf-8") if isinstance(body, str) else body,
            ) as response:
                raw = await response.read()
                return response.status, dict(response.headers), raw

        status, response_headers, raw = await asyncio.wait_for(
            _call(), timeout=ACCESS_EXECUTE_TIMEOUT
        )

        if len(raw) > ACCESS_MAX_RESPONSE_BYTES:
            return {
                "error": (
                    f"Response of {len(raw)} bytes exceeds the "
                    f"{ACCESS_MAX_RESPONSE_BYTES} byte relay limit."
                )
            }

        return self._encode_response(status, response_headers, raw)

    @staticmethod
    def _encode_response(
        status: int, response_headers: Dict[str, Any], raw: bytes
    ) -> Dict[str, Any]:
        """Shape a local response for the relay.

        Bodies cross as strings, so anything that is not text is base64 encoded
        and labelled as such. The content type is preserved either way, because
        it is the only thing telling the technician what they are looking at.
        """
        lowered = {key.lower(): value for key, value in response_headers.items()}
        headers = {
            key: lowered[key] for key in _RELAYED_RESPONSE_HEADERS if key in lowered
        }

        if is_textual(lowered.get("content-type", "")):
            body = raw.decode("utf-8", errors="replace")
        else:
            body = base64.b64encode(raw).decode("ascii")
            headers["Content-Transfer-Encoding"] = "base64"

        return {"status": status, "headers": headers, "body": body}

    # -- local credentials --------------------------------------------------

    def _group_for(self, scope: str) -> str:
        """The least-privileged Home Assistant group that can serve a scope."""
        return GROUP_READ_ONLY if scope in ACCESS_READ_ONLY_SCOPES else GROUP_ADMIN

    async def _async_access_token(self, scope: str) -> Optional[str]:
        """Mint a short-lived access token for the privilege this scope needs."""
        group_id = self._group_for(scope)

        try:
            refresh_token = self._refresh_tokens.get(group_id)
            if refresh_token is None:
                refresh_token = await self._async_refresh_token(group_id)
                self._refresh_tokens[group_id] = refresh_token
            return self.hass.auth.async_create_access_token(refresh_token)
        except (AttributeError, KeyError, TypeError, ValueError) as err:
            _LOGGER.error("Could not create a local Home Assistant token: %s", err)
            return None

    async def _async_refresh_token(self, group_id: str):
        """Find or create this integration's system user and its token."""
        name = _SYSTEM_USER_NAMES[group_id]

        user = None
        for candidate in await self.hass.auth.async_get_users():
            if candidate.name == name and getattr(candidate, "system_generated", False):
                user = candidate
                break

        if user is None:
            _LOGGER.info("Creating the %s Home Assistant user for remote access", name)
            user = await self.hass.auth.async_create_system_user(
                name, group_ids=[group_id]
            )

        # Reuse the token from a previous run rather than accumulating one per
        # restart in the user's token list.
        for token in getattr(user, "refresh_tokens", {}).values():
            if getattr(token, "client_name", None) == name:
                return token

        return await self.hass.auth.async_create_refresh_token(user, client_name=name)

    def _local_base_url(self) -> str:
        """Where this Home Assistant answers its own REST API.

        Prefers whatever Home Assistant considers its internal URL, since that
        is the one configured to work with however TLS is set up here, and
        falls back to loopback.
        """
        try:
            from homeassistant.helpers.network import get_url

            return get_url(
                self.hass,
                allow_external=False,
                allow_ip=True,
                require_current_request=False,
            ).rstrip("/")
        except Exception:  # noqa: BLE001 - any failure means "use loopback"
            port = getattr(getattr(self.hass, "http", None), "server_port", None)
            if port:
                return f"http://127.0.0.1:{port}"
            return ACCESS_LOCAL_FALLBACK_URL
