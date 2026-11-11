"""Enhanced permissions ("Hands-off remote configuration"), the house's end.

Enhanced permissions are a homeowner's standing yes to their installer setting
up and changing their Home Assistant remotely -- dashboards, floor plans,
configuration -- without asking each time. The grant lives on the server.
There are three ways it comes about:

- the homeowner approves a request in Home Assistant (Repairs, through
  remote_access.py -- a standing scope like any other request);
- the homeowner approves it on the web consent page;
- an admin at the installer ticks the box in Dispatch, attesting that the
  homeowner agreed (``method: admin_attested``).

The first two the homeowner did themselves. The third they did not do *here*,
so this module tells them, in their own Home Assistant: hands-off
configuration is on, who turned it on and when, and how to turn it off -- a
revoke-only link from the server and the ``revoke_enhanced_permissions``
action. One notification per grant, remembered across restarts by
``grant_id``, taken down when the grant goes.

Polled on the coordinator cycle and unable to raise, like everything else on
it. A 404 means a server that predates the feature: asked once per run, and
never mistaken for the installation having gone.

Design: docs/technical/remote-access.md, "Enhanced permissions".
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api_client import EnhancedPermissionsUnavailable
from .const import (
    DOMAIN,
    ENHANCED_METHOD_ADMIN_ATTESTED,
    ENHANCED_NOTIFICATION_ID,
    ENHANCED_STORAGE_KEY,
    ENHANCED_STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

NOTICE_TITLE = "Your installer turned on hands-off configuration"

# Shown when the server does not name the admin.
_UNNAMED_GRANTER = "Your installer"


def _when(granted_at: Any) -> Optional[str]:
    """``2026-11-11T14:05:00+00:00`` -> ``11 November 2026 at 14:05``, local time."""
    parsed = dt_util.parse_datetime(granted_at) if granted_at else None
    if parsed is None:
        return None
    as_local = getattr(dt_util, "as_local", None)
    if as_local is not None:
        parsed = as_local(parsed)
    return f"{parsed.day} {parsed:%B %Y} at {parsed:%H:%M}"


def build_notice(state: Any) -> Optional[Tuple[str, str]]:
    """The notification's (title, message), or None if nothing is to be said.

    Pure; tested directly. Only a grant that is on AND was attested by an
    admin is announced: a grant the homeowner gave themselves needs no "this
    is on" from us, and one that is off needs nothing at all.
    """
    if not isinstance(state, dict) or not state.get("granted"):
        return None
    if state.get("method") != ENHANCED_METHOD_ADMIN_ATTESTED:
        return None

    who = str(state.get("granted_by") or "").strip() or _UNNAMED_GRANTER
    when = _when(state.get("granted_at"))
    turned_on = f"{who} turned on hands-off configuration for your Home Assistant"
    turned_on += f" on {when}." if when else "."

    off_switch = state.get("off_switch_url")
    how_to_turn_off = []
    if isinstance(off_switch, str) and off_switch.strip():
        how_to_turn_off.append(f"[Turn it off]({off_switch.strip()})")
        how_to_turn_off.append("")
        how_to_turn_off.append(
            "Or, from here: open Developer tools > Actions and run "
            f"`{DOMAIN}.revoke_enhanced_permissions`."
        )
    else:
        how_to_turn_off.append(
            "To turn it off: open Developer tools > Actions and run "
            f"`{DOMAIN}.revoke_enhanced_permissions`."
        )

    message = "\n".join(
        [
            turned_on,
            "",
            "It lets your installer set up and update your Home Assistant "
            "remotely -- dashboards, floor plans, configuration -- without "
            "asking you each time. Everything they do is recorded under "
            "their name.",
            "",
            "It stays on until you turn it off.",
            "",
            *how_to_turn_off,
        ]
    )
    return NOTICE_TITLE, message


class HADispatchEnhancedPermissions:
    """Tells the homeowner about an admin-attested grant, and turns it off."""

    def __init__(self, hass: HomeAssistant, coordinator) -> None:
        """Initialise. Nothing touches the network until the first poll."""
        self.hass = hass
        self.coordinator = coordinator
        self._store = Store(hass, ENHANCED_STORAGE_VERSION, ENHANCED_STORAGE_KEY)
        # The last state the server sent, for anything that wants to show it.
        self.state: Optional[Dict[str, Any]] = None
        # The grant_id whose notification is up (or was taken down by the
        # homeowner -- dismissing it is their business, not a reason to raise
        # it again). Persisted, so a restart does not say it twice.
        self._announced_grant_id: Optional[Any] = None
        # A 404 means a server without the feature: asked once per run.
        self._unavailable = False

    @property
    def available(self) -> bool:
        return not self._unavailable

    @property
    def granted(self) -> bool:
        return bool((self.state or {}).get("granted"))

    async def async_load(self) -> None:
        """Read which grant was last announced."""
        try:
            data = await self._store.async_load()
        except Exception as err:  # noqa: BLE001 - a corrupt file must not stop setup
            _LOGGER.warning("Could not read enhanced-permissions state: %s", err)
            data = None
        if isinstance(data, dict):
            self._announced_grant_id = data.get("announced_grant_id")

    async def _async_save(self) -> None:
        await self._store.async_save({"announced_grant_id": self._announced_grant_id})

    async def async_poll(self) -> None:
        """One cycle: fetch and reconcile the notification. Never raises."""
        try:
            await self._async_poll()
        except Exception as err:  # noqa: BLE001 - metrics must keep flowing
            _LOGGER.error("Unexpected error checking enhanced permissions: %s", err)

    async def _async_poll(self) -> None:
        if self._unavailable:
            return
        api = getattr(self.coordinator, "api_client", None)
        installation_id = getattr(self.coordinator, "installation_id", None)
        if api is None or installation_id is None:
            return

        try:
            state = await api.fetch_enhanced_permissions(installation_id)
        except EnhancedPermissionsUnavailable:
            self._unavailable = True
            _LOGGER.info(
                "This Dispatch server has no enhanced-permissions endpoint; not asking again"
            )
            return
        except (aiohttp.ClientError, TimeoutError, KeyError, ValueError) as err:
            # Transient: leave whatever is on screen exactly as it is.
            _LOGGER.debug("Could not fetch enhanced permissions: %s", err)
            return

        if not isinstance(state, dict):
            return
        self.state = state
        await self._async_reconcile(state)

    async def _async_reconcile(self, state: Dict[str, Any]) -> None:
        notice = build_notice(state)
        grant_id = state.get("grant_id")

        if notice is not None and grant_id is not None:
            # Compared as text: the id round-trips through JSON storage, and an
            # int on the wire and a string on disk are the same grant.
            if str(grant_id) == str(self._announced_grant_id):
                return
            title, message = notice
            persistent_notification.async_create(
                self.hass, message, title=title, notification_id=ENHANCED_NOTIFICATION_ID
            )
            self._announced_grant_id = grant_id
            await self._async_save()
            _LOGGER.info(
                "Told the homeowner hands-off configuration is on (grant %s)", grant_id
            )
            return

        # Off, or on by the homeowner's own hand: an installer's notice from
        # an earlier grant is no longer true, so it comes down -- once.
        if self._announced_grant_id is not None:
            self._dismiss()
            await self._async_save()

    def _dismiss(self) -> None:
        persistent_notification.async_dismiss(self.hass, ENHANCED_NOTIFICATION_ID)
        self._announced_grant_id = None

    async def async_revoke(self) -> bool:
        """The homeowner's off-switch. Returns whether the server took it.

        Recorded by the server as an in-app revoke. The notification comes
        down only once the server has said yes, so a failure leaves the
        homeowner looking at something that is still true.
        """
        api = getattr(self.coordinator, "api_client", None)
        installation_id = getattr(self.coordinator, "installation_id", None)
        if api is None or installation_id is None:
            return False
        try:
            await api.revoke_enhanced_permissions(installation_id)
        except EnhancedPermissionsUnavailable:
            self._unavailable = True
            _LOGGER.error(
                "This Dispatch server has no enhanced permissions to turn off"
            )
            return False
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.error("Could not turn enhanced permissions off: %s", err)
            return False

        self.state = {**(self.state or {}), "granted": False}
        self._dismiss()
        await self._async_save()
        _LOGGER.info("Hands-off configuration turned off from Home Assistant")
        return True


def async_all_managers(hass: HomeAssistant) -> List[HADispatchEnhancedPermissions]:
    """Every installation's manager, for the revoke action."""
    managers = []
    for coordinator in (hass.data.get(DOMAIN) or {}).values():
        manager = getattr(coordinator, "enhanced_permissions", None)
        if manager is not None:
            managers.append(manager)
    return managers
