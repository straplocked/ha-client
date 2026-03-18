# Alert API Integration - Complete

> **Note:** This is a historical development document. For current documentation, see [docs/INDEX.md](../INDEX.md).

**Version:** 1.2.0
**Date:** November 17, 2025

## Summary

Successfully integrated the HA Dispatch server's Alert API into the client. The client now properly submits structured alerts using the dedicated Alert API endpoints instead of relying on metrics with warnings.

---

## What Changed

### New API Methods (`api_client.py`)

Added three new methods to communicate with the Alert API:

1. **`submit_alert()`** - Submit a single structured alert
   - Parameters: severity, type, title, message, context, resolved
   - Returns: alert_id and action (created/exists/resolved)

2. **`submit_alerts_batch()`** - Submit up to 100 alerts in one request
   - Parameters: list of alert dictionaries
   - Returns: processed_count and results list

3. **`resolve_alert()`** - Resolve all alerts of a specific type
   - Parameters: alert_type
   - Returns: resolved_count

### New Services (`__init__.py`)

Added two new Home Assistant services:

1. **`ha_dispatch_client.submit_alert`**
   - Full control over alert submission
   - Fields: severity, type, title, message, context, resolved
   - Use for custom alerts with specific data

2. **`ha_dispatch_client.resolve_alert`**
   - Resolve all alerts of a specific type
   - Field: type
   - Use to clear alerts when conditions resolve

### Updated Existing Service

**`ha_dispatch_client.trigger_alert`** - Now uses Alert API directly
- Previously: Sent metrics with warnings array
- Now: Submits proper structured alerts
- Supports: disk_low, cpu_high, memory_high, all
- Uses batch submission for "all" option

### Updated Documentation

- `services.yaml` - Added definitions for new services
- `strings.json` - Added UI strings for new services
- `CHANGELOG.md` - Documented changes for v1.2.0
- `VERSION` -> 1.2.0
- `manifest.json` -> 1.2.0

---

## How to Test

### 1. Deploy the Update

```bash
cd /home/straplocked/Documents/ha-client
./deploy.sh
```

This will:
- Deploy version 1.2.0 to your Home Assistant server
- Create a backup of the previous version
- Prompt you to restart Home Assistant

### 2. Test Quick Alert Trigger

Use the existing service with the new Alert API backend:

```yaml
service: ha_dispatch_client.trigger_alert
data:
  alert_type: disk_low
```

**Expected Result:**
- Log message: "Alert triggered! ID=123, Action=created. Check server."
- Server should show new alert in dashboard

### 3. Test Custom Alert Submission

Submit a fully custom alert:

```yaml
service: ha_dispatch_client.submit_alert
data:
  severity: warning
  type: custom_test_alert
  title: Test Alert from HA Client
  message: This is a test alert with custom data
  context:
    test_field: "test_value"
    numeric_value: 42
  resolved: false
```

**Expected Result:**
- Alert appears on server
- Context data is visible
- If submitted again, returns `action: "exists"` (deduplication)

### 4. Test Alert Resolution

Resolve all alerts of a specific type:

```yaml
service: ha_dispatch_client.resolve_alert
data:
  type: custom_test_alert
```

**Expected Result:**
- Log message: "Resolved N alert(s) of type: custom_test_alert"
- Alerts marked as resolved on server

### 5. Test Batch Alert Submission

Trigger all alert types at once:

```yaml
service: ha_dispatch_client.trigger_alert
data:
  alert_type: all
```

**Expected Result:**
- Log message: "All alerts triggered! Processed 3 alerts. Check server."
- Three alerts appear on server: low_disk_space, high_cpu_load, high_memory_usage

---

## API Endpoints Used

The client now properly uses these server endpoints:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/installations/{id}/alerts` | POST | Submit single alert |
| `/api/v1/installations/{id}/alerts/batch` | POST | Submit batch alerts |
| `/api/v1/installations/{id}/alerts/{type}/resolve` | POST | Resolve alerts by type |

All endpoints use Bearer token authentication from the installation's access token.

---

## Alert Structure

Alerts now follow the proper structure expected by the server:

```json
{
  "severity": "warning",           // info|warning|critical
  "type": "low_disk_space",        // Alert type identifier
  "title": "Low Disk Space",       // Short title (max 255 chars)
  "message": "Disk space is...",   // Detailed message
  "context": {                     // Optional JSON context
    "disk_free_percent": 5.0,
    "disk_free_mb": 5120
  },
  "triggered_at": "2025-11-17...", // ISO timestamp
  "resolved": false                // Resolution status
}
```

---

## Service Comparison

| Service | Purpose | When to Use |
|---------|---------|-------------|
| `trigger_alert` | Quick predefined alerts | Testing, demos, quick checks |
| `submit_alert` | Full custom alerts | Automations, custom monitoring |
| `resolve_alert` | Resolve by type | Clear alerts when issue fixed |
| `send_test_metrics` | Legacy metrics | Basic connectivity testing |

---

## Server-Side Deduplication

The server automatically prevents duplicate alerts:

1. **First submission** -> `action: "created"`, new alert created
2. **Same alert (same type + installation)** -> `action: "exists"`, returns existing alert_id
3. **Submit with resolved=true** -> `action: "resolved"`, marks alert as resolved

This means you can safely submit alerts repeatedly without creating duplicates!

---

## Logging

All alert operations are logged for debugging:

**Client logs** (Home Assistant):
```
INFO: Submitting alert: type=low_disk_space, severity=warning, title=Low Disk Space
INFO: Alert submitted: id=123, action=created
```

**Server logs** (HA Dispatch):
```bash
docker compose exec php grep "Alert API called" /var/www/html/storage/logs/laravel.log
```

---

## Migration Notes

### Breaking Changes
**None!** Existing services continue to work, they just use the new Alert API backend.

### Backward Compatibility
- `trigger_alert` service still works with same parameters
- `send_test_metrics` and other services unchanged
- All existing automations continue to function

### New Capabilities
- Can now resolve alerts programmatically
- Can submit alerts with structured context data
- Server prevents duplicate alerts automatically
- Get feedback on alert actions (created/exists/resolved)

---

## Example Automation

Use alerts in your Home Assistant automations:

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

## Next Steps

### Now: Deploy & Test
1. Run `./deploy.sh` to deploy v1.2.0
2. Restart Home Assistant
3. Test the new services from Developer Tools
4. Verify alerts appear on server dashboard

### Future Enhancements (Optional)
- **Automatic monitoring**: Have coordinator auto-submit/resolve alerts based on metrics
- **Threshold configuration**: Define alert thresholds in config
- **Alert templates**: Predefined alert types in configuration
- **Notification integration**: Tie alerts to HA notifications

---

## Files Modified

```
custom_components/ha_dispatch_client/
  ├── api_client.py         <- Added 3 new methods (114 lines)
  ├── __init__.py           <- Added 2 services, updated trigger_alert (56 lines)
  ├── services.yaml         <- Added service definitions (66 lines)
  ├── strings.json          <- Added UI strings (40 lines)
  └── manifest.json         <- Version bump to 1.2.0

VERSION                     <- Updated to 1.2.0
CHANGELOG.md                <- Documented changes
ALERT_INTEGRATION_ANALYSIS.md        <- Analysis document
ALERT_API_INTEGRATION_COMPLETE.md    <- This file
```

---

## Verification Checklist

After deployment, verify:

- [ ] Services appear in Developer Tools > Services
  - [ ] `ha_dispatch_client.submit_alert` visible
  - [ ] `ha_dispatch_client.resolve_alert` visible
  - [ ] `ha_dispatch_client.trigger_alert` still works

- [ ] Alert submission works
  - [ ] Submit single alert -> creates alert on server
  - [ ] Submit same alert -> returns "exists"
  - [ ] Check server dashboard shows alert

- [ ] Alert resolution works
  - [ ] Resolve by type -> alerts marked resolved on server
  - [ ] Resolve non-existent type -> returns 0 resolved

- [ ] Batch submission works
  - [ ] `trigger_alert` with `alert_type: all` -> creates 3 alerts

- [ ] Logging is clear
  - [ ] Client logs show alert IDs and actions
  - [ ] Server logs show alert submissions

---

## Support

If you encounter issues:

1. **Check Home Assistant logs**
   ```
   Settings > System > Logs
   Search for "ha_dispatch_client"
   ```

2. **Check server logs**
   ```bash
   docker compose exec php tail -f /var/www/html/storage/logs/laravel.log
   ```

3. **Verify server API is accessible**
   ```bash
   curl -H "Authorization: Bearer YOUR_TOKEN" \
     http://your-server:8080/api/v1/installations/YOUR_ID/alerts
   ```

4. **Review the analysis document**
   - See [alert-integration-analysis.md](alert-integration-analysis.md) for detailed technical info
   - See [Alert API Reference](../technical/alert-api.md) for server API reference
