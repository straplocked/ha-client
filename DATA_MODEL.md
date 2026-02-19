# HA Dispatch - Data Model Reference

**Version:** 1.0  
**Date:** November 16, 2025  
**Purpose:** Client-side application development reference

---

## Table of Contents

1. [Overview](#overview)
2. [Database Schema](#database-schema)
3. [Data Models](#data-models)
4. [Relationships](#relationships)
5. [API Endpoints](#api-endpoints)
6. [Data Flow](#data-flow)
7. [JSON Examples](#json-examples)
8. [Business Rules](#business-rules)
9. [Enumerations & Constants](#enumerations--constants)

---

## Overview

HA Dispatch is a centralized management system for multiple Home Assistant installations. The data model follows a **controller-agent architecture**:

- **Server (Controller)**: Laravel 12 + MySQL 8 backend
- **Client (Agent)**: Home Assistant HACS integration (to be built)

### Core Entities

1. **Installation** - Represents a registered Home Assistant instance
2. **Configuration** - Versioned configuration for an installation
3. **Metric** - Time-series system metrics from installations
4. **Alert** - Generated alerts based on thresholds and conditions
5. **Setting** - Global system settings

---

## Database Schema

### 1. Installations Table

Primary table for registered Home Assistant installations.

```sql
CREATE TABLE installations (
  -- Primary key
  id                     BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  
  -- Identification
  client_id              UUID UNIQUE NOT NULL,
  name                   VARCHAR(255) NOT NULL,
  hostname               VARCHAR(255) NULL,
  
  -- Version information
  ha_version             VARCHAR(255) NULL,
  os_info                VARCHAR(255) NULL,
  
  -- Authentication
  access_token           VARCHAR(64) UNIQUE NOT NULL,
  
  -- Status tracking
  status                 ENUM('online', 'offline', 'warning') DEFAULT 'offline',
  is_active              BOOLEAN DEFAULT TRUE,
  last_seen_at           TIMESTAMP NULL,
  
  -- Configuration management
  config_version         INT DEFAULT 0,
  poll_interval_seconds  INT DEFAULT 60,
  
  -- Flexible storage
  metadata               JSON NULL,
  
  -- Timestamps
  created_at             TIMESTAMP NULL,
  updated_at             TIMESTAMP NULL,
  deleted_at             TIMESTAMP NULL,
  
  -- Indexes
  INDEX idx_status_last_seen (status, last_seen_at),
  INDEX idx_is_active (is_active)
);
```

#### Field Descriptions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | BIGINT | Yes | Server-assigned unique identifier |
| `client_id` | UUID | Yes | Client-generated UUID (for re-registration) |
| `name` | VARCHAR(255) | Yes | Friendly display name |
| `hostname` | VARCHAR(255) | No | Network hostname |
| `ha_version` | VARCHAR(255) | No | Home Assistant version (e.g., "2025.11.0") |
| `os_info` | VARCHAR(255) | No | Operating system information |
| `access_token` | VARCHAR(64) | Yes | Authentication token (64 random chars) |
| `status` | ENUM | Yes | Current status: online, offline, warning |
| `is_active` | BOOLEAN | Yes | Admin can deactivate installation |
| `last_seen_at` | TIMESTAMP | No | Last successful communication |
| `config_version` | INT | Yes | Incremented when config changes |
| `poll_interval_seconds` | INT | Yes | How often client should poll (default: 60) |
| `metadata` | JSON | No | Flexible JSON field for custom data |
| `deleted_at` | TIMESTAMP | No | Soft delete timestamp |

---

### 2. Installation Configurations Table

Stores versioned configuration snapshots for each installation.

```sql
CREATE TABLE installation_configurations (
  -- Primary key
  id              BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  
  -- Foreign key
  installation_id BIGINT UNSIGNED NOT NULL,
  
  -- Version control
  version         INT NOT NULL,
  
  -- Configuration data
  desired_state   JSON NOT NULL,
  description     TEXT NULL,
  
  -- Status
  is_active       BOOLEAN DEFAULT TRUE,
  applied_at      TIMESTAMP NULL,
  
  -- Timestamps
  created_at      TIMESTAMP NULL,
  updated_at      TIMESTAMP NULL,
  
  -- Foreign key constraint
  FOREIGN KEY (installation_id) 
    REFERENCES installations(id) 
    ON DELETE CASCADE,
  
  -- Constraints & Indexes
  UNIQUE KEY unique_version (installation_id, version),
  INDEX idx_is_active (is_active)
);
```

#### Field Descriptions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | BIGINT | Yes | Unique configuration ID |
| `installation_id` | BIGINT | Yes | Foreign key to installations table |
| `version` | INT | Yes | Configuration version (starts at 1) |
| `desired_state` | JSON | Yes | Configuration JSON object |
| `description` | TEXT | No | Human-readable change description |
| `is_active` | BOOLEAN | Yes | Only one active config per installation |
| `applied_at` | TIMESTAMP | No | When client confirmed application |

#### Desired State Structure

```json
{
  "report_interval": 60,
  "thresholds": {
    "disk_free_percent_warn": 10,
    "cpu_load_warn": 80,
    "memory_used_percent_warn": 90
  },
  "features": {
    "metrics_enabled": true,
    "alerts_enabled": true,
    "debug_mode": false
  },
  "custom_settings": {
    "key": "value"
  }
}
```

---

### 3. Installation Metrics Table

Stores time-series system metrics from installations.

```sql
CREATE TABLE installation_metrics (
  -- Primary key
  id                    BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  
  -- Foreign key
  installation_id       BIGINT UNSIGNED NOT NULL,
  
  -- Timestamp
  recorded_at           TIMESTAMP NOT NULL,
  
  -- CPU metrics
  cpu_load_1m           DECIMAL(8,2) NULL,  -- 1-minute load average
  cpu_load_5m           DECIMAL(8,2) NULL,  -- 5-minute load average
  cpu_load_15m          DECIMAL(8,2) NULL,  -- 15-minute load average
  
  -- Memory metrics
  memory_used_percent   DECIMAL(5,2) NULL,  -- 0-100
  memory_used_mb        DECIMAL(10,2) NULL, -- Megabytes
  
  -- Disk metrics
  disk_used_percent     DECIMAL(5,2) NULL,  -- 0-100
  disk_free_percent     DECIMAL(5,2) NULL,  -- 0-100
  disk_free_mb          BIGINT NULL,        -- Megabytes
  
  -- System metrics
  uptime_seconds        INT NULL,           -- System uptime
  
  -- Flexible storage
  warnings              JSON NULL,          -- Array of warning strings
  additional_metrics    JSON NULL,          -- Custom metrics
  
  -- Timestamps
  created_at            TIMESTAMP NULL,
  updated_at            TIMESTAMP NULL,
  
  -- Foreign key constraint
  FOREIGN KEY (installation_id) 
    REFERENCES installations(id) 
    ON DELETE CASCADE,
  
  -- Indexes
  INDEX idx_installation_recorded (installation_id, recorded_at),
  INDEX idx_recorded_at (recorded_at)
);
```

#### Field Descriptions

| Field | Type | Range | Description |
|-------|------|-------|-------------|
| `id` | BIGINT | - | Unique metric ID |
| `installation_id` | BIGINT | - | Foreign key to installations |
| `recorded_at` | TIMESTAMP | - | When metric was collected |
| `cpu_load_1m` | DECIMAL(8,2) | 0-999999.99 | 1-minute load average |
| `cpu_load_5m` | DECIMAL(8,2) | 0-999999.99 | 5-minute load average |
| `cpu_load_15m` | DECIMAL(8,2) | 0-999999.99 | 15-minute load average |
| `memory_used_percent` | DECIMAL(5,2) | 0-100 | Percentage of memory used |
| `memory_used_mb` | DECIMAL(10,2) | 0+ | Megabytes of memory used |
| `disk_used_percent` | DECIMAL(5,2) | 0-100 | Percentage of disk used |
| `disk_free_percent` | DECIMAL(5,2) | 0-100 | Percentage of disk free |
| `disk_free_mb` | BIGINT | 0+ | Megabytes of disk free |
| `uptime_seconds` | INT | 0+ | System uptime in seconds |
| `warnings` | JSON | - | Array of warning identifiers |
| `additional_metrics` | JSON | - | Custom integration metrics |

#### Warnings Array Structure

```json
["low_disk", "high_cpu", "high_memory", "custom_warning"]
```

#### Additional Metrics Structure

```json
{
  "ha_database_size_mb": 250,
  "addon_count": 15,
  "entity_count": 342,
  "temperature_celsius": 45.5,
  "custom_metric": "value"
}
```

---

### 4. Installation Alerts Table

Stores generated alerts for threshold violations and issues.

```sql
CREATE TABLE installation_alerts (
  -- Primary key
  id               BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  
  -- Foreign key
  installation_id  BIGINT UNSIGNED NOT NULL,
  
  -- Alert details
  severity         ENUM('info', 'warning', 'critical') DEFAULT 'warning',
  type             VARCHAR(255) NOT NULL,
  title            VARCHAR(255) NOT NULL,
  message          TEXT NOT NULL,
  context          JSON NULL,
  
  -- Timestamps
  triggered_at     TIMESTAMP NOT NULL,
  resolved_at      TIMESTAMP NULL,
  
  -- Acknowledgment
  is_acknowledged  BOOLEAN DEFAULT FALSE,
  acknowledged_at  TIMESTAMP NULL,
  acknowledged_by  BIGINT UNSIGNED NULL,
  
  -- Timestamps
  created_at       TIMESTAMP NULL,
  updated_at       TIMESTAMP NULL,
  
  -- Foreign key constraints
  FOREIGN KEY (installation_id) 
    REFERENCES installations(id) 
    ON DELETE CASCADE,
  FOREIGN KEY (acknowledged_by) 
    REFERENCES users(id) 
    ON DELETE SET NULL,
  
  -- Indexes
  INDEX idx_installation_severity_resolved (installation_id, severity, resolved_at),
  INDEX idx_triggered_at (triggered_at)
);
```

#### Field Descriptions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | BIGINT | Yes | Unique alert ID |
| `installation_id` | BIGINT | Yes | Foreign key to installations |
| `severity` | ENUM | Yes | info, warning, or critical |
| `type` | VARCHAR(255) | Yes | Alert type identifier |
| `title` | VARCHAR(255) | Yes | Short alert title |
| `message` | TEXT | Yes | Detailed alert message |
| `context` | JSON | No | Alert-specific context data |
| `triggered_at` | TIMESTAMP | Yes | When alert was triggered |
| `resolved_at` | TIMESTAMP | No | When condition resolved |
| `is_acknowledged` | BOOLEAN | Yes | Admin acknowledgment status |
| `acknowledged_at` | TIMESTAMP | No | When admin acknowledged |
| `acknowledged_by` | BIGINT | No | User ID who acknowledged |

#### Alert Types

Common server-generated alert types:

- `installation_offline` - No communication for threshold period
- `disk_space_low` - Disk free below threshold
- `memory_high` - Memory usage above threshold
- `cpu_high` - CPU load above threshold
- `metric_missing` - Expected metric not received
- `custom_alert` - Integration-specific alert

#### Context Structure

```json
{
  "metric_name": "disk_free_percent",
  "current_value": 8.5,
  "threshold_value": 10,
  "recorded_at": "2025-11-16T12:34:56Z"
}
```

---

### 5. Settings Table

Global system settings (server-side only).

```sql
CREATE TABLE settings (
  -- Primary key
  id          BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  
  -- Setting identification
  key         VARCHAR(255) UNIQUE NOT NULL,
  group       VARCHAR(255) NOT NULL,
  
  -- Value storage
  value       JSON NOT NULL,
  type        VARCHAR(255) DEFAULT 'string',
  description TEXT NULL,
  
  -- Timestamps
  created_at  TIMESTAMP NULL,
  updated_at  TIMESTAMP NULL,
  
  -- Index
  INDEX idx_group (group)
);
```

#### Setting Groups

- `alerts` - Alert threshold configurations
- `retention` - Data retention policies
- `monitoring` - Monitoring intervals
- `application` - Application settings
- `notifications` - Notification preferences
- `api` - API configuration

---

## Data Models

### Installation Model

**PHP Model:** `App\Models\Installation`

#### Relationships

```php
// One-to-many relationships
$installation->configurations()      // All configurations
$installation->metrics()             // All metrics
$installation->alerts()              // All alerts
$installation->unresolvedAlerts()    // Unresolved alerts only

// One-to-one relationships
$installation->activeConfiguration() // Current active config
$installation->latestMetric()        // Most recent metric
```

#### Scopes

```php
Installation::online()        // status = 'online'
Installation::offline()       // status = 'offline'
Installation::withWarnings()  // status = 'warning'
Installation::active()        // is_active = true
```

#### Key Methods

```php
$installation->isOnline(): bool      // Check if online based on last_seen_at
$installation->updateStatus(): void  // Update status based on alerts
```

---

### Configuration Model

**PHP Model:** `App\Models\InstallationConfiguration`

#### Business Logic

- Only one configuration can be `is_active = true` per installation
- `version` field is auto-incremented per installation
- Older configurations remain in database for audit trail
- Client compares local `config_version` with server version

---

### Metric Model

**PHP Model:** `App\Models\InstallationMetric`

#### Business Logic

- All metric fields except `recorded_at` are optional
- Metrics are pruned after retention period (default: 30 days)
- `recorded_at` can be historical (for offline buffering)
- Server updates installation's `last_seen_at` when metrics received

---

### Alert Model

**PHP Model:** `App\Models\InstallationAlert`

#### Alert Lifecycle

1. **Triggered**: Alert created when condition detected (`triggered_at` set)
2. **Acknowledged**: Admin acknowledges alert (optional)
3. **Resolved**: Condition no longer present (`resolved_at` set)
4. **Pruned**: Deleted after retention period (default: 90 days)

#### Severity Escalation

- **info**: Informational only, no action required
- **warning**: Attention needed, system functional
- **critical**: Immediate action required, system impaired

---

## Relationships

### Entity Relationship Diagram

```
┌─────────────────────┐
│    installations    │
│                     │
│  PK: id             │
│  UK: client_id      │
│  UK: access_token   │
└──────┬──────────────┘
       │
       │ 1:N
       │
       ├──────────────────────────┐
       │                          │
       ▼                          ▼
┌──────────────────┐    ┌──────────────────┐
│ configurations   │    │     metrics      │
│                  │    │                  │
│ PK: id           │    │ PK: id           │
│ FK: install_id   │    │ FK: install_id   │
│ UK: (install, v) │    │                  │
└──────────────────┘    └──────────────────┘
       
       │
       ▼
┌──────────────────┐
│     alerts       │
│                  │
│ PK: id           │
│ FK: install_id   │
│ FK: ack_by       │──┐
└──────────────────┘  │
                      │
                      ▼
               ┌──────────┐
               │  users   │
               │          │
               │ PK: id   │
               └──────────┘
```

### Relationship Details

#### Installation → Configuration (1:N)

```sql
SELECT * FROM installation_configurations 
WHERE installation_id = ?
ORDER BY version DESC;
```

**Active Configuration:**
```sql
SELECT * FROM installation_configurations 
WHERE installation_id = ? 
  AND is_active = true
ORDER BY version DESC 
LIMIT 1;
```

#### Installation → Metrics (1:N)

```sql
SELECT * FROM installation_metrics 
WHERE installation_id = ?
ORDER BY recorded_at DESC;
```

**Latest Metric:**
```sql
SELECT * FROM installation_metrics 
WHERE installation_id = ?
ORDER BY recorded_at DESC 
LIMIT 1;
```

#### Installation → Alerts (1:N)

```sql
SELECT * FROM installation_alerts 
WHERE installation_id = ?
ORDER BY triggered_at DESC;
```

**Unresolved Alerts:**
```sql
SELECT * FROM installation_alerts 
WHERE installation_id = ? 
  AND resolved_at IS NULL
ORDER BY severity DESC, triggered_at ASC;
```

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

---

## JSON Examples

### Complete Registration Request/Response

**Request:**
```json
{
  "client_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "hostname": "homeassistant.local",
  "name": "Main Home Assistant",
  "ha_version": "2025.11.0",
  "os_info": "Home Assistant OS 11.1"
}
```

**Response:**
```json
{
  "installation_id": "1",
  "access_token": "3kj4h5g6j7h8k9l0m1n2b3v4c5x6z7a8s9d0f1g2h3j4k5l6m7n8b9v0c1x2z3a4s5",
  "poll_interval_seconds": 60,
  "config_version": 1
}
```

---

### Complete Configuration Object

```json
{
  "config_version": 5,
  "poll_interval_seconds": 60,
  "desired_state": {
    "report_interval": 60,
    "thresholds": {
      "disk_free_percent_warn": 10,
      "disk_free_percent_critical": 5,
      "cpu_load_warn": 80,
      "cpu_load_critical": 95,
      "memory_used_percent_warn": 90,
      "memory_used_percent_critical": 95
    },
    "features": {
      "metrics_enabled": true,
      "alerts_enabled": true,
      "debug_mode": false,
      "websocket_enabled": false
    },
    "custom_settings": {
      "enable_temperature_monitoring": true,
      "temperature_threshold": 60,
      "custom_metrics": ["temperature", "fan_speed"],
      "additional_config": {
        "key": "value"
      }
    }
  },
  "description": "Updated thresholds and enabled temperature monitoring"
}
```

---

### Complete Metric Submission

```json
{
  "timestamp": "2025-11-16T12:34:56.789Z",
  "cpu_load_1m": 1.25,
  "cpu_load_5m": 0.98,
  "cpu_load_15m": 0.75,
  "memory_used_percent": 45.67,
  "memory_used_mb": 3584.50,
  "disk_used_percent": 72.34,
  "disk_free_percent": 27.66,
  "disk_free_mb": 28672,
  "uptime_seconds": 2592000,
  "warnings": [
    "high_memory",
    "disk_approaching_full"
  ],
  "additional_metrics": {
    "ha_database_size_mb": 456.78,
    "addon_count": 23,
    "integration_count": 87,
    "entity_count": 542,
    "automation_count": 67,
    "device_count": 145,
    "cpu_temperature_celsius": 52.5,
    "network_rx_mb": 1234.56,
    "network_tx_mb": 789.12,
    "custom_sensor_value": 42
  }
}
```

---

### Alert Context Examples

**Disk Space Low Alert:**
```json
{
  "type": "disk_space_low",
  "severity": "warning",
  "title": "Low Disk Space",
  "message": "Disk free space is below threshold",
  "context": {
    "metric_name": "disk_free_percent",
    "current_value": 8.5,
    "threshold_value": 10,
    "disk_free_mb": 2048,
    "recorded_at": "2025-11-16T12:34:56Z"
  }
}
```

**Installation Offline Alert:**
```json
{
  "type": "installation_offline",
  "severity": "critical",
  "title": "Installation Offline",
  "message": "No communication received for 15 minutes",
  "context": {
    "last_seen_at": "2025-11-16T12:20:00Z",
    "offline_duration_minutes": 15,
    "threshold_minutes": 5
  }
}
```

**Memory High Alert:**
```json
{
  "type": "memory_high",
  "severity": "warning",
  "title": "High Memory Usage",
  "message": "Memory usage is above warning threshold",
  "context": {
    "metric_name": "memory_used_percent",
    "current_value": 92.5,
    "threshold_value": 90,
    "memory_used_mb": 7250.25,
    "recorded_at": "2025-11-16T12:34:56Z"
  }
}
```

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
2. Alert severity can escalate (warning → critical)
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
- [CLIENT_SIDE_DEVELOPMENT_GUIDE.md](CLIENT_SIDE_DEVELOPMENT_GUIDE.md)
- [central_manager_plan.md](central_manager_plan.md)

---

**Document Version:** 1.0  
**Last Updated:** November 16, 2025  
**For Questions:** Refer to existing codebase in `/src/app/Models/` and `/src/app/Http/Controllers/Api/`

