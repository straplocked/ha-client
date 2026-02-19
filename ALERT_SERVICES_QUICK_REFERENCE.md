# Alert Services Quick Reference

## 🚀 Quick Start

Deploy the update:
```bash
./deploy.sh
```

---

## 📋 New Services (v1.2.0)

### 1. Submit Custom Alert

**Service:** `ha_dispatch_client.submit_alert`

**Example - Basic Alert:**
```yaml
service: ha_dispatch_client.submit_alert
data:
  severity: warning
  type: low_disk_space
  title: Low Disk Space
  message: Disk space has dropped below 10%
```

**Example - Alert with Context:**
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

**Example - Submit Already Resolved:**
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

### 2. Resolve Alert

**Service:** `ha_dispatch_client.resolve_alert`

**Example:**
```yaml
service: ha_dispatch_client.resolve_alert
data:
  type: low_disk_space
```

**Returns:**
- Number of alerts resolved
- Resolves ALL unresolved alerts of that type for this installation

---

### 3. Quick Test Alerts (Updated)

**Service:** `ha_dispatch_client.trigger_alert`

**Examples:**
```yaml
# Low disk alert
service: ha_dispatch_client.trigger_alert
data:
  alert_type: disk_low

# High CPU alert
service: ha_dispatch_client.trigger_alert
data:
  alert_type: cpu_high

# High memory alert
service: ha_dispatch_client.trigger_alert
data:
  alert_type: memory_high

# ALL alerts at once (batch)
service: ha_dispatch_client.trigger_alert
data:
  alert_type: all
```

---

## 📊 Alert Severity Levels

| Severity | Use For | Examples |
|----------|---------|----------|
| `info` | Informational events | Updates, completions, status changes |
| `warning` | Issues needing attention | Low disk, high load, slow responses |
| `critical` | Urgent problems | Service down, system failure, security breach |

---

## 🏷️ Common Alert Types

Suggested standard types (you can create your own):

| Type | Severity | Use Case |
|------|----------|----------|
| `low_disk_space` | warning | Disk < 10% |
| `high_cpu_load` | warning | CPU > 90% |
| `high_memory_usage` | warning/critical | Memory > 90% |
| `installation_offline` | critical | No heartbeat for X minutes |
| `service_down` | critical | Required service not running |
| `backup_failed` | warning | Backup didn't complete |
| `update_available` | info | Software update ready |
| `update_complete` | info | Update successfully applied |
| `config_error` | warning | Invalid configuration detected |
| `security_alert` | critical | Security event detected |

---

## 🔄 Alert Lifecycle

```
1. SUBMIT ALERT
   ↓
2. SERVER CHECKS FOR DUPLICATE
   ↓
   ├─→ NEW: Creates alert, returns action="created"
   ├─→ EXISTS: Returns existing alert_id, action="exists"
   └─→ RESOLVED: Marks as resolved, action="resolved"
   ↓
3. ALERT VISIBLE ON SERVER DASHBOARD
   ↓
4. RESOLVE ALERT (when condition clears)
   ↓
5. ALERT MARKED RESOLVED
```

---

## 🤖 Automation Examples

### Monitor & Alert on Disk Space

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

---

## 🎯 Context Data Examples

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

---

## 🔍 Debugging

### Check if alert was created
Look for log message:
```
INFO: Alert submitted successfully: id=123, action=created
```

### Check if alert already exists
```
INFO: Alert submitted successfully: id=123, action=exists
```

### Check resolution
```
INFO: Resolved 2 alert(s) of type: low_disk_space
```

### No alerts to resolve
```
INFO: No unresolved alerts found for type: low_disk_space
```

---

## ⚡ Tips & Best Practices

1. **Use consistent type identifiers**
   - Use snake_case: `low_disk_space` not `Low Disk Space`
   - Be specific: `mysql_backup_failed` not `backup_failed`

2. **Include useful context**
   - Add metrics, timestamps, entity IDs
   - Makes debugging and analysis easier

3. **Resolve when conditions clear**
   - Always pair alerts with resolutions
   - Keeps dashboard clean and accurate

4. **Use appropriate severity**
   - Don't overuse `critical`
   - Reserve for truly urgent issues

5. **Test with trigger_alert first**
   - Quick way to verify server connectivity
   - Then build custom automations

6. **Check for deduplication**
   - Server prevents duplicate alerts
   - Safe to submit repeatedly

---

## 📚 More Information

- **Full Documentation:** `ALERT_API_INTEGRATION_COMPLETE.md`
- **Technical Analysis:** `ALERT_INTEGRATION_ANALYSIS.md`
- **Server API Reference:** `ALERT_API_DOCUMENTATION.md`
- **Changelog:** `CHANGELOG.md`




