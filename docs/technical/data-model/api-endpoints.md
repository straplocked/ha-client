<!-- Split from DATA_MODEL.md -->

# API Endpoints & Data Flow

> See also: [Database Schema](schema.md) | [Data Models & Relationships](relationships.md) | [JSON Examples](json-examples.md) | [Business Rules & Constants](business-rules.md)
>
> Back to [Data Model Overview](README.md)

---

## API Endpoints

### Base URL

```
http(s)://server:8080/api
```

### Authentication

All endpoints except registration require Bearer token authentication:

```http
Authorization: Bearer {access_token}
```

---

### 1. Register Installation

**Endpoint:** `POST /v1/installations/register`
**Authentication:** None (public endpoint)

#### Request Body

```json
{
  "client_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "hostname": "homeassistant.local",
  "name": "Home Assistant Main",
  "ha_version": "2025.11.0",
  "os_info": "Home Assistant OS 11.1"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `client_id` | UUID | Yes | Client-generated UUID |
| `hostname` | String | Yes | Network hostname |
| `name` | String | No | Friendly name (defaults to hostname) |
| `ha_version` | String | No | Home Assistant version |
| `os_info` | String | No | Operating system info |

#### Response (201 Created)

```json
{
  "installation_id": "12345",
  "access_token": "randombase64string...",
  "poll_interval_seconds": 60,
  "config_version": 1
}
```

---

### 2. Report Status

**Endpoint:** `POST /v1/installations/{installation_id}/status`
**Authentication:** Required

#### Request Body

```json
{
  "timestamp": "2025-11-16T12:34:56Z",
  "ha_version": "2025.11.0",
  "os_info": "Home Assistant OS 11.1"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `timestamp` | ISO 8601 | Yes | Current timestamp |
| `ha_version` | String | No | HA version (if changed) |
| `os_info` | String | No | OS info (if changed) |

#### Response (200 OK)

```json
{
  "status": "ok",
  "installation_status": "online",
  "config_version": 1
}
```

---

### 3. Fetch Configuration

**Endpoint:** `GET /v1/installations/{installation_id}/config`
**Authentication:** Required

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `current_version` | Integer | No | Client's current config version |

#### Response (200 OK) - New Configuration

```json
{
  "config_version": 2,
  "poll_interval_seconds": 60,
  "desired_state": {
    "report_interval": 60,
    "thresholds": {
      "disk_free_percent_warn": 10,
      "cpu_load_warn": 80,
      "memory_used_percent_warn": 90
    },
    "features": {
      "metrics_enabled": true,
      "alerts_enabled": true
    }
  },
  "description": "Updated thresholds for monitoring"
}
```

#### Response (204 No Content)

No configuration changes (current version is up-to-date).

---

### 4. Submit Metric

**Endpoint:** `POST /v1/installations/{installation_id}/metrics`
**Authentication:** Required

#### Request Body

```json
{
  "timestamp": "2025-11-16T12:34:56Z",
  "cpu_load_1m": 0.75,
  "cpu_load_5m": 0.68,
  "cpu_load_15m": 0.52,
  "memory_used_percent": 42.5,
  "memory_used_mb": 2048.5,
  "disk_used_percent": 68.3,
  "disk_free_percent": 31.7,
  "disk_free_mb": 15360,
  "uptime_seconds": 3600000,
  "warnings": ["low_disk"],
  "additional_metrics": {
    "temperature": 45.5,
    "custom_field": "value"
  }
}
```

All fields except `timestamp` are optional.

#### Response (201 Created)

```json
{
  "status": "ok",
  "metric_id": "67890"
}
```

---

### 5. Submit Batch Metrics

**Endpoint:** `POST /v1/installations/{installation_id}/metrics/batch`
**Authentication:** Required

#### Request Body

```json
{
  "metrics": [
    {
      "timestamp": "2025-11-16T12:30:00Z",
      "cpu_load_1m": 0.75,
      "memory_used_percent": 42.5,
      "disk_free_percent": 31.7
    },
    {
      "timestamp": "2025-11-16T12:31:00Z",
      "cpu_load_1m": 0.82,
      "memory_used_percent": 43.1,
      "disk_free_percent": 31.5
    }
  ]
}
```

**Constraints:**
- Minimum 1 metric
- Maximum 100 metrics per batch
- Each metric must have `timestamp`

#### Response (201 Created)

```json
{
  "status": "ok",
  "created_count": 2,
  "metric_ids": ["67890", "67891"]
}
```

---

## Data Flow

### Registration Flow

```
┌────────┐                                    ┌────────┐
│ Client │                                    │ Server │
└───┬────┘                                    └───┬────┘
    │                                             │
    │ 1. Generate client_id (UUID)                │
    │                                             │
    │ 2. POST /v1/installations/register          │
    │    {client_id, hostname, name, ...}        │
    ├────────────────────────────────────────────>│
    │                                             │
    │                               3. Create installation
    │                                  Generate access_token
    │                                  Create default config
    │                                  Set config_version = 1
    │                                             │
    │ 4. Return credentials                       │
    │    {installation_id, access_token,          │
    │     poll_interval, config_version}         │
    │<────────────────────────────────────────────┤
    │                                             │
    │ 5. Store credentials locally                │
    │    Start periodic tasks                     │
    │                                             │
```

---

### Periodic Reporting Flow

```
┌────────┐                                    ┌────────┐
│ Client │                                    │ Server │
└───┬────┘                                    └───┬────┘
    │                                             │
    │ ┌──────────────────────────────┐           │
    │ │ Every poll_interval seconds  │           │
    │ └──────────────────────────────┘           │
    │                                             │
    │ 1. POST /v1/installations/{id}/status       │
    │    {timestamp, ha_version, os_info}        │
    ├────────────────────────────────────────────>│
    │                                             │
    │                               2. Update last_seen_at
    │                                  Update status
    │                                  Check config version
    │                                             │
    │ 3. {status: "ok",                           │
    │     installation_status: "online",         │
    │     config_version: 2}                     │
    │<────────────────────────────────────────────┤
    │                                             │
    │ 4. Compare config_version                   │
    │    Local: 1, Server: 2 → Fetch config      │
    │                                             │
    │ 5. GET /v1/installations/{id}/config        │
    │    ?current_version=1                      │
    ├────────────────────────────────────────────>│
    │                                             │
    │                               6. Load active config
    │                                  Compare versions
    │                                             │
    │ 7. {config_version: 2,                      │
    │     desired_state: {...}}                  │
    │<────────────────────────────────────────────┤
    │                                             │
    │ 8. Apply configuration                      │
    │    Update local config_version = 2          │
    │                                             │
```

---

### Metrics Collection Flow

```
┌────────┐                                    ┌────────┐
│ Client │                                    │ Server │
└───┬────┘                                    └───┬────┘
    │                                             │
    │ ┌──────────────────────────────┐           │
    │ │ Every report_interval secs   │           │
    │ └──────────────────────────────┘           │
    │                                             │
    │ 1. Collect system metrics                   │
    │    (CPU, memory, disk, uptime)              │
    │                                             │
    │ 2. POST /v1/installations/{id}/metrics      │
    │    {timestamp, cpu_load_1m, ...}           │
    ├────────────────────────────────────────────>│
    │                                             │
    │                               3. Store metric
    │                                  Update last_seen_at
    │                                  Check thresholds
    │                                  Generate alerts if needed
    │                                             │
    │ 4. {status: "ok", metric_id: "123"}        │
    │<────────────────────────────────────────────┤
    │                                             │
```

---

### Offline Buffering Flow

```
┌────────┐                                    ┌────────┐
│ Client │                                    │ Server │
└───┬────┘                                    └───┬────┘
    │                                             │
    │ 1. Server unreachable                       │
    │    Buffer metrics locally                   │
    │    (up to 1000 metrics)                     │
    │                                             │
    │ 2. Connection restored                      │
    │                                             │
    │ 3. POST /v1/installations/{id}/metrics/batch│
    │    {metrics: [{...}, {...}, ...]}          │
    ├────────────────────────────────────────────>│
    │                                             │
    │                               4. Store all metrics
    │                                  Process alerts
    │                                             │
    │ 5. {status: "ok", created_count: 50}       │
    │<────────────────────────────────────────────┤
    │                                             │
    │ 6. Clear local buffer                       │
    │                                             │
```
