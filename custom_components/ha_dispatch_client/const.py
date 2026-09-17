"""Constants for HA Dispatch Client integration."""

DOMAIN = "ha_dispatch_client"

# Configuration keys
CONF_SERVER_URL = "server_url"
CONF_INSTALLATION_ID = "installation_id"
CONF_ACCESS_TOKEN = "access_token"
CONF_CLIENT_ID = "client_id"
CONF_REGISTRATION_SECRET = "registration_secret"

# Default values
DEFAULT_SCAN_INTERVAL = 60  # seconds
DEFAULT_NAME = "HA Dispatch"

# Health signal thresholds. Overridable from the server via desired_state.
DEFAULT_STALE_ENTITY_HOURS = 24
DEFAULT_BATTERY_LOW_PERCENT = 20
DEFAULT_BATTERY_CRITICAL_PERCENT = 5

# Domains worth checking for staleness. Restricted to things that report on a
# cadence -- a scene or an input_boolean legitimately never changes on its own,
# so flagging those as stale would be pure noise.
STALE_CANDIDATE_DOMAINS = frozenset({"sensor", "binary_sensor"})

# Must stay at or under the server's retention cap. The server keeps the first
# 500 items and drops the rest; rollup counts are sent separately, so they stay
# accurate even when detail is truncated.
HEALTH_ITEM_CAP = 500

# Health item types. These strings are a wire contract with the server -- see
# InstallationHealthItem::TYPES. Unknown types are rejected with a 422.
HEALTH_TYPE_UNAVAILABLE_ENTITY = "unavailable_entity"
HEALTH_TYPE_STALE_ENTITY = "stale_entity"
HEALTH_TYPE_FAILED_INTEGRATION = "failed_integration"
HEALTH_TYPE_LOW_BATTERY = "low_battery"
HEALTH_TYPE_DISABLED_AUTOMATION = "disabled_automation"
HEALTH_TYPE_UNAVAILABLE_AUTOMATION = "unavailable_automation"

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

# Attribute keys
ATTR_INSTALLATION_ID = "installation_id"
ATTR_LAST_SEEN = "last_seen_at"
ATTR_HA_VERSION = "ha_version"
ATTR_CONFIG_VERSION = "config_version"
ATTR_POLL_INTERVAL = "poll_interval_seconds"

