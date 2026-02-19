"""Sensor platform for HA Dispatch Client."""
import logging
from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import PERCENTAGE

from .const import DOMAIN, ATTR_INSTALLATION_ID
from .coordinator import HADispatchCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        HADispatchStatusSensor(coordinator, entry),
        HADispatchCPULoadSensor(coordinator, entry),
        HADispatchMemorySensor(coordinator, entry),
    ]

    async_add_entities(entities)


class HADispatchSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for HA Dispatch sensors."""

    def __init__(self, coordinator: HADispatchCoordinator, entry: ConfigEntry):
        """Initialize sensor."""
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


class HADispatchStatusSensor(HADispatchSensorBase):
    """Status sensor."""

    @property
    def unique_id(self):
        """Return unique ID."""
        return f"{self.entry.entry_id}_status"

    @property
    def name(self):
        """Return name."""
        return "HA Dispatch Status"

    @property
    def native_value(self):
        """Return state."""
        if self.coordinator.data:
            return self.coordinator.data.get("status")
        return None

    @property
    def extra_state_attributes(self):
        """Return attributes."""
        if self.coordinator.data:
            return {
                "config_version": self.coordinator.data.get("config_version"),
                ATTR_INSTALLATION_ID: self.coordinator.installation_id,
            }
        return {}


class HADispatchCPULoadSensor(HADispatchSensorBase):
    """CPU load sensor."""

    @property
    def unique_id(self):
        """Return unique ID."""
        return f"{self.entry.entry_id}_cpu_load"

    @property
    def name(self):
        """Return name."""
        return "HA Dispatch CPU Load"

    @property
    def native_value(self):
        """Return state."""
        if self.coordinator.data and "metrics" in self.coordinator.data:
            metrics = self.coordinator.data["metrics"]
            return metrics.get("cpu_load_1m")
        return None

    @property
    def native_unit_of_measurement(self):
        """Return unit."""
        return "load"

    @property
    def state_class(self):
        """Return state class."""
        return SensorStateClass.MEASUREMENT

    @property
    def icon(self):
        """Return icon."""
        return "mdi:cpu-64-bit"


class HADispatchMemorySensor(HADispatchSensorBase):
    """Memory usage sensor."""

    @property
    def unique_id(self):
        """Return unique ID."""
        return f"{self.entry.entry_id}_memory_used"

    @property
    def name(self):
        """Return name."""
        return "HA Dispatch Memory Used"

    @property
    def native_value(self):
        """Return state."""
        if self.coordinator.data and "metrics" in self.coordinator.data:
            metrics = self.coordinator.data["metrics"]
            return metrics.get("memory_used_percent")
        return None

    @property
    def native_unit_of_measurement(self):
        """Return unit."""
        return PERCENTAGE

    @property
    def state_class(self):
        """Return state class."""
        return SensorStateClass.MEASUREMENT

    @property
    def device_class(self):
        """Return device class."""
        return SensorDeviceClass.POWER_FACTOR

    @property
    def icon(self):
        """Return icon."""
        return "mdi:memory"

