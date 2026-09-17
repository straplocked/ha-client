# Changelog

All notable changes to HA Dispatch Client will be documented in this file.

## [1.3.0] - 2026-08-09

### Added
- Home Assistant health signal reporting (`health.py`), sent alongside metrics:
  - Unavailable entities, with per-entity detail
  - Entities that have gone quiet relative to their own reporting cadence, based
    on `last_reported` rather than `last_changed`
  - Config entries in a failed or retrying state
  - Battery levels, split into low and critical bands
  - Automations that are disabled or unavailable
- Server-controlled thresholds via `desired_state`: `stale_entity_hours`,
  `battery_low_percent`, `battery_critical_percent`
- Unit tests for health classification (`tests/test_health.py`), which stub the
  handful of Home Assistant symbols involved rather than requiring a full install

### Added (registration secret)
- `register_installation()` accepts a `registration_secret`, sent as the
  `X-Registration-Secret` header. Internet-facing servers require one: registration
  is the only unauthenticated endpoint and it issues a bearer token.
- New optional "Registration Secret" field in the config flow. A rejected secret
  raises `RegistrationSecretError` and surfaces as its own error message, rather
  than being reported as a connection failure.
- Leave the field blank for servers on a trusted network with no secret configured.

### Changed
- `api_client.submit_metrics()` accepts an optional `health` payload. The key is
  omitted entirely when collection fails, which the server reads as "not
  reported" and so leaves existing health state untouched. An empty payload
  would instead mean "everything recovered".

### Notes
- Backward compatible. A server without the health migration ignores the extra
  key and continues accepting metrics as before.
- Item detail is capped at 500 per report. Rollup counts are sent separately and
  are not derived from that list, so they stay accurate during a mass outage.

## [1.2.2] - 2025-11-17

### Changed
- domain fix


## [1.2.1] - 2025-11-16

### Changed
- Fixed Hostname bug


## [1.2.0] - 2025-11-17

### Added
- **Alert API Integration** - Full support for server's Alert API
  - Added `submit_alert` service for structured alert submission
  - Added `resolve_alert` service to resolve alerts by type
  - Added `submit_alerts_batch` API method for batch alert submission
  - Alert deduplication support (server prevents duplicate alerts)
  - Proper alert context with JSON data fields

### Changed
- **Updated `trigger_alert` service** - Now uses Alert API directly instead of metrics
  - Submits structured alerts with severity, type, title, message, context
  - Single alerts: `disk_low`, `cpu_high`, `memory_high`
  - Batch submission when `alert_type: all` is used
  - More explicit logging of alert IDs and actions

### Technical Details
- Added 3 new methods to `api_client.py`:
  - `submit_alert()` - Submit single alert
  - `submit_alerts_batch()` - Submit up to 100 alerts
  - `resolve_alert()` - Resolve alerts by type
- Updated service schemas, services.yaml, and strings.json
- Alerts now follow server's expected structure with proper field validation

## [1.1.1] - 2025-11-16

### Changed
- Testing

## [1.1.0] - 2025-11-16

### Fixed
- **Service Registration** - Fixed critical issue where services weren't appearing in Home Assistant
  - Services now register once per domain (not per config entry)
  - Added `setup_services()` function with duplicate check
  - Added `get_coordinator_for_service()` helper for dynamic coordinator access
  - Added `teardown_services()` for proper cleanup
  - Pattern now matches professional integrations (meross_lan, mqtt, zha)

### Added
- **Unified Deploy Script** - Single `deploy.sh` script handles both install and updates
  - Auto-detects install vs update
  - Creates automatic backups before updates
  - Comprehensive verification
  - Smart restart prompt
  - Version tracking

### Changed
- Consolidated 15+ deployment scripts into one unified `deploy.sh`
- Added VERSION file for tracking releases
- Updated manifest.json to reflect version

### Documentation
- Added DEPLOY_README.md for deployment guide
- Added FIX_SUMMARY.md explaining the service registration fix
- Added ANALYSIS_COMPLETE.md with detailed analysis
- Added cleanup_old_scripts.sh for removing obsolete scripts

## [1.0.0] - 2025-11-15

### Added
- Initial release
- Client registration with HA Dispatch server
- System metrics collection (CPU, memory, disk, uptime)
- Configuration polling and application
- Status reporting
- 4 debug services:
  - `send_test_metrics` - Send test metrics with custom values
  - `trigger_alert` - Trigger alerts for testing
  - `force_update` - Force immediate update
  - `send_custom_metric` - Send custom metrics
- Sensor entities for status display
- Config flow for easy setup
- Bearer token authentication

### Known Issues
- Services not appearing in Developer Tools (fixed in 1.1.0)
