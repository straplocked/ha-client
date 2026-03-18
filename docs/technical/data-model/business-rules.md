<!-- Split from DATA_MODEL.md -->

# Business Rules, Enumerations & Client Checklist

> See also: [Database Schema](schema.md) | [Data Models & Relationships](relationships.md) | [API Endpoints & Data Flow](api-endpoints.md) | [JSON Examples](json-examples.md)
>
> Back to [Data Model Overview](README.md)

---

## Business Rules

### Installation Registration

1. `client_id` must be a valid UUID
2. `client_id` must be unique across all installations
3. `access_token` is auto-generated (64 random characters)
4. `access_token` must be unique across all installations
5. Default `config_version` is 0 (incremented to 1 after first config)
6. Default `poll_interval_seconds` is 60
7. Default `status` is 'offline'
8. Default `is_active` is true

### Status Management

1. Installation is **online** if `last_seen_at` is within offline threshold (default: 5 minutes)
2. Installation is **offline** if no communication for > 5 minutes
3. Installation is **warning** if online but has unresolved alerts
4. `status` is automatically updated by server background jobs
5. `last_seen_at` is updated on status report OR metric submission

### Configuration Management

1. Only one configuration per installation can have `is_active = true`
2. `version` field is unique per installation (auto-incremented)
3. Client polls configuration when `config_version` changes
4. Server returns 204 if client's `current_version` matches server
5. `applied_at` is reserved for future client confirmation feature

### Metric Submission

1. Only `timestamp` is required; all other fields are optional
2. `timestamp` can be historical (for offline buffering)
3. Metrics update `installation.last_seen_at` timestamp
4. Metrics older than retention period (30 days) are auto-deleted
5. Batch submissions limited to 100 metrics per request
6. Server validates metric value ranges (e.g., percentages 0-100)

### Alert Generation

1. Alerts are generated server-side based on metric thresholds
2. Alert severity can escalate (warning -> critical)
3. Alerts auto-resolve when condition clears (optional)
4. Unresolved alerts affect installation `status`
5. Alerts older than retention period (90 days) are auto-deleted
6. Only unresolved critical/warning alerts cause 'warning' status

### Authentication & Security

1. `access_token` is stored hashed in database
2. Token is sent as Bearer token in Authorization header
3. Invalid token returns 401 Unauthorized
4. Inactive installation (`is_active = false`) returns 403 Forbidden
5. Token does not expire (long-lived)
6. Re-registration with same `client_id` is prevented

### Data Retention

Default retention periods (configurable via admin panel):

- **Metrics**: 30 days
- **Resolved Alerts**: 90 days
- **Soft Deletes**: 30 days (then permanent deletion)
- **Configurations**: Never deleted (audit trail)

### Rate Limiting

- Default: 60 requests per minute per installation
- Returns 429 Too Many Requests when exceeded
- Includes Retry-After header in response

---

## Enumerations & Constants

### Status Enum

```typescript
type InstallationStatus = 'online' | 'offline' | 'warning';
```

| Value | Description |
|-------|-------------|
| `online` | Recently communicated, no critical issues |
| `offline` | No communication for threshold period |
| `warning` | Online but has unresolved alerts |

---

### Severity Enum

```typescript
type AlertSeverity = 'info' | 'warning' | 'critical';
```

| Value | Description |
|-------|-------------|
| `info` | Informational, no action required |
| `warning` | Attention needed, system functional |
| `critical` | Immediate action required, system impaired |

---

### Alert Type Constants

Common server-generated alert types:

```typescript
const ALERT_TYPES = {
  INSTALLATION_OFFLINE: 'installation_offline',
  DISK_SPACE_LOW: 'disk_space_low',
  MEMORY_HIGH: 'memory_high',
  CPU_HIGH: 'cpu_high',
  METRIC_MISSING: 'metric_missing',
  CUSTOM_ALERT: 'custom_alert'
};
```

---

### Warning Constants

Common warning identifiers in metrics:

```typescript
const WARNING_TYPES = {
  LOW_DISK: 'low_disk',
  HIGH_CPU: 'high_cpu',
  HIGH_MEMORY: 'high_memory',
  OFFLINE: 'offline',
  CUSTOM_WARNING: 'custom_warning'
};
```

---

### Default Values

```typescript
const DEFAULTS = {
  POLL_INTERVAL_SECONDS: 60,
  REPORT_INTERVAL_SECONDS: 60,
  OFFLINE_THRESHOLD_MINUTES: 5,
  METRICS_RETENTION_DAYS: 30,
  ALERTS_RETENTION_DAYS: 90,
  SOFT_DELETE_RETENTION_DAYS: 30,

  THRESHOLDS: {
    CPU_LOAD_WARN: 80,
    CPU_LOAD_CRITICAL: 95,
    MEMORY_USED_PERCENT_WARN: 90,
    MEMORY_USED_PERCENT_CRITICAL: 95,
    DISK_FREE_PERCENT_WARN: 10,
    DISK_FREE_PERCENT_CRITICAL: 5
  }
};
```

---

### HTTP Status Codes

| Code | Meaning | When Used |
|------|---------|-----------|
| 200 | OK | Successful request |
| 201 | Created | Resource created (registration, metric) |
| 204 | No Content | No config update available |
| 401 | Unauthorized | Invalid/missing token |
| 403 | Forbidden | Token valid but access denied (inactive) |
| 422 | Unprocessable Entity | Validation failed |
| 429 | Too Many Requests | Rate limit exceeded |
| 500 | Internal Server Error | Server error |
| 503 | Service Unavailable | Server temporarily unavailable |

---

## Client Implementation Checklist

### Required Features

- [ ] Generate and store unique `client_id` (UUID)
- [ ] Registration with server
- [ ] Secure storage of `access_token`
- [ ] Periodic status reporting (every `poll_interval_seconds`)
- [ ] Configuration polling and application
- [ ] System metrics collection
- [ ] Metrics submission (single and batch)
- [ ] Bearer token authentication
- [ ] Error handling and retry logic
- [ ] Offline metric buffering

### Optional Features

- [ ] WebSocket connection for real-time updates
- [ ] Alert polling and notification
- [ ] Custom metrics collection
- [ ] Configuration validation
- [ ] Metric visualization
- [ ] Integration with Home Assistant entities

### Data Storage Requirements

Client must persistently store:

- `client_id` - UUID generated during first run
- `installation_id` - Received from registration
- `access_token` - Received from registration
- `config_version` - To detect configuration changes
- `poll_interval_seconds` - How often to poll
- Buffered metrics (when offline)

---

## Summary

This data model provides a complete foundation for building a client-side application that integrates with HA Dispatch. Key takeaways:

1. **Four Core Tables**: installations, configurations, metrics, alerts
2. **Simple Authentication**: Token-based (Bearer)
3. **Versioned Configuration**: Detect changes via `config_version`
4. **Flexible Metrics**: All fields optional except timestamp
5. **Server-Side Alerts**: Generated based on threshold violations
6. **Soft Deletes**: Support data recovery
7. **JSON Flexibility**: `metadata`, `desired_state`, `additional_metrics`, `context`

For complete API examples, client implementation templates, and integration guides, see:
- [Dev Guide](../dev-guide/README.md)
- [API Reference](../api-reference.md)

---

**Document Version:** 1.0
**Last Updated:** November 16, 2025
**For Questions:** Refer to existing codebase in `/src/app/Models/` and `/src/app/Http/Controllers/Api/`
