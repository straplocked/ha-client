<!-- Split from DATA_MODEL.md -->

# Database Schema

> See also: [Data Models & Relationships](relationships.md) | [API Endpoints & Data Flow](api-endpoints.md) | [JSON Examples](json-examples.md) | [Business Rules & Constants](business-rules.md)
>
> Back to [Data Model Overview](README.md)

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
