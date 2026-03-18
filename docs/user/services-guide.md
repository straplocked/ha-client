# Services Guide

The HA Dispatch Client provides 6 services for testing connectivity, submitting metrics, managing alerts, and forcing data refreshes. This guide covers all of them with parameters, examples, and usage patterns.

## Service Summary

| Service | Purpose |
|---------|---------|
| `ha_dispatch_client.send_test_metrics` | Test connectivity with mock metric values |
| `ha_dispatch_client.trigger_alert` | Quick alert testing (disk_low, cpu_high, memory_high, all) |
| `ha_dispatch_client.force_update` | Trigger immediate coordinator refresh |
| `ha_dispatch_client.send_custom_metric` | Submit arbitrary metric values |
| `ha_dispatch_client.submit_alert` | Full alert submission (severity, type, title, message, context) |
| `ha_dispatch_client.resolve_alert` | Resolve all unresolved alerts of a given type |

## Service Details

### 1. Send Test Metrics

**Service**: `ha_dispatch_client.send_test_metrics`

Send test metrics with custom values to verify server connectivity and metric submission.

#### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `cpu_load` | float | 75.0 | CPU load percentage (0-200) |
| `memory_used` | float | 85.0 | Memory usage percentage (0-100) |
| `disk_free` | float | 15.0 | Disk free percentage (0-100) |

#### Examples

**Via Developer Tools -> Services:**

```yaml
service: ha_dispatch_client.send_test_metrics
data:
  cpu_load: 80
  memory_used: 90
  disk_free: 20
```

**Via Automation:**

```yaml
automation:
  - alias: "Test HA Dispatch Metrics"
    trigger:
      - platform: state
        entity_id: input_button.test_metrics
    action:
      - service: ha_dispatch_client.send_test_metrics
        data:
          cpu_load: 75.5
          memory_used: 85.0
          disk_free: 15.0
```

---

### 2. Trigger Alert

**Service**: `ha_dispatch_client.trigger_alert`

Send extreme metric values that trigger alerts on the server. Useful for testing the alert system without constructing full alert payloads.

#### Parameters

| Parameter | Type | Required | Options | Description |
|-----------|------|----------|---------|-------------|
| `alert_type` | select | Yes | `disk_low`, `cpu_high`, `memory_high`, `all` | Type of alert to trigger |

#### Alert Types

| Type | What It Sends | Result |
|------|---------------|--------|
| `disk_low` | 5% disk free | Triggers low disk alert |
| `cpu_high` | 850% CPU load | Triggers high CPU alert |
| `memory_high` | 95% memory usage | Triggers high memory alert |
| `all` | All extreme values at once | Triggers all alerts |

#### Examples

**Via Developer Tools -> Services:**

```yaml
service: ha_dispatch_client.trigger_alert
data:
  alert_type: disk_low
```

**Via Script (test all alert types sequentially):**

```yaml
script:
  test_all_alerts:
    sequence:
      - service: ha_dispatch_client.trigger_alert
        data:
          alert_type: disk_low
      - delay: 5
      - service: ha_dispatch_client.trigger_alert
        data:
          alert_type: cpu_high
      - delay: 5
      - service: ha_dispatch_client.trigger_alert
        data:
          alert_type: memory_high
```

---

### 3. Force Update

**Service**: `ha_dispatch_client.force_update`

Force an immediate coordinator update instead of waiting for the next scheduled interval (default: 60 seconds).

#### Parameters

None.

#### Examples

**Via Developer Tools -> Services:**

```yaml
service: ha_dispatch_client.force_update
data: {}
```

**Via Automation (force update after HA restart):**

```yaml
automation:
  - alias: "Force HA Dispatch Update on Restart"
    trigger:
      - platform: homeassistant
        event: start
    action:
      - delay: 30  # Wait for system to stabilize
      - service: ha_dispatch_client.force_update
```

---

### 4. Send Custom Metric

**Service**: `ha_dispatch_client.send_custom_metric`

Send completely custom metric values for advanced testing scenarios. All parameters are optional.

#### Parameters

| Parameter | Type | Range | Description |
|-----------|------|-------|-------------|
| `cpu_load_1m` | float | 0+ | 1-minute CPU load average |
| `memory_used_percent` | float | 0-100 | Memory usage percentage |
| `disk_free_percent` | float | 0-100 | Disk free percentage |
| `warnings` | string | -- | Comma-separated warning tags |

#### Examples

**Via Developer Tools -> Services:**

```yaml
service: ha_dispatch_client.send_custom_metric
data:
  cpu_load_1m: 2.5
  memory_used_percent: 67.8
  disk_free_percent: 35.5
  warnings: "low_disk,high_cpu"
```

**Test extreme edge cases:**

```yaml
service: ha_dispatch_client.send_custom_metric
data:
  cpu_load_1m: 99.99
  memory_used_percent: 99.9
  disk_free_percent: 0.1
  warnings: "critical,low_disk,high_cpu,high_memory"
```

---

### 5. Submit Alert

**Service**: `ha_dispatch_client.submit_alert`

Submit a structured alert to the HA Dispatch server with full control over severity, type, title, message, and context. For details on alert severity levels, common types, and automation patterns, see the [Alerts Guide](alerts-guide.md).

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `severity` | select | Yes | `info`, `warning`, or `critical` |
| `type` | string | Yes | Alert type identifier (e.g., `low_disk_space`) |
| `title` | string | Yes | Short alert title (max 255 characters) |
| `message` | string | Yes | Detailed alert message |
| `context` | object | No | Additional context data as a JSON object |
| `resolved` | boolean | No | Mark the alert as already resolved (default: `false`) |

#### Examples

**Basic alert:**

```yaml
service: ha_dispatch_client.submit_alert
data:
  severity: warning
  type: low_disk_space
  title: Low Disk Space
  message: Disk space has dropped below 10%
```

**Alert with context data:**

```yaml
service: ha_dispatch_client.submit_alert
data:
  severity: critical
  type: database_backup_failed
  title: Database Backup Failed
  message: The nightly database backup failed to complete
  context:
    backup_size: 0
    error_code: "TIMEOUT"
    last_successful: "2025-11-16T22:00:00Z"
```

**Submit as already resolved:**

```yaml
service: ha_dispatch_client.submit_alert
data:
  severity: info
  type: update_complete
  title: System Update Complete
  message: Home Assistant updated to 2025.11.3
  resolved: true
```

---

### 6. Resolve Alert

**Service**: `ha_dispatch_client.resolve_alert`

Resolve all unresolved alerts of a specific type for this installation.

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `type` | string | Yes | Alert type to resolve (e.g., `low_disk_space`) |

#### Examples

```yaml
service: ha_dispatch_client.resolve_alert
data:
  type: low_disk_space
```

The server returns the number of alerts resolved. If no unresolved alerts of the given type exist, the call succeeds with zero resolved.

---

## How to Access Services

### Method 1: Developer Tools (Web UI)

1. Go to **Developer Tools** -> **Services**.
2. Search for `ha_dispatch_client`.
3. Select a service from the dropdown.
4. Fill in parameters using the form.
5. Click **Call Service**.

### Method 2: Automations

Add service calls to your automations:

```yaml
automation:
  - alias: "Daily Alert Test"
    trigger:
      - platform: time
        at: "09:00:00"
    action:
      - service: ha_dispatch_client.trigger_alert
        data:
          alert_type: disk_low
```

### Method 3: Scripts

Create reusable scripts:

```yaml
script:
  test_ha_dispatch:
    sequence:
      - service: ha_dispatch_client.send_test_metrics
        data:
          cpu_load: 75
          memory_used: 80
          disk_free: 25
      - delay: 2
      - service: ha_dispatch_client.force_update
```

### Method 4: REST API

Call services via the Home Assistant REST API:

```bash
curl -X POST \
  http://homeassistant.local:8123/api/services/ha_dispatch_client/send_test_metrics \
  -H "Authorization: Bearer YOUR_HA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "cpu_load": 80,
    "memory_used": 90,
    "disk_free": 20
  }'
```

## Testing Workflow

### Step 1: Verify Basic Connectivity

```yaml
service: ha_dispatch_client.send_test_metrics
data:
  cpu_load: 50
  memory_used: 60
  disk_free: 40
```

**Expected:** Log message "Test metrics sent successfully". New metric appears on the server admin panel. No alerts triggered (all values normal).

### Step 2: Test Alert System

```yaml
service: ha_dispatch_client.trigger_alert
data:
  alert_type: disk_low
```

**Expected:** Log message "Alert-triggering metrics sent! Check server for alerts." A "Low Disk Space" alert appears in the server admin panel.

### Step 3: Test All Alert Types

```yaml
service: ha_dispatch_client.trigger_alert
data:
  alert_type: all
```

**Expected:** Multiple alerts generated on server. Installation marked as "warning".

### Step 4: Force Immediate Update

```yaml
service: ha_dispatch_client.force_update
data: {}
```

**Expected:** Coordinator runs immediately. Real metrics collected and sent.

### Step 5: Custom Scenario Testing

```yaml
service: ha_dispatch_client.send_custom_metric
data:
  cpu_load_1m: 5.0
  memory_used_percent: 80.0
  disk_free_percent: 12.0
  warnings: "custom_warning"
```

**Expected:** Custom metrics appear on server. Warnings array includes "custom_warning".

### Step 6: Submit and Resolve Alerts

```yaml
# Submit an alert
service: ha_dispatch_client.submit_alert
data:
  severity: warning
  type: low_disk_space
  title: Low Disk Space
  message: Disk space has dropped below 10%

# Then resolve it
service: ha_dispatch_client.resolve_alert
data:
  type: low_disk_space
```

**Expected:** Alert created on server, then resolved. Logs show "Alert submitted successfully" and "Resolved N alert(s) of type: low_disk_space".

## Verification

After calling any service, verify results in three places:

### Home Assistant Logs

```
Settings -> System -> Logs -> Search for "ha_dispatch"
```

Or via SSH:
```bash
ssh straplocked@homeassistant.local "tail -f /config/home-assistant.log | grep ha_dispatch"
```

### Server Admin Panel

1. Open `http://your-server:8080/admin`
2. Go to **Monitoring** -> **Installations** -> Your Installation
3. Check **Metrics** tab for submitted metrics
4. Check **Installation Alerts** for triggered alerts

### Server Logs

```bash
# If using Docker
docker compose logs -f --tail=50 | grep -i metric
```

## Troubleshooting Services

If services are not working as expected, see the [Troubleshooting Guide](troubleshooting.md) for solutions to common issues like services not appearing, service calls failing, and metrics not arriving on the server.

## Related Guides

- [Alerts Guide](alerts-guide.md) -- Alert severity levels, lifecycle, and automation examples
- [Testing Guide](testing.md) -- End-to-end testing workflow
- [Troubleshooting](troubleshooting.md) -- Fixing service-related issues
