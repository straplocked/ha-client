# Testing Guide

This guide walks through end-to-end testing of the HA Dispatch Client integration, from verifying server availability through to configuration updates and error handling.

## Prerequisites

1. **HA Dispatch Server Running**
   - Server accessible at a known URL (e.g., `http://localhost:8080`)
   - Database initialized and migrated
   - API endpoints functional

2. **Home Assistant Instance**
   - Running version 2024.1.0 or newer
   - SSH/terminal access for installation
   - Access to the web UI for configuration

## Step 1: Verify Server Is Running

Test the registration endpoint:

```bash
curl -X POST http://localhost:8080/api/v1/installations/register \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "test-uuid-12345",
    "hostname": "test-host",
    "name": "Test Installation"
  }'
```

Expected response (201 Created):
```json
{
  "installation_id": "1",
  "access_token": "...",
  "poll_interval_seconds": 60,
  "config_version": 1
}
```

If you get an error:
- Check server logs
- Verify the database is initialized
- Ensure the server is running on the correct port

## Step 2: Install Integration

### Option A: Using the Deploy Script

```bash
./deploy.sh
```

See the [Deployment Guide](deployment.md) for details on the deploy script.

### Option B: Manual Copy

```bash
cp -r custom_components/ha_dispatch_client /path/to/ha/config/custom_components/
```

### Verify Installation

Check that all required files are present:

```bash
ls -l /path/to/ha/config/custom_components/ha_dispatch_client/
```

Expected files: `__init__.py`, `manifest.json`, `const.py`, `config_flow.py`, `api_client.py`, `coordinator.py`, `sensor.py`, `strings.json`, `services.yaml`.

## Step 3: Restart Home Assistant

```bash
# Via Home Assistant CLI
ha core restart

# Or via UI
# Settings -> System -> Restart
```

Wait for Home Assistant to fully restart (usually 1-2 minutes).

## Step 4: Check Integration Is Loaded

### Via UI
1. Go to Settings -> Devices & Services
2. Click "+ Add Integration"
3. Search for "dispatch"
4. You should see "HA Dispatch Client"

### Via Logs
```bash
ha core logs | grep ha_dispatch_client
```

There should be no import errors or exceptions.

## Step 5: Configure Integration

1. Click "Add Integration"
2. Search for "HA Dispatch Client"
3. Enter server URL (e.g., `http://192.168.1.100:8080`)
4. Enter installation name (e.g., "Living Room HA")
5. Click "Submit"

### Expected Behavior

**Success:**
- Integration shows "Configuration successful"
- New device appears under Devices & Services
- Three sensors are created

**Failure:**
- "Cannot connect" error: Check server URL and network
- "Unknown error": Check Home Assistant logs for details

### Watch Logs During Setup

```bash
# In one terminal, tail logs
ha core logs -f | grep ha_dispatch

# In another terminal/browser, complete setup
```

Look for:
- "Registering installation with server"
- "Successfully registered installation: X"
- "Fetching data from API"

## Step 6: Verify Entities Created

### Via UI

Settings -> Devices & Services -> HA Dispatch Client -> Device

Should show 3 entities:
1. HA Dispatch Status
2. HA Dispatch CPU Load
3. HA Dispatch Memory Used

### Via Developer Tools

Developer Tools -> States

Search for:
- `sensor.ha_dispatch_status`
- `sensor.ha_dispatch_cpu_load`
- `sensor.ha_dispatch_memory_used`

All should show values (not "unavailable"). Example states:

```yaml
sensor.ha_dispatch_status:
  state: "online"
  attributes:
    config_version: 1
    installation_id: "1"

sensor.ha_dispatch_cpu_load:
  state: "0.75"
  unit_of_measurement: "load"

sensor.ha_dispatch_memory_used:
  state: "42.5"
  unit_of_measurement: "%"
```

## Step 7: Verify Server Receives Data

### Check Server Admin Panel

1. Open server admin: `http://your-server:8080/admin`
2. Log in
3. Go to Monitoring -> Installations
4. Find your installation

Should show:
- Status: Online (green)
- Last Seen: Recent timestamp (less than 2 minutes ago)
- HA Version: Your HA version
- Configuration Version: 1

### Check Server Database (Optional)

```bash
# Connect to server's MySQL
docker compose exec mysql mysql -u laravel -psecret laravel

# Check installations
SELECT id, name, status, last_seen_at FROM installations;

# Check latest metrics
SELECT * FROM installation_metrics ORDER BY recorded_at DESC LIMIT 5;
```

Should show your installation with status "online" and recent metrics.

## Step 8: Test Metrics Collection

Wait 60 seconds (default poll interval), then check:

### In Home Assistant
- Sensors should update with new values
- Check Developer Tools -> States for updated timestamps

### On Server
- Go to Monitoring -> Installations -> Your Installation
- Click "Metrics" tab
- Should see new entries every 60 seconds

Metrics include: CPU load (1m, 5m, 15m), memory used (percent and MB), disk used/free (percent and MB), uptime (seconds).

## Step 9: Test Configuration Updates

### Update Configuration on Server

1. Go to Monitoring -> Installations -> Your Installation
2. Click "Configurations" tab
3. Click "Create Configuration"
4. Edit `desired_state` JSON:

```json
{
  "report_interval": 30,
  "thresholds": {
    "disk_free_percent_warn": 15,
    "cpu_load_warn": 70,
    "memory_used_percent_warn": 85
  },
  "features": {
    "metrics_enabled": true,
    "alerts_enabled": true
  }
}
```

5. Save

### Verify Client Updates

Check Home Assistant logs:
```bash
ha core logs | grep -i "configuration"
```

Should see (within 60 seconds):
- "Received new configuration version: 2"
- "Applied configuration version 2"
- "Updated poll interval to 30 seconds"

After this, metrics should be sent every 30 seconds instead of 60.

## Step 10: Test Services

Use the debug and alert services to validate functionality. See the [Services Guide](services-guide.md) for full details on each service.

### Quick Service Test

```yaml
# Verify connectivity
service: ha_dispatch_client.send_test_metrics
data:
  cpu_load: 50
  memory_used: 60
  disk_free: 40

# Test alert pipeline
service: ha_dispatch_client.trigger_alert
data:
  alert_type: disk_low

# Force an immediate refresh
service: ha_dispatch_client.force_update
data: {}

# Submit a structured alert
service: ha_dispatch_client.submit_alert
data:
  severity: warning
  type: low_disk_space
  title: Test Alert
  message: Testing alert submission

# Resolve the alert
service: ha_dispatch_client.resolve_alert
data:
  type: low_disk_space
```

## Step 11: Test Error Handling

### Test Server Offline

1. Stop the HA Dispatch server
2. Wait 60+ seconds
3. Check Home Assistant logs

Should see:
- "Error communicating with API: ..."
- Integration continues running (no crash)

4. Restart the server
5. Integration should reconnect automatically
6. Check server shows installation back online

### Test Invalid Token

1. In HA config storage, corrupt the access token
2. Restart Home Assistant
3. Should get authentication errors
4. May need to reconfigure the integration

## Step 12: Performance Testing

### Monitor Resource Usage

```bash
# CPU usage
top -b -n 1 | grep python

# Memory usage
ps aux | grep home-assistant
```

Expected impact:
- CPU: less than 1% average
- Memory: less than 50MB

### Check Update Frequency

```bash
ha core logs -f | grep "Submitting metrics"
```

Should see entries every 60 seconds (or at the configured interval).

## Success Criteria

- Integration installs without errors
- Configuration flow completes successfully
- Three sensor entities are created and show values
- Metrics appear on server every 60 seconds
- Installation shows "online" on server
- Configuration updates are received and applied
- All 6 services work as documented
- Integration survives server restarts
- No memory leaks or performance issues
- Logs show regular successful API calls

## Next Steps After Testing

1. **Document issues** -- Note any bugs or unexpected behavior
2. **Test edge cases** -- Network interruptions, invalid data, server maintenance
3. **Set up automations** -- Use the [Alerts Guide](alerts-guide.md) for automated monitoring
4. **Package for HACS** -- If deploying to multiple installations

## Getting Help

- Check logs: `ha core logs | grep ha_dispatch`
- Review server logs: Server admin panel or container logs
- Verify API endpoints: Use `curl` to test directly
- Consult the [Troubleshooting Guide](troubleshooting.md) for common issues
