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

    def async_update_listeners(self):
        """Real coordinators push entity updates; nothing is listening here."""


class _CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


class _Entity:
    """Stand-in for the entity base classes the platforms inherit from."""


class _RepairsFlow:
    """Stand-in for a Home Assistant repair flow.

    Returns the flow result dictionaries Home Assistant would build, so a test
    can assert on which step a dialog landed on without a flow manager.
    """

    hass = None
    context = None

    def async_show_menu(self, *, step_id, menu_options, description_placeholders=None):
        return {
            "type": "menu",
            "step_id": step_id,
            "menu_options": menu_options,
        }

    def async_show_form(self, *, step_id, data_schema=None, **kwargs):
        return {"type": "form", "step_id": step_id}

    def async_create_entry(self, *, title=None, data=None, **kwargs):
        return {"type": "create_entry", "title": title, "data": data}


class _IssueSeverity:
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"


def _parse_datetime(value):
    """Home Assistant's dt_util.parse_datetime, near enough for ISO 8601."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _install_persistent_notification(components):
    """persistent_notification, keeping a record of everything it was told.

    Tests read these lists directly rather than patching, which keeps the
    assertions about what the customer actually sees in one obvious place.
    """
    module = _module("homeassistant.components.persistent_notification")
    components.persistent_notification = module

    if hasattr(module, "created"):
        return

    module.created = []
    module.dismissed = []

    def async_create(hass, message, title=None, notification_id=None):
        module.created.append(
            {"message": message, "title": title, "notification_id": notification_id}
        )

    def async_dismiss(hass, notification_id):
        module.dismissed.append(notification_id)

    def reset():
        module.created.clear()
        module.dismissed.clear()

    module.async_create = async_create
    module.async_dismiss = async_dismiss
    module.reset = reset


def _install_issue_registry(helpers):
    """issue_registry, likewise recording rather than registering."""
    module = _module("homeassistant.helpers.issue_registry")
    helpers.issue_registry = module

    if hasattr(module, "created_issues"):
        return

    module.IssueSeverity = _IssueSeverity
    module.created_issues = []
    module.deleted_issues = []
    module.issues = {}

    def async_create_issue(hass, domain, issue_id, **kwargs):
        entry = {"domain": domain, "issue_id": issue_id, **kwargs}
        module.created_issues.append(entry)
        module.issues[(domain, issue_id)] = entry

    def async_delete_issue(hass, domain, issue_id):
        module.deleted_issues.append((domain, issue_id))
        module.issues.pop((domain, issue_id), None)

    def reset():
        module.created_issues.clear()
        module.deleted_issues.clear()
        module.issues.clear()

    module.async_create_issue = async_create_issue
    module.async_delete_issue = async_delete_issue
    module.reset = reset


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


async def _async_get_integrations(hass, domains):
    """No manifests unless a test supplies them.

    components.py treats an unresolvable manifest as "no name, no version",
    which is exactly what a built-in integration looks like, so this is a
    realistic default rather than a broken one.
    """
    return {}


async def _async_get_custom_components(hass):
    return {}


def _install_hassio(components):
    """Supervisor helpers, answering the way a Core-only install does.

    Core-only is the majority case and the one most likely to be got wrong, so
    it is what every test gets until it says otherwise. A test simulating a
    supervised install overrides these four.
    """
    module = _module("homeassistant.components.hassio")
    components.hassio = module

    _set_missing(
        module,
        is_hassio=lambda hass: False,
        get_os_info=lambda hass: None,
        get_info=lambda hass: None,
        get_supervisor_info=lambda hass: None,
        get_addons_info=lambda hass: None,
    )


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

    data_entry_flow = _module("homeassistant.data_entry_flow")
    _set_missing(data_entry_flow, FlowResult=dict)

    components = _module("homeassistant.components")
    _install_persistent_notification(components)
    _install_hassio(components)

    binary_sensor = _module("homeassistant.components.binary_sensor")
    _set_missing(
        binary_sensor, BinarySensorEntity=_Entity, BinarySensorDeviceClass=object
    )
    components.binary_sensor = binary_sensor

    repairs = _module("homeassistant.components.repairs")
    _set_missing(repairs, RepairsFlow=_RepairsFlow)
    components.repairs = repairs

    update_coordinator = _module("homeassistant.helpers.update_coordinator")
    _set_missing(
        update_coordinator,
        UpdateFailed=_UpdateFailed,
        DataUpdateCoordinator=_DataUpdateCoordinator,
        CoordinatorEntity=_CoordinatorEntity,
    )
    helpers.update_coordinator = update_coordinator

    _install_issue_registry(helpers)

    entity_platform = _module("homeassistant.helpers.entity_platform")
    _set_missing(entity_platform, AddEntitiesCallback=object)
    helpers.entity_platform = entity_platform

    network = _module("homeassistant.helpers.network")
    _set_missing(network, get_url=lambda hass, **kwargs: "http://127.0.0.1:8123")
    helpers.network = network

    storage = _module("homeassistant.helpers.storage")
    _set_missing(storage, Store=_Store)
    helpers.storage = storage

    aiohttp_client = _module("homeassistant.helpers.aiohttp_client")
    _set_missing(aiohttp_client, async_get_clientsession=lambda hass: None)
    helpers.aiohttp_client = aiohttp_client

    loader = _module("homeassistant.loader")
    _set_missing(
        loader,
        async_get_integration=_async_get_integration,
        async_get_integrations=_async_get_integrations,
        async_get_custom_components=_async_get_custom_components,
    )

    util = _module("homeassistant.util")
    dt_module = _module("homeassistant.util.dt")
    _set_missing(
        dt_module,
        utcnow=lambda: datetime.now(timezone.utc),
        # The coordinator checks update windows against local wall-clock time.
        now=lambda: datetime.now(),
        parse_datetime=_parse_datetime,
    )
    util.dt = dt_module


install_stubs()
