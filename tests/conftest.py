"""Shared Home Assistant stubs for the test suite.

Home Assistant is not installed here, so each test module has historically
registered the handful of symbols the code under test imports. That works until
two modules need overlapping sets and the one that runs first decides what the
other sees -- which is exactly what happened when coordinator.py started
importing updater.py and pulled in three more homeassistant modules.

pytest imports conftest before any test module, so installing the union once,
here, makes the per-module installers additive no-ops regardless of collection
order. Add to this when the integration imports something new.
"""
import sys
import types
from datetime import datetime, timezone


def _module(name: str):
    """Fetch or create a stub module, registering it in sys.modules."""
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)
    return sys.modules[name]


def _set_missing(module, **attrs) -> None:
    """Set attributes that are not already present."""
    for name, value in attrs.items():
        if not hasattr(module, name):
            setattr(module, name, value)


class _ConfigEntry:
    def __init__(self, data):
        self.data = dict(data)


class _ConfigEntryState:
    LOADED = "loaded"
    SETUP_ERROR = "setup_error"
    SETUP_RETRY = "setup_retry"
    MIGRATION_ERROR = "migration_error"
    FAILED_UNLOAD = "failed_unload"
    NOT_LOADED = "not_loaded"


class _HomeAssistant:
    pass


class _State:
    pass


class _HomeAssistantError(Exception):
    pass


class _UpdateFailed(Exception):
    pass


class _DataUpdateCoordinator:
    def __init__(self, hass, logger, name=None, update_interval=None):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval


class _Store:
    """In-memory stand-in for Home Assistant's JSON store."""

    def __init__(self, hass, version, key):
        self.hass = hass
        self.version = version
        self.key = key
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data

    async def async_remove(self):
        self.data = None


async def _async_get_integration(hass, domain):
    raise RuntimeError("no integration available in tests unless patched")


def install_stubs() -> None:
    """Register every homeassistant symbol the integration imports."""
    _module("homeassistant")

    config_entries = _module("homeassistant.config_entries")
    _set_missing(
        config_entries, ConfigEntry=_ConfigEntry, ConfigEntryState=_ConfigEntryState
    )

    const = _module("homeassistant.const")
    _set_missing(
        const,
        __version__="2026.6.4",
        STATE_OFF="off",
        STATE_UNAVAILABLE="unavailable",
        STATE_UNKNOWN="unknown",
        PERCENTAGE="%",
    )

    core = _module("homeassistant.core")
    _set_missing(core, HomeAssistant=_HomeAssistant, State=_State)

    exceptions = _module("homeassistant.exceptions")
    _set_missing(exceptions, HomeAssistantError=_HomeAssistantError)

    helpers = _module("homeassistant.helpers")

    update_coordinator = _module("homeassistant.helpers.update_coordinator")
    _set_missing(
        update_coordinator,
        UpdateFailed=_UpdateFailed,
        DataUpdateCoordinator=_DataUpdateCoordinator,
    )
    helpers.update_coordinator = update_coordinator

    storage = _module("homeassistant.helpers.storage")
    _set_missing(storage, Store=_Store)
    helpers.storage = storage

    aiohttp_client = _module("homeassistant.helpers.aiohttp_client")
    _set_missing(aiohttp_client, async_get_clientsession=lambda hass: None)
    helpers.aiohttp_client = aiohttp_client

    loader = _module("homeassistant.loader")
    _set_missing(loader, async_get_integration=_async_get_integration)

    util = _module("homeassistant.util")
    dt_module = _module("homeassistant.util.dt")
    _set_missing(
        dt_module,
        utcnow=lambda: datetime.now(timezone.utc),
        # The coordinator checks update windows against local wall-clock time.
        now=lambda: datetime.now(),
    )
    util.dt = dt_module


install_stubs()
