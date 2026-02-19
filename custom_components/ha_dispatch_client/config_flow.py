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
import aiohttp
import yarl

from .const import (
    DOMAIN,
    CONF_SERVER_URL,
    CONF_INSTALLATION_ID,
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
)
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
                    session=session, server_url=user_input[CONF_SERVER_URL]
                )

                # Generate client ID
                client_id = str(uuid.uuid4())

                # Attempt registration
                # Get the base hostname (e.g., "homeassistant")
                base_hostname = socket.gethostname()
                
                # Get the port from Home Assistant's URL
                try:
                    ha_url = yarl.URL(get_url(self.hass, allow_internal=True))
                    port = ha_url.port or 8123  # Default to 8123 if not specified
                except (NoURLAvailableError, ValueError):
                    port = 8123  # Fallback to default HA port
                
                # Construct hostname with .local domain and port (e.g., homeassistant.local:8123)
                hostname = f"{base_hostname}.local:{port}"
                
                # Use user input as the friendly display name (can be different from hostname)
                name = user_input.get(CONF_NAME) or self.hass.config.location_name or base_hostname
                
                _LOGGER.info("Registering with hostname: %s, name: %s", hostname, name)
                
                registration_data = await client.register_installation(
                    client_id=client_id,
                    hostname=hostname,
                    name=name,
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

            except aiohttp.ClientError as err:
                _LOGGER.error("Cannot connect to server: %s", err)
                errors["base"] = "cannot_connect"
            except (KeyError, ValueError, TypeError, TimeoutError) as err:
                _LOGGER.exception("Unexpected error during registration: %s", err)
                errors["base"] = "unknown"

        # Show form
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SERVER_URL, default="http://localhost:8080"
                    ): str,
                    vol.Optional(
                        CONF_NAME,
                        default=self.hass.config.location_name or "Home Assistant",
                    ): str,
                }
            ),
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
            data_schema=vol.Schema(
                {
                    # Add configurable options here
                    # For example: scan interval, enable/disable metrics, etc.
                }
            ),
        )

