"""Binary sensor platform for HA Dispatch Client.

One entity: is anybody waiting on the customer to answer a remote access
request. It exists so the prompt is something automations can act on -- flash a
light, speak it on a media player, push it to a phone -- rather than something
that only shows up if somebody happens to open the Home Assistant UI.

The live sessions are carried as attributes on the same entity, which is what
a dashboard button needs in order to call ``revoke_access`` for a specific
session.
"""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_INSTALLATION_ID, DOMAIN
from .coordinator import HADispatchCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([HADispatchPendingConsentSensor(coordinator, entry)])


class HADispatchPendingConsentSensor(CoordinatorEntity, BinarySensorEntity):
    """On while somebody is waiting for the customer to approve access."""

    def __init__(self, coordinator: HADispatchCoordinator, entry: ConfigEntry):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entry = entry

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": "HA Dispatch Client",
            "manufacturer": "HA Dispatch",
            "model": "Client Integration",
        }

    @property
    def unique_id(self):
        """Return unique ID."""
        return f"{self.entry.entry_id}_pending_consent"

    @property
    def name(self):
        """Return name."""
        return "HA Dispatch Remote Access Requested"

    @property
    def icon(self):
        """Return icon."""
        return "mdi:account-clock" if self.is_on else "mdi:account-lock"

    @property
    def available(self) -> bool:
        """Unavailable when the server has no remote access to report on."""
        manager = getattr(self.coordinator, "remote_access", None)
        return manager is not None and manager.available

    @property
    def is_on(self) -> bool:
        """Whether a request is waiting for an answer."""
        manager = getattr(self.coordinator, "remote_access", None)
        return bool(manager and manager.pending)

    @property
    def extra_state_attributes(self):
        """Everything an automation or a dashboard button needs."""
        manager = getattr(self.coordinator, "remote_access", None)
        if manager is None:
            return {}

        return {
            ATTR_INSTALLATION_ID: self.coordinator.installation_id,
            "policy": manager.policy,
            "standing_consent_until": manager.standing_consent_until,
            "pending_count": len(manager.pending),
            "requests": [
                {
                    "session_id": session_id,
                    "requested_by": record.get("requested_by"),
                    "scope": record.get("scope"),
                    "scope_label": record.get("scope_label"),
                    "scope_description": record.get("scope_description"),
                    "reason": record.get("reason"),
                    "duration_minutes": record.get("duration_minutes"),
                    "expires_at": record.get("expires_at"),
                    "consent_url": record.get("consent_url"),
                }
                for session_id, record in sorted(manager.pending.items())
            ],
            "live_sessions": manager.live_sessions(),
        }
