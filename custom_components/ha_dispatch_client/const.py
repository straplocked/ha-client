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
API_CLIENT_UPDATE = "/api/v1/installations/{installation_id}/client-update"

# --- Client self-update -----------------------------------------------------
# Full design: docs/technical/self-update.md

# Ed25519 public keys trusted to sign client releases, keyed by the key_id the
# server quotes in its release payload. Values are base64-encoded 32-byte raw
# public keys.
#
# Pinned here, in the client, on purpose. Installing a release is remote code
# execution on this machine, so the server must not be the thing that decides
# what code runs -- only whether and when an update is offered. An attacker who
# fully owns the Dispatch server can still serve nothing but releases we signed.
#
# EMPTY BY DEFAULT, AND THAT IS DELIBERATE: with no keys pinned, every install
# fails verification and self-update is inert. Shipping a placeholder key that
# nobody controls would be far worse than shipping none. Populate this from
# scripts/generate_signing_key.py before enabling updates.
RELEASE_SIGNING_KEYS: dict[str, str] = {}

# Hosts a release archive may be downloaded from. The signature is the real
# control -- a tampered archive fails verification wherever it came from -- so
# this is defence in depth, and keeps a server-supplied URL from being used to
# probe arbitrary hosts from inside the user's network.
RELEASE_ALLOWED_HOSTS = frozenset({
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
})

# desired_state keys controlling updates, pushed from the server.
CONF_UPDATE_CHANNEL = "update_channel"
CONF_AUTO_UPDATE = "auto_update"
CONF_TARGET_VERSION = "target_version"
CONF_UPDATE_WINDOW = "update_window"
CONF_RESTART_AFTER_UPDATE = "restart_after_update"

DEFAULT_UPDATE_CHANNEL = "stable"
DEFAULT_AUTO_UPDATE = False
DEFAULT_RESTART_AFTER_UPDATE = True

# Download and extraction caps. The archive is a handful of Python files; these
# are generous by an order of magnitude and exist to bound the damage from a
# hostile or corrupt payload rather than to be tuned.
UPDATE_MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
UPDATE_MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
# Refuse to start if free disk space is under this multiple of the archive size.
UPDATE_DISK_HEADROOM_FACTOR = 5

# Working tree, relative to the Home Assistant config directory. Deliberately
# NOT under custom_components/: Home Assistant's loader treats every directory
# there containing a manifest.json as an integration, so a backup copy parked
# there would be a second integration claiming this domain.
UPDATE_WORK_DIR = ".ha_dispatch_client"
UPDATE_BACKUP_SUBDIR = "backups"
UPDATE_STAGING_SUBDIR = "staging"
UPDATE_BACKUP_KEEP = 2

# Pending-update record. Stored via Home Assistant's storage helper, in
# config/.storage/ -- it has to outlive the swap that replaces this very
# directory, so it cannot live alongside the code it describes.
UPDATE_STORAGE_KEY = "ha_dispatch_client.update"
UPDATE_STORAGE_VERSION = 1

# Release payload keys, as sent by the server on the status response.
RELEASE_KEY = "client_release"

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

