<!-- Split from CLIENT_SIDE_DEVELOPMENT_GUIDE.md -->

# Client Implementation Guide

## HACS Integration Structure

Recommended file structure for Home Assistant HACS integration:

```
custom_components/
└── ha_dispatch_client/
    ├── __init__.py                 # Component initialization
    ├── manifest.json               # Integration metadata
    ├── config_flow.py              # Setup flow (UI config)
    ├── const.py                    # Constants and defaults
    ├── coordinator.py              # Data update coordinator
    ├── api_client.py               # API communication layer
    ├── sensor.py                   # Sensor entities
    ├── binary_sensor.py            # Binary sensor entities
    ├── switch.py                   # Switch entities (optional)
    ├── services.yaml               # Service definitions (optional)
    ├── strings.json                # UI translations
    ├── translations/               # Localization files
    │   ├── en.json
    │   └── ...
    └── README.md                   # Documentation
```

---

## manifest.json

```json
{
  "domain": "ha_dispatch_client",
  "name": "HA Dispatch Client",
  "version": "1.5.0",
  "documentation": "https://github.com/straplocked/ha-client",
  "issue_tracker": "https://github.com/straplocked/ha-client/issues",
  "requirements": [
    "aiohttp>=3.8.0",
    "psutil>=5.9.0",
    "cryptography>=41.0.0"
  ],
  "dependencies": [],
  "codeowners": ["@straplocked"],
  "config_flow": true,
  "iot_class": "cloud_polling"
}
```

**Key Points**:
- `config_flow: true`: Enables UI-based configuration
- `iot_class: "cloud_polling"`: Indicates polling communication
- `requirements`: Python packages (installable via pip)
- `quality_scale`: Aim for "gold" or "silver" for HACS

---

## const.py

```python
"""Constants for HA Dispatch Client integration."""

DOMAIN = "ha_dispatch_client"

# Configuration keys
CONF_SERVER_URL = "server_url"
CONF_INSTALLATION_ID = "installation_id"
CONF_ACCESS_TOKEN = "access_token"
CONF_CLIENT_ID = "client_id"

# Default values
DEFAULT_SCAN_INTERVAL = 60  # seconds
DEFAULT_NAME = "HA Dispatch"

# API endpoints
API_REGISTER = "/api/v1/installations/register"
API_STATUS = "/api/v1/installations/{installation_id}/status"
API_CONFIG = "/api/v1/installations/{installation_id}/config"
API_METRICS = "/api/v1/installations/{installation_id}/metrics"
API_METRICS_BATCH = "/api/v1/installations/{installation_id}/metrics/batch"

# Entity keys
SENSOR_STATUS = "status"
SENSOR_CONFIG_VERSION = "config_version"
SENSOR_CPU_LOAD = "cpu_load"
SENSOR_MEMORY_USED = "memory_used"
SENSOR_DISK_FREE = "disk_free"
BINARY_SENSOR_ONLINE = "online"

# Attribute keys
ATTR_INSTALLATION_ID = "installation_id"
ATTR_LAST_SEEN = "last_seen_at"
ATTR_HA_VERSION = "ha_version"
ATTR_CONFIG_VERSION = "config_version"
ATTR_POLL_INTERVAL = "poll_interval_seconds"
```

---

## config_flow.py

```python
"""Config flow for HA Dispatch Client."""
import logging
import uuid
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import aiohttp_client
import aiohttp

from .const import DOMAIN, CONF_SERVER_URL, CONF_INSTALLATION_ID, CONF_ACCESS_TOKEN, CONF_CLIENT_ID
from .api_client import HADispatchApiClient

_LOGGER = logging.getLogger(__name__)

class HADispatchConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HA Dispatch Client."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            # Validate server URL and attempt registration
            try:
                # Create API client
                session = aiohttp_client.async_get_clientsession(self.hass)
                client = HADispatchApiClient(
                    session=session,
                    server_url=user_input[CONF_SERVER_URL]
                )

                # Generate client ID
                client_id = str(uuid.uuid4())

                # Attempt registration
                hostname = user_input.get(CONF_NAME, self.hass.config.location_name)
                registration_data = await client.register_installation(
                    client_id=client_id,
                    hostname=hostname,
                    name=user_input.get(CONF_NAME),
                )

                # Store configuration
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME, "HA Dispatch"),
                    data={
                        CONF_SERVER_URL: user_input[CONF_SERVER_URL],
                        CONF_CLIENT_ID: client_id,
                        CONF_INSTALLATION_ID: registration_data["installation_id"],
                        CONF_ACCESS_TOKEN: registration_data["access_token"],
                    },
                )

            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            except Exception as e:
                _LOGGER.exception("Unexpected error during registration: %s", e)
                errors["base"] = "unknown"

        # Show form
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_SERVER_URL, default="http://localhost:8080"): str,
                vol.Optional(CONF_NAME, default=self.hass.config.location_name): str,
            }),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return HADispatchOptionsFlowHandler(config_entry)


class HADispatchOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                # Add configurable options here
                # For example: scan interval, enable/disable metrics, etc.
            }),
        )
```

---

## api_client.py

```python
"""API Client for HA Dispatch Server."""
import logging
from typing import Any, Dict, Optional
import aiohttp
from datetime import datetime, timezone

_LOGGER = logging.getLogger(__name__)

class HADispatchApiClient:
    """HA Dispatch API Client."""

    def __init__(self, session: aiohttp.ClientSession, server_url: str, token: Optional[str] = None):
        """Initialize API client."""
        self.session = session
        self.server_url = server_url.rstrip('/')
        self.token = token

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers."""
        headers = {
            "Content-Type": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def register_installation(
        self,
        client_id: str,
        hostname: str,
        name: Optional[str] = None,
        ha_version: Optional[str] = None,
        os_info: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register installation with server."""
        url = f"{self.server_url}/api/v1/installations/register"
        data = {
            "client_id": client_id,
            "hostname": hostname,
            "name": name or hostname,
            "ha_version": ha_version,
            "os_info": os_info,
        }

        async with self.session.post(url, json=data, headers=self._get_headers()) as response:
            response.raise_for_status()
            return await response.json()

    async def report_status(
        self,
        installation_id: str,
        ha_version: Optional[str] = None,
        os_info: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Report installation status."""
        url = f"{self.server_url}/api/v1/installations/{installation_id}/status"
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ha_version": ha_version,
            "os_info": os_info,
        }

        async with self.session.post(url, json=data, headers=self._get_headers()) as response:
            response.raise_for_status()
            return await response.json()

    async def fetch_configuration(
        self,
        installation_id: str,
        current_version: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Fetch configuration from server."""
        url = f"{self.server_url}/api/v1/installations/{installation_id}/config"
        params = {"current_version": current_version}

        async with self.session.get(url, params=params, headers=self._get_headers()) as response:
            if response.status == 204:
                return None  # No update
            response.raise_for_status()
            return await response.json()

    async def submit_metrics(
        self,
        installation_id: str,
        metrics: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Submit metrics to server."""
        url = f"{self.server_url}/api/v1/installations/{installation_id}/metrics"
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **metrics,
        }

        async with self.session.post(url, json=data, headers=self._get_headers()) as response:
            response.raise_for_status()
            return await response.json()

    async def submit_metrics_batch(
        self,
        installation_id: str,
        metrics_list: list,
    ) -> Dict[str, Any]:
        """Submit batch of metrics to server."""
        url = f"{self.server_url}/api/v1/installations/{installation_id}/metrics/batch"
        data = {"metrics": metrics_list}

        async with self.session.post(url, json=data, headers=self._get_headers()) as response:
            response.raise_for_status()
            return await response.json()
```

---

## coordinator.py

```python
"""DataUpdateCoordinator for HA Dispatch Client."""
import logging
from datetime import timedelta
import psutil
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.const import __version__ as HA_VERSION

from .api_client import HADispatchApiClient
from .const import DOMAIN, DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

class HADispatchCoordinator(DataUpdateCoordinator):
    """Coordinator to manage data updates."""

    def __init__(self, hass: HomeAssistant, api_client: HADispatchApiClient, installation_id: str):
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api_client = api_client
        self.installation_id = installation_id
        self.config_version = 0
        self.poll_interval = DEFAULT_SCAN_INTERVAL

    async def _async_update_data(self):
        """Fetch data from API."""
        try:
            # Report status
            status_data = await self.api_client.report_status(
                installation_id=self.installation_id,
                ha_version=HA_VERSION,
                os_info=self._get_os_info(),
            )

            # Check for configuration updates
            if status_data.get("config_version", 0) != self.config_version:
                config_data = await self.api_client.fetch_configuration(
                    installation_id=self.installation_id,
                    current_version=self.config_version,
                )
                if config_data:
                    await self._apply_configuration(config_data)

            # Collect and submit metrics
            metrics = self._collect_metrics()
            await self.api_client.submit_metrics(
                installation_id=self.installation_id,
                metrics=metrics,
            )

            # Return combined data for sensors
            return {
                "status": status_data.get("installation_status"),
                "config_version": status_data.get("config_version"),
                "metrics": metrics,
            }

        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}")

    async def _apply_configuration(self, config_data):
        """Apply configuration from server."""
        try:
            self.config_version = config_data["config_version"]
            desired_state = config_data.get("desired_state", {})

            # Update poll interval if changed
            new_interval = desired_state.get("report_interval", DEFAULT_SCAN_INTERVAL)
            if new_interval != self.poll_interval:
                self.poll_interval = new_interval
                self.update_interval = timedelta(seconds=new_interval)
                _LOGGER.info("Updated poll interval to %s seconds", new_interval)

            # Apply other configuration
            # (thresholds, features, etc.)
            _LOGGER.info("Applied configuration version %s", self.config_version)

        except Exception as err:
            _LOGGER.error("Error applying configuration: %s", err)

    def _collect_metrics(self) -> dict:
        """Collect system metrics."""
        try:
            cpu_load = psutil.getloadavg()
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            boot_time = psutil.boot_time()
            uptime = self.hass.loop.time() - boot_time

            return {
                "cpu_load_1m": cpu_load[0],
                "cpu_load_5m": cpu_load[1],
                "cpu_load_15m": cpu_load[2],
                "memory_used_percent": memory.percent,
                "memory_used_mb": memory.used / (1024 * 1024),
                "disk_used_percent": disk.percent,
                "disk_free_percent": 100 - disk.percent,
                "disk_free_mb": disk.free / (1024 * 1024),
                "uptime_seconds": int(uptime),
                "warnings": self._check_warnings(memory, disk),
            }
        except Exception as err:
            _LOGGER.error("Error collecting metrics: %s", err)
            return {}

    def _check_warnings(self, memory, disk) -> list:
        """Check for warning conditions."""
        warnings = []
        if disk.percent > 90:
            warnings.append("low_disk")
        if memory.percent > 90:
            warnings.append("high_memory")
        return warnings

    def _get_os_info(self) -> str:
        """Get OS information."""
        try:
            import platform
            return f"{platform.system()} {platform.release()}"
        except:
            return "Unknown"
```

---

## __init__.py

```python
"""HA Dispatch Client integration."""
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client

from .const import DOMAIN, CONF_SERVER_URL, CONF_INSTALLATION_ID, CONF_ACCESS_TOKEN
from .api_client import HADispatchApiClient
from .coordinator import HADispatchCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HA Dispatch Client from a config entry."""
    # Create API client
    session = aiohttp_client.async_get_clientsession(hass)
    api_client = HADispatchApiClient(
        session=session,
        server_url=entry.data[CONF_SERVER_URL],
        token=entry.data[CONF_ACCESS_TOKEN],
    )

    # Create coordinator
    coordinator = HADispatchCoordinator(
        hass=hass,
        api_client=api_client,
        installation_id=entry.data[CONF_INSTALLATION_ID],
    )

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Forward to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Remove coordinator
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
```

---

## sensor.py

```python
"""Sensor platform for HA Dispatch Client."""
import logging
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import PERCENTAGE, UnitOfInformation

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
        HADispatchDiskSensor(coordinator, entry),
        HADispatchUptimeSensor(coordinator, entry),
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
    def state(self):
        """Return state."""
        return self.coordinator.data.get("status")

    @property
    def extra_state_attributes(self):
        """Return attributes."""
        return {
            "config_version": self.coordinator.data.get("config_version"),
            ATTR_INSTALLATION_ID: self.coordinator.installation_id,
        }


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
    def state(self):
        """Return state."""
        metrics = self.coordinator.data.get("metrics", {})
        return metrics.get("cpu_load_1m")

    @property
    def unit_of_measurement(self):
        """Return unit."""
        return "load"

    @property
    def state_class(self):
        """Return state class."""
        return SensorStateClass.MEASUREMENT


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
    def state(self):
        """Return state."""
        metrics = self.coordinator.data.get("metrics", {})
        return metrics.get("memory_used_percent")

    @property
    def unit_of_measurement(self):
        """Return unit."""
        return PERCENTAGE

    @property
    def state_class(self):
        """Return state class."""
        return SensorStateClass.MEASUREMENT


class HADispatchDiskSensor(HADispatchSensorBase):
    """Disk free sensor."""

    @property
    def unique_id(self):
        """Return unique ID."""
        return f"{self.entry.entry_id}_disk_free"

    @property
    def name(self):
        """Return name."""
        return "HA Dispatch Disk Free"

    @property
    def state(self):
        """Return state."""
        metrics = self.coordinator.data.get("metrics", {})
        return metrics.get("disk_free_percent")

    @property
    def unit_of_measurement(self):
        """Return unit."""
        return PERCENTAGE

    @property
    def state_class(self):
        """Return state class."""
        return SensorStateClass.MEASUREMENT


class HADispatchUptimeSensor(HADispatchSensorBase):
    """Uptime sensor."""

    @property
    def unique_id(self):
        """Return unique ID."""
        return f"{self.entry.entry_id}_uptime"

    @property
    def name(self):
        """Return name."""
        return "HA Dispatch Uptime"

    @property
    def state(self):
        """Return state."""
        metrics = self.coordinator.data.get("metrics", {})
        uptime_seconds = metrics.get("uptime_seconds", 0)
        # Convert to days for display
        return round(uptime_seconds / 86400, 2)

    @property
    def unit_of_measurement(self):
        """Return unit."""
        return "days"

    @property
    def state_class(self):
        """Return state class."""
        return SensorStateClass.TOTAL_INCREASING
```

---

## Complete Minimal Client Example

For a standalone client outside of Home Assistant (useful for testing):

```python
"""Minimal HA Dispatch client example."""
import asyncio
import aiohttp
import uuid
import psutil
from datetime import datetime, timezone

class MinimalHADispatchClient:
    """Minimal client implementation."""

    def __init__(self, server_url):
        """Initialize client."""
        self.server_url = server_url.rstrip('/')
        self.token = None
        self.installation_id = None
        self.running = False

    async def register(self):
        """Register with server."""
        client_id = str(uuid.uuid4())

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.server_url}/api/v1/installations/register",
                json={
                    "client_id": client_id,
                    "hostname": "test-ha",
                    "name": "Test Installation",
                }
            ) as resp:
                data = await resp.json()
                self.installation_id = data["installation_id"]
                self.token = data["access_token"]
                print(f"Registered: {self.installation_id}")

    async def report_loop(self):
        """Main reporting loop."""
        headers = {"Authorization": f"Bearer {self.token}"}

        async with aiohttp.ClientSession(headers=headers) as session:
            while self.running:
                # Collect metrics
                cpu = psutil.getloadavg()
                mem = psutil.virtual_memory()
                disk = psutil.disk_usage('/')

                # Submit metrics
                try:
                    async with session.post(
                        f"{self.server_url}/api/v1/installations/{self.installation_id}/metrics",
                        json={
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "cpu_load_1m": cpu[0],
                            "memory_used_percent": mem.percent,
                            "disk_free_percent": 100 - disk.percent,
                        }
                    ) as resp:
                        if resp.status == 201:
                            print(f"Metrics sent: CPU={cpu[0]:.2f}, MEM={mem.percent:.1f}%")
                except Exception as e:
                    print(f"Error: {e}")

                await asyncio.sleep(60)

    async def run(self):
        """Run client."""
        await self.register()
        self.running = True
        await self.report_loop()

# Usage
async def main():
    client = MinimalHADispatchClient("http://localhost:8080")
    await client.run()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Related Documentation

- [API Reference](../api-reference.md) for full endpoint documentation
- [System Architecture](system-architecture.md) for client responsibilities overview
- [Metrics Collection](metrics-collection.md) for metric collection strategies
- [Configuration Management](configuration.md) for configuration application details
- [Error Handling](error-handling.md) for network and HTTP error handling
- [Deployment & Distribution](deployment.md) for packaging and releasing the integration
