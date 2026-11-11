"""Config flow for HA Dispatch Client."""
import logging
import socket
import uuid
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.network import get_url, NoURLAvailableError
from homeassistant.loader import async_get_integration
import aiohttp
import yarl

from .const import (
    DOMAIN,
    CONF_SERVER_URL,
    CONF_INSTALLATION_ID,
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_PAIRING_CODE,
    CONF_REGISTRATION_SECRET,
    DEFAULT_NAME,
    DEFAULT_SERVER_URL,
)
from .api_client import (
    HADispatchApiClient,
    PairingCodeError,
    RegistrationSecretError,
    RegistrationThrottledError,
)
from .pairing import (
    ERROR_INVALID_PAIRING_CODE,
    ERROR_UNKNOWN,
    is_well_formed_pairing_code,
    normalize_pairing_code,
    registration_error_key,
)

_LOGGER = logging.getLogger(__name__)


async def _async_client_version(hass: HomeAssistant) -> str | None:
    """Return the client version on disk, so the server knows it from enrolment.

    Read from the loaded manifest rather than a constant, which keeps it honest
    after a self-update has replaced these files.
    """
    try:
        integration = await async_get_integration(hass, DOMAIN)
    except Exception as err:  # noqa: BLE001 - the loader raises several types
        _LOGGER.warning("Could not determine the client version: %s", err)
        return None
    return None if integration.version is None else str(integration.version)


class HADispatchConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HA Dispatch Client.

    Onboarding starts in Dispatch: the installer makes a one-time pairing code
    there, and this dialog asks for that code and nothing else -- the server
    address is DEFAULT_SERVER_URL. A lab or self-hosted Dispatch still needs a
    URL and, perhaps, the legacy enrolment secret; that path is offered only
    to a user with Advanced Mode switched on in their profile, so a homeowner
    never sees a field they have no answer for.

    Thin on purpose: voluptuous is not installed in the dev environment, so
    this module cannot be imported by the tests. The decisions worth testing
    -- normalising the code, which error a failure shows -- live in pairing.py
    and api_client.py.
    """

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """First step: straight to the code, unless the user is in Advanced Mode."""
        if self.show_advanced_options:
            return self.async_show_menu(
                step_id="user", menu_options=["pair", "custom_server"]
            )
        return await self.async_step_pair(user_input)

    async def async_step_pair(self, user_input=None):
        """Ask for the installer's pairing code. The only question asked."""
        errors = {}

        if user_input is not None:
            code = normalize_pairing_code(user_input.get(CONF_PAIRING_CODE))
            if not is_well_formed_pairing_code(code):
                # Caught here so a typo does not spend one of the server's
                # throttled attempts.
                errors["base"] = ERROR_INVALID_PAIRING_CODE
            else:
                result, error = await self._async_register(
                    server_url=DEFAULT_SERVER_URL, pairing_code=code
                )
                if result is not None:
                    return result
                errors["base"] = error

        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema({vol.Required(CONF_PAIRING_CODE): str}),
            errors=errors,
        )

    async def async_step_custom_server(self, user_input=None):
        """Advanced: a server of your own, with a pairing code or the legacy secret."""
        errors = {}

        if user_input is not None:
            code = normalize_pairing_code(user_input.get(CONF_PAIRING_CODE))
            if code and not is_well_formed_pairing_code(code):
                errors["base"] = ERROR_INVALID_PAIRING_CODE
            else:
                result, error = await self._async_register(
                    server_url=user_input[CONF_SERVER_URL],
                    name=user_input.get(CONF_NAME),
                    registration_secret=user_input.get(CONF_REGISTRATION_SECRET) or None,
                    pairing_code=code or None,
                )
                if result is not None:
                    return result
                errors["base"] = error

        return self.async_show_form(
            step_id="custom_server",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SERVER_URL, default=DEFAULT_SERVER_URL): str,
                    vol.Optional(
                        CONF_NAME,
                        default=self.hass.config.location_name or "Home Assistant",
                    ): str,
                    vol.Optional(CONF_PAIRING_CODE, default=""): str,
                    # Required by internet-facing servers unless a pairing code
                    # is given; leave blank on a trusted network where the
                    # server has no secret configured.
                    vol.Optional(CONF_REGISTRATION_SECRET, default=""): str,
                }
            ),
            errors=errors,
        )

    async def _async_register(
        self,
        *,
        server_url: str,
        name: str | None = None,
        registration_secret: str | None = None,
        pairing_code: str | None = None,
    ):
        """Enrol with the server. Returns (create_entry result, None) or (None, error key)."""
        session = aiohttp_client.async_get_clientsession(self.hass)
        client = HADispatchApiClient(session=session, server_url=server_url)
        client_id = str(uuid.uuid4())

        # The base hostname (e.g. "homeassistant") with .local and Home
        # Assistant's own port, e.g. homeassistant.local:8123.
        base_hostname = socket.gethostname()
        try:
            ha_url = yarl.URL(get_url(self.hass, allow_internal=True))
            port = ha_url.port or 8123
        except (NoURLAvailableError, ValueError):
            port = 8123
        hostname = f"{base_hostname}.local:{port}"

        # The friendly display name, which can differ from the hostname.
        name = name or self.hass.config.location_name or base_hostname
        _LOGGER.info("Registering with hostname: %s, name: %s", hostname, name)

        try:
            registration_data = await client.register_installation(
                client_id=client_id,
                hostname=hostname,
                name=name,
                registration_secret=registration_secret,
                client_version=await _async_client_version(self.hass),
                pairing_code=pairing_code,
            )
            entry = self.async_create_entry(
                title=name or DEFAULT_NAME,
                data={
                    CONF_SERVER_URL: server_url,
                    CONF_CLIENT_ID: client_id,
                    CONF_INSTALLATION_ID: registration_data["installation_id"],
                    CONF_ACCESS_TOKEN: registration_data["access_token"],
                    # Stored so the coordinator can re-enrol unattended if the
                    # server ever stops recognising our token. The pairing
                    # code is NOT stored: it is single use, and spent now.
                    CONF_REGISTRATION_SECRET: registration_secret or "",
                },
            )
        except (
            PairingCodeError,
            RegistrationThrottledError,
            RegistrationSecretError,
            aiohttp.ClientError,
            KeyError,
            ValueError,
            TypeError,
            TimeoutError,
        ) as err:
            error = registration_error_key(err)
            if error == ERROR_UNKNOWN:
                _LOGGER.exception("Unexpected error during registration: %s", err)
            else:
                _LOGGER.error("Registration failed (%s): %s", error, err)
            return None, error

        return entry, None

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
            data_schema=vol.Schema(
                {
                    # Add configurable options here
                    # For example: scan interval, enable/disable metrics, etc.
                }
            ),
        )

