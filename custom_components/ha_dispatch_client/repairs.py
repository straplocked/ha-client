"""Repair flows for HA Dispatch Client.

Home Assistant's Repairs panel is where a fixable issue gets a native dialog,
and that is the whole reason consent lives here: the customer taps Approve or
Deny on the screen they already trust, in the system they already own, without
being sent to a web page to prove anything.

Two flows:

- ``access_request_<session>`` -- Approve / Deny a pending request.
- ``access_active_<session>``  -- end a session that is currently live. The
  off-switch, one tap, no YAML.

Neither flow decides anything. Both hand the decision to
``HADispatchRemoteAccess``, which reports it to the server and takes the
prompts down.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import (
    ACCESS_ACTIVE_ISSUE_PREFIX,
    ACCESS_DECISION_DENY,
    ACCESS_DECISION_GRANT,
    ACCESS_REQUEST_ISSUE_PREFIX,
)
from .remote_access import async_find_manager

_LOGGER = logging.getLogger(__name__)


def session_id_from(issue_id: str, data: Optional[dict[str, Any]]) -> Optional[str]:
    """Recover the session this issue is about.

    ``data`` is what was passed to ``async_create_issue`` and is the intended
    route. The issue id is parsed as a fallback so a flow started against an
    issue restored from the registry still works.
    """
    if data and data.get("session_id") is not None:
        return str(data["session_id"])

    for prefix in (ACCESS_REQUEST_ISSUE_PREFIX, ACCESS_ACTIVE_ISSUE_PREFIX):
        if issue_id.startswith(prefix):
            return issue_id[len(prefix):] or None

    return None


class HADispatchConsentRepairFlow(RepairsFlow):
    """Approve or deny a pending remote access request."""

    def __init__(self, session_id: Optional[str]) -> None:
        """Remember which request this dialog is answering."""
        self._session_id = session_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Offer the two answers, and nothing else."""
        return self.async_show_menu(
            step_id="init", menu_options=["grant", "deny"]
        )

    async def async_step_grant(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """The customer approved."""
        await self._async_answer(ACCESS_DECISION_GRANT)
        return self.async_create_entry(title="", data={})

    async def async_step_deny(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """The customer declined."""
        await self._async_answer(ACCESS_DECISION_DENY)
        return self.async_create_entry(title="", data={})

    async def _async_answer(self, decision: str) -> None:
        """Report the decision verbatim."""
        manager = async_find_manager(self.hass, self._session_id)
        if manager is None or self._session_id is None:
            _LOGGER.error("No HA Dispatch installation owns access request %s",
                          self._session_id)
            return

        await manager.async_respond(
            self._session_id,
            decision,
            responder=await async_responder_name(self.hass, self.context),
        )


class HADispatchRevokeRepairFlow(RepairsFlow):
    """End a live remote access session."""

    def __init__(self, session_id: Optional[str]) -> None:
        """Remember which session this dialog would close."""
        self._session_id = session_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Confirm before pulling the plug."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show the confirmation, then revoke on submit."""
        if user_input is None:
            return self.async_show_form(step_id="confirm", data_schema=None)

        manager = async_find_manager(self.hass, self._session_id)
        if manager is not None and self._session_id is not None:
            await manager.async_revoke(self._session_id, note="Ended in Home Assistant")

        return self.async_create_entry(title="", data={})


async def async_responder_name(hass: HomeAssistant, context: Any) -> Optional[str]:
    """Name the Home Assistant user who acted, when that is knowable.

    The receipt on the server says "somebody agreed" unless it is told who, so
    this is worth the effort. Home Assistant does not guarantee a user id on a
    repair flow's context, though, so this is best-effort: an unknown user
    means the field is simply omitted rather than guessed at.
    """
    user_id = None
    if isinstance(context, dict):
        user_id = context.get("user_id")

    if not user_id:
        return None

    try:
        user = await hass.auth.async_get_user(user_id)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None

    return getattr(user, "name", None)


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Home Assistant's entry point into the flows above."""
    session_id = session_id_from(issue_id, data)

    if issue_id.startswith(ACCESS_ACTIVE_ISSUE_PREFIX):
        return HADispatchRevokeRepairFlow(session_id)

    return HADispatchConsentRepairFlow(session_id)
