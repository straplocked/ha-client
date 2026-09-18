"""HA Dispatch Client integration."""
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import aiohttp_client
import voluptuous as vol
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    CONF_SERVER_URL,
    CONF_INSTALLATION_ID,
    CONF_ACCESS_TOKEN,
    RELEASE_KEY,
)
from .api_client import HADispatchApiClient
from .coordinator import HADispatchCoordinator
from .updater import ClientUpdater

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "update"]

# Set once the pending-update record has been resolved, so a second config
# entry does not re-run the confirmation and report the same update twice.
# Kept out of hass.data[DOMAIN], which holds coordinators keyed by entry_id and
# is iterated as such by the service helpers and the teardown check.
UPDATE_CONFIRMED = f"{DOMAIN}_update_confirmed"

# Service schemas
SERVICE_SEND_TEST_METRICS_SCHEMA = vol.Schema({
    vol.Optional("cpu_load", default=75.0): cv.positive_float,
    vol.Optional("memory_used", default=85.0): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
    vol.Optional("disk_free", default=15.0): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
})

SERVICE_TRIGGER_ALERT_SCHEMA = vol.Schema({
    vol.Required("alert_type", default="disk_low"): vol.In(["disk_low", "cpu_high", "memory_high", "all"]),
})

SERVICE_SEND_CUSTOM_METRIC_SCHEMA = vol.Schema({
    vol.Optional("cpu_load_1m"): cv.positive_float,
    vol.Optional("memory_used_percent"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
    vol.Optional("disk_free_percent"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
    vol.Optional("warnings"): cv.string,
})

SERVICE_SUBMIT_ALERT_SCHEMA = vol.Schema({
    vol.Required("severity"): vol.In(["info", "warning", "critical"]),
    vol.Required("type"): cv.string,
    vol.Required("title"): cv.string,
    vol.Required("message"): cv.string,
    vol.Optional("context"): dict,
    vol.Optional("resolved", default=False): cv.boolean,
})

SERVICE_RESOLVE_ALERT_SCHEMA = vol.Schema({
    vol.Required("type"): cv.string,
})

SERVICE_INSTALL_UPDATE_SCHEMA = vol.Schema({
    # Allows reinstalling the same version or going backwards. Off by default:
    # a downgrade is occasionally the right call during an incident, but never
    # something to do by accident.
    vol.Optional("force", default=False): cv.boolean,
})


def get_coordinator_for_service(hass: HomeAssistant) -> HADispatchCoordinator | None:
    """Get the first available coordinator for service calls."""
    coordinators = hass.data.get(DOMAIN, {})
    if not coordinators:
        _LOGGER.error("No HA Dispatch installations configured")
        return None
    
    # Get the first coordinator
    entry_id = next(iter(coordinators))
    return coordinators[entry_id]


def setup_services(hass: HomeAssistant):
    """Set up services for HA Dispatch Client (called once)."""
    
    # Check if services are already registered
    if hass.services.has_service(DOMAIN, "send_test_metrics"):
        _LOGGER.debug("Services already registered, skipping")
        return


    async def handle_send_test_metrics(call: ServiceCall):
        """Handle send_test_metrics service call."""
        coordinator = get_coordinator_for_service(hass)
        if not coordinator:
            return
        
        cpu_load = call.data.get("cpu_load", 75.0)
        memory_used = call.data.get("memory_used", 85.0)
        disk_free = call.data.get("disk_free", 15.0)
        
        metrics = {
            "cpu_load_1m": round(cpu_load / 100, 2),
            "cpu_load_5m": round(cpu_load / 100, 2),
            "cpu_load_15m": round(cpu_load / 100, 2),
            "memory_used_percent": round(memory_used, 2),
            "memory_used_mb": round(memory_used * 80, 2),
            "disk_used_percent": round(100 - disk_free, 2),
            "disk_free_percent": round(disk_free, 2),
            "disk_free_mb": int(disk_free * 100),
            "uptime_seconds": 3600,
            "warnings": [],
        }
        
        _LOGGER.info("Sending test metrics: CPU=%.1f%%, Memory=%.1f%%, Disk Free=%.1f%%", 
                     cpu_load, memory_used, disk_free)
        
        await coordinator.api_client.submit_metrics(
            coordinator.installation_id,
            metrics
        )
        _LOGGER.info("Test metrics sent successfully")
    
    async def handle_trigger_alert(call: ServiceCall):
        """Handle trigger_alert service call - uses Alert API directly."""
        coordinator = get_coordinator_for_service(hass)
        if not coordinator:
            return
        
        alert_type = call.data.get("alert_type", "disk_low")
        
        # Define alert templates
        alerts_config = {
            "disk_low": {
                "severity": "warning",
                "type": "low_disk_space",
                "title": "Low Disk Space",
                "message": "Disk space is critically low at 5%",
                "context": {"disk_free_percent": 5.0, "disk_used_percent": 95.0},
            },
            "cpu_high": {
                "severity": "warning",
                "type": "high_cpu_load",
                "title": "High CPU Load",
                "message": "CPU load is critically high at 850%",
                "context": {"cpu_load_1m": 8.5, "cpu_cores": 1},
            },
            "memory_high": {
                "severity": "critical",
                "type": "high_memory_usage",
                "title": "High Memory Usage",
                "message": "Memory usage is critically high at 95%",
                "context": {"memory_used_percent": 95.0},
            },
        }
        
        if alert_type == "all":
            # Submit all alerts in batch
            alerts = [alerts_config["disk_low"], alerts_config["cpu_high"], alerts_config["memory_high"]]
            _LOGGER.info("Triggering ALL alerts via batch submission")
            result = await coordinator.api_client.submit_alerts_batch(
                coordinator.installation_id,
                alerts
            )
            _LOGGER.warning(
                "All alerts triggered! Processed %s alerts. Check server.",
                result.get("processed_count")
            )
        else:
            # Submit single alert
            alert_config = alerts_config.get(alert_type)
            if not alert_config:
                _LOGGER.error("Unknown alert type: %s", alert_type)
                return
            
            _LOGGER.info("Triggering %s alert", alert_type.upper())
            result = await coordinator.api_client.submit_alert(
                coordinator.installation_id,
                severity=alert_config["severity"],
                alert_type=alert_config["type"],
                title=alert_config["title"],
                message=alert_config["message"],
                context=alert_config["context"],
            )
            _LOGGER.warning(
                "Alert triggered! ID=%s, Action=%s. Check server.",
                result.get("alert_id"),
                result.get("action")
            )
    
    async def handle_force_update(call: ServiceCall):
        """Handle force_update service call."""
        coordinator = get_coordinator_for_service(hass)
        if not coordinator:
            return
        
        _LOGGER.info("Forcing immediate coordinator update")
        await coordinator.async_request_refresh()
        _LOGGER.info("Coordinator update completed")
    
    async def handle_send_custom_metric(call: ServiceCall):
        """Handle send_custom_metric service call."""
        coordinator = get_coordinator_for_service(hass)
        if not coordinator:
            return
        
        metrics = {}
        
        if "cpu_load_1m" in call.data:
            metrics["cpu_load_1m"] = call.data["cpu_load_1m"]
        if "memory_used_percent" in call.data:
            metrics["memory_used_percent"] = call.data["memory_used_percent"]
        if "disk_free_percent" in call.data:
            metrics["disk_free_percent"] = call.data["disk_free_percent"]
        if "warnings" in call.data:
            warnings_str = call.data["warnings"]
            metrics["warnings"] = [w.strip() for w in warnings_str.split(",") if w.strip()]
        
        _LOGGER.info("Sending custom metrics: %s", metrics)
        await coordinator.api_client.submit_metrics(
            coordinator.installation_id,
            metrics
        )
        _LOGGER.info("Custom metrics sent successfully")
    
    async def handle_submit_alert(call: ServiceCall):
        """Handle submit_alert service call."""
        coordinator = get_coordinator_for_service(hass)
        if not coordinator:
            return
        
        _LOGGER.info(
            "Submitting alert: type=%s, severity=%s, title=%s",
            call.data["type"],
            call.data["severity"],
            call.data["title"]
        )
        
        result = await coordinator.api_client.submit_alert(
            coordinator.installation_id,
            severity=call.data["severity"],
            alert_type=call.data["type"],
            title=call.data["title"],
            message=call.data["message"],
            context=call.data.get("context"),
            resolved=call.data.get("resolved", False),
        )
        
        _LOGGER.info(
            "Alert submitted successfully: id=%s, action=%s",
            result.get("alert_id"),
            result.get("action")
        )
    
    async def handle_resolve_alert(call: ServiceCall):
        """Handle resolve_alert service call."""
        coordinator = get_coordinator_for_service(hass)
        if not coordinator:
            return
        
        alert_type = call.data["type"]
        _LOGGER.info("Resolving alerts of type: %s", alert_type)
        
        result = await coordinator.api_client.resolve_alert(
            coordinator.installation_id,
            alert_type=alert_type,
        )
        
        resolved_count = result.get("resolved_count", 0)
        if resolved_count > 0:
            _LOGGER.info("Resolved %s alert(s) of type: %s", resolved_count, alert_type)
        else:
            _LOGGER.info("No unresolved alerts found for type: %s", alert_type)
    
    async def handle_install_update(call: ServiceCall):
        """Handle install_update service call."""
        coordinator = get_coordinator_for_service(hass)
        if not coordinator:
            return

        if coordinator.updater is None:
            _LOGGER.error("Self-update is unavailable on this installation")
            return

        # Refresh first: the release payload rides on the status response, so
        # this both picks up a release offered since the last poll and avoids
        # acting on a stale one.
        await coordinator.async_request_refresh()

        release = (coordinator.data or {}).get(RELEASE_KEY)
        if not release:
            _LOGGER.warning("The server is not offering an update for this installation")
            return

        _LOGGER.info("Installing client update %s on request", release.get("version"))
        await coordinator.updater.async_install(
            release, force=call.data.get("force", False)
        )

    # Register all services
    hass.services.async_register(
        DOMAIN,
        "send_test_metrics",
        handle_send_test_metrics,
        schema=SERVICE_SEND_TEST_METRICS_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        "trigger_alert",
        handle_trigger_alert,
        schema=SERVICE_TRIGGER_ALERT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        "force_update",
        handle_force_update
    )
    hass.services.async_register(
        DOMAIN,
        "send_custom_metric",
        handle_send_custom_metric,
        schema=SERVICE_SEND_CUSTOM_METRIC_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        "submit_alert",
        handle_submit_alert,
        schema=SERVICE_SUBMIT_ALERT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        "resolve_alert",
        handle_resolve_alert,
        schema=SERVICE_RESOLVE_ALERT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        "install_update",
        handle_install_update,
        schema=SERVICE_INSTALL_UPDATE_SCHEMA
    )

    _LOGGER.info("HA Dispatch Client services registered successfully")


def teardown_services(hass: HomeAssistant):
    """Remove services when last config entry is unloaded."""
    # Only remove services if no more coordinators exist
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, "send_test_metrics")
        hass.services.async_remove(DOMAIN, "trigger_alert")
        hass.services.async_remove(DOMAIN, "force_update")
        hass.services.async_remove(DOMAIN, "send_custom_metric")
        hass.services.async_remove(DOMAIN, "submit_alert")
        hass.services.async_remove(DOMAIN, "resolve_alert")
        hass.services.async_remove(DOMAIN, "install_update")
        _LOGGER.info("HA Dispatch Client services unregistered")


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
        # Lets the coordinator persist a new token if it has to re-enrol.
        entry=entry,
    )

    # Wire up self-update before the first refresh, so the very first status
    # report already carries the version actually on disk.
    updater = ClientUpdater(hass, coordinator)
    coordinator.updater = updater
    coordinator.client_version = await updater.async_installed_version()

    # Resolve any update left pending by a previous run. Done before the first
    # refresh so a failed update is reported even if the server is unreachable
    # later in setup, and only once no matter how many entries are configured.
    if not hass.data.get(UPDATE_CONFIRMED):
        hass.data[UPDATE_CONFIRMED] = True
        await updater.async_confirm_pending()

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up services ONCE (singleton pattern like meross_lan)
    # This will only register if not already registered
    setup_services(hass)

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
        
        # If this was the last coordinator, remove services
        teardown_services(hass)

    return unload_ok

