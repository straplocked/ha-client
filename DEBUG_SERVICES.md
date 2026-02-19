# Debug Services - HA Dispatch Client

The HA Dispatch Client includes several debug services to help you test the integration and trigger alerts manually.

## Available Services

### 1. Send Test Metrics

**Service**: `ha_dispatch_client.send_test_metrics`

Send test metrics with custom values to verify server connectivity and metric submission.

#### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `cpu_load` | float | 75.0 | CPU load percentage (0-100) |
| `memory_used` | float | 85.0 | Memory usage percentage (0-100) |
| `disk_free` | float | 15.0 | Disk free percentage (0-100) |

#### Usage Example

**Via Developer Tools → Services:**

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

Send extreme metric values that will trigger alerts on the server for testing the alert system.

#### Parameters

| Parameter | Type | Required | Options | Description |
|-----------|------|----------|---------|-------------|
| `alert_type` | select | Yes | disk_low, cpu_high, memory_high, all | Type of alert to trigger |

#### Alert Types

- **`disk_low`**: Sends 5% disk free (triggers low disk alert)
- **`cpu_high`**: Sends 850% CPU load (triggers high CPU alert)
- **`memory_high`**: Sends 95% memory usage (triggers high memory alert)
- **`all`**: Sends all extreme values at once

#### Usage Example

**Via Developer Tools → Services:**

```yaml
service: ha_dispatch_client.trigger_alert
data:
  alert_type: disk_low
```

**Via Script:**

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

None

#### Usage Example

**Via Developer Tools → Services:**

```yaml
service: ha_dispatch_client.force_update
data: {}
```

**Via Automation:**

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

Send completely custom metric values for advanced testing scenarios.

#### Parameters

| Parameter | Type | Optional | Range | Description |
|-----------|------|----------|-------|-------------|
| `cpu_load_1m` | float | Yes | 0+ | 1-minute CPU load average |
| `memory_used_percent` | float | Yes | 0-100 | Memory usage percentage |
| `disk_free_percent` | float | Yes | 0-100 | Disk free percentage |
| `warnings` | string | Yes | - | Comma-separated warning tags |

#### Usage Example

**Via Developer Tools → Services:**

```yaml
service: ha_dispatch_client.send_custom_metric
data:
  cpu_load_1m: 2.5
  memory_used_percent: 67.8
  disk_free_percent: 35.5
  warnings: "low_disk,high_cpu"
```

**Advanced Test Scenarios:**

```yaml
# Test extreme edge cases
service: ha_dispatch_client.send_custom_metric
data:
  cpu_load_1m: 99.99
  memory_used_percent: 99.9
  disk_free_percent: 0.1
  warnings: "critical,low_disk,high_cpu,high_memory"
```

---

## Testing Workflow

### Step 1: Verify Basic Connectivity

```yaml
service: ha_dispatch_client.send_test_metrics
data:
  cpu_load: 50
  memory_used: 60
  disk_free: 40
```

**Expected Result:**
- Check logs: "Test metrics sent successfully"
- Check server admin panel: New metric appears
- No alerts triggered (all values normal)

---

### Step 2: Test Alert System

```yaml
# Test low disk alert
service: ha_dispatch_client.trigger_alert
data:
  alert_type: disk_low
```

**Expected Result:**
- Check logs: "Alert-triggering metrics sent! Check server for alerts."
- Check server admin panel → Alerts: New "Low Disk Space" alert appears
- Alert severity: Warning or Critical
- Installation status changes to "warning"

---

### Step 3: Test All Alert Types

```yaml
service: ha_dispatch_client.trigger_alert
data:
  alert_type: all
```

**Expected Result:**
- Multiple alerts generated on server
- Installation marked as "warning"
- Alert notifications sent (if configured)

---

### Step 4: Force Immediate Update

```yaml
service: ha_dispatch_client.force_update
data: {}
```

**Expected Result:**
- Coordinator runs immediately (doesn't wait 60 seconds)
- Status reported to server
- Real metrics collected and sent
- Check logs: "Coordinator update completed"

---

### Step 5: Custom Scenario Testing

```yaml
service: ha_dispatch_client.send_custom_metric
data:
  cpu_load_1m: 5.0
  memory_used_percent: 80.0
  disk_free_percent: 12.0
  warnings: "custom_warning"
```

**Expected Result:**
- Custom metrics appear in server
- Warnings array includes "custom_warning"
- Can test specific threshold values

---

## How to Access Services

### Method 1: Developer Tools (Web UI)

1. Go to: **Developer Tools** → **Services**
2. Search for: `ha_dispatch_client`
3. Select a service from dropdown
4. Fill in parameters
5. Click **Call Service**

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

Call services via Home Assistant REST API:

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

---

## Verification Steps

After calling any service:

### 1. Check Home Assistant Logs

```bash
# View logs
Settings → System → Logs → Search for "ha_dispatch"

# Or via SSH
ssh straplocked@homeassistant.local
tail -f /config/home-assistant.log | grep ha_dispatch
```

Look for:
- "Sending test metrics: CPU=X%, Memory=Y%, Disk Free=Z%"
- "Test metrics sent successfully"
- "Triggering LOW DISK alert"
- "Alert-triggering metrics sent! Check server for alerts."

### 2. Check Server Admin Panel

**Metrics:**
1. Open: `http://your-server:8080/admin`
2. Go to: **Monitoring** → **Installations** → Your Installation
3. Click: **Metrics** tab
4. Verify: New metric appears with your test values

**Alerts:**
1. Go to: **Monitoring** → **Installation Alerts**
2. Filter by: Your installation
3. Verify: New alerts appear for threshold violations

### 3. Check Server Logs

```bash
# If using Docker
cd /path/to/ha-dispatch
docker compose logs -f --tail=50 | grep -i metric

# Look for
# "Received metric submission for installation X"
# "Alert generated: Low Disk Space"
```

---

## Common Use Cases

### Use Case 1: Test New Server Installation

```yaml
# Quick connectivity test
service: ha_dispatch_client.send_test_metrics
data: {}  # Use defaults
```

### Use Case 2: Verify Alert Notifications

```yaml
# Trigger alerts to test email/SMS notifications
service: ha_dispatch_client.trigger_alert
data:
  alert_type: all
```

### Use Case 3: Demo to Stakeholders

```yaml
# Show dashboard updates in real-time
service: ha_dispatch_client.send_test_metrics
data:
  cpu_load: 95  # High but not alerting
  memory_used: 88
  disk_free: 25
```

### Use Case 4: Threshold Testing

```yaml
# Find exact alert threshold
service: ha_dispatch_client.send_custom_metric
data:
  disk_free_percent: 10.5  # Just above threshold
  
# Then try
service: ha_dispatch_client.send_custom_metric
data:
  disk_free_percent: 9.5   # Just below threshold
```

---

## Troubleshooting

### Services Don't Appear

**Problem**: Services not visible in Developer Tools

**Solution:**
1. Verify integration is loaded: Settings → Devices & Services
2. Restart Home Assistant
3. Check logs for service registration: "HA Dispatch debug services registered"

### Service Call Fails

**Problem**: "Failed to call service" error

**Solution:**
1. Check integration is active
2. Verify server URL is correct and accessible
3. Check access token is valid
4. View Home Assistant logs for details

### Metrics Not Appearing on Server

**Problem**: Service succeeds but no data on server

**Solution:**
1. Check server is running
2. Verify server API endpoints are accessible
3. Check server logs for validation errors
4. Verify access token matches installation

### Alerts Not Triggering

**Problem**: Extreme metrics sent but no alerts

**Solution:**
1. Check server alert settings: Settings → System → Settings → Alerts
2. Verify thresholds are configured
3. Check installation is active on server
4. View server logs: `docker compose logs | grep Alert`

---

## Best Practices

1. **Test in Development First**: Use test/staging server before production
2. **Document Threshold Values**: Note what values trigger alerts
3. **Clean Up Test Data**: Delete test metrics/alerts after testing
4. **Monitor Server Load**: Don't spam services rapidly
5. **Use Descriptive Warnings**: Custom warnings help identify test data

---

## Examples Collection

### Quick Health Check

```yaml
script:
  ha_dispatch_health_check:
    sequence:
      - service: ha_dispatch_client.force_update
      - delay: 2
      - service: ha_dispatch_client.send_test_metrics
        data:
          cpu_load: 25
          memory_used: 50
          disk_free: 60
```

### Alert System Validation

```yaml
script:
  validate_alert_system:
    sequence:
      # Test each alert type
      - service: ha_dispatch_client.trigger_alert
        data:
          alert_type: disk_low
      - delay: 10
      - service: ha_dispatch_client.trigger_alert
        data:
          alert_type: cpu_high
      - delay: 10
      - service: ha_dispatch_client.trigger_alert
        data:
          alert_type: memory_high
      - delay: 10
      # Send normal metrics to clear
      - service: ha_dispatch_client.send_test_metrics
        data:
          cpu_load: 30
          memory_used: 50
          disk_free: 50
```

### Automated Testing Automation

```yaml
automation:
  - alias: "Nightly HA Dispatch Test"
    trigger:
      - platform: time
        at: "03:00:00"
    action:
      - service: ha_dispatch_client.force_update
      - delay: 5
      - service: ha_dispatch_client.send_test_metrics
        data:
          cpu_load: 40
          memory_used: 55
          disk_free: 45
```

---

## Summary

✅ **4 debug services** available for testing  
✅ **Easy to use** via Developer Tools UI  
✅ **Flexible testing** with custom values  
✅ **Alert triggering** for validation  
✅ **Force updates** for immediate feedback  

Use these services to verify your HA Dispatch setup and troubleshoot issues!

