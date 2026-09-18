"""Update platform for HA Dispatch Client.

Surfaces the client's own version as a Home Assistant update entity, so an
operator can see and install a new client from Settings -> Updates without
shell access. The install itself is handled by updater.ClientUpdater.
"""
import logging
from typing import Any, Optional

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, RELEASE_KEY
from .coordinator import HADispatchCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the update platform."""
    coordinator: HADispatchCoordinator = hass.data[DOMAIN][entry.entry_id]

    if coordinator.updater is None:
        _LOGGER.debug("Updater unavailable; skipping the update entity")
        return

    installed = await coordinator.updater.async_installed_version()
    async_add_entities([HADispatchUpdateEntity(coordinator, entry, installed)])


class HADispatchUpdateEntity(CoordinatorEntity, UpdateEntity):
    """The HA Dispatch client's own version."""

    # SPECIFIC_VERSION is deliberately absent. The server offers exactly one
    # applicable release at a time, so this entity cannot honour a request for
    # an arbitrary version and should not claim it can -- version pinning is
    # done server-side through the target_version setting.
    #
    # BACKUP is absent for the same reason: it advertises a full Home Assistant
    # backup, which this does not take. The updater keeps a copy of the previous
    # integration directory, which is a different and smaller promise.
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.PROGRESS
        | UpdateEntityFeature.RELEASE_NOTES
    )
    _attr_title = "HA Dispatch Client"

    def __init__(
        self,
        coordinator: HADispatchCoordinator,
        entry: ConfigEntry,
        installed_version: Optional[str],
    ) -> None:
        """Initialize the update entity."""
        super().__init__(coordinator)
        self.entry = entry
        self._installed_version = installed_version

    async def async_added_to_hass(self) -> None:
        """Subscribe to install progress on top of the coordinator."""
        await super().async_added_to_hass()
        if self.coordinator.updater is not None:
            self.async_on_remove(
                self.coordinator.updater.add_listener(self.async_write_ha_state)
            )

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self.entry.entry_id}_client_update"

    @property
    def name(self) -> str:
        """Return name."""
        return "HA Dispatch Client Update"

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
    def _release(self) -> dict:
        """Return the release the server is currently offering, if any."""
        if not self.coordinator.data:
            return {}
        release = self.coordinator.data.get(RELEASE_KEY)
        return release if isinstance(release, dict) else {}

    @property
    def installed_version(self) -> Optional[str]:
        """Return the version on disk."""
        return self._installed_version

    @property
    def latest_version(self) -> Optional[str]:
        """Return the newest version the server has offered us.

        Falls back to the installed version rather than None, so an offline
        server reads as "up to date" instead of "unknown".
        """
        return self._release.get("version") or self._installed_version

    @property
    def release_url(self) -> Optional[str]:
        """Return the release page URL."""
        return self._release.get("release_url")

    @property
    def release_summary(self) -> Optional[str]:
        """Return a short summary; Home Assistant caps this at 255 characters."""
        notes = self._release.get("release_notes")
        if not notes:
            return None
        summary = str(notes).strip().splitlines()[0]
        return summary[:252] + "..." if len(summary) > 255 else summary

    @property
    def auto_update(self) -> bool:
        """Return whether the server is configured to install updates for us."""
        return bool(getattr(self.coordinator, "auto_update", False))

    @property
    def in_progress(self) -> bool:
        """Return whether an install is running.

        A plain bool is correct on current Home Assistant and was accepted by
        the older integer-percentage contract too, so this stays valid across
        the versions this integration supports.
        """
        updater = self.coordinator.updater
        return bool(updater is not None and updater.in_progress)

    @property
    def update_percentage(self) -> Optional[float]:
        """Return install progress, where Home Assistant supports it."""
        updater = self.coordinator.updater
        if updater is None or not updater.in_progress:
            return None
        return updater.progress

    async def async_release_notes(self) -> Optional[str]:
        """Return the full release notes markdown."""
        notes = self._release.get("release_notes")
        return None if notes is None else str(notes)

    async def async_install(self, version: Optional[str], backup: bool, **kwargs: Any) -> None:
        """Install the offered release."""
        release = self._release
        if not release:
            raise HomeAssistantError("The server has not offered an update to install")

        offered = release.get("version")
        if version is not None and str(version) != str(offered):
            raise HomeAssistantError(
                f"Only version {offered} is available from the server; pin a different "
                "version from the HA Dispatch dashboard instead."
            )

        await self.coordinator.updater.async_install(release)
