# HA Dispatch - Client Side Development Guide

**Version:** 1.0  
**Date:** November 16, 2025  
**Server Version:** Laravel 12 + Filament 4  
**Target Platform:** Home Assistant (HACS Integration)

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [System Architecture](#system-architecture)
3. [Server Infrastructure](#server-infrastructure)
4. [Authentication & Security](#authentication--security)
5. [API Reference](#api-reference)
6. [Data Models](#data-models)
7. [Communication Patterns](#communication-patterns)
8. [Client Implementation Guide](#client-implementation-guide)
9. [Configuration Management](#configuration-management)
10. [Metrics Collection](#metrics-collection)
11. [Alert Handling](#alert-handling)
12. [WebSocket Integration](#websocket-integration)
13. [Error Handling](#error-handling)
14. [Testing Strategy](#testing-strategy)
15. [Deployment & Distribution](#deployment--distribution)
16. [Examples & Code Samples](#examples--code-samples)

---

## Executive Summary

**HA Dispatch** is a centralized management system for multiple Home Assistant installations. The system uses a **controller-agent architecture** where:

- **Server (Controller)**: Laravel 12 + Filament 4 application managing all installations
- **Client (Agent)**: HACS integration installed on each Home Assistant instance

### Key Principles

1. **Client-Initiated Communication**: All communication is initiated by the client to avoid firewall/NAT issues
2. **Token-Based Authentication**: Each installation has a unique API token
3. **Configuration Push/Pull**: Server defines desired state, client polls and applies
4. **Metrics Reporting**: Client sends periodic system metrics to server
5. **Alert Generation**: Server monitors metrics and generates alerts based on configurable thresholds

---

## System Architecture

### High-Level Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    HA Dispatch Server                        │
│              (Laravel 12 + Filament 4 + MySQL)              │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │  REST API    │  │  WebSockets  │  │  Admin Panel │     │
│  │  (Sanctum)   │  │  (Port 6001) │  │  (Filament)  │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │         MySQL Database                                │  │
│  │  - Installations  - Configurations                    │  │
│  │  - Metrics        - Alerts                            │  │
│  │  - Settings       - Users/Roles                       │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            ▲
                            │ HTTPS (Port 8080)
                            │ WSS (Port 6001)
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│ Home Assistant│   │ Home Assistant│   │ Home Assistant│
│  Installation │   │  Installation │   │  Installation │
│      #1       │   │      #2       │   │      #3       │
│               │   │               │   │               │
│  ┌─────────┐  │   │  ┌─────────┐  │   │  ┌─────────┐  │
│  │ HACS    │  │   │  │ HACS    │  │   │  │ HACS    │  │
│  │ Client  │  │   │  │ Client  │  │   │  │ Client  │  │
│  └─────────┘  │   │  └─────────┘  │   │  └─────────┘  │
└───────────────┘   └───────────────┘   └───────────────┘
```

### Component Responsibilities

#### Server Responsibilities
- **Installation Registry**: Track all registered Home Assistant installations
- **Configuration Management**: Store and distribute configuration to clients
- **Metrics Storage**: Store time-series metrics from all installations
- **Alert Management**: Generate alerts based on thresholds and conditions
- **User Management**: Role-based access control for administrators
- **Settings Management**: Global defaults and operational parameters
- **API Gateway**: Expose RESTful and WebSocket endpoints
- **Background Jobs**: Health checks, data pruning, alert generation

#### Client Responsibilities
- **Self-Registration**: Register with server and obtain API token
- **Status Reporting**: Send periodic heartbeat/status updates
- **Metrics Collection**: Gather system metrics and send to server
- **Configuration Polling**: Check for configuration updates
- **Configuration Application**: Apply received configuration locally
- **Entity Exposure**: Expose status/config as Home Assistant entities
- **Error Handling**: Graceful handling of network/server issues

---

## Server Infrastructure

### Technology Stack

#### Backend Framework: Laravel 12
- **Eloquent ORM**: Database models and relationships
- **Laravel Sanctum**: API token authentication
- **Laravel Broadcasting**: Real-time WebSocket events
- **Laravel Queue**: Background job processing
- **Laravel Scheduler**: Periodic tasks
- **Middleware**: Custom authentication for installation tokens

#### Frontend/Admin: Filament 4
- **Resources**: CRUD operations for installations, alerts, users
- **Widgets**: Dashboard with real-time monitoring
- **Notifications**: In-app alert system
- **Charts**: Metrics visualization with Chart.js
- **Forms**: Configuration and settings management
- **Shield Integration**: Role-based permissions

#### Infrastructure
- **MySQL 8**: Primary database
- **Nginx**: Web server and reverse proxy
- **PHP 8.3-FPM**: Application runtime
- **Docker Compose**: Containerized deployment
- **Queue Worker**: Background job processor
- **Node.js**: Frontend asset compilation

### Key Laravel Packages
- `laravel/sanctum` - API authentication
- `beyondcode/laravel-websockets` - WebSocket server
- `filament/filament` - Admin panel framework
- `spatie/laravel-permission` - Role and permission management
- `bezhansalleh/filament-shield` - Filament permission integration

### Default Settings (Configurable via Admin Panel)

#### Alert Thresholds
- CPU Load Warning: 80%
- Memory Usage Warning: 90%
- Disk Free Warning: 10%
- Offline Threshold: 5 minutes

#### Data Retention
- Metrics Retention: 30 days
- Alerts Retention: 90 days
- Soft Deletes Retention: 30 days

#### Monitoring Intervals
- Default Poll Interval: 60 seconds
- Health Check Frequency: 300 seconds (5 minutes)
- Metrics Prune Time: 03:00 daily

#### Application Settings
- Brand Name: "HA Dispatch"
- Timezone: UTC
- Dashboard Refresh Rate: 30 seconds

### Scheduled Jobs
- **CheckInstallationHealthJob**: Every 5 minutes (marks offline installations)
- **PruneOldMetricsJob**: Daily at 03:00 (deletes old metrics)
- **PruneOldAlertsJob**: Daily at 03:30 (deletes old resolved alerts)
- **PruneOldSoftDeletesJob**: Weekly Sunday 04:00 (permanent deletion)

---

## Authentication & Security

### Authentication Flow

#### 1. Initial Registration (No Authentication)
```http
POST /api/v1/installations/register
Content-Type: application/json

{
  "client_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "hostname": "homeassistant.local",
  "name": "Home Assistant Main",
  "ha_version": "2025.11.0",
  "os_info": "Home Assistant OS 11.1"
}
```

**Response:**
```json
{
  "installation_id": "12345",
  "access_token": "randombase64string...",
  "poll_interval_seconds": 60,
  "config_version": 1
}
```

#### 2. Authenticated Requests
All subsequent requests must include the Bearer token:

```http
Authorization: Bearer {access_token}
```

### Security Implementation

#### Token Generation
- 64-character random string
- Generated during registration
- Stored hashed in database (SHA-256)
- Unique per installation
- Long-lived (no expiration by default)

#### Token Storage (Client Side)
- Store in Home Assistant's configuration storage
- Encrypt if possible
- Never log token in plain text
- Regenerate if compromised (requires re-registration)

#### Request Authentication Middleware
- Custom middleware: `auth.installation`
- Validates Bearer token
- Loads Installation model into request
- Returns 401 if invalid/missing
- Returns 403 if installation is inactive

#### HTTPS Requirements
- **PRODUCTION**: Always use HTTPS
- **DEVELOPMENT**: HTTP acceptable for local testing
- **WEBSOCKETS**: Use WSS in production

#### Rate Limiting
- Default: 60 requests/minute per installation
- Configurable via admin panel
- 429 status code when exceeded
- Retry-After header included

### Security Best Practices for Client

1. **Never hardcode tokens**: Always use configuration storage
2. **Validate server certificates**: Prevent MITM attacks
3. **Use secure storage**: Encrypt sensitive data
4. **Handle 401/403 properly**: Prompt for re-registration
5. **Implement exponential backoff**: On rate limit or server errors
6. **Sanitize metrics data**: Don't send sensitive HA data
7. **Verify server identity**: Check SSL certificate in production

---

## API Reference

### Base URLs
- **REST API**: `http(s)://server:8080/api`
- **WebSocket**: `ws(s)://server:6001`

### API Version
- Current: `v1`
- All endpoints prefixed with `/v1`

---

### Endpoint 1: Register Installation

**Purpose**: Register a new Home Assistant installation with the server.

**Method**: `POST`  
**Path**: `/v1/installations/register`  
**Authentication**: None (public endpoint)

#### Request Body
```json
{
  "client_id": "uuid",              // Required, unique UUID generated by client
  "hostname": "string",             // Required, max 255 chars
  "name": "string",                 // Optional, max 255 chars (defaults to hostname)
  "ha_version": "string",           // Optional, max 255 chars
  "os_info": "string"               // Optional, max 255 chars
}
```

#### Success Response (201 Created)
```json
{
  "installation_id": "12345",       // Server-assigned ID
  "access_token": "string",         // 64-char authentication token
  "poll_interval_seconds": 60,      // How often to poll for config
  "config_version": 1               // Current configuration version
}
```

#### Error Responses
**422 Unprocessable Entity**: Validation failed
```json
{
  "error": "Validation failed",
  "messages": {
    "client_id": ["The client id has already been taken."],
    "hostname": ["The hostname field is required."]
  }
}
```

#### Client Implementation Notes
- Generate `client_id` using UUID v4
- Store `client_id` persistently (for re-registration scenarios)
- Store `access_token` securely (encrypted if possible)
- Store `installation_id` for reference
- Use `poll_interval_seconds` for configuration polling
- **Do not** re-register if already registered (check stored token first)

#### Example Implementation (Python)
```python
import uuid
import requests

def register_installation(server_url, hostname, name=None, ha_version=None):
    """Register this Home Assistant installation with HA Dispatch server."""
    client_id = str(uuid.uuid4())
    
    payload = {
        "client_id": client_id,
        "hostname": hostname,
        "name": name or hostname,
        "ha_version": ha_version,
        "os_info": get_os_info()  # Implement this
    }
    
    response = requests.post(
        f"{server_url}/api/v1/installations/register",
        json=payload,
        timeout=10
    )
    
    if response.status_code == 201:
        data = response.json()
        # Store these values securely
        store_config({
            "client_id": client_id,
            "installation_id": data["installation_id"],
            "access_token": data["access_token"],
            "poll_interval_seconds": data["poll_interval_seconds"]
        })
        return True
    else:
        # Handle error
        return False
```

---

### Endpoint 2: Report Status

**Purpose**: Send periodic status update to indicate installation is online.

**Method**: `POST`  
**Path**: `/v1/installations/{installation_id}/status`  
**Authentication**: Required (Bearer token)

#### Request Body
```json
{
  "timestamp": "2025-11-16T12:34:56Z",  // Required, ISO 8601 format
  "ha_version": "2025.11.0",             // Optional, updates if changed
  "os_info": "Home Assistant OS 11.1"    // Optional, updates if changed
}
```

#### Success Response (200 OK)
```json
{
  "status": "ok",
  "installation_status": "online",   // Current server-side status
  "config_version": 1                // Current config version (check if changed)
}
```

#### Error Responses
**403 Forbidden**: Token doesn't match installation
```json
{
  "error": "Unauthorized"
}
```

**422 Unprocessable Entity**: Validation failed
```json
{
  "error": "Validation failed",
  "messages": {
    "timestamp": ["The timestamp field is required."]
  }
}
```

#### Client Implementation Notes
- Send every `poll_interval_seconds` (default 60s)
- Update `last_seen_at` timestamp on server
- Server marks offline if no status received for 5+ minutes (configurable)
- Compare returned `config_version` with local version
- If `config_version` differs, fetch new configuration
- Update local HA version if it changes

#### Example Implementation (Python)
```python
from datetime import datetime, timezone

def report_status(server_url, installation_id, token, ha_version=None):
    """Send status heartbeat to server."""
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ha_version": ha_version,
        "os_info": get_os_info()
    }
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    response = requests.post(
        f"{server_url}/api/v1/installations/{installation_id}/status",
        json=payload,
        headers=headers,
        timeout=10
    )
    
    if response.status_code == 200:
        data = response.json()
        # Check if config version changed
        if data["config_version"] != get_local_config_version():
            fetch_configuration(server_url, installation_id, token)
        return True
    else:
        handle_error(response)
        return False
```

---

### Endpoint 3: Fetch Configuration

**Purpose**: Retrieve configuration updates from server.

**Method**: `GET`  
**Path**: `/v1/installations/{installation_id}/config`  
**Authentication**: Required (Bearer token)

#### Query Parameters
- `current_version` (optional, integer): Client's current config version

#### Success Response (200 OK)
New configuration available:
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

#### Success Response (204 No Content)
No configuration changes (current version is up-to-date).

#### Error Responses
**403 Forbidden**: Token doesn't match installation
```json
{
  "error": "Unauthorized"
}
```

#### Client Implementation Notes
- Poll periodically or when `config_version` changes
- Server returns 204 if no updates available
- Process `desired_state` and apply locally
- Update local `config_version` after successful application
- Handle configuration errors gracefully
- Log configuration changes for debugging

#### Configuration Structure

**`desired_state` Object**:
```typescript
{
  report_interval: number,          // Seconds between status reports
  thresholds: {
    disk_free_percent_warn: number, // Percentage (0-100)
    cpu_load_warn: number,          // Percentage (0-100)
    memory_used_percent_warn: number // Percentage (0-100)
  },
  features: {
    metrics_enabled: boolean,       // Enable metrics collection
    alerts_enabled: boolean         // Enable alert generation
  }
}
```

#### Example Implementation (Python)
```python
def fetch_configuration(server_url, installation_id, token):
    """Fetch latest configuration from server."""
    current_version = get_local_config_version()
    
    headers = {
        "Authorization": f"Bearer {token}"
    }
    
    params = {
        "current_version": current_version
    }
    
    response = requests.get(
        f"{server_url}/api/v1/installations/{installation_id}/config",
        headers=headers,
        params=params,
        timeout=10
    )
    
    if response.status_code == 200:
        config = response.json()
        apply_configuration(config)
        store_config_version(config["config_version"])
        return config
    elif response.status_code == 204:
        # No update needed
        return None
    else:
        handle_error(response)
        return None
```

---

### Endpoint 4: Submit Single Metric

**Purpose**: Send a single system metric snapshot to server.

**Method**: `POST`  
**Path**: `/v1/installations/{installation_id}/metrics`  
**Authentication**: Required (Bearer token)

#### Request Body
```json
{
  "timestamp": "2025-11-16T12:34:56Z",  // Required, ISO 8601
  "cpu_load_1m": 0.75,                  // Optional, decimal(8,2)
  "cpu_load_5m": 0.68,                  // Optional, decimal(8,2)
  "cpu_load_15m": 0.52,                 // Optional, decimal(8,2)
  "memory_used_percent": 42.5,          // Optional, decimal(5,2), 0-100
  "memory_used_mb": 2048.5,             // Optional, decimal(10,2)
  "disk_used_percent": 68.3,            // Optional, decimal(5,2), 0-100
  "disk_free_percent": 31.7,            // Optional, decimal(5,2), 0-100
  "disk_free_mb": 15360,                // Optional, integer
  "uptime_seconds": 3600000,            // Optional, integer
  "warnings": ["low_disk"],             // Optional, array of strings
  "additional_metrics": {               // Optional, custom JSON data
    "temperature": 45.5,
    "custom_field": "value"
  }
}
```

#### Success Response (201 Created)
```json
{
  "status": "ok",
  "metric_id": "67890"
}
```

#### Error Responses
**403 Forbidden**: Token doesn't match installation
```json
{
  "error": "Unauthorized"
}
```

**422 Unprocessable Entity**: Validation failed
```json
{
  "error": "Validation failed",
  "messages": {
    "timestamp": ["The timestamp field is required."],
    "cpu_load_1m": ["The cpu load 1m must not be greater than 999999.99."]
  }
}
```

#### Client Implementation Notes
- All metric fields except `timestamp` are optional
- Send at least one metric value per request
- Use `additional_metrics` for custom data
- Server automatically updates `last_seen_at` when metrics received
- Metrics are used for alert generation on server side
- Consider batching metrics for efficiency (see next endpoint)

#### Metric Collection Strategy
1. **Polling Interval**: Use `report_interval` from configuration
2. **Metric Sources**: 
   - CPU: `/proc/loadavg` or `psutil` library
   - Memory: `/proc/meminfo` or `psutil.virtual_memory()`
   - Disk: `df` command or `psutil.disk_usage()`
   - Uptime: `/proc/uptime` or `psutil.boot_time()`
3. **Error Handling**: If metric collection fails, send what you have
4. **Performance**: Don't block HA event loop

#### Example Implementation (Python)
```python
import psutil
from datetime import datetime, timezone

def collect_and_send_metrics(server_url, installation_id, token):
    """Collect system metrics and send to server."""
    # Collect metrics
    cpu_load = psutil.getloadavg()
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    boot_time = psutil.boot_time()
    uptime = time.time() - boot_time
    
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cpu_load_1m": cpu_load[0],
        "cpu_load_5m": cpu_load[1],
        "cpu_load_15m": cpu_load[2],
        "memory_used_percent": memory.percent,
        "memory_used_mb": memory.used / (1024 * 1024),
        "disk_used_percent": disk.percent,
        "disk_free_percent": 100 - disk.percent,
        "disk_free_mb": disk.free / (1024 * 1024),
        "uptime_seconds": int(uptime),
        "warnings": check_warnings(memory, disk),  # Implement this
        "additional_metrics": {
            "ha_database_size_mb": get_db_size()  # Custom metric
        }
    }
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    response = requests.post(
        f"{server_url}/api/v1/installations/{installation_id}/metrics",
        json=payload,
        headers=headers,
        timeout=10
    )
    
    return response.status_code == 201
```

---

### Endpoint 5: Submit Batch Metrics

**Purpose**: Send multiple metric snapshots in a single request (for efficiency or offline buffering).

**Method**: `POST`  
**Path**: `/v1/installations/{installation_id}/metrics/batch`  
**Authentication**: Required (Bearer token)

#### Request Body
```json
{
  "metrics": [
    {
      "timestamp": "2025-11-16T12:30:00Z",
      "cpu_load_1m": 0.75,
      "memory_used_percent": 42.5,
      "disk_free_percent": 31.7
      // ... other metric fields
    },
    {
      "timestamp": "2025-11-16T12:31:00Z",
      "cpu_load_1m": 0.82,
      "memory_used_percent": 43.1,
      "disk_free_percent": 31.5
    }
    // Up to 100 metrics per batch
  ]
}
```

#### Success Response (201 Created)
```json
{
  "status": "ok",
  "created_count": 2,
  "metric_ids": ["67890", "67891"]
}
```

#### Error Responses
Same as single metric endpoint (403, 422)

#### Validation Rules
- `metrics` array: Required, minimum 1, maximum 100 items
- Each item must have `timestamp` (required)
- Other metric fields optional per item

#### Client Implementation Notes
- Use when offline: Buffer metrics locally and batch when reconnected
- Use for efficiency: Send multiple metrics in single HTTP request
- Maximum 100 metrics per batch (send multiple batches if needed)
- Server processes each metric individually
- All metrics in batch belong to same installation
- Timestamps can be historical (for offline buffering)

#### Example Implementation (Python)
```python
def send_metrics_batch(server_url, installation_id, token, metrics_buffer):
    """Send multiple buffered metrics in batch."""
    # Split into chunks of 100
    chunk_size = 100
    for i in range(0, len(metrics_buffer), chunk_size):
        chunk = metrics_buffer[i:i + chunk_size]
        
        payload = {
            "metrics": chunk
        }
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(
            f"{server_url}/api/v1/installations/{installation_id}/metrics/batch",
            json=payload,
            headers=headers,
            timeout=30  # Longer timeout for batch
        )
        
        if response.status_code == 201:
            # Remove sent metrics from buffer
            clear_buffer(chunk)
        else:
            # Keep in buffer for retry
            break
```

---

## Data Models

### Installation Model

Represents a registered Home Assistant installation.

#### Database Schema
```sql
CREATE TABLE installations (
  id BIGINT UNSIGNED PRIMARY KEY,
  client_id UUID UNIQUE NOT NULL,
  name VARCHAR(255) NOT NULL,
  hostname VARCHAR(255),
  ha_version VARCHAR(255),
  os_info VARCHAR(255),
  access_token VARCHAR(64) UNIQUE NOT NULL,
  status ENUM('online', 'offline', 'warning') DEFAULT 'offline',
  last_seen_at TIMESTAMP,
  config_version INT DEFAULT 0,
  poll_interval_seconds INT DEFAULT 60,
  metadata JSON,
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  deleted_at TIMESTAMP,
  
  INDEX idx_status_last_seen (status, last_seen_at)
);
```

#### Field Descriptions
- `id`: Server-assigned unique identifier
- `client_id`: Client-generated UUID (for re-registration)
- `name`: Friendly display name
- `hostname`: Network hostname
- `ha_version`: Home Assistant version string
- `os_info`: Operating system information
- `access_token`: Authentication token (hashed)
- `status`: Current status (online/offline/warning)
- `last_seen_at`: Last successful communication
- `config_version`: Incremented when config changes
- `poll_interval_seconds`: How often client should poll
- `metadata`: Flexible JSON field for custom data
- `is_active`: Admin can deactivate installation
- `deleted_at`: Soft delete timestamp

#### Status Values
- **online**: Recently communicated (within offline threshold)
- **offline**: No communication for 5+ minutes (configurable)
- **warning**: Metrics exceed thresholds or warnings present

#### Client Considerations
- Store `id` (installation_id) from registration response
- Use `config_version` to detect configuration changes
- Respect `poll_interval_seconds` for polling frequency
- `metadata` can store custom integration data

---

### Configuration Model

Represents configuration snapshots for an installation.

#### Database Schema
```sql
CREATE TABLE installation_configurations (
  id BIGINT UNSIGNED PRIMARY KEY,
  installation_id BIGINT UNSIGNED NOT NULL,
  version INT NOT NULL,
  desired_state JSON NOT NULL,
  description TEXT,
  is_active BOOLEAN DEFAULT TRUE,
  applied_at TIMESTAMP,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  
  FOREIGN KEY (installation_id) REFERENCES installations(id) ON DELETE CASCADE,
  UNIQUE KEY (installation_id, version),
  INDEX idx_is_active (is_active)
);
```

#### Field Descriptions
- `installation_id`: Foreign key to installation
- `version`: Configuration version (incremented on change)
- `desired_state`: JSON configuration object
- `description`: Human-readable change description
- `is_active`: Only one active config per installation
- `applied_at`: When client confirmed application

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
    "alerts_enabled": true
  },
  "custom_settings": {
    // Integration-specific settings
  }
}
```

#### Client Considerations
- Compare `version` with local stored version
- Only one configuration is active at a time
- Apply changes incrementally (don't require restart if possible)
- Report `applied_at` via status update (future feature)
- Store configuration locally for offline operation

---

### Metric Model

Represents a system metric snapshot.

#### Database Schema
```sql
CREATE TABLE installation_metrics (
  id BIGINT UNSIGNED PRIMARY KEY,
  installation_id BIGINT UNSIGNED NOT NULL,
  recorded_at TIMESTAMP NOT NULL,
  cpu_load_1m DECIMAL(8,2),
  cpu_load_5m DECIMAL(8,2),
  cpu_load_15m DECIMAL(8,2),
  memory_used_percent DECIMAL(5,2),
  memory_used_mb DECIMAL(10,2),
  disk_used_percent DECIMAL(5,2),
  disk_free_percent DECIMAL(5,2),
  disk_free_mb BIGINT,
  uptime_seconds INT,
  warnings JSON,
  additional_metrics JSON,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  
  FOREIGN KEY (installation_id) REFERENCES installations(id) ON DELETE CASCADE,
  INDEX idx_installation_recorded (installation_id, recorded_at),
  INDEX idx_recorded_at (recorded_at)
);
```

#### Metric Field Ranges
- `cpu_load_*`: 0 to 999999.99 (some systems can have high load)
- `memory_used_percent`: 0 to 100
- `disk_used_percent`: 0 to 100
- `disk_free_percent`: 0 to 100
- `disk_free_mb`: 0 to BIGINT max
- `uptime_seconds`: 0 to INT max

#### Warnings Array
Array of string identifiers for detected issues:
```json
["low_disk", "high_cpu", "high_memory"]
```

Common warning types:
- `low_disk`: Disk space below threshold
- `high_cpu`: CPU load above threshold
- `high_memory`: Memory usage above threshold
- `offline`: Installation not responding
- `custom_warning`: Integration-specific warning

#### Additional Metrics Object
Flexible JSON for integration-specific metrics:
```json
{
  "ha_database_size_mb": 250,
  "addon_count": 15,
  "entity_count": 342,
  "automation_count": 45,
  "temperature_celsius": 45.5,
  "custom_metric": "value"
}
```

#### Data Retention
- Default: 30 days (configurable via admin panel)
- Pruned daily at 03:00 AM server time
- Consider local retention strategy for offline buffering

---

### Alert Model

Represents generated alerts for threshold violations or issues.

#### Database Schema
```sql
CREATE TABLE installation_alerts (
  id BIGINT UNSIGNED PRIMARY KEY,
  installation_id BIGINT UNSIGNED NOT NULL,
  severity ENUM('info', 'warning', 'critical') DEFAULT 'warning',
  type VARCHAR(255) NOT NULL,
  title VARCHAR(255) NOT NULL,
  message TEXT NOT NULL,
  context JSON,
  triggered_at TIMESTAMP NOT NULL,
  resolved_at TIMESTAMP,
  is_acknowledged BOOLEAN DEFAULT FALSE,
  acknowledged_at TIMESTAMP,
  acknowledged_by BIGINT UNSIGNED,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  
  FOREIGN KEY (installation_id) REFERENCES installations(id) ON DELETE CASCADE,
  FOREIGN KEY (acknowledged_by) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_installation_severity_resolved (installation_id, severity, resolved_at),
  INDEX idx_triggered_at (triggered_at)
);
```

#### Severity Levels
- **info**: Informational, no action required
- **warning**: Attention needed, system functional
- **critical**: Immediate action required, system impaired

#### Alert Types
Common alert types generated by server:
- `installation_offline`: No communication for threshold period
- `disk_space_low`: Disk free below threshold
- `memory_high`: Memory usage above threshold
- `cpu_high`: CPU load above threshold
- `metric_missing`: Expected metric not received
- `custom_alert`: Integration-specific alert

#### Context Object
JSON with alert-specific details:
```json
{
  "metric_name": "disk_free_percent",
  "current_value": 8.5,
  "threshold_value": 10,
  "recorded_at": "2025-11-16T12:34:56Z"
}
```

#### Alert Lifecycle
1. **Triggered**: Alert created when condition detected
2. **Acknowledged**: Admin acknowledges alert (optional)
3. **Resolved**: Condition no longer present (auto or manual)
4. **Pruned**: Deleted after retention period (90 days default)

#### Client Considerations
- Alerts are generated server-side based on metrics
- Client can read alerts via future API endpoint (not yet implemented)
- Consider exposing unresolved alert count as HA entity
- Future: WebSocket notifications for real-time alerts

---

## Communication Patterns

### Pattern 1: Initial Setup & Registration

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

### Pattern 2: Periodic Status & Configuration Check

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

### Pattern 3: Metrics Collection & Submission

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

### Pattern 4: Alert Notification (Future via WebSocket)

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

## Client Implementation Guide

### HACS Integration Structure

Recommended file structure for Home Assistant HACS integration:

```
custom_components/
└── ha_dispatch_client/
    ├── __init__.py                 # Component initialization
    ├── manifest.json               # Integration metadata
    ├── config_flow.py              # Setup flow (UI config)
    ├── const.py                    # Constants and defaults
    ├── coordinator.py              # Data update coordinator
    ├── api_client.py               # API communication layer
    ├── sensor.py                   # Sensor entities
    ├── binary_sensor.py            # Binary sensor entities
    ├── switch.py                   # Switch entities (optional)
    ├── services.yaml               # Service definitions (optional)
    ├── strings.json                # UI translations
    ├── translations/               # Localization files
    │   ├── en.json
    │   └── ...
    └── README.md                   # Documentation
```

---

### manifest.json

```json
{
  "domain": "ha_dispatch_client",
  "name": "HA Dispatch Client",
  "version": "1.0.0",
  "documentation": "https://github.com/yourusername/ha-dispatch-client",
  "issue_tracker": "https://github.com/yourusername/ha-dispatch-client/issues",
  "requirements": [
    "aiohttp>=3.8.0",
    "psutil>=5.9.0"
  ],
  "dependencies": [],
  "codeowners": ["@yourusername"],
  "config_flow": true,
  "iot_class": "cloud_polling",
  "quality_scale": "silver"
}
```

**Key Points**:
- `config_flow: true`: Enables UI-based configuration
- `iot_class: "cloud_polling"`: Indicates polling communication
- `requirements`: Python packages (installable via pip)
- `quality_scale`: Aim for "gold" or "silver" for HACS

---

### const.py

```python
"""Constants for HA Dispatch Client integration."""

DOMAIN = "ha_dispatch_client"

# Configuration keys
CONF_SERVER_URL = "server_url"
CONF_INSTALLATION_ID = "installation_id"
CONF_ACCESS_TOKEN = "access_token"
CONF_CLIENT_ID = "client_id"

# Default values
DEFAULT_SCAN_INTERVAL = 60  # seconds
DEFAULT_NAME = "HA Dispatch"

# API endpoints
API_REGISTER = "/api/v1/installations/register"
API_STATUS = "/api/v1/installations/{installation_id}/status"
API_CONFIG = "/api/v1/installations/{installation_id}/config"
API_METRICS = "/api/v1/installations/{installation_id}/metrics"
API_METRICS_BATCH = "/api/v1/installations/{installation_id}/metrics/batch"

# Entity keys
SENSOR_STATUS = "status"
SENSOR_CONFIG_VERSION = "config_version"
SENSOR_CPU_LOAD = "cpu_load"
SENSOR_MEMORY_USED = "memory_used"
SENSOR_DISK_FREE = "disk_free"
BINARY_SENSOR_ONLINE = "online"

# Attribute keys
ATTR_INSTALLATION_ID = "installation_id"
ATTR_LAST_SEEN = "last_seen_at"
ATTR_HA_VERSION = "ha_version"
ATTR_CONFIG_VERSION = "config_version"
ATTR_POLL_INTERVAL = "poll_interval_seconds"
```

---

### config_flow.py

```python
"""Config flow for HA Dispatch Client."""
import logging
import uuid
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import aiohttp_client
import aiohttp

from .const import DOMAIN, CONF_SERVER_URL, CONF_INSTALLATION_ID, CONF_ACCESS_TOKEN, CONF_CLIENT_ID
from .api_client import HADispatchApiClient

_LOGGER = logging.getLogger(__name__)

class HADispatchConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HA Dispatch Client."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            # Validate server URL and attempt registration
            try:
                # Create API client
                session = aiohttp_client.async_get_clientsession(self.hass)
                client = HADispatchApiClient(
                    session=session,
                    server_url=user_input[CONF_SERVER_URL]
                )

                # Generate client ID
                client_id = str(uuid.uuid4())

                # Attempt registration
                hostname = user_input.get(CONF_NAME, self.hass.config.location_name)
                registration_data = await client.register_installation(
                    client_id=client_id,
                    hostname=hostname,
                    name=user_input.get(CONF_NAME),
                )

                # Store configuration
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME, "HA Dispatch"),
                    data={
                        CONF_SERVER_URL: user_input[CONF_SERVER_URL],
                        CONF_CLIENT_ID: client_id,
                        CONF_INSTALLATION_ID: registration_data["installation_id"],
                        CONF_ACCESS_TOKEN: registration_data["access_token"],
                    },
                )

            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            except Exception as e:
                _LOGGER.exception("Unexpected error during registration: %s", e)
                errors["base"] = "unknown"

        # Show form
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_SERVER_URL, default="http://localhost:8080"): str,
                vol.Optional(CONF_NAME, default=self.hass.config.location_name): str,
            }),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return HADispatchOptionsFlowHandler(config_entry)


class HADispatchOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                # Add configurable options here
                # For example: scan interval, enable/disable metrics, etc.
            }),
        )
```

---

### api_client.py

```python
"""API Client for HA Dispatch Server."""
import logging
from typing import Any, Dict, Optional
import aiohttp
from datetime import datetime, timezone

_LOGGER = logging.getLogger(__name__)

class HADispatchApiClient:
    """HA Dispatch API Client."""

    def __init__(self, session: aiohttp.ClientSession, server_url: str, token: Optional[str] = None):
        """Initialize API client."""
        self.session = session
        self.server_url = server_url.rstrip('/')
        self.token = token

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers."""
        headers = {
            "Content-Type": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def register_installation(
        self,
        client_id: str,
        hostname: str,
        name: Optional[str] = None,
        ha_version: Optional[str] = None,
        os_info: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register installation with server."""
        url = f"{self.server_url}/api/v1/installations/register"
        data = {
            "client_id": client_id,
            "hostname": hostname,
            "name": name or hostname,
            "ha_version": ha_version,
            "os_info": os_info,
        }

        async with self.session.post(url, json=data, headers=self._get_headers()) as response:
            response.raise_for_status()
            return await response.json()

    async def report_status(
        self,
        installation_id: str,
        ha_version: Optional[str] = None,
        os_info: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Report installation status."""
        url = f"{self.server_url}/api/v1/installations/{installation_id}/status"
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ha_version": ha_version,
            "os_info": os_info,
        }

        async with self.session.post(url, json=data, headers=self._get_headers()) as response:
            response.raise_for_status()
            return await response.json()

    async def fetch_configuration(
        self,
        installation_id: str,
        current_version: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Fetch configuration from server."""
        url = f"{self.server_url}/api/v1/installations/{installation_id}/config"
        params = {"current_version": current_version}

        async with self.session.get(url, params=params, headers=self._get_headers()) as response:
            if response.status == 204:
                return None  # No update
            response.raise_for_status()
            return await response.json()

    async def submit_metrics(
        self,
        installation_id: str,
        metrics: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Submit metrics to server."""
        url = f"{self.server_url}/api/v1/installations/{installation_id}/metrics"
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **metrics,
        }

        async with self.session.post(url, json=data, headers=self._get_headers()) as response:
            response.raise_for_status()
            return await response.json()

    async def submit_metrics_batch(
        self,
        installation_id: str,
        metrics_list: list,
    ) -> Dict[str, Any]:
        """Submit batch of metrics to server."""
        url = f"{self.server_url}/api/v1/installations/{installation_id}/metrics/batch"
        data = {"metrics": metrics_list}

        async with self.session.post(url, json=data, headers=self._get_headers()) as response:
            response.raise_for_status()
            return await response.json()
```

---

### coordinator.py

```python
"""DataUpdateCoordinator for HA Dispatch Client."""
import logging
from datetime import timedelta
import psutil
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.const import __version__ as HA_VERSION

from .api_client import HADispatchApiClient
from .const import DOMAIN, DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

class HADispatchCoordinator(DataUpdateCoordinator):
    """Coordinator to manage data updates."""

    def __init__(self, hass: HomeAssistant, api_client: HADispatchApiClient, installation_id: str):
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api_client = api_client
        self.installation_id = installation_id
        self.config_version = 0
        self.poll_interval = DEFAULT_SCAN_INTERVAL

    async def _async_update_data(self):
        """Fetch data from API."""
        try:
            # Report status
            status_data = await self.api_client.report_status(
                installation_id=self.installation_id,
                ha_version=HA_VERSION,
                os_info=self._get_os_info(),
            )

            # Check for configuration updates
            if status_data.get("config_version", 0) != self.config_version:
                config_data = await self.api_client.fetch_configuration(
                    installation_id=self.installation_id,
                    current_version=self.config_version,
                )
                if config_data:
                    await self._apply_configuration(config_data)

            # Collect and submit metrics
            metrics = self._collect_metrics()
            await self.api_client.submit_metrics(
                installation_id=self.installation_id,
                metrics=metrics,
            )

            # Return combined data for sensors
            return {
                "status": status_data.get("installation_status"),
                "config_version": status_data.get("config_version"),
                "metrics": metrics,
            }

        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}")

    async def _apply_configuration(self, config_data):
        """Apply configuration from server."""
        try:
            self.config_version = config_data["config_version"]
            desired_state = config_data.get("desired_state", {})

            # Update poll interval if changed
            new_interval = desired_state.get("report_interval", DEFAULT_SCAN_INTERVAL)
            if new_interval != self.poll_interval:
                self.poll_interval = new_interval
                self.update_interval = timedelta(seconds=new_interval)
                _LOGGER.info("Updated poll interval to %s seconds", new_interval)

            # Apply other configuration
            # (thresholds, features, etc.)
            _LOGGER.info("Applied configuration version %s", self.config_version)

        except Exception as err:
            _LOGGER.error("Error applying configuration: %s", err)

    def _collect_metrics(self) -> dict:
        """Collect system metrics."""
        try:
            cpu_load = psutil.getloadavg()
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            boot_time = psutil.boot_time()
            uptime = self.hass.loop.time() - boot_time

            return {
                "cpu_load_1m": cpu_load[0],
                "cpu_load_5m": cpu_load[1],
                "cpu_load_15m": cpu_load[2],
                "memory_used_percent": memory.percent,
                "memory_used_mb": memory.used / (1024 * 1024),
                "disk_used_percent": disk.percent,
                "disk_free_percent": 100 - disk.percent,
                "disk_free_mb": disk.free / (1024 * 1024),
                "uptime_seconds": int(uptime),
                "warnings": self._check_warnings(memory, disk),
            }
        except Exception as err:
            _LOGGER.error("Error collecting metrics: %s", err)
            return {}

    def _check_warnings(self, memory, disk) -> list:
        """Check for warning conditions."""
        warnings = []
        if disk.percent > 90:
            warnings.append("low_disk")
        if memory.percent > 90:
            warnings.append("high_memory")
        return warnings

    def _get_os_info(self) -> str:
        """Get OS information."""
        try:
            import platform
            return f"{platform.system()} {platform.release()}"
        except:
            return "Unknown"
```

---

### __init__.py

```python
"""HA Dispatch Client integration."""
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client

from .const import DOMAIN, CONF_SERVER_URL, CONF_INSTALLATION_ID, CONF_ACCESS_TOKEN
from .api_client import HADispatchApiClient
from .coordinator import HADispatchCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HA Dispatch Client from a config entry."""
    # Create API client
    session = aiohttp_client.async_get_clientsession(hass)
    api_client = HADispatchApiClient(
        session=session,
        server_url=entry.data[CONF_SERVER_URL],
        token=entry.data[CONF_ACCESS_TOKEN],
    )

    # Create coordinator
    coordinator = HADispatchCoordinator(
        hass=hass,
        api_client=api_client,
        installation_id=entry.data[CONF_INSTALLATION_ID],
    )

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Forward to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Remove coordinator
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
```

---

### sensor.py

```python
"""Sensor platform for HA Dispatch Client."""
import logging
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import PERCENTAGE, UnitOfInformation

from .const import DOMAIN, ATTR_INSTALLATION_ID
from .coordinator import HADispatchCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        HADispatchStatusSensor(coordinator, entry),
        HADispatchCPULoadSensor(coordinator, entry),
        HADispatchMemorySensor(coordinator, entry),
        HADispatchDiskSensor(coordinator, entry),
        HADispatchUptimeSensor(coordinator, entry),
    ]

    async_add_entities(entities)


class HADispatchSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for HA Dispatch sensors."""

    def __init__(self, coordinator: HADispatchCoordinator, entry: ConfigEntry):
        """Initialize sensor."""
        super().__init__(coordinator)
        self.entry = entry

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": "HA Dispatch Client",
            "manufacturer": "HA Dispatch",
            "model": "Client Integration",
        }


class HADispatchStatusSensor(HADispatchSensorBase):
    """Status sensor."""

    @property
    def unique_id(self):
        """Return unique ID."""
        return f"{self.entry.entry_id}_status"

    @property
    def name(self):
        """Return name."""
        return "HA Dispatch Status"

    @property
    def state(self):
        """Return state."""
        return self.coordinator.data.get("status")

    @property
    def extra_state_attributes(self):
        """Return attributes."""
        return {
            "config_version": self.coordinator.data.get("config_version"),
            ATTR_INSTALLATION_ID: self.coordinator.installation_id,
        }


class HADispatchCPULoadSensor(HADispatchSensorBase):
    """CPU load sensor."""

    @property
    def unique_id(self):
        """Return unique ID."""
        return f"{self.entry.entry_id}_cpu_load"

    @property
    def name(self):
        """Return name."""
        return "HA Dispatch CPU Load"

    @property
    def state(self):
        """Return state."""
        metrics = self.coordinator.data.get("metrics", {})
        return metrics.get("cpu_load_1m")

    @property
    def unit_of_measurement(self):
        """Return unit."""
        return "load"

    @property
    def state_class(self):
        """Return state class."""
        return SensorStateClass.MEASUREMENT


class HADispatchMemorySensor(HADispatchSensorBase):
    """Memory usage sensor."""

    @property
    def unique_id(self):
        """Return unique ID."""
        return f"{self.entry.entry_id}_memory_used"

    @property
    def name(self):
        """Return name."""
        return "HA Dispatch Memory Used"

    @property
    def state(self):
        """Return state."""
        metrics = self.coordinator.data.get("metrics", {})
        return metrics.get("memory_used_percent")

    @property
    def unit_of_measurement(self):
        """Return unit."""
        return PERCENTAGE

    @property
    def state_class(self):
        """Return state class."""
        return SensorStateClass.MEASUREMENT


class HADispatchDiskSensor(HADispatchSensorBase):
    """Disk free sensor."""

    @property
    def unique_id(self):
        """Return unique ID."""
        return f"{self.entry.entry_id}_disk_free"

    @property
    def name(self):
        """Return name."""
        return "HA Dispatch Disk Free"

    @property
    def state(self):
        """Return state."""
        metrics = self.coordinator.data.get("metrics", {})
        return metrics.get("disk_free_percent")

    @property
    def unit_of_measurement(self):
        """Return unit."""
        return PERCENTAGE

    @property
    def state_class(self):
        """Return state class."""
        return SensorStateClass.MEASUREMENT


class HADispatchUptimeSensor(HADispatchSensorBase):
    """Uptime sensor."""

    @property
    def unique_id(self):
        """Return unique ID."""
        return f"{self.entry.entry_id}_uptime"

    @property
    def name(self):
        """Return name."""
        return "HA Dispatch Uptime"

    @property
    def state(self):
        """Return state."""
        metrics = self.coordinator.data.get("metrics", {})
        uptime_seconds = metrics.get("uptime_seconds", 0)
        # Convert to days for display
        return round(uptime_seconds / 86400, 2)

    @property
    def unit_of_measurement(self):
        """Return unit."""
        return "days"

    @property
    def state_class(self):
        """Return state class."""
        return SensorStateClass.TOTAL_INCREASING
```

---

## Configuration Management

### Configuration Flow

1. **Server Pushes Config**: Admin updates configuration in Filament UI
2. **Version Incremented**: `config_version` incremented on server
3. **Client Detects Change**: Status endpoint returns new `config_version`
4. **Client Fetches Config**: GET request to config endpoint
5. **Client Applies Config**: Update local behavior based on `desired_state`
6. **Client Confirms**: Store new `config_version` locally

### Configuration Parameters

#### Core Parameters
- `report_interval`: Seconds between status/metric reports
- `poll_interval_seconds`: Returned in registration/status responses

#### Threshold Parameters
```json
{
  "thresholds": {
    "disk_free_percent_warn": 10,
    "cpu_load_warn": 80,
    "memory_used_percent_warn": 90
  }
}
```

**Client Usage**: 
- Use for local warning detection
- Include in `warnings` array when submitting metrics
- Display in entity attributes for user visibility

#### Feature Flags
```json
{
  "features": {
    "metrics_enabled": true,
    "alerts_enabled": true,
    "debug_mode": false
  }
}
```

**Client Usage**:
- `metrics_enabled`: Enable/disable metrics collection
- `alerts_enabled`: Enable/disable local alert processing
- `debug_mode`: Enable verbose logging

### Custom Configuration

Administrators can add custom configuration in `desired_state`:

```json
{
  "custom_settings": {
    "enable_temperature_monitoring": true,
    "temperature_threshold": 60,
    "custom_metrics": ["temperature", "fan_speed"]
  }
}
```

**Client Implementation**:
- Parse `custom_settings` object
- Implement custom behavior based on flags
- Use for feature toggles and integration-specific settings

---

## Metrics Collection

### Required Metrics

All metrics are optional but recommended for full functionality:

#### CPU Metrics
- **cpu_load_1m**: 1-minute load average
- **cpu_load_5m**: 5-minute load average
- **cpu_load_15m**: 15-minute load average

**Collection Method** (Linux):
```python
import psutil
cpu_load = psutil.getloadavg()
# Returns tuple: (1min, 5min, 15min)
```

#### Memory Metrics
- **memory_used_percent**: Percentage of memory used (0-100)
- **memory_used_mb**: Megabytes of memory used

**Collection Method**:
```python
import psutil
memory = psutil.virtual_memory()
memory_used_percent = memory.percent
memory_used_mb = memory.used / (1024 * 1024)
```

#### Disk Metrics
- **disk_used_percent**: Percentage of disk used (0-100)
- **disk_free_percent**: Percentage of disk free (0-100)
- **disk_free_mb**: Megabytes of disk free

**Collection Method**:
```python
import psutil
disk = psutil.disk_usage('/')
disk_used_percent = disk.percent
disk_free_percent = 100 - disk.percent
disk_free_mb = disk.free / (1024 * 1024)
```

#### Uptime Metric
- **uptime_seconds**: System uptime in seconds

**Collection Method**:
```python
import psutil
import time
boot_time = psutil.boot_time()
uptime_seconds = int(time.time() - boot_time)
```

### Optional Metrics

#### Warnings Array
Array of string identifiers for detected issues:
```python
warnings = []
if disk.percent > 90:
    warnings.append("low_disk")
if memory.percent > 90:
    warnings.append("high_memory")
if cpu_load[0] > 5.0:
    warnings.append("high_cpu")
```

#### Additional Metrics
Custom JSON object for integration-specific metrics:
```python
additional_metrics = {
    "ha_database_size_mb": get_database_size(),
    "addon_count": count_addons(),
    "entity_count": count_entities(),
    "temperature_celsius": get_cpu_temperature(),
}
```

### Metric Submission Strategy

#### Normal Operation
- Collect metrics every `report_interval` seconds
- Submit immediately via single metric endpoint
- Use async/non-blocking HTTP requests
- Don't block Home Assistant event loop

#### Offline Buffering
- Buffer up to 1000 metrics in local storage
- Submit via batch endpoint when reconnected
- Clear buffer after successful submission
- Discard oldest if buffer full

#### Error Handling
- If collection fails, send available metrics
- If submission fails, buffer for next interval
- Log errors but don't crash integration
- Implement exponential backoff for repeated failures

---

## Alert Handling

### Server-Side Alert Generation

Alerts are generated server-side based on:
- Metric threshold violations
- Missing metrics (installation offline)
- Configuration errors
- Custom alert rules (future)

### Alert Types

#### Installation Offline
- **Trigger**: No status report for 5+ minutes (configurable)
- **Severity**: warning → critical (after 15 minutes)
- **Resolution**: Automatic when status received

#### Disk Space Low
- **Trigger**: `disk_free_percent` < threshold
- **Severity**: warning (< 10%) → critical (< 5%)
- **Resolution**: Manual or automatic when above threshold

#### Memory High
- **Trigger**: `memory_used_percent` > threshold
- **Severity**: warning (> 90%) → critical (> 95%)
- **Resolution**: Automatic when below threshold

#### CPU High
- **Trigger**: `cpu_load_1m` > threshold
- **Severity**: warning (> 80%) → critical (> 95%)
- **Resolution**: Automatic when below threshold

### Client Alert Handling (Future)

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

## WebSocket Integration

### WebSocket Server

- **URL**: `ws(s)://server:6001/app/{app_key}`
- **Protocol**: Laravel WebSockets (Pusher protocol)
- **Port**: 6001 (default)

### Channel Subscription

#### Installation-Specific Channel
```javascript
channel = `installations.${installation_id}`
```

**Events**:
- `InstallationStatusUpdated`: Status changed (online/offline/warning)
- `ConfigurationChanged`: New configuration version available
- `AlertCreated`: New alert generated for this installation
- `AlertResolved`: Alert resolved

#### Global Admin Channel (Future)
```javascript
channel = `admin`
```

**Events**:
- `InstallationRegistered`: New installation registered
- `SystemAlert`: System-wide notification

### WebSocket Implementation (Python)

**Note**: WebSocket integration is optional. Polling-based approach is fully functional.

```python
import asyncio
import websockets
import json

async def connect_websocket(server_url, installation_id):
    """Connect to WebSocket server."""
    ws_url = server_url.replace('http', 'ws') + f':6001/app/ha-dispatch'
    
    async with websockets.connect(ws_url) as websocket:
        # Subscribe to channel
        subscribe_message = {
            "event": "pusher:subscribe",
            "data": {
                "channel": f"installations.{installation_id}"
            }
        }
        await websocket.send(json.dumps(subscribe_message))
        
        # Listen for events
        async for message in websocket:
            data = json.loads(message)
            await handle_websocket_event(data)

async def handle_websocket_event(data):
    """Handle incoming WebSocket event."""
    event = data.get('event')
    
    if event == 'ConfigurationChanged':
        # Fetch new configuration
        await fetch_configuration()
    elif event == 'AlertCreated':
        # Show notification
        await create_notification(data['data'])
    elif event == 'InstallationStatusUpdated':
        # Update entity state
        await update_status(data['data'])
```

---

## Error Handling

### HTTP Status Codes

#### 200 OK
- **Meaning**: Request successful
- **Action**: Process response data

#### 201 Created
- **Meaning**: Resource created (registration, metric submission)
- **Action**: Process response, store IDs

#### 204 No Content
- **Meaning**: No update available (configuration fetch)
- **Action**: No action needed, current version is latest

#### 401 Unauthorized
- **Meaning**: Invalid or missing token
- **Action**: Prompt user to reconfigure integration, may need to re-register

#### 403 Forbidden
- **Meaning**: Token valid but access denied (inactive installation)
- **Action**: Notify user, check server admin panel

#### 422 Unprocessable Entity
- **Meaning**: Validation failed
- **Action**: Log validation errors, fix request format

#### 429 Too Many Requests
- **Meaning**: Rate limit exceeded
- **Action**: Respect Retry-After header, reduce request frequency

#### 500 Internal Server Error
- **Meaning**: Server error
- **Action**: Retry with exponential backoff, log for debugging

#### 503 Service Unavailable
- **Meaning**: Server temporarily unavailable
- **Action**: Retry with exponential backoff

### Network Errors

#### Connection Timeout
```python
try:
    response = await session.post(url, json=data, timeout=10)
except asyncio.TimeoutError:
    # Log error, retry on next interval
    _LOGGER.warning("Request timeout, will retry")
```

#### Connection Refused
```python
except aiohttp.ClientConnectorError:
    # Server unreachable, buffer metrics if possible
    _LOGGER.error("Cannot connect to server")
```

#### SSL Certificate Error
```python
except aiohttp.ClientSSLError:
    # Invalid certificate, warn user
    _LOGGER.error("SSL certificate validation failed")
```

### Retry Strategy

#### Exponential Backoff
```python
max_retries = 5
base_delay = 2  # seconds

for attempt in range(max_retries):
    try:
        response = await make_request()
        break
    except Exception as e:
        if attempt < max_retries - 1:
            delay = base_delay * (2 ** attempt)  # 2, 4, 8, 16, 32 seconds
            await asyncio.sleep(delay)
        else:
            _LOGGER.error("Max retries exceeded")
```

#### Jitter
Add randomness to prevent thundering herd:
```python
import random
delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
```

---

## Testing Strategy

### Unit Tests

#### API Client Tests
```python
import pytest
from unittest.mock import AsyncMock, patch
from .api_client import HADispatchApiClient

@pytest.mark.asyncio
async def test_registration():
    """Test installation registration."""
    session = AsyncMock()
    client = HADispatchApiClient(session, "http://test.com")
    
    # Mock response
    session.post.return_value.__aenter__.return_value.json = AsyncMock(
        return_value={
            "installation_id": "123",
            "access_token": "token123",
        }
    )
    
    result = await client.register_installation(
        client_id="test-uuid",
        hostname="test-host",
    )
    
    assert result["installation_id"] == "123"
    assert result["access_token"] == "token123"
```

#### Coordinator Tests
```python
@pytest.mark.asyncio
async def test_coordinator_update():
    """Test data coordinator update."""
    hass = Mock()
    api_client = AsyncMock()
    coordinator = HADispatchCoordinator(hass, api_client, "123")
    
    # Mock API responses
    api_client.report_status.return_value = {
        "installation_status": "online",
        "config_version": 1,
    }
    
    data = await coordinator._async_update_data()
    
    assert data["status"] == "online"
    assert data["config_version"] == 1
```

### Integration Tests

#### Config Flow Tests
```python
from homeassistant import config_entries
from .const import DOMAIN

async def test_config_flow(hass):
    """Test configuration flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    
    # Submit form
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "server_url": "http://test.com",
            "name": "Test Installation",
        },
    )
    
    assert result["type"] == "create_entry"
```

### Manual Testing Checklist

- [ ] Initial setup via UI config flow
- [ ] Registration with server succeeds
- [ ] Status reports sent every interval
- [ ] Configuration updates applied
- [ ] Metrics collected and submitted
- [ ] Sensors display correct values
- [ ] Offline buffering works
- [ ] Network error handling graceful
- [ ] Re-authentication on token expiry
- [ ] Uninstall cleans up properly

### Server-Side Testing

Test integration against actual HA Dispatch server:

1. **Deploy server**: Use Docker Compose setup
2. **Configure server**: Access admin panel at http://localhost:8080/admin
3. **Install client**: Add to Home Assistant via HACS
4. **Configure client**: Use server URL in config flow
5. **Monitor**: Check server admin panel for installation
6. **Verify metrics**: Confirm metrics appear in server dashboard
7. **Test alerts**: Trigger threshold violation, verify alert generation
8. **Test configuration**: Update config in admin panel, verify client applies

---

## Deployment & Distribution

### HACS Repository Structure

```
ha-dispatch-client/
├── custom_components/
│   └── ha_dispatch_client/
│       ├── __init__.py
│       ├── manifest.json
│       ├── config_flow.py
│       └── ... (all integration files)
├── .github/
│   └── workflows/
│       ├── validate.yml      # HACS validation
│       └── release.yml       # Release automation
├── README.md
├── LICENSE
├── hacs.json                 # HACS metadata
└── info.md                   # HACS store page
```

### hacs.json

```json
{
  "name": "HA Dispatch Client",
  "hacs": "1.6.0",
  "domains": ["sensor", "binary_sensor"],
  "iot_class": "Cloud Polling",
  "homeassistant": "2024.1.0"
}
```

### info.md

```markdown
# HA Dispatch Client

Connects your Home Assistant installation to HA Dispatch central management server.

## Features

- Automatic registration with central server
- Periodic status reporting
- System metrics collection (CPU, memory, disk)
- Remote configuration management
- Alert integration
- Secure token-based authentication

## Configuration

1. Add this repository to HACS
2. Install "HA Dispatch Client" integration
3. Go to Settings → Devices & Services → Add Integration
4. Search for "HA Dispatch"
5. Enter your HA Dispatch server URL
6. Integration will automatically register and start reporting

## Requirements

- HA Dispatch server instance
- Home Assistant 2024.1.0 or newer
- Python packages: aiohttp, psutil
```

### Release Process

1. **Version Bump**: Update `manifest.json` version
2. **Changelog**: Document changes in README
3. **Git Tag**: Create version tag (e.g., `v1.0.0`)
4. **GitHub Release**: Create release with tag
5. **HACS Discovery**: Repository will appear in HACS default store (after validation)

### User Installation

#### Via HACS
1. Open HACS in Home Assistant
2. Go to Integrations
3. Search for "HA Dispatch Client"
4. Click Install
5. Restart Home Assistant
6. Go to Settings → Devices & Services
7. Click "Add Integration"
8. Search for "HA Dispatch"
9. Follow configuration flow

#### Manual Installation
1. Download repository
2. Copy `custom_components/ha_dispatch_client` to `config/custom_components/`
3. Restart Home Assistant
4. Follow configuration flow

---

## Examples & Code Samples

### Complete Minimal Client (Python)

```python
"""Minimal HA Dispatch client example."""
import asyncio
import aiohttp
import uuid
import psutil
from datetime import datetime, timezone

class MinimalHADispatchClient:
    """Minimal client implementation."""
    
    def __init__(self, server_url):
        """Initialize client."""
        self.server_url = server_url.rstrip('/')
        self.token = None
        self.installation_id = None
        self.running = False
    
    async def register(self):
        """Register with server."""
        client_id = str(uuid.uuid4())
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.server_url}/api/v1/installations/register",
                json={
                    "client_id": client_id,
                    "hostname": "test-ha",
                    "name": "Test Installation",
                }
            ) as resp:
                data = await resp.json()
                self.installation_id = data["installation_id"]
                self.token = data["access_token"]
                print(f"Registered: {self.installation_id}")
    
    async def report_loop(self):
        """Main reporting loop."""
        headers = {"Authorization": f"Bearer {self.token}"}
        
        async with aiohttp.ClientSession(headers=headers) as session:
            while self.running:
                # Collect metrics
                cpu = psutil.getloadavg()
                mem = psutil.virtual_memory()
                disk = psutil.disk_usage('/')
                
                # Submit metrics
                try:
                    async with session.post(
                        f"{self.server_url}/api/v1/installations/{self.installation_id}/metrics",
                        json={
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "cpu_load_1m": cpu[0],
                            "memory_used_percent": mem.percent,
                            "disk_free_percent": 100 - disk.percent,
                        }
                    ) as resp:
                        if resp.status == 201:
                            print(f"Metrics sent: CPU={cpu[0]:.2f}, MEM={mem.percent:.1f}%")
                except Exception as e:
                    print(f"Error: {e}")
                
                await asyncio.sleep(60)
    
    async def run(self):
        """Run client."""
        await self.register()
        self.running = True
        await self.report_loop()

# Usage
async def main():
    client = MinimalHADispatchClient("http://localhost:8080")
    await client.run()

if __name__ == "__main__":
    asyncio.run(main())
```

### Configuration Application Example

```python
def apply_configuration(config_data):
    """Apply configuration from server."""
    desired_state = config_data.get("desired_state", {})
    
    # Update report interval
    report_interval = desired_state.get("report_interval", 60)
    set_report_interval(report_interval)
    
    # Update thresholds
    thresholds = desired_state.get("thresholds", {})
    update_thresholds(
        cpu_warn=thresholds.get("cpu_load_warn", 80),
        memory_warn=thresholds.get("memory_used_percent_warn", 90),
        disk_warn=thresholds.get("disk_free_percent_warn", 10),
    )
    
    # Update feature flags
    features = desired_state.get("features", {})
    set_feature_enabled("metrics", features.get("metrics_enabled", True))
    set_feature_enabled("alerts", features.get("alerts_enabled", True))
    
    # Process custom settings
    custom = desired_state.get("custom_settings", {})
    for key, value in custom.items():
        apply_custom_setting(key, value)
    
    # Store config version
    store_config_version(config_data["config_version"])
```

---

## Appendix: Server Details

### Default Credentials (Development Only)

**CHANGE THESE IN PRODUCTION!**

| Role | Email | Password |
|------|-------|----------|
| Super Admin | admin@hadispatch.local | password |
| Admin | admin.user@hadispatch.local | password |
| Operator | operator@hadispatch.local | password |
| Viewer | viewer@hadispatch.local | password |

### Admin Panel URLs

- **Dashboard**: http://localhost:8080/admin
- **Installations**: http://localhost:8080/admin/monitoring/installations
- **Alerts**: http://localhost:8080/admin/monitoring/installation-alerts
- **Settings**: http://localhost:8080/admin/system/settings
- **Users**: http://localhost:8080/admin/system/users
- **Roles**: http://localhost:8080/admin/system/shield/roles

### Database Access

```bash
# Via Docker
docker compose exec mysql mysql -u laravel -psecret laravel

# Direct connection
Host: localhost
Port: 3307
Database: laravel
Username: laravel
Password: secret
```

### Useful Artisan Commands

```bash
# Run in PHP container
docker compose exec php php artisan <command>

# Clear caches
php artisan optimize:clear
php artisan settings:clear-cache
php artisan permission:cache-reset

# View routes
php artisan route:list

# Run migrations
php artisan migrate

# Seed database
php artisan db:seed

# View jobs
php artisan queue:work --once
```

---

## Summary & Next Steps

### What You Have

✅ **Fully Functional Server**
- Laravel 12 + Filament 4 admin panel
- REST API with token authentication
- Real-time WebSocket server
- Comprehensive settings management
- Role-based access control
- Automated health checks and data pruning

### What You Need to Build

📋 **Client Integration (HACS)**
- Home Assistant custom component
- Config flow for setup
- API client for communication
- Data coordinator for updates
- Sensor entities for display
- Metrics collection and submission
- Configuration management

### Development Approach

1. **Start Simple**: Basic registration and status reporting
2. **Add Metrics**: System metrics collection and submission
3. **Configuration**: Implement config polling and application
4. **Entities**: Create sensor entities for display
5. **Polish**: Error handling, offline buffering, UI improvements
6. **Publish**: Package for HACS and release

### Resources

- **Server Code**: /home/straplocked/Documents/ha-dispatch/
- **Laravel Docs**: https://laravel.com/docs/12.x
- **Filament Docs**: https://filamentphp.com/docs/4.x
- **Home Assistant Docs**: https://developers.home-assistant.io/
- **HACS Docs**: https://hacs.xyz/docs/publish/start

---

**Good luck with your client-side development!** 🚀

This guide should provide everything needed to build a fully functional HACS integration that works seamlessly with your HA Dispatch server.

