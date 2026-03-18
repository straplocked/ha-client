# Alert Integration Analysis

> **Note:** This is a historical development document. For current documentation, see [docs/INDEX.md](../INDEX.md).

## Summary

**The client needs significant updates to properly use the server's Alert API.**

Currently, the client attempts to trigger alerts by sending metrics with "warnings" arrays, expecting the server to create alerts based on those metrics. However, the server has a **dedicated Alert API** that expects structured alert submissions.

---

## Current Implementation Problems

### 1. **No Direct Alert Submission**
The `api_client.py` only has methods for:
- `submit_metrics()` - sends metrics data
- `submit_metrics_batch()` - sends batch metrics

**Missing**: Methods to submit alerts directly to the Alert API

### 2. **Indirect Alert Triggering**
The `trigger_alert` service currently works by:
```python
# Sends metrics with warnings array
metrics = {
    "cpu_load_1m": 8.5,
    "memory_used_percent": 45.0,
    "disk_free_percent": 50.0,
    "warnings": ["high_cpu"],  # <- Relies on server to create alert
}
```

**Problem**: This assumes the server converts metrics with warnings into alerts, which may not be the intended design.

### 3. **No Alert Resolution**
There's no way for the client to:
- Resolve an existing alert
- Check if an alert already exists
- Update an alert's status

---

## What the Server Alert API Expects

According to `ALERT_API_DOCUMENTATION.md`, the server provides:

### Endpoint 1: Submit Single Alert
**POST** `/api/v1/installations/{installation_id}/alerts`

**Required fields:**
```json
{
  "severity": "warning|info|critical",
  "type": "low_disk_space",
  "title": "Low Disk Space",
  "message": "Disk space is below 10%",
  "context": {
    "disk_free_percent": 8.5,
    "disk_free_mb": 5120
  },
  "triggered_at": "2025-11-16T23:30:00Z",
  "resolved": false
}
```

### Endpoint 2: Submit Batch Alerts
**POST** `/api/v1/installations/{installation_id}/alerts/batch`

```json
{
  "alerts": [
    { /* alert 1 */ },
    { /* alert 2 */ }
  ]
}
```

### Endpoint 3: Resolve Alert by Type
**POST** `/api/v1/installations/{installation_id}/alerts/{type}/resolve`

---

## Required Changes

### 1. **Add Alert Methods to `api_client.py`**

Add these new methods:

```python
async def submit_alert(
    self,
    installation_id: str,
    severity: str,
    alert_type: str,
    title: str,
    message: str,
    context: Optional[Dict[str, Any]] = None,
    resolved: bool = False,
) -> Dict[str, Any]:
    """Submit a single alert to server."""
    url = f"{self.server_url}/api/v1/installations/{installation_id}/alerts"
    data = {
        "severity": severity,
        "type": alert_type,
        "title": title,
        "message": message,
        "triggered_at": datetime.now(timezone.utc).isoformat(),
        "resolved": resolved,
    }
    if context:
        data["context"] = context

    async with self.session.post(url, json=data, headers=self._get_headers()) as response:
        response.raise_for_status()
        return await response.json()

async def submit_alerts_batch(
    self,
    installation_id: str,
    alerts: list,
) -> Dict[str, Any]:
    """Submit batch of alerts to server."""
    url = f"{self.server_url}/api/v1/installations/{installation_id}/alerts/batch"
    data = {"alerts": alerts}

    async with self.session.post(url, json=data, headers=self._get_headers()) as response:
        response.raise_for_status()
        return await response.json()

async def resolve_alert(
    self,
    installation_id: str,
    alert_type: str,
) -> Dict[str, Any]:
    """Resolve all alerts of a specific type."""
    url = f"{self.server_url}/api/v1/installations/{installation_id}/alerts/{alert_type}/resolve"

    async with self.session.post(url, headers=self._get_headers()) as response:
        response.raise_for_status()
        return await response.json()
```

### 2. **Add New Services to `__init__.py`**

Add service schemas:

```python
SERVICE_SUBMIT_ALERT_SCHEMA = vol.Schema({
    vol.Required("severity"): vol.In(["info", "warning", "critical"]),
    vol.Required("type"): cv.string,
    vol.Required("title"): cv.string,
    vol.Required("message"): cv.string,
    vol.Optional("context"): dict,
    vol.Optional("resolved", default=False): cv.boolean,
})

SERVICE_RESOLVE_ALERT_SCHEMA = vol.Schema({
    vol.Required("type"): cv.string,
})
```

Add service handlers:

```python
async def handle_submit_alert(call: ServiceCall):
    """Handle submit_alert service call."""
    coordinator = get_coordinator_for_service(hass)
    if not coordinator:
        return

    result = await coordinator.api_client.submit_alert(
        coordinator.installation_id,
        severity=call.data["severity"],
        alert_type=call.data["type"],
        title=call.data["title"],
        message=call.data["message"],
        context=call.data.get("context"),
        resolved=call.data.get("resolved", False),
    )
    _LOGGER.info("Alert submitted: %s (action: %s)", result.get("alert_id"), result.get("action"))

async def handle_resolve_alert(call: ServiceCall):
    """Handle resolve_alert service call."""
    coordinator = get_coordinator_for_service(hass)
    if not coordinator:
        return

    result = await coordinator.api_client.resolve_alert(
        coordinator.installation_id,
        alert_type=call.data["type"],
    )
    _LOGGER.info("Resolved %s alerts of type: %s", result.get("resolved_count"), call.data["type"])
```

### 3. **Update `services.yaml`**

Add:

```yaml
submit_alert:
  name: Submit Alert
  description: Submit a structured alert to the server
  fields:
    severity:
      name: Severity
      description: Alert severity level
      required: true
      selector:
        select:
          options:
            - label: Info
              value: info
            - label: Warning
              value: warning
            - label: Critical
              value: critical
    type:
      name: Type
      description: Alert type identifier (e.g., low_disk_space, high_cpu_load)
      required: true
      selector:
        text:
    title:
      name: Title
      description: Short alert title
      required: true
      selector:
        text:
    message:
      name: Message
      description: Detailed alert message
      required: true
      selector:
        text:
          multiline: true
    resolved:
      name: Resolved
      description: Mark alert as resolved
      default: false
      selector:
        boolean:

resolve_alert:
  name: Resolve Alert
  description: Resolve all alerts of a specific type
  fields:
    type:
      name: Type
      description: Alert type to resolve
      required: true
      selector:
        text:
```

### 4. **Update `strings.json`**

Add service descriptions for the new services.

### 5. **Decision: Keep or Update `trigger_alert`?**

**Option A: Keep it as-is** for backward compatibility
- It sends metrics with warnings
- Useful for testing the metric-based alert system (if server supports it)

**Option B: Update it to use the new Alert API**
- Make it submit proper structured alerts
- More explicit and direct
- Aligns with server's designed API

**Recommendation**: Keep both approaches:
- `trigger_alert` - Quick test alerts (updated to use Alert API)
- `submit_alert` - Full control over alert fields

---

## Migration Path

### Phase 1: Add Alert API Support
1. Add alert methods to `api_client.py`
2. Add `submit_alert` and `resolve_alert` services
3. Test new services

### Phase 2: Update Existing Services (Optional)
1. Update `trigger_alert` to use Alert API instead of metrics
2. Add predefined alerts for common scenarios

### Phase 3: Automatic Alert Generation (Future)
1. Monitor system metrics in coordinator
2. Automatically submit/resolve alerts based on thresholds
3. Example: Auto-submit "low_disk_space" when disk < 10%

---

## Testing Plan

### 1. Test Single Alert Submission
```bash
# From Home Assistant Developer Tools > Services
service: ha_dispatch_client.submit_alert
data:
  severity: warning
  type: low_disk_space
  title: Low Disk Space
  message: Disk space is below 10%
```

### 2. Test Alert Resolution
```bash
service: ha_dispatch_client.resolve_alert
data:
  type: low_disk_space
```

### 3. Test Deduplication
- Submit same alert twice
- Verify server returns "exists" action

### 4. Test with Context
```bash
service: ha_dispatch_client.submit_alert
data:
  severity: critical
  type: high_cpu_load
  title: High CPU Load
  message: CPU load exceeded 90%
  context:
    cpu_load_1m: 95.2
    cpu_load_5m: 92.1
```

---

## Benefits of This Approach

1. **Direct API Usage**: Uses the server's designed Alert API correctly
2. **Better Structure**: Alerts have proper severity, title, message, context
3. **Deduplication**: Server handles preventing duplicate alerts
4. **Resolution**: Can explicitly resolve alerts when condition clears
5. **Flexibility**: Can submit any custom alert type with any context
6. **Logging**: Server logs all alert activity for monitoring

---

## Questions to Resolve

1. **Should we keep the metrics-with-warnings approach?**
   - Does the server also create alerts from metrics with warnings?
   - Or is the Alert API the only way to create alerts?

2. **Should `trigger_alert` be updated or deprecated?**
   - Update to use Alert API for consistency
   - Or keep for backward compatibility

3. **Should we add automatic alert monitoring?**
   - Have coordinator automatically submit alerts based on metrics
   - Or leave it to user automation/scripts

---

## Recommendation

**Implement Phase 1 immediately:**
- Add complete Alert API support to `api_client.py`
- Add `submit_alert` and `resolve_alert` services
- Document the new services

**This provides:**
- Full access to server's Alert API
- Explicit alert management
- Foundation for future automation

**Keep `trigger_alert` service** but update its description to clarify it's for quick testing, while `submit_alert` provides full control.
