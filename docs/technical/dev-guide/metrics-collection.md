<!-- Split from CLIENT_SIDE_DEVELOPMENT_GUIDE.md -->

# Metrics Collection

## Required Metrics

All metrics are optional but recommended for full functionality:

### CPU Metrics
- **cpu_load_1m**: 1-minute load average
- **cpu_load_5m**: 5-minute load average
- **cpu_load_15m**: 15-minute load average

**Collection Method** (Linux):
```python
import psutil
cpu_load = psutil.getloadavg()
# Returns tuple: (1min, 5min, 15min)
```

### Memory Metrics
- **memory_used_percent**: Percentage of memory used (0-100)
- **memory_used_mb**: Megabytes of memory used

**Collection Method**:
```python
import psutil
memory = psutil.virtual_memory()
memory_used_percent = memory.percent
memory_used_mb = memory.used / (1024 * 1024)
```

### Disk Metrics
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

### Uptime Metric
- **uptime_seconds**: System uptime in seconds

**Collection Method**:
```python
import psutil
import time
boot_time = psutil.boot_time()
uptime_seconds = int(time.time() - boot_time)
```

## Optional Metrics

### Warnings Array
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

### Additional Metrics
Custom JSON object for integration-specific metrics:
```python
additional_metrics = {
    "ha_database_size_mb": get_database_size(),
    "addon_count": count_addons(),
    "entity_count": count_entities(),
    "temperature_celsius": get_cpu_temperature(),
}
```

## Metric Submission Strategy

### Normal Operation
- Collect metrics every `report_interval` seconds
- Submit immediately via single metric endpoint
- Use async/non-blocking HTTP requests
- Don't block Home Assistant event loop

### Offline Buffering
- Buffer up to 1000 metrics in local storage
- Submit via batch endpoint when reconnected
- Clear buffer after successful submission
- Discard oldest if buffer full

### Error Handling
- If collection fails, send available metrics
- If submission fails, buffer for next interval
- Log errors but don't crash integration
- Implement exponential backoff for repeated failures

---

## Related Documentation

- [API Reference](../api-reference.md) for the metrics submission endpoints (single and batch)
- [Communication Patterns](communication-patterns.md) for the metrics submission flow and offline buffering pattern
- [Client Implementation](client-implementation.md) for the coordinator's `_collect_metrics` method
- [Configuration Management](configuration.md) for threshold parameters that affect warning detection
- See [Data Models](../data-model/) for the Metric model schema and field ranges
