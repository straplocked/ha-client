# Services Reference

Complete reference for all 6 services exposed by the HA Dispatch Client integration. Services are accessible via **Developer Tools > Services** in the Home Assistant UI, and can be called from automations, scripts, and the REST API.

All services use the singleton registration pattern -- they are registered once for the domain and dynamically resolve the active coordinator at call time.

---

## send_test_metrics

**Full name:** `ha_dispatch_client.send_test_metrics`

Send test metrics to the server to verify connectivity. All parameters are optional and default to representative values.

### Parameters

| Parameter | Type | Required | Default | Range | Description |
|-----------|------|----------|---------|-------|-------------|
| `cpu_load` | float | No | 75.0 | 0-200 | CPU load value (0-100 typical) |
| `memory_used` | float | No | 85.0 | 0-100 | Memory usage percentage |
| `disk_free` | float | No | 15.0 | 0-100 | Disk free percentage |

### Schema (voluptuous)

```python
SERVICE_SEND_TEST_METRICS_SCHEMA = vol.Schema({
    vol.Optional("cpu_load", default=75.0): cv.positive_float,
    vol.Optional("memory_used", default=85.0): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
    vol.Optional("disk_free", default=15.0): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
})
```

### Handler behavior

1. Resolves the active coordinator via `get_coordinator_for_service()`
2. Builds a full metrics payload from the provided values (derives `cpu_load_1m/5m/15m`, `memory_used_mb`, `disk_used_percent`, etc.)
3. Calls `api_client.submit_metrics()` to send to the server
4. Logs success/failure

### Example

```yaml
service: ha_dispatch_client.send_test_metrics
data:
  cpu_load: 50.0
  memory_used: 60.0
  disk_free: 40.0
```

---

## trigger_alert

**Full name:** `ha_dispatch_client.trigger_alert`

Submit predefined test alerts to the server using the Alert API. Useful for testing alert generation, notifications, and server-side thresholds.

### Parameters

| Parameter | Type | Required | Default | Options | Description |
|-----------|------|----------|---------|---------|-------------|
| `alert_type` | select | Yes | `disk_low` | `disk_low`, `cpu_high`, `memory_high`, `all` | Type of alert to trigger |

### Schema (voluptuous)

```python
SERVICE_TRIGGER_ALERT_SCHEMA = vol.Schema({
    vol.Required("alert_type", default="disk_low"): vol.In(["disk_low", "cpu_high", "memory_high", "all"]),
})
```

### Alert templates

| alert_type | Server type | Severity | Context |
|------------|-------------|----------|---------|
| `disk_low` | `low_disk_space` | warning | `disk_free_percent: 5.0` |
| `cpu_high` | `high_cpu_load` | warning | `cpu_load_1m: 8.5` |
| `memory_high` | `high_memory_usage` | critical | `memory_used_percent: 95.0` |
| `all` | (batch of all three) | mixed | -- |

### Handler behavior

1. Resolves the active coordinator
2. Looks up the selected alert template
3. For `all`: calls `api_client.submit_alerts_batch()` with all three templates
4. For single types: calls `api_client.submit_alert()` with the matching template
5. Logs the alert ID and action returned by the server

### Example

```yaml
service: ha_dispatch_client.trigger_alert
data:
  alert_type: all
```

---

## force_update

**Full name:** `ha_dispatch_client.force_update`

Trigger an immediate coordinator refresh instead of waiting for the next scheduled interval (default 60 seconds). This runs the full update cycle: status report, config check, metrics collection, and metrics submission.

### Parameters

None.

### Schema

No schema -- accepts no parameters.

### Handler behavior

1. Resolves the active coordinator
2. Calls `coordinator.async_request_refresh()`
3. Logs completion

### Example

```yaml
service: ha_dispatch_client.force_update
data: {}
```

---

## send_custom_metric

**Full name:** `ha_dispatch_client.send_custom_metric`

Send a custom metric payload with arbitrary values for advanced testing. Unlike `send_test_metrics`, this sends only the fields you provide -- no defaults are filled in.

### Parameters

| Parameter | Type | Required | Default | Range | Description |
|-----------|------|----------|---------|-------|-------------|
| `cpu_load_1m` | float | No | -- | 0-999 | 1-minute CPU load average |
| `memory_used_percent` | float | No | -- | 0-100 | Memory usage percentage |
| `disk_free_percent` | float | No | -- | 0-100 | Disk free percentage |
| `warnings` | string | No | -- | -- | Comma-separated warning tags |

### Schema (voluptuous)

```python
SERVICE_SEND_CUSTOM_METRIC_SCHEMA = vol.Schema({
    vol.Optional("cpu_load_1m"): cv.positive_float,
    vol.Optional("memory_used_percent"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
    vol.Optional("disk_free_percent"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
    vol.Optional("warnings"): cv.string,
})
```

### Handler behavior

1. Resolves the active coordinator
2. Builds a metrics dict from only the provided fields
3. If `warnings` is provided, splits by comma into a list
4. Calls `api_client.submit_metrics()` with the partial payload
5. Logs the submitted metrics

### Example

```yaml
service: ha_dispatch_client.send_custom_metric
data:
  cpu_load_1m: 2.5
  memory_used_percent: 67.8
  disk_free_percent: 35.5
  warnings: "low_disk,custom_warning"
```

---

## submit_alert

**Full name:** `ha_dispatch_client.submit_alert`

Submit a fully custom structured alert to the HA Dispatch server. Provides complete control over all alert fields. Use this for automations and custom monitoring rules.

### Parameters

| Parameter | Type | Required | Default | Options | Description |
|-----------|------|----------|---------|---------|-------------|
| `severity` | select | Yes | -- | `info`, `warning`, `critical` | Alert severity level |
| `type` | string | Yes | -- | -- | Alert type identifier (e.g., `low_disk_space`) |
| `title` | string | Yes | -- | -- | Short alert title (max 255 characters) |
| `message` | string (multiline) | Yes | -- | -- | Detailed alert message |
| `context` | object | No | -- | -- | Additional context data as JSON |
| `resolved` | boolean | No | `false` | -- | Mark alert as already resolved |

### Schema (voluptuous)

```python
SERVICE_SUBMIT_ALERT_SCHEMA = vol.Schema({
    vol.Required("severity"): vol.In(["info", "warning", "critical"]),
    vol.Required("type"): cv.string,
    vol.Required("title"): cv.string,
    vol.Required("message"): cv.string,
    vol.Optional("context"): dict,
    vol.Optional("resolved", default=False): cv.boolean,
})
```

### Handler behavior

1. Resolves the active coordinator
2. Logs the alert type, severity, and title
3. Calls `api_client.submit_alert()` with all provided fields
4. Logs the returned alert ID and action (`created`, `exists`, or `resolved`)

### Example

```yaml
service: ha_dispatch_client.submit_alert
data:
  severity: warning
  type: low_disk_space
  title: "Low Disk Space"
  message: "Disk space has dropped below 10% on the system drive"
  context:
    disk_free_percent: 8.5
    disk_free_mb: 5120
  resolved: false
```

### Automation example

```yaml
automation:
  - alias: "Monitor Disk Space"
    trigger:
      - platform: numeric_state
        entity_id: sensor.disk_use_percent
        above: 90
    action:
      - service: ha_dispatch_client.submit_alert
        data:
          severity: warning
          type: low_disk_space
          title: "Low Disk Space on {{ trigger.to_state.attributes.friendly_name }}"
          message: "Disk usage is {{ trigger.to_state.state }}%"
          context:
            disk_use_percent: "{{ trigger.to_state.state }}"
            entity_id: "{{ trigger.entity_id }}"
```

---

## resolve_alert

**Full name:** `ha_dispatch_client.resolve_alert`

Resolve all unresolved alerts of a specific type for this installation. Use this to clear alerts when the underlying condition has been fixed.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `type` | string | Yes | -- | Alert type to resolve (e.g., `low_disk_space`) |

### Schema (voluptuous)

```python
SERVICE_RESOLVE_ALERT_SCHEMA = vol.Schema({
    vol.Required("type"): cv.string,
})
```

### Handler behavior

1. Resolves the active coordinator
2. Logs the alert type being resolved
3. Calls `api_client.resolve_alert()` with the provided type
4. Logs the count of resolved alerts (or "no unresolved alerts found")

### Example

```yaml
service: ha_dispatch_client.resolve_alert
data:
  type: low_disk_space
```

### Automation example

```yaml
automation:
  - alias: "Clear Disk Space Alert"
    trigger:
      - platform: numeric_state
        entity_id: sensor.disk_use_percent
        below: 85
    action:
      - service: ha_dispatch_client.resolve_alert
        data:
          type: low_disk_space
```

---

## install_update

**Full name:** `ha_dispatch_client.install_update`

Install the client release the HA Dispatch server is currently offering. The
archive is verified against a signing key pinned in `const.py` before anything
is replaced, and Home Assistant restarts afterwards to load it.

The same install is available without a service call from Settings -> Updates,
via the `update.ha_dispatch_client_update` entity. This service exists for
automations and for triggering an install from a script.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `force` | boolean | No | `false` | Install even when the offered version is not newer than the installed one |

### Schema (voluptuous)

```python
SERVICE_INSTALL_UPDATE_SCHEMA = vol.Schema({
    vol.Optional("force", default=False): cv.boolean,
})
```

### Handler behavior

1. Resolves the active coordinator; aborts if self-update is unavailable
2. Refreshes the coordinator first, so the release payload is current rather
   than whatever the last poll happened to carry
3. Aborts with a warning if the server is not offering a release
4. Calls `updater.async_install()`, which downloads, verifies the digest and
   signature, validates the archive, swaps the directory, and restarts

### Example

```yaml
service: ha_dispatch_client.install_update
```

### Notes

- `force` allows reinstalling the same version or moving backwards. A downgrade
  is occasionally the right call during an incident, but never something to do
  by accident, hence the explicit flag.
- This restarts Home Assistant on success. See
  [Updating the client](../user/updating.md) for the operator-facing guide and
  [Self-Update](self-update.md) for the design.

---

## Service Registration Pattern

All services are registered once using the singleton pattern in `setup_services()` (called from `async_setup_entry()`). A guard check prevents duplicate registration:

```python
if hass.services.has_service(DOMAIN, "send_test_metrics"):
    return  # Already registered
```

Services are removed only when the last config entry unloads via `teardown_services()`.

For detailed analysis of this pattern, see [Service Registration](service-registration.md).

---

## Related Documentation

- [Alert API](alert-api.md) -- Full Alert API endpoint reference
- [Architecture](architecture.md) -- System architecture and data flow
- [Service Registration](service-registration.md) -- Singleton registration pattern
