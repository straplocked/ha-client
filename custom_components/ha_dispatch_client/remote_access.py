"""Consent-gated remote access, the Home Assistant end.

A technician asks the Dispatch server for access to this installation. Nothing
happens until the person who owns this Home Assistant says yes -- and they say
it here, on the screen they already trust, without leaving the house's own
system for a web page they have no reason to believe.

So a pending request is surfaced two ways:

- a **persistent notification** carrying who is asking, why, what they will be
  able to do and for how long, and
- a **fixable Repairs issue** whose repair flow is a native Approve / Deny
  dialog.

`scope_description` comes from the server already written for a homeowner and
is passed through verbatim. Rewording it in technical language would defeat
the entire point of asking.

Anything that leaves the pending list -- answered here, answered on the web
consent page, or simply lapsed -- has its notification and its issue cleared
immediately. A prompt for a request that is already dead is worse than no
prompt at all: it teaches people that these prompts mean nothing.

While a session is live the customer gets an off-switch that works, because a
consent model without one is theatre.

This module owns consent and the session bookkeeping. It makes no decisions
about the local Home Assistant, which is tunnel.py's job, and it enforces no
scope, which is the server's.

Agent-side spec: docs/technical/remote-access.md
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

import aiohttp

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api_client import RemoteAccessConflict, RemoteAccessUnavailable
from .const import (
    ACCESS_ACTIVE_ISSUE_PREFIX,
    ACCESS_ACTIVE_NOTIFICATION_PREFIX,
    ACCESS_DECISION_GRANT,
    ACCESS_NOTIFICATION_PREFIX,
    ACCESS_REQUEST_ISSUE_PREFIX,
    ACCESS_SCOPE_DIAGNOSTIC,
    ACCESS_STORAGE_KEY,
    ACCESS_STORAGE_VERSION,
    ACCESS_UNKNOWN_REQUESTER,
    DOMAIN,
)
from .tunnel import HADispatchTunnel

_LOGGER = logging.getLogger(__name__)

# Used when the server sends no duration, which it should never do.
_FALLBACK_DURATION_MINUTES = 30


def requester_name(record: Dict[str, Any]) -> str:
    """Who is asking, in a form safe to put in a notification title."""
    return str(record.get("requested_by") or ACCESS_UNKNOWN_REQUESTER)


def build_prompt(record: Dict[str, Any]) -> tuple[str, str]:
    """Render the persistent notification for a pending request.

    Everything the person needs in order to decide, and nothing they have to go
    and look up: who, why, what, how long. The scope description is the
    server's own homeowner-facing wording, used as-is.
    """
    who = requester_name(record)
    reason = str(record.get("reason") or "Not given")
    what = str(record.get("scope_description") or "")
    minutes = record.get("duration_minutes")

    title = f"{who} is asking to access your Home Assistant"

    lines = [
        f"Reason: {reason}",
        "",
        f"What they will be able to do: {what}",
        "",
        f"For how long: {minutes} minutes.",
    ]

    consent_url = record.get("consent_url")
    lines += [
        "",
        "Approve or decline under Settings > System > Repairs.",
    ]
    if consent_url:
        lines.append(f"You can also answer here: {consent_url}")

    return title, "\n".join(lines)


def issue_placeholders(record: Dict[str, Any]) -> Dict[str, str]:
    """The same four facts, for the Repairs dialog's translated text."""
    return {
        "requested_by": requester_name(record),
        "reason": str(record.get("reason") or "Not given"),
        "scope_description": str(record.get("scope_description") or ""),
        "scope_label": str(record.get("scope_label") or ""),
        "duration_minutes": str(record.get("duration_minutes")),
    }


class HADispatchRemoteAccess:
    """Surfaces consent requests in Home Assistant and reports the answers."""

    def __init__(self, hass: HomeAssistant, coordinator) -> None:
        """Initialise. Nothing touches the network until the first poll."""
        self.hass = hass
        self.coordinator = coordinator
        self.available = True
        self.policy: Optional[str] = None
        self.standing_consent_until: Optional[str] = None

        # session id (as a string) -> the request body the server sent.
        self.pending: Dict[str, Dict[str, Any]] = {}
        # session id (as a string) -> {"scope": str, "until": datetime, ...}
        self._live: Dict[str, Dict[str, Any]] = {}

        self._store = Store(hass, ACCESS_STORAGE_VERSION, ACCESS_STORAGE_KEY)
        self.tunnel = HADispatchTunnel(hass, self)

    # -- what the tunnel asks us -------------------------------------------

    @property
    def api(self):
        """The API client, which the coordinator may have re-tokened."""
        return self.coordinator.api_client

    @property
    def installation_id(self):
        """The installation id, which re-enrolment may have changed."""
        return self.coordinator.installation_id

    def has_live_sessions(self) -> bool:
        """Whether any session might still be serving requests."""
        return bool(self._live)

    def scope_for(self, session_id: Any) -> Optional[str]:
        """The scope a live session was granted, or None if we have no record.

        None is the fail-closed answer: the tunnel refuses rather than guessing
        at a privilege level.
        """
        entry = self._live.get(str(session_id))
        return entry["scope"] if entry else None

    def async_disable(self) -> None:
        """Stop trying: this server does not offer remote access."""
        self.available = False
        self.tunnel.async_stop()

    # -- lifecycle ----------------------------------------------------------

    async def async_load(self) -> None:
        """Restore sessions that were live before the last restart.

        Restarting Home Assistant is one of the main reasons to grant
        maintenance access in the first place, so losing the session across the
        restart would break the feature exactly when it is being used.
        """
        try:
            stored = await self._store.async_load()
        except (OSError, ValueError) as err:
            _LOGGER.warning("Could not read stored remote access sessions: %s", err)
            return

        if not stored:
            return

        now = dt_util.utcnow()
        for session_id, entry in (stored.get("sessions") or {}).items():
            until = dt_util.parse_datetime(entry.get("until") or "")
            if until is None or until <= now:
                continue
            self._live[str(session_id)] = {
                "scope": entry.get("scope") or ACCESS_SCOPE_DIAGNOSTIC,
                "scope_label": entry.get("scope_label") or "",
                "requested_by": entry.get("requested_by") or ACCESS_UNKNOWN_REQUESTER,
                "until": until,
                "announced": bool(entry.get("announced")),
            }

        if self._live:
            _LOGGER.info(
                "Resuming %s remote access session(s) after restart", len(self._live)
            )
            self.tunnel.async_sync()

    async def async_unload(self) -> None:
        """Stop polling. Live sessions stay on disk; consent is not withdrawn."""
        self.tunnel.async_stop()

    # -- the consent loop ---------------------------------------------------

    async def async_poll_pending(self) -> None:
        """Fetch the pending list and reconcile everything we are showing.

        Called on the coordinator's cadence, and deliberately incapable of
        raising: a customer's Home Assistant must keep reporting metrics even
        when remote access is broken.
        """
        try:
            await self._async_poll_pending()
        except Exception as err:  # noqa: BLE001 - see the docstring
            _LOGGER.error("Unexpected error handling access requests: %s", err)

    async def _async_poll_pending(self) -> None:
        """One consent cycle: fetch, reconcile, expire, persist, sync."""
        if not self.available:
            return

        try:
            payload = await self.api.fetch_pending_access(self.installation_id)
        except RemoteAccessUnavailable:
            _LOGGER.info(
                "This Dispatch server has no remote access endpoints; not asking again"
            )
            self.async_disable()
            return
        except (aiohttp.ClientError, TimeoutError, KeyError, ValueError) as err:
            # A transient failure must not clear prompts that are still valid,
            # so leave everything exactly as it is.
            _LOGGER.debug("Could not fetch pending access requests: %s", err)
            return

        self.policy = payload.get("policy")
        self.standing_consent_until = payload.get("standing_consent_until")
        self._reconcile(payload.get("requests") or [])
        self._expire_live()
        await self._async_persist()
        self.tunnel.async_sync()
        self._notify_listeners()

    def _reconcile(self, requests: List[Dict[str, Any]]) -> None:
        """Bring the prompts on screen into line with the server's list."""
        incoming = {str(request.get("id")): request for request in requests}

        for session_id, request in incoming.items():
            if session_id not in self.pending:
                self._announce(session_id, request)
            self.pending[session_id] = request

        for session_id in list(self.pending):
            if session_id in incoming:
                continue
            # It left the list and we did not answer it here, so it was
            # answered on the web consent page or it lapsed. Nothing here can
            # tell which, and guessing "denied" would leave a session the
            # customer really did grant with no agent on the other end.
            #
            # So the tunnel runs for the window the session could occupy -- the
            # server only ever queues work for a session that is genuinely
            # active, so polling for one that is not costs an idle connection
            # and nothing else -- but no "access is active" prompt is raised.
            # Claiming access is live when the customer may have just declined
            # it is the same lie as leaving a dead request on screen.
            record = self.pending[session_id]
            self._clear_prompt(session_id)
            self._mark_live(session_id, record, announce=False)

    def _announce(self, session_id: str, record: Dict[str, Any]) -> None:
        """Put a new request in front of the customer, both ways."""
        title, message = build_prompt(record)

        persistent_notification.async_create(
            self.hass,
            message,
            title=title,
            notification_id=f"{ACCESS_NOTIFICATION_PREFIX}{session_id}",
        )

        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{ACCESS_REQUEST_ISSUE_PREFIX}{session_id}",
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="access_request",
            translation_placeholders=issue_placeholders(record),
            data={"session_id": session_id},
        )

        _LOGGER.info(
            "Remote access requested by %s (%s) for %s minutes",
            requester_name(record),
            record.get("scope"),
            record.get("duration_minutes"),
        )

    def _clear_prompt(self, session_id: str) -> None:
        """Take a request's notification and Repairs issue down."""
        self.pending.pop(session_id, None)
        persistent_notification.async_dismiss(
            self.hass, f"{ACCESS_NOTIFICATION_PREFIX}{session_id}"
        )
        ir.async_delete_issue(
            self.hass, DOMAIN, f"{ACCESS_REQUEST_ISSUE_PREFIX}{session_id}"
        )

    # -- answering ----------------------------------------------------------

    async def async_respond(
        self,
        session_id: Any,
        decision: str,
        note: Optional[str] = None,
        responder: Optional[str] = None,
    ) -> bool:
        """Report the customer's decision, then clear the prompt either way.

        Returns True when the server accepted the decision. A 422 means the
        request had already been answered or had lapsed, which is a normal
        outcome: the prompt still comes down, and nothing is retried.
        """
        key = str(session_id)
        record = self.pending.get(key, {})

        try:
            result = await self.api.respond_to_access(
                self.installation_id,
                session_id,
                decision,
                note=note,
                responder=responder,
            )
        except RemoteAccessConflict:
            _LOGGER.info(
                "Access request %s was already answered or had lapsed", session_id
            )
            self._clear_prompt(key)
            self._notify_listeners()
            return False
        except (RemoteAccessUnavailable, aiohttp.ClientError, TimeoutError,
                ValueError) as err:
            _LOGGER.error("Could not report the access decision: %s", err)
            return False

        self._clear_prompt(key)

        if decision == ACCESS_DECISION_GRANT:
            self._mark_live(key, record, expires_at=result.get("expires_at"))
        else:
            _LOGGER.info("Remote access request %s declined", session_id)

        await self._async_persist()
        self.tunnel.async_sync()
        self._notify_listeners()
        return True

    async def async_revoke(
        self,
        session_id: Any = None,
        note: Optional[str] = None,
    ) -> int:
        """Close a live session, or every live session. The off-switch.

        Returns how many sessions were closed. A session the server has
        already closed counts as closed here too.
        """
        targets = [str(session_id)] if session_id is not None else list(self._live)
        closed = 0

        for key in targets:
            try:
                await self.api.revoke_access(self.installation_id, key, note=note)
                closed += 1
            except RemoteAccessConflict:
                _LOGGER.info("Access session %s was already closed", key)
                closed += 1
            except (RemoteAccessUnavailable, aiohttp.ClientError, TimeoutError,
                    ValueError) as err:
                _LOGGER.error("Could not revoke access session %s: %s", key, err)
                continue

            self._end_live(key)

        await self._async_persist()
        self.tunnel.async_sync()
        self._notify_listeners()
        return closed

    # -- live session bookkeeping ------------------------------------------

    def live_sessions(self) -> List[Dict[str, Any]]:
        """Live sessions, shaped for entity attributes."""
        return [
            {
                "session_id": key,
                "scope": entry["scope"],
                "scope_label": entry.get("scope_label") or "",
                "requested_by": entry.get("requested_by") or ACCESS_UNKNOWN_REQUESTER,
                "until": entry["until"].isoformat(),
                # False when the session was answered on the web consent page:
                # we serve it, but we never claimed on screen that it is live.
                "confirmed_here": bool(entry.get("announced")),
            }
            for key, entry in sorted(self._live.items())
        ]

    def _mark_live(
        self,
        session_id: str,
        record: Dict[str, Any],
        expires_at: Optional[str] = None,
        announce: bool = True,
    ) -> None:
        """Record a session as able to serve requests.

        announce is False for a session we only *infer* is live, because it
        left the pending list without being answered here. The tunnel runs
        either way; the on-screen "access is active" prompt and its one-tap
        off-switch are only raised when we know consent was actually given.
        """
        until = dt_util.parse_datetime(expires_at) if expires_at else None
        if until is None:
            minutes = record.get("duration_minutes") or _FALLBACK_DURATION_MINUTES
            try:
                minutes = int(minutes)
            except (TypeError, ValueError):
                minutes = _FALLBACK_DURATION_MINUTES
            until = dt_util.utcnow() + timedelta(minutes=minutes)

        scope = record.get("scope") or ACCESS_SCOPE_DIAGNOSTIC
        scope_label = record.get("scope_label") or ""
        who = requester_name(record)

        self._live[session_id] = {
            "scope": scope,
            "scope_label": scope_label,
            "requested_by": who,
            "until": until,
            "announced": announce,
        }

        if not announce:
            _LOGGER.info(
                "Access request %s was answered elsewhere; serving it until %s",
                session_id,
                until,
            )
            return

        persistent_notification.async_create(
            self.hass,
            (
                f"{who} can access your Home Assistant until "
                f"{until.isoformat()}.\n\n"
                f"Access level: {scope_label or scope}\n\n"
                "You can end this at any time under Settings > System > Repairs, "
                f"or with the {DOMAIN}.revoke_access service."
            ),
            title="Remote access is active",
            notification_id=f"{ACCESS_ACTIVE_NOTIFICATION_PREFIX}{session_id}",
        )

        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{ACCESS_ACTIVE_ISSUE_PREFIX}{session_id}",
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="access_active",
            translation_placeholders={
                "requested_by": who,
                "scope_label": scope_label or scope,
                "until": until.isoformat(),
            },
            data={"session_id": session_id},
        )

        _LOGGER.info("Remote access session %s is live until %s", session_id, until)

    def _end_live(self, session_id: str) -> None:
        """Drop a session and take its "access is active" prompts down."""
        self._live.pop(session_id, None)
        persistent_notification.async_dismiss(
            self.hass, f"{ACCESS_ACTIVE_NOTIFICATION_PREFIX}{session_id}"
        )
        ir.async_delete_issue(
            self.hass, DOMAIN, f"{ACCESS_ACTIVE_ISSUE_PREFIX}{session_id}"
        )

    def _expire_live(self) -> None:
        """Forget sessions that have run out of time."""
        now = dt_util.utcnow()
        for key in [k for k, v in self._live.items() if v["until"] <= now]:
            _LOGGER.info("Remote access session %s has expired", key)
            self._end_live(key)

    async def _async_persist(self) -> None:
        """Write live sessions to disk so a restart does not end them."""
        try:
            await self._store.async_save(
                {
                    "sessions": {
                        key: {
                            "scope": entry["scope"],
                            "scope_label": entry.get("scope_label") or "",
                            "requested_by": entry.get("requested_by") or "",
                            "until": entry["until"].isoformat(),
                            "announced": bool(entry.get("announced")),
                        }
                        for key, entry in self._live.items()
                    }
                }
            )
        except (OSError, ValueError) as err:
            _LOGGER.warning("Could not persist remote access sessions: %s", err)

    def _notify_listeners(self) -> None:
        """Push the binary sensor without waiting for the next refresh."""
        notify = getattr(self.coordinator, "async_update_listeners", None)
        if notify is not None:
            notify()


def async_all_managers(hass: HomeAssistant) -> List[HADispatchRemoteAccess]:
    """Every configured installation's remote access manager."""
    return [
        coordinator.remote_access
        for coordinator in (hass.data.get(DOMAIN) or {}).values()
        if getattr(coordinator, "remote_access", None) is not None
    ]


def async_find_manager(
    hass: HomeAssistant, session_id: Any = None
) -> Optional[HADispatchRemoteAccess]:
    """Find the manager holding a session, for code with no coordinator.

    The Repairs flow is handed an issue id and nothing else, so this is how it
    gets back to the installation the request belongs to. With several
    installations configured, the one that knows about the session wins.
    """
    managers = async_all_managers(hass)

    if session_id is not None:
        key = str(session_id)
        for manager in managers:
            if key in manager.pending or manager.scope_for(key) is not None:
                return manager

    return managers[0] if managers else None


__all__ = [
    "HADispatchRemoteAccess",
    "async_all_managers",
    "async_find_manager",
    "build_prompt",
    "issue_placeholders",
    "requester_name",
]
