"""API Client for HA Dispatch Server."""
import logging
from typing import Any, Dict, List, Optional
import aiohttp
from datetime import datetime, timezone

from .const import (
    API_ACCESS_EXCHANGE,
    API_ACCESS_PENDING,
    API_ACCESS_POLL,
    API_ACCESS_RESPOND,
    API_ACCESS_REVOKE,
    API_COMPONENTS,
    ACCESS_POLL_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


class RegistrationSecretError(Exception):
    """Raised when the server rejects the enrolment secret."""


class InstallationAuthError(Exception):
    """Raised when the server rejects this installation's bearer token.

    Deliberately not an aiohttp.ClientError: a rejected token is not a
    transient network problem and must not be retried indefinitely. It means
    the server no longer recognises this installation -- its record was removed,
    or the database was rebuilt -- and the only route back is to re-enrol.
    """


class InstallationGoneError(InstallationAuthError):
    """Raised when the server has no installation with our stored id.

    Subclasses InstallationAuthError because the remedy is identical: re-enrol.
    This is the more common case in practice -- Laravel resolves the route model
    before the auth middleware runs, so an installation whose record was removed
    (or a database that was rebuilt, renumbering ids) answers 404 rather than
    401, no matter what token is presented.
    """


class ClientIdTakenError(Exception):
    """Raised when the server already has an installation with this client_id."""


class RemoteAccessUnavailable(Exception):
    """Raised when this server does not offer the remote access endpoints.

    Deliberately NOT an InstallationGoneError, even though both arrive as a
    404. A server predating remote access answers 404 for every /access/ path
    while the installation itself is perfectly healthy, and treating that as
    "the server has forgotten us" would re-enrol a working installation once a
    minute, forever.
    """


class RemoteAccessConflict(Exception):
    """Raised when the server refuses an access decision with a 422.

    A normal outcome, not a failure: the request was already answered on the
    web consent page, or it lapsed while the notification sat on screen. The
    only correct response is to clear the prompt, never to retry.
    """


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

    @staticmethod
    async def _raise_for_status(response, *, installation_scoped: bool = True) -> None:
        """Map "server does not recognise us" onto distinct errors.

        installation_scoped is False for registration, where a 404 means the
        server URL or path is wrong rather than the installation being absent.
        """
        if response.status == 401:
            raise InstallationAuthError(
                f"Server rejected the installation token (HTTP 401) for {response.url}"
            )
        if installation_scoped and response.status == 404:
            raise InstallationGoneError(
                f"Server has no record of this installation (HTTP 404) at {response.url}"
            )
        response.raise_for_status()

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
        client_version: Optional[str] = None,
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
            "client_version": client_version,
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
            if response.status == 422:
                body = await response.text()
                if "client_id" in body:
                    raise ClientIdTakenError(body)
            await self._raise_for_status(response, installation_scoped=False)
            result = await response.json()
            _LOGGER.info("Successfully registered installation: %s", result.get("installation_id"))
            return result

    async def report_status(
        self,
        installation_id: str,
        ha_version: Optional[str] = None,
        os_info: Optional[str] = None,
        client_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Report installation status.

        client_version lets the server see which client build is actually
        running. It is also how an update is confirmed independently of the
        client's own success report -- a version that changes on the next
        heartbeat is proof the swap took, whatever the client claimed.
        """
        url = f"{self.server_url}/api/v1/installations/{installation_id}/status"
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ha_version": ha_version,
            "os_info": os_info,
            "client_version": client_version,
        }

        _LOGGER.debug("Reporting status to server")
        async with self.session.post(
            url, json=data, headers=self._get_headers()
        ) as response:
            await self._raise_for_status(response)
            return await response.json()

    async def report_client_update(
        self,
        installation_id: str,
        status: str,
        from_version: Optional[str] = None,
        to_version: Optional[str] = None,
        phase: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Report the outcome of a client self-update.

        Args:
            installation_id: Installation ID
            status: started, success, or failed
            from_version: Version running before the update
            to_version: Version being installed
            phase: Which step failed (download, verify, validate, swap, ...)
            error: Failure detail, omitted on success

        An installation that reports "started" and is never heard from again is
        the signal that halts a fleet rollout, so this is called before the
        swap as well as after it.
        """
        url = (
            f"{self.server_url}/api/v1/installations/{installation_id}/client-update"
        )
        data = {
            "status": status,
            "from_version": from_version,
            "to_version": to_version,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if phase:
            data["phase"] = phase
        if error:
            data["error"] = error

        _LOGGER.debug("Reporting client update: status=%s, phase=%s", status, phase)
        async with self.session.post(
            url, json=data, headers=self._get_headers()
        ) as response:
            await self._raise_for_status(response)
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
            await self._raise_for_status(response)
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
            await self._raise_for_status(response)
            return await response.json()

    async def report_components(
        self,
        installation_id: str,
        components: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Report the full current component inventory.

        A full snapshot every time, never a delta. The server reconciles what
        it is sent against what it holds and retires anything absent, so an
        omitted component reads as an uninstalled one.
        """
        url = self.server_url + API_COMPONENTS.format(installation_id=installation_id)
        data = {"components": components}

        _LOGGER.debug("Reporting %s components to server", len(components))
        async with self.session.post(
            url, json=data, headers=self._get_headers()
        ) as response:
            await self._raise_for_status(response)
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
            await self._raise_for_status(response)
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
            await self._raise_for_status(response)
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
            await self._raise_for_status(response)
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
            await self._raise_for_status(response)
            result = await response.json()
            _LOGGER.info(
                "Alerts resolved: type=%s, count=%s",
                alert_type,
                result.get("resolved_count")
            )
            return result

    # --- Consent-gated remote access ---------------------------------------
    #
    # Every method here passes installation_scoped=False. A 404 on an /access/
    # path means the server has no remote access, not that this installation
    # has vanished -- see RemoteAccessUnavailable.

    @staticmethod
    async def _raise_for_access_status(response) -> None:
        """Map the two answers that are not failures onto their own errors."""
        if response.status == 404:
            raise RemoteAccessUnavailable(
                f"Server has no remote access endpoint at {response.url}"
            )
        if response.status == 422:
            raise RemoteAccessConflict(await response.text())
        await HADispatchApiClient._raise_for_status(
            response, installation_scoped=False
        )

    async def fetch_pending_access(self, installation_id: str) -> Dict[str, Any]:
        """Fetch the access requests waiting on the customer.

        Returns the policy, any standing consent, and the requests themselves.
        The request bodies are written for a homeowner; pass them through to
        the notification unchanged.
        """
        url = self.server_url + API_ACCESS_PENDING.format(
            installation_id=installation_id
        )

        async with self.session.get(url, headers=self._get_headers()) as response:
            await self._raise_for_access_status(response)
            return await response.json()

    async def respond_to_access(
        self,
        installation_id: str,
        session_id: Any,
        decision: str,
        note: Optional[str] = None,
        responder: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Report what the customer decided inside Home Assistant.

        decision is "grant" or "deny" and is sent verbatim. responder names the
        Home Assistant user who acted, so the audit receipt can say who agreed
        rather than "somebody"; it is omitted when we cannot identify them.

        Raises RemoteAccessConflict if the request was already answered or has
        lapsed.
        """
        url = self.server_url + API_ACCESS_RESPOND.format(
            installation_id=installation_id, session_id=session_id
        )
        data: Dict[str, Any] = {"decision": decision}
        if note:
            data["note"] = note
        if responder:
            data["responder"] = responder

        _LOGGER.debug("Reporting access decision %s for session %s", decision, session_id)
        async with self.session.post(
            url, json=data, headers=self._get_headers()
        ) as response:
            await self._raise_for_access_status(response)
            return await response.json()

    async def revoke_access(
        self,
        installation_id: str,
        session_id: Any,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Close a live session from inside Home Assistant."""
        url = self.server_url + API_ACCESS_REVOKE.format(
            installation_id=installation_id, session_id=session_id
        )
        data: Dict[str, Any] = {}
        if note:
            data["note"] = note

        _LOGGER.debug("Revoking access session %s", session_id)
        async with self.session.post(
            url, json=data, headers=self._get_headers()
        ) as response:
            await self._raise_for_access_status(response)
            return await response.json()

    async def poll_access(self, installation_id: str) -> List[Dict[str, Any]]:
        """Long-poll for authorised requests to run locally.

        The server holds this open for up to 25 s and returns the moment there
        is anything to do, so this is called again immediately after it
        returns. The client-side timeout only fires when the connection itself
        has stalled.
        """
        url = self.server_url + API_ACCESS_POLL.format(
            installation_id=installation_id
        )
        timeout = aiohttp.ClientTimeout(total=ACCESS_POLL_TIMEOUT)

        async with self.session.get(
            url, headers=self._get_headers(), timeout=timeout
        ) as response:
            await self._raise_for_access_status(response)
            payload = await response.json()
            return payload.get("requests") or []

    async def respond_to_exchange(
        self,
        installation_id: str,
        request_id: str,
        status: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None,
        body: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Hand back what the local Home Assistant said, or why it could not.

        An error and a response are mutually exclusive on the server side: any
        non-empty error is recorded as a failure and the status/body ignored.
        """
        url = self.server_url + API_ACCESS_EXCHANGE.format(
            installation_id=installation_id, request_id=request_id
        )

        if error:
            data: Dict[str, Any] = {"error": error[:255]}
        else:
            data = {
                "status": status if status is not None else 200,
                "headers": headers or {},
                "body": body,
            }

        async with self.session.post(
            url, json=data, headers=self._get_headers()
        ) as response:
            await self._raise_for_access_status(response)
            return await response.json()

