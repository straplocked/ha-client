# Alerts Guide

The HA Dispatch Client provides two dedicated alert services -- `submit_alert` and `resolve_alert` -- for managing structured alerts on the HA Dispatch server. This guide covers alert severity levels, common types, the alert lifecycle, automation examples, and best practices.

## Alert Severity Levels

| Severity | Use For | Examples |
|----------|---------|----------|
| `info` | Informational events | Updates complete, status changes, daily reports |
| `warning` | Issues needing attention | Low disk, high load, slow responses, backup failures |
| `critical` | Urgent problems | Service down, system failure, security breach |

## Common Alert Types

These are suggested standard types. You can create your own using any `snake_case` identifier.

| Type | Typical Severity | Use Case |
|------|------------------|----------|
| `low_disk_space` | warning | Disk usage exceeds 90% |
| `high_cpu_load` | warning | CPU load above 90% |
| `high_memory_usage` | warning/critical | Memory usage above 90% |
| `installation_offline` | critical | No heartbeat for X minutes |
| `service_down` | critical | Required service not running |
| `backup_failed` | warning | Backup did not complete |
| `update_available` | info | Software update ready |
| `update_complete` | info | Update successfully applied |
| `config_error` | warning | Invalid configuration detected |
| `security_alert` | critical | Security event detected |

## Alert Lifecycle

```
1. SUBMIT ALERT
   |
2. SERVER CHECKS FOR DUPLICATE
   |
   |---> NEW: Creates alert, returns action="created"
   |---> EXISTS: Returns existing alert_id, action="exists"
   |---> RESOLVED: Marks as resolved, action="resolved"
   |
3. ALERT VISIBLE ON SERVER DASHBOARD
   |
4. RESOLVE ALERT (when condition clears)
   |
5. ALERT MARKED RESOLVED
```

Key points:
- The server deduplicates alerts. Submitting the same type for the same installation while an unresolved alert already exists will return `action="exists"` rather than creating a duplicate.
- Resolving an alert type resolves **all** unresolved alerts of that type for the installation.
- You can submit an alert as already resolved by setting `resolved: true`.

## Submitting Alerts

### Basic Alert

```yaml
service: ha_dispatch_client.submit_alert
data:
  severity: warning
  type: low_disk_space
  title: Low Disk Space
  message: Disk space has dropped below 10%
```

### Alert with Context Data

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

### Submit as Already Resolved

Useful for informational events that do not need follow-up:

```yaml
service: ha_dispatch_client.submit_alert
data:
  severity: info
  type: update_complete
  title: System Update Complete
  message: Home Assistant updated to 2025.11.3
  resolved: true
```

## Resolving Alerts

Resolve all unresolved alerts of a specific type:

```yaml
service: ha_dispatch_client.resolve_alert
data:
  type: low_disk_space
```

The server returns the count of resolved alerts. If none exist, the call succeeds with zero resolved.

## Automation Examples

### Monitor and Alert on Disk Space

```yaml
automation:
  # Trigger alert when disk is low
  - alias: "Alert: Low Disk Space"
    trigger:
      platform: numeric_state
      entity_id: sensor.disk_use_percent
      above: 90
    action:
      service: ha_dispatch_client.submit_alert
      data:
        severity: warning
        type: low_disk_space
        title: "Low Disk Space"
        message: "Disk usage is {{ trigger.to_state.state }}%"
        context:
          disk_use_percent: "{{ trigger.to_state.state }}"

  # Resolve when disk space recovers
  - alias: "Resolve: Low Disk Space"
    trigger:
      platform: numeric_state
      entity_id: sensor.disk_use_percent
      below: 85
    action:
      service: ha_dispatch_client.resolve_alert
      data:
        type: low_disk_space
```

### Monitor Service Status

```yaml
automation:
  - alias: "Alert: Service Down"
    trigger:
      platform: state
      entity_id: binary_sensor.mqtt_broker
      to: "unavailable"
      for: "00:05:00"
    action:
      service: ha_dispatch_client.submit_alert
      data:
        severity: critical
        type: mqtt_broker_down
        title: "MQTT Broker Offline"
        message: "MQTT broker has been unavailable for 5 minutes"
        context:
          last_seen: "{{ trigger.from_state.last_changed }}"

  - alias: "Resolve: Service Up"
    trigger:
      platform: state
      entity_id: binary_sensor.mqtt_broker
      to: "on"
    action:
      service: ha_dispatch_client.resolve_alert
      data:
        type: mqtt_broker_down
```

### Daily Status Report

```yaml
automation:
  - alias: "Daily Status Report"
    trigger:
      platform: time
      at: "09:00:00"
    action:
      service: ha_dispatch_client.submit_alert
      data:
        severity: info
        type: daily_status_report
        title: "Daily Status: {{ now().strftime('%Y-%m-%d') }}"
        message: "System running normally"
        context:
          uptime_hours: "{{ (as_timestamp(now()) - as_timestamp(states('sensor.uptime'))) / 3600 }}"
          cpu_load: "{{ states('sensor.processor_use') }}"
          memory_use: "{{ states('sensor.memory_use_percent') }}"
        resolved: true
```

### Quick Test with trigger_alert

For quick validation of server connectivity and the alert pipeline, use the `trigger_alert` service instead of constructing full payloads:

```yaml
# Test individual alert types
service: ha_dispatch_client.trigger_alert
data:
  alert_type: disk_low

# Or test all at once
service: ha_dispatch_client.trigger_alert
data:
  alert_type: all
```

See the [Services Guide](services-guide.md) for full details on `trigger_alert`.

## Context Data Examples

Context is an optional JSON object attached to an alert. It provides additional details that help with debugging and analysis on the server dashboard.

### System Metrics

```yaml
context:
  cpu_load_1m: 2.5
  cpu_load_5m: 2.1
  cpu_cores: 4
  memory_used_mb: 6144
  memory_total_mb: 8192
```

### Disk Information

```yaml
context:
  disk_free_percent: 8.5
  disk_free_gb: 42
  disk_total_gb: 500
  path: "/home"
```

### Service Status

```yaml
context:
  service_name: "postgresql"
  last_successful: "2025-11-17T08:00:00Z"
  error_message: "Connection timeout"
  retry_count: 3
```

### Backup Information

```yaml
context:
  backup_size_mb: 1024
  backup_duration_seconds: 180
  files_backed_up: 15234
  backup_path: "/backups/daily/2025-11-17.tar.gz"
```

## Debugging Alerts

### Check If Alert Was Created

Look for this log message:
```
INFO: Alert submitted successfully: id=123, action=created
```

### Check If Alert Already Exists

```
INFO: Alert submitted successfully: id=123, action=exists
```

### Check Resolution

```
INFO: Resolved 2 alert(s) of type: low_disk_space
```

### No Alerts to Resolve

```
INFO: No unresolved alerts found for type: low_disk_space
```

### Alerts Not Triggering

If `trigger_alert` succeeds but no alerts appear on the server:
1. Check alert threshold settings on the server: Settings -> System -> Settings -> Alerts
2. Verify thresholds are configured for the metric type you sent.
3. Confirm the installation is active on the server.
4. Check server logs: `docker compose logs | grep Alert`

## Tips and Best Practices

1. **Use consistent type identifiers.** Use `snake_case` (e.g., `low_disk_space`, not `Low Disk Space`). Be specific: `mysql_backup_failed` is better than `backup_failed`.

2. **Include useful context.** Add metrics, timestamps, and entity IDs. This makes debugging and analysis easier on the server side.

3. **Always pair alerts with resolutions.** When the triggering condition clears, call `resolve_alert`. This keeps the server dashboard clean and accurate.

4. **Use appropriate severity.** Reserve `critical` for truly urgent issues. Overusing it reduces its signal value.

5. **Test with `trigger_alert` first.** It is the quickest way to verify server connectivity. Then build custom automations using `submit_alert` and `resolve_alert`.

6. **Deduplication is safe.** The server prevents duplicate alerts for the same type and installation. You can submit repeatedly without creating duplicates.

## Related Guides

- [Services Guide](services-guide.md) -- Full parameter reference for all 6 services
- [Testing Guide](testing.md) -- End-to-end testing workflow
- [Troubleshooting](troubleshooting.md) -- Fixing alert-related issues
