<!-- Split from DATA_MODEL.md -->

# JSON Examples

> See also: [Database Schema](schema.md) | [Data Models & Relationships](relationships.md) | [API Endpoints & Data Flow](api-endpoints.md) | [Business Rules & Constants](business-rules.md)
>
> Back to [Data Model Overview](README.md)

---

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
