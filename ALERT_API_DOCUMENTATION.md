# Alert API Documentation

## Overview

The Alert API allows Home Assistant installations to submit alerts to the HA Dispatch server. Alerts can be used to notify about system issues, threshold violations, or any custom conditions.

## Endpoints

All endpoints require authentication using the installation's API token in the Authorization header:
```
Authorization: Bearer {installation_token}
```

### Base URL
```
http://your-server:8080/api/v1/installations/{installation_id}
```

---

## 1. Submit Single Alert

**Endpoint:** `POST /api/v1/installations/{installation_id}/alerts`

### Request Body

```json
{
  "severity": "warning",
  "type": "custom_alert_type",
  "title": "Alert Title",
  "message": "Detailed alert message",
  "context": {
    "custom_field": "value",
    "another_field": 123
  },
  "triggered_at": "2025-11-16T23:30:00Z",
  "resolved": false,
  "resolved_at": null
}
```

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `severity` | string | Yes | Must be one of: `info`, `warning`, `critical` |
| `type` | string | Yes | Alert type identifier (e.g., `low_disk_space`, `high_cpu_load`) |
| `title` | string | Yes | Short alert title (max 255 chars) |
| `message` | string | Yes | Detailed alert message |
| `context` | object | No | Additional context data (any JSON object) |
| `triggered_at` | datetime | No | When alert was triggered (defaults to current time) |
| `resolved` | boolean | No | Whether alert is already resolved |
| `resolved_at` | datetime | No | When alert was resolved |

### Response

**Success (201 Created):**
```json
{
  "status": "ok",
  "alert_id": 123,
  "action": "created"
}
```

**Alert Already Exists (200 OK):**
```json
{
  "status": "ok",
  "alert_id": 123,
  "action": "exists"
}
```

**Alert Resolved (200 OK):**
```json
{
  "status": "ok",
  "alert_id": 123,
  "action": "resolved"
}
```

**Validation Error (422):**
```json
{
  "error": "Validation failed",
  "messages": {
    "severity": ["The severity field is required."]
  },
  "received_fields": ["type", "title", "message"]
}
```

---

## 2. Submit Batch Alerts

**Endpoint:** `POST /api/v1/installations/{installation_id}/alerts/batch`

Submit multiple alerts in a single request (max 100 alerts per batch).

### Request Body

```json
{
  "alerts": [
    {
      "severity": "warning",
      "type": "low_disk_space",
      "title": "Low Disk Space",
      "message": "Disk space is below 10%",
      "context": {"disk_free_percent": 8.5}
    },
    {
      "severity": "critical",
      "type": "high_cpu_load",
      "title": "High CPU Load",
      "message": "CPU load exceeded 90%",
      "context": {"cpu_load_1m": 95.2}
    }
  ]
}
```

### Response

**Success (201 Created):**
```json
{
  "status": "ok",
  "processed_count": 2,
  "results": [
    {"alert_id": 123, "action": "created"},
    {"alert_id": 124, "action": "created"}
  ]
}
```

---

## 3. Resolve Alert by Type

**Endpoint:** `POST /api/v1/installations/{installation_id}/alerts/{type}/resolve`

Resolve all unresolved alerts of a specific type for this installation.

### Example

```bash
POST /api/v1/installations/22/alerts/low_disk_space/resolve
```

### Response

**Success (200 OK):**
```json
{
  "status": "ok",
  "message": "Alerts resolved",
  "resolved_count": 3
}
```

**No Alerts Found (200 OK):**
```json
{
  "status": "ok",
  "message": "No unresolved alerts found",
  "resolved_count": 0
}
```

---

## Alert Deduplication

The system automatically prevents duplicate alerts:
- If an alert with the same `type` and `installation_id` already exists and is **unresolved**, the submission will return the existing alert ID with `action: "exists"`
- To resolve an existing alert, set `resolved: true` or provide `resolved_at` in the request

---

## Common Alert Types

| Type | Severity | Description |
|------|----------|-------------|
| `low_disk_space` | warning | Disk space below threshold |
| `high_cpu_load` | warning | CPU load exceeds threshold |
| `high_memory_usage` | warning | Memory usage exceeds threshold |
| `installation_offline` | critical | Installation hasn't reported in X minutes |
| `service_down` | critical | Required service is not running |
| `update_available` | info | Software update is available |

You can define custom alert types as needed.

---

## Example: cURL Requests

### Submit Single Alert

```bash
curl -X POST http://localhost:8080/api/v1/installations/22/alerts \
  -H "Authorization: Bearer YOUR_INSTALLATION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "severity": "warning",
    "type": "low_disk_space",
    "title": "Low Disk Space",
    "message": "Disk space is below 10%",
    "context": {
      "disk_free_percent": 8.5,
      "disk_free_mb": 5120
    }
  }'
```

### Resolve Alert

```bash
curl -X POST http://localhost:8080/api/v1/installations/22/alerts/low_disk_space/resolve \
  -H "Authorization: Bearer YOUR_INSTALLATION_TOKEN" \
  -H "Content-Type: application/json"
```

---

## Example: Python Client

```python
import requests
from datetime import datetime

class AlertClient:
    def __init__(self, base_url, installation_id, token):
        self.base_url = base_url
        self.installation_id = installation_id
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    
    def submit_alert(self, severity, alert_type, title, message, context=None):
        """Submit a single alert"""
        url = f"{self.base_url}/api/v1/installations/{self.installation_id}/alerts"
        data = {
            "severity": severity,
            "type": alert_type,
            "title": title,
            "message": message,
            "triggered_at": datetime.utcnow().isoformat() + "Z"
        }
        if context:
            data["context"] = context
        
        response = requests.post(url, json=data, headers=self.headers)
        return response.json()
    
    def resolve_alert(self, alert_type):
        """Resolve all alerts of a specific type"""
        url = f"{self.base_url}/api/v1/installations/{self.installation_id}/alerts/{alert_type}/resolve"
        response = requests.post(url, headers=self.headers)
        return response.json()

# Usage
client = AlertClient(
    base_url="http://localhost:8080",
    installation_id=22,
    token="YOUR_TOKEN_HERE"
)

# Submit alert
result = client.submit_alert(
    severity="warning",
    alert_type="low_disk_space",
    title="Low Disk Space",
    message="Disk space is below 10%",
    context={"disk_free_percent": 8.5}
)
print(f"Alert created: {result['alert_id']}")

# Later, resolve the alert
result = client.resolve_alert("low_disk_space")
print(f"Resolved {result['resolved_count']} alerts")
```

---

## Error Handling

### Authentication Errors

**401 Unauthorized:**
- Missing or invalid Authorization header
- Token expired or revoked

**403 Forbidden:**
- Token belongs to different installation

### Validation Errors

**422 Unprocessable Entity:**
- Invalid field values
- Missing required fields
- Invalid data types

### Server Errors

**500 Internal Server Error:**
- Check server logs for details
- Contact system administrator

---

## Monitoring

All alert submissions are logged to `/var/www/html/storage/logs/laravel.log` with:
- Installation ID
- Alert type and severity
- Request data
- Validation errors (if any)

Search logs for alert activity:
```bash
docker compose exec php grep "Alert API called" /var/www/html/storage/logs/laravel.log
```

---

## Best Practices

1. **Use Consistent Alert Types**: Define standard alert types and use them consistently
2. **Meaningful Messages**: Include actionable information in alert messages
3. **Context Data**: Use the `context` field to provide relevant metrics/data
4. **Resolve Alerts**: Always resolve alerts when the condition clears
5. **Batch When Possible**: Use batch endpoint for multiple alerts to reduce API calls
6. **Handle Deduplication**: Check response action to see if alert already exists
7. **Error Handling**: Always check response status and handle errors gracefully

