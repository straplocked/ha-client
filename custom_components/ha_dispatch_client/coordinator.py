"""DataUpdateCoordinator for HA Dispatch Client."""
import logging
import time
from datetime import timedelta
import psutil
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.const import __version__ as HA_VERSION

from .api_client import HADispatchApiClient
from .const import DOMAIN, DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class HADispatchCoordinator(DataUpdateCoordinator):
    """Coordinator to manage data updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: HADispatchApiClient,
        installation_id: str,
    ):
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
            _LOGGER.error("Error communicating with API: %s", err)
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
            # (thresholds, features, etc. can be stored for future use)
            _LOGGER.info("Applied configuration version %s", self.config_version)

        except Exception as err:
            _LOGGER.error("Error applying configuration: %s", err)

    def _collect_metrics(self) -> dict:
        """Collect system metrics."""
        try:
            cpu_load = psutil.getloadavg()
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            
            # Calculate uptime correctly using time.time() not loop.time()
            boot_time = psutil.boot_time()
            uptime_seconds = int(time.time() - boot_time)
            
            # Ensure uptime is positive (sanity check)
            if uptime_seconds < 0:
                _LOGGER.warning("Negative uptime calculated, using 0 instead")
                uptime_seconds = 0

            return {
                "cpu_load_1m": round(cpu_load[0], 2),
                "cpu_load_5m": round(cpu_load[1], 2),
                "cpu_load_15m": round(cpu_load[2], 2),
                "memory_used_percent": round(memory.percent, 2),
                "memory_used_mb": round(memory.used / (1024 * 1024), 2),
                "disk_used_percent": round(disk.percent, 2),
                "disk_free_percent": round(100 - disk.percent, 2),
                "disk_free_mb": int(disk.free / (1024 * 1024)),
                "uptime_seconds": uptime_seconds,
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
        except Exception:
            return "Unknown"

