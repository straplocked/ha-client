<!-- Split from CLIENT_SIDE_DEVELOPMENT_GUIDE.md -->

# Communication Patterns

## Pattern 1: Initial Setup & Registration

```
┌────────┐                                   ┌────────┐
│ Client │                                   │ Server │
└───┬────┘                                   └───┬────┘
    │                                            │
    │ 1. User initiates integration setup       │
    ├─────────────────────────────────────────→│
    │                                            │
    │ 2. Generate client_id (UUID)              │
    │    Collect system info                    │
    │                                            │
    │ 3. POST /v1/installations/register         │
    │    {client_id, hostname, name, ...}       │
    ├──────────────────────────────────────────>│
    │                                            │
    │                                  4. Validate request
    │                                     Generate token
    │                                     Create installation
    │                                     Create default config
    │                                            │
    │ 5. Return registration data                │
    │    {installation_id, access_token, ...}   │
    │<──────────────────────────────────────────┤
    │                                            │
    │ 6. Store credentials securely              │
    │    Start periodic tasks                    │
    │                                            │
```

**Frequency**: Once per installation (unless re-registering)

**Error Handling**:
- Network error: Retry with exponential backoff
- Validation error: Show user-friendly error message
- Duplicate client_id: Use stored credentials or generate new client_id

---

## Pattern 2: Periodic Status & Configuration Check

```
┌────────┐                                   ┌────────┐
│ Client │                                   │ Server │
└───┬────┘                                   └───┬────┘
    │                                            │
    │ ┌──────────────────────────────┐          │
    │ │ Every poll_interval_seconds  │          │
    │ └──────────────────────────────┘          │
    │                                            │
    │ 1. POST /v1/installations/{id}/status      │
    │    Authorization: Bearer {token}          │
    │    {timestamp, ha_version, os_info}       │
    ├──────────────────────────────────────────>│
    │                                            │
    │                                  2. Update last_seen_at
    │                                     Update status
    │                                     Check config version
    │                                            │
    │ 3. Return status                           │
    │    {status: "ok", installation_status,    │
    │     config_version}                        │
    │<──────────────────────────────────────────┤
    │                                            │
    │ 4. Compare config_version                  │
    │    If different, fetch config              │
    │                                            │
    │ 5. GET /v1/installations/{id}/config       │
    │    ?current_version={local_version}       │
    ├──────────────────────────────────────────>│
    │                                            │
    │                                  6. Compare versions
    │                                     Load active config
    │                                            │
    │ 7a. Return config (200) OR                 │
    │ 7b. Return 204 (no change)                 │
    │<──────────────────────────────────────────┤
    │                                            │
    │ 8. Apply configuration if changed          │
    │    Update local config_version             │
    │                                            │
```

**Frequency**: Every 60 seconds (default, configurable)

**Optimization**: Only fetch config when version changes

**Error Handling**:
- Network timeout: Log and retry on next interval
- 401/403: Token invalid, prompt for re-registration
- 429: Respect Retry-After header, increase interval temporarily

---

## Pattern 3: Metrics Collection & Submission

```
┌────────┐                                   ┌────────┐
│ Client │                                   │ Server │
└───┬────┘                                   └───┬────┘
    │                                            │
    │ ┌──────────────────────────────┐          │
    │ │ Every report_interval seconds│          │
    │ └──────────────────────────────┘          │
    │                                            │
    │ 1. Collect system metrics                  │
    │    - CPU load                              │
    │    - Memory usage                          │
    │    - Disk space                            │
    │    - Uptime                                │
    │    - Custom metrics                        │
    │                                            │
    │ 2. POST /v1/installations/{id}/metrics     │
    │    {timestamp, cpu_load_1m, ...}          │
    ├──────────────────────────────────────────>│
    │                                            │
    │                                  3. Store metric
    │                                     Update last_seen_at
    │                                     Check thresholds
    │                                     Generate alerts if needed
    │                                            │
    │ 4. Return confirmation                     │
    │    {status: "ok", metric_id}              │
    │<──────────────────────────────────────────┤
    │                                            │
```

**Frequency**: Every 60 seconds (default, configurable via `report_interval`)

**Offline Buffering**:
```
┌────────┐                                   ┌────────┐
│ Client │                                   │ Server │
└───┬────┘                                   └───┬────┘
    │                                            │
    │ 1. Collect metrics while offline           │
    │    Buffer to local storage                 │
    │    (max 1000 metrics)                      │
    │                                            │
    │ 2. Connection restored                     │
    │                                            │
    │ 3. POST /v1/installations/{id}/metrics/batch
    │    {metrics: [{...}, {...}, ...]}         │
    ├──────────────────────────────────────────>│
    │                                            │
    │                                  4. Store all metrics
    │                                     Process alerts
    │                                            │
    │ 5. Return confirmation                     │
    │    {status: "ok", created_count, ...}     │
    │<──────────────────────────────────────────┤
    │                                            │
    │ 6. Clear local buffer                      │
    │                                            │
```

---

## Pattern 4: Alert Notification (Future via WebSocket)

```
┌────────┐                                   ┌────────┐
│ Client │                                   │ Server │
└───┬────┘                                   └───┬────┘
    │                                            │
    │ 1. Connect to WebSocket                    │
    │    ws://server:6001/app/{app_key}         │
    ├──────────────────────────────────────────>│
    │                                            │
    │ 2. Subscribe to channel                    │
    │    installations.{installation_id}        │
    ├──────────────────────────────────────────>│
    │                                            │
    │                                  3. Alert generated
    │                                     (based on metrics)
    │                                            │
    │ 4. Broadcast alert event                   │
    │    {event: "AlertCreated", data: {...}}   │
    │<──────────────────────────────────────────┤
    │                                            │
    │ 5. Process alert                           │
    │    - Create HA notification                │
    │    - Update entity state                   │
    │    - Trigger automation                    │
    │                                            │
```

**Note**: WebSocket integration is planned but not yet implemented in API. Current implementation should rely on polling or server-side notifications.

---

## Related Documentation

- [API Reference](../api-reference.md) for full endpoint documentation
- [Authentication & Security](authentication.md) for token-based auth details
- [Metrics Collection](metrics-collection.md) for metric collection strategies
- [WebSocket Integration](websocket.md) for WebSocket implementation details
- [Error Handling](error-handling.md) for retry and backoff strategies
