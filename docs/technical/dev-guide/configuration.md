<!-- Split from CLIENT_SIDE_DEVELOPMENT_GUIDE.md -->

# Configuration Management

## Configuration Flow

1. **Server Pushes Config**: Admin updates configuration in Filament UI
2. **Version Incremented**: `config_version` incremented on server
3. **Client Detects Change**: Status endpoint returns new `config_version`
4. **Client Fetches Config**: GET request to config endpoint
5. **Client Applies Config**: Update local behavior based on `desired_state`
6. **Client Confirms**: Store new `config_version` locally

## Configuration Parameters

### Core Parameters
- `report_interval`: Seconds between status/metric reports
- `poll_interval_seconds`: Returned in registration/status responses

### Threshold Parameters
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

### Feature Flags
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

## Custom Configuration

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

## Configuration Application Example

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

## Related Documentation

- [API Reference](../api-reference.md) for the configuration fetch endpoint
- [Communication Patterns](communication-patterns.md) for the configuration polling flow
- [Client Implementation](client-implementation.md) for the coordinator's `_apply_configuration` method
- See [Data Models](../data-model/) for the Configuration model schema
