"""DataUpdateCoordinator for HA Dispatch Client."""
import logging
import socket
import time
import uuid
from datetime import timedelta
import aiohttp
import psutil
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.util import dt as dt_util

from .api_client import (
    ClientIdTakenError,
    HADispatchApiClient,
    InstallationAuthError,
    RegistrationSecretError,
)
# InstallationGoneError subclasses InstallationAuthError, so the handler below
# covers both a rejected token (401) and a vanished record (404).
from .const import (
    COMPONENT_REPORT_INTERVAL,
    CONF_ACCESS_TOKEN,
    CONF_AUTO_UPDATE,
    CONF_CLIENT_ID,
    CONF_INSTALLATION_ID,
    CONF_REGISTRATION_SECRET,
    CONF_RESTART_AFTER_UPDATE,
    CONF_TARGET_VERSION,
    CONF_UPDATE_CHANNEL,
    CONF_UPDATE_WINDOW,
    DEFAULT_AUTO_UPDATE,
    DEFAULT_BATTERY_CRITICAL_PERCENT,
    DEFAULT_BATTERY_LOW_PERCENT,
    DEFAULT_RESTART_AFTER_UPDATE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STALE_ENTITY_HOURS,
    DEFAULT_UPDATE_CHANNEL,
    DOMAIN,
    RELEASE_KEY,
)
from .components import async_collect_components
from .health import collect_health
from .updater import UpdateError, in_update_window, is_newer

_LOGGER = logging.getLogger(__name__)


class HADispatchCoordinator(DataUpdateCoordinator):
    """Coordinator to manage data updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: HADispatchApiClient,
        installation_id: str,
        entry: ConfigEntry | None = None,
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
        # Needed to persist a new token after re-enrolment. Optional so existing
        # callers and tests keep working.
        self.entry = entry
        self.config_version = 0
        self.poll_interval = DEFAULT_SCAN_INTERVAL
        self.stale_entity_hours = DEFAULT_STALE_ENTITY_HOURS
        self.battery_low_percent = DEFAULT_BATTERY_LOW_PERCENT
        self.battery_critical_percent = DEFAULT_BATTERY_CRITICAL_PERCENT

        # Consent-gated remote access. Supplied during setup, so a coordinator
        # built without one (the tests, and any caller predating the feature)
        # simply never polls for consent requests.
        self.remote_access = None

        # Consent-gated remote updates. Same arrangement: absent unless setup
        # wires it, so a coordinator built without one never polls for updates.
        self.updates = None

        # Component inventory. Reported on its own slow cadence rather than on
        # every metrics poll: versions change rarely, and the fleet's
        # attribution window is two hours wide. None means "never reported",
        # which is why the very first tick after a restart always sends one.
        self.component_report_interval = COMPONENT_REPORT_INTERVAL
        self.components_reported_at = None

        # Self-update state. The updater and the version on disk are both
        # supplied during setup, once Home Assistant's loader can be asked.
        self.updater = None
        self.client_version: str | None = None
        self.update_channel = DEFAULT_UPDATE_CHANNEL
        self.auto_update = DEFAULT_AUTO_UPDATE
        self.target_version: str | None = None
        self.update_window: str | None = None
        self.restart_after_update = DEFAULT_RESTART_AFTER_UPDATE

    async def _async_update_data(self):
        """Fetch data from API."""
        try:
            # Report status
            status_data = await self.api_client.report_status(
                installation_id=self.installation_id,
                ha_version=HA_VERSION,
                os_info=self._get_os_info(),
                client_version=self.client_version,
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
            health = self._collect_health()
            await self.api_client.submit_metrics(
                installation_id=self.installation_id,
                metrics=metrics,
                health=health,
            )

            # A release is only present when the server considers one
            # applicable to this installation, so the common "nothing to do"
            # case costs no extra request.
            # Component inventory, when it is due. Cheap on the ticks it is not
            # due, which is 29 out of every 30 of them.
            await self.async_report_components()

            release = status_data.get(RELEASE_KEY)
            if not isinstance(release, dict):
                release = None
            if release:
                self._schedule_auto_update(release)

            # Consent requests ride the same cadence -- the spec asks for
            # 30-60 s and this already runs at 60 s. Deliberately last, and
            # deliberately unable to raise: a customer's Home Assistant must
            # keep reporting metrics even if remote access is broken.
            if self.remote_access is not None:
                await self.remote_access.async_poll_pending()

            # Consented updates ride the same cadence and are just as unable to
            # raise, for the same reason.
            if self.updates is not None:
                await self.updates.async_poll_pending()

            # Return combined data for sensors
            return {
                "status": status_data.get("installation_status"),
                "config_version": status_data.get("config_version"),
                "metrics": metrics,
                "health": health,
                RELEASE_KEY: release,
            }

        except InstallationAuthError as err:
            # The server no longer recognises this installation -- its record was
            # deleted or the database was rebuilt. Retrying the same token can
            # never succeed, so re-enrol instead of failing forever. Without
            # this, the only fix is deleting and re-adding the integration by
            # hand, which is what used to be required after every server reset.
            _LOGGER.warning("Server does not recognise this installation: %s -- re-enrolling", err)

            if await self._async_reregister():
                # Re-enrolled successfully; report on the next cycle rather than
                # recursing, so a persistent failure cannot spin.
                raise UpdateFailed("Re-enrolled with the server; retrying shortly")

            raise UpdateFailed(f"Token rejected and re-enrolment failed: {err}")

        except (aiohttp.ClientError, TimeoutError, KeyError, ValueError) as err:
            _LOGGER.error("Error communicating with API: %s", err)
            raise UpdateFailed(f"Error communicating with API: {err}")

    async def _async_reregister(self) -> bool:
        """Obtain a fresh installation record and token from the server.

        Reuses the stored client_id, which is this installation's stable
        identity. If the server still holds that client_id -- meaning the record
        exists but our token is stale -- a new one is generated instead, since
        registration will not overwrite an existing record.
        """
        if self.entry is None:
            _LOGGER.error("Cannot re-enrol: coordinator has no config entry")
            return False

        data = dict(self.entry.data)
        secret = data.get(CONF_REGISTRATION_SECRET) or None
        client_id = data.get(CONF_CLIENT_ID) or str(uuid.uuid4())

        try:
            hostname = f"{socket.gethostname()}.local:8123"
            name = self.hass.config.location_name or socket.gethostname()

            try:
                result = await self.api_client.register_installation(
                    client_id=client_id,
                    hostname=hostname,
                    name=name,
                    ha_version=HA_VERSION,
                    os_info=self._get_os_info(),
                    registration_secret=secret,
                    client_version=self.client_version,
                )
            except ClientIdTakenError:
                client_id = str(uuid.uuid4())
                _LOGGER.info("Stored client_id already registered; enrolling as %s", client_id)
                result = await self.api_client.register_installation(
                    client_id=client_id,
                    hostname=hostname,
                    name=name,
                    ha_version=HA_VERSION,
                    os_info=self._get_os_info(),
                    registration_secret=secret,
                    client_version=self.client_version,
                )
        except RegistrationSecretError:
            _LOGGER.error(
                "Re-enrolment refused: the server requires a registration secret and "
                "the stored one is missing or wrong. Reconfigure the integration."
            )
            return False
        except (aiohttp.ClientError, TimeoutError, KeyError, ValueError) as err:
            _LOGGER.error("Re-enrolment failed: %s", err)
            return False

        self.installation_id = result["installation_id"]
        self.api_client.token = result["access_token"]
        self.config_version = 0

        # Persist so the new token survives a restart.
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={
                **data,
                CONF_CLIENT_ID: client_id,
                CONF_INSTALLATION_ID: result["installation_id"],
                CONF_ACCESS_TOKEN: result["access_token"],
            },
        )

        _LOGGER.info("Re-enrolled with the server as installation %s", result["installation_id"])
        return True

    def _schedule_auto_update(self, release: dict) -> None:
        """Start an unattended install, if everything lines up for one.

        Deliberately fire-and-forget: a successful install restarts Home
        Assistant, and that must not happen inside the coordinator's refresh.
        """
        if not self.auto_update or self.updater is None:
            return
        if self.updater.in_progress:
            return

        version = release.get("version")
        if not version or not is_newer(version, self.client_version):
            return

        # A pinned target means exactly that version and no other -- the server
        # should already be filtering, but an installation held back on purpose
        # is not something to get wrong.
        if self.target_version and str(version) != str(self.target_version):
            _LOGGER.debug(
                "Skipping auto-update to %s: pinned to %s", version, self.target_version
            )
            return

        if not in_update_window(dt_util.now(), self.update_window):
            _LOGGER.debug(
                "Deferring auto-update to %s until the %s window",
                version,
                self.update_window,
            )
            return

        _LOGGER.info("Auto-updating client from %s to %s", self.client_version, version)
        self.hass.async_create_task(self._async_auto_update(release))

    async def _async_auto_update(self, release: dict) -> None:
        """Run an unattended install, logging rather than raising."""
        try:
            await self.updater.async_install(release)
        except UpdateError as err:
            # Already reported to the server and logged with its phase by the
            # updater; swallowed here so a failed auto-update never surfaces as
            # an unhandled task exception.
            _LOGGER.error("Unattended update to %s failed: %s", release.get("version"), err)

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

            # Health signal thresholds are server-controlled so an integrator can
            # tune noise levels per installation without touching the client.
            self.stale_entity_hours = int(
                desired_state.get("stale_entity_hours", DEFAULT_STALE_ENTITY_HOURS)
            )
            self.battery_low_percent = int(
                desired_state.get("battery_low_percent", DEFAULT_BATTERY_LOW_PERCENT)
            )
            self.battery_critical_percent = int(
                desired_state.get(
                    "battery_critical_percent", DEFAULT_BATTERY_CRITICAL_PERCENT
                )
            )

            # Update policy. auto_update stays off unless the server explicitly
            # turns it on for this installation -- installing code unattended
            # is not a sensible default to inherit.
            self.update_channel = str(
                desired_state.get(CONF_UPDATE_CHANNEL, DEFAULT_UPDATE_CHANNEL)
            )
            self.auto_update = bool(
                desired_state.get(CONF_AUTO_UPDATE, DEFAULT_AUTO_UPDATE)
            )
            target = desired_state.get(CONF_TARGET_VERSION)
            self.target_version = str(target) if target else None
            window = desired_state.get(CONF_UPDATE_WINDOW)
            self.update_window = str(window) if window else None
            self.restart_after_update = bool(
                desired_state.get(
                    CONF_RESTART_AFTER_UPDATE, DEFAULT_RESTART_AFTER_UPDATE
                )
            )

            _LOGGER.info("Applied configuration version %s", self.config_version)

        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.error("Error applying configuration: %s", err)

    async def async_report_components(self, *, force: bool = False) -> bool:
        """Report the full component inventory, if it is due.

        force skips the cadence check and is how an update reports promptly --
        the transition is what opens the fleet's observation window, and a
        version discovered half an hour late is attributed to half an hour of
        unrelated events.

        Returns whether a report actually went out. Never raises on a
        collection or transport failure: the inventory is intelligence, and
        losing one report of it must not take metrics down with it.
        """
        now = dt_util.utcnow()
        if not force and not self._components_due(now):
            return False

        try:
            components = await async_collect_components(self.hass)
        except (AttributeError, KeyError, TypeError, ValueError) as err:
            _LOGGER.error("Error collecting component inventory: %s", err)
            return False

        if not components:
            # The server validates `components` as required, so an empty list
            # is a 422 rather than "this installation runs nothing".
            _LOGGER.debug("No components collected; skipping the inventory report")
            return False

        try:
            await self.api_client.report_components(
                installation_id=self.installation_id,
                components=components,
            )
        except (
            aiohttp.ClientError,
            TimeoutError,
            AttributeError,
            KeyError,
            ValueError,
        ) as err:
            # Left un-stamped on purpose: the next tick tries again rather than
            # waiting out the full interval on a transport blip.
            _LOGGER.warning("Could not report the component inventory: %s", err)
            return False

        self.components_reported_at = now
        _LOGGER.debug("Reported %s components", len(components))
        return True

    def _components_due(self, now) -> bool:
        """Whether enough time has passed since the last inventory report."""
        if self.components_reported_at is None:
            return True
        elapsed = (now - self.components_reported_at).total_seconds()
        return elapsed >= self.component_report_interval

    def _collect_health(self) -> dict | None:
        """Collect Home Assistant health signals.

        Returns None on failure rather than an empty payload. The server treats
        a missing health key as "no report" and leaves existing items alone,
        whereas an empty payload would read as "everything recovered" and
        wrongly resolve every open problem.
        """
        try:
            return collect_health(
                self.hass,
                stale_hours=self.stale_entity_hours,
                battery_low=self.battery_low_percent,
                battery_critical=self.battery_critical_percent,
            )
        except (AttributeError, KeyError, TypeError, ValueError) as err:
            _LOGGER.error("Error collecting health signals: %s", err)
            return None

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
        except OSError as err:
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
        except OSError:
            return "Unknown"

