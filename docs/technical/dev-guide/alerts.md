<!-- Split from CLIENT_SIDE_DEVELOPMENT_GUIDE.md -->

# Alert Handling

## Server-Side Alert Generation

Alerts are generated server-side based on:
- Metric threshold violations
- Missing metrics (installation offline)
- Configuration errors
- Custom alert rules (future)

## Alert Types

### Installation Offline
- **Trigger**: No status report for 5+ minutes (configurable)
- **Severity**: warning -> critical (after 15 minutes)
- **Resolution**: Automatic when status received

### Disk Space Low
- **Trigger**: `disk_free_percent` < threshold
- **Severity**: warning (< 10%) -> critical (< 5%)
- **Resolution**: Manual or automatic when above threshold

### Memory High
- **Trigger**: `memory_used_percent` > threshold
- **Severity**: warning (> 90%) -> critical (> 95%)
- **Resolution**: Automatic when below threshold

### CPU High
- **Trigger**: `cpu_load_1m` > threshold
- **Severity**: warning (> 80%) -> critical (> 95%)
- **Resolution**: Automatic when below threshold

## Client Alert Handling (Future)

Future API endpoint for fetching alerts:
```
GET /v1/installations/{installation_id}/alerts
```

**Recommended Client Implementation**:
1. Poll alerts endpoint periodically (every 5 minutes)
2. Filter for unresolved alerts
3. Create Home Assistant persistent notifications
4. Expose alert count as sensor entity
5. Provide service to acknowledge alerts
6. Trigger automations based on alert severity

---

## Related Documentation

- [API Reference](../api-reference.md) for alert submission and resolution endpoints
- [Metrics Collection](metrics-collection.md) for the metrics that trigger alerts
- [Configuration Management](configuration.md) for threshold parameters
- [WebSocket Integration](websocket.md) for real-time alert notifications (future)
- See [Data Models](../data-model/) for the Alert model schema, severity levels, and lifecycle
