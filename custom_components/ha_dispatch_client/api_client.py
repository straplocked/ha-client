"""API Client for HA Dispatch Server."""
import logging
from typing import Any, Dict, Optional
import aiohttp
from datetime import datetime, timezone

_LOGGER = logging.getLogger(__name__)


class RegistrationSecretError(Exception):
    """Raised when the server rejects the enrolment secret."""


class HADispatchApiClient:
    """HA Dispatch API Client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        server_url: str,
        token: Optional[str] = None,
    ):
        """Initialize API client."""
        self.session = session
        self.server_url = server_url.rstrip("/")
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
        registration_secret: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register installation with server.

        Internet-facing servers require an enrolment secret: registration is the
        only unauthenticated endpoint and it issues a bearer token, so without
        one anybody who can reach the URL can mint installations. Servers on a
        trusted network may leave it unset, hence the optional argument.
        """
        url = f"{self.server_url}/api/v1/installations/register"
        data = {
            "client_id": client_id,
            "hostname": hostname,
            "name": name or hostname,
            "ha_version": ha_version,
            "os_info": os_info,
        }

        headers = self._get_headers()
        if registration_secret:
            headers["X-Registration-Secret"] = registration_secret

        _LOGGER.debug("Registering installation with server: %s", url)
        async with self.session.post(url, json=data, headers=headers) as response:
            if response.status == 401:
                # Distinct from a transport failure: the server is reachable and
                # answering, the secret is simply missing or wrong.
                raise RegistrationSecretError(
                    "Server rejected the registration secret"
                )
            response.raise_for_status()
            result = await response.json()
            _LOGGER.info("Successfully registered installation: %s", result.get("installation_id"))
            return result

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

        _LOGGER.debug("Reporting status to server")
        async with self.session.post(
            url, json=data, headers=self._get_headers()
        ) as response:
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

        _LOGGER.debug("Fetching configuration from server")
        async with self.session.get(
            url, params=params, headers=self._get_headers()
        ) as response:
            if response.status == 204:
                _LOGGER.debug("Configuration is up-to-date")
                return None  # No update
            response.raise_for_status()
            result = await response.json()
            _LOGGER.info("Received new configuration version: %s", result.get("config_version"))
            return result

    async def submit_metrics(
        self,
        installation_id: str,
        metrics: Dict[str, Any],
        health: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Submit metrics, and optionally Home Assistant health signals.

        The health key is omitted entirely when None. The server reads its
        absence as "not reported" and preserves existing health state, so a
        collection failure never resolves open problems by accident.
        """
        url = f"{self.server_url}/api/v1/installations/{installation_id}/metrics"
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **metrics,
        }

        if health is not None:
            data["health"] = health

        _LOGGER.debug("Submitting metrics to server")
        async with self.session.post(
            url, json=data, headers=self._get_headers()
        ) as response:
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

        _LOGGER.debug("Submitting batch of %s metrics to server", len(metrics_list))
        async with self.session.post(
            url, json=data, headers=self._get_headers()
        ) as response:
            response.raise_for_status()
            return await response.json()

    async def submit_alert(
        self,
        installation_id: str,
        severity: str,
        alert_type: str,
        title: str,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        resolved: bool = False,
        resolved_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit a single alert to server.
        
        Args:
            installation_id: Installation ID
            severity: Alert severity (info, warning, critical)
            alert_type: Alert type identifier (e.g., low_disk_space)
            title: Short alert title
            message: Detailed alert message
            context: Optional context data (any JSON object)
            resolved: Whether alert is already resolved
            resolved_at: When alert was resolved (ISO format)
        
        Returns:
            Response with alert_id and action (created, exists, or resolved)
        """
        url = f"{self.server_url}/api/v1/installations/{installation_id}/alerts"
        data = {
            "severity": severity,
            "type": alert_type,
            "title": title,
            "message": message,
            "triggered_at": datetime.now(timezone.utc).isoformat(),
            "resolved": resolved,
        }
        
        if context:
            data["context"] = context
        if resolved_at:
            data["resolved_at"] = resolved_at
        
        _LOGGER.debug("Submitting alert to server: type=%s, severity=%s", alert_type, severity)
        async with self.session.post(
            url, json=data, headers=self._get_headers()
        ) as response:
            response.raise_for_status()
            result = await response.json()
            _LOGGER.info(
                "Alert submitted: id=%s, action=%s",
                result.get("alert_id"),
                result.get("action")
            )
            return result

    async def submit_alerts_batch(
        self,
        installation_id: str,
        alerts: list,
    ) -> Dict[str, Any]:
        """Submit batch of alerts to server (max 100 per batch).
        
        Args:
            installation_id: Installation ID
            alerts: List of alert dictionaries with severity, type, title, message, etc.
        
        Returns:
            Response with processed_count and results list
        """
        url = f"{self.server_url}/api/v1/installations/{installation_id}/alerts/batch"
        data = {"alerts": alerts}

        _LOGGER.debug("Submitting batch of %s alerts to server", len(alerts))
        async with self.session.post(
            url, json=data, headers=self._get_headers()
        ) as response:
            response.raise_for_status()
            result = await response.json()
            _LOGGER.info(
                "Alert batch submitted: processed=%s",
                result.get("processed_count")
            )
            return result

    async def resolve_alert(
        self,
        installation_id: str,
        alert_type: str,
    ) -> Dict[str, Any]:
        """Resolve all unresolved alerts of a specific type.
        
        Args:
            installation_id: Installation ID
            alert_type: Alert type to resolve (e.g., low_disk_space)
        
        Returns:
            Response with resolved_count
        """
        url = f"{self.server_url}/api/v1/installations/{installation_id}/alerts/{alert_type}/resolve"
        
        _LOGGER.debug("Resolving alerts of type: %s", alert_type)
        async with self.session.post(
            url, headers=self._get_headers()
        ) as response:
            response.raise_for_status()
            result = await response.json()
            _LOGGER.info(
                "Alerts resolved: type=%s, count=%s",
                alert_type,
                result.get("resolved_count")
            )
            return result

