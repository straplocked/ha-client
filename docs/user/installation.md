# Installation Guide

This guide covers installation methods, the setup flow, the entities the integration creates, and server requirements.

## Installation Methods

### Method 1: Copy to Home Assistant

1. Copy the `custom_components/ha_dispatch_client` directory to your Home Assistant's `config/custom_components/` directory:

```bash
# If your HA config directory is at /config
cp -r custom_components/ha_dispatch_client /config/custom_components/
```

2. Restart Home Assistant.

### Method 2: Manual Installation

1. SSH or access your Home Assistant instance.
2. Navigate to your config directory (usually `/config`).
3. Create the directory: `mkdir -p custom_components/ha_dispatch_client`
4. Copy all files from this repository's `custom_components/ha_dispatch_client/` to that directory.
5. Restart Home Assistant.

### Method 3: Deploy Script

For remote deployment over SSH, use the unified deploy script:

```bash
./deploy.sh
```

The script auto-detects whether this is a fresh install or an update. See the [Deployment Guide](deployment.md) for full details.

### Verify Installation

After copying files, confirm the following files exist in `custom_components/ha_dispatch_client/`:

- `__init__.py`
- `manifest.json`
- `const.py`
- `config_flow.py`
- `api_client.py`
- `coordinator.py`
- `sensor.py`
- `strings.json`
- `services.yaml`

## Setup Flow

1. Go to **Settings** -> **Devices & Services** -> **Add Integration**.
2. Search for "HA Dispatch Client".
3. Enter your HA Dispatch server URL (e.g., `http://your-server:8080`).
4. Optionally provide a friendly name for this installation.
5. Click **Submit**.

The integration will:
- Generate a unique client ID (UUID).
- Register with your server via the `/api/v1/installations/register` endpoint.
- Start sending status updates and metrics every 60 seconds.

For a detailed walkthrough of what happens during config flow, see the [Configuration Guide](configuration.md).

## Entities

After setup, three new sensor entities are created:

### 1. HA Dispatch Status

- **Entity ID**: `sensor.ha_dispatch_status`
- **Shows**: Current installation status (`online`, `offline`, or `warning`)
- **Attributes**:
  - `config_version` -- Current configuration version from the server
  - `installation_id` -- Server-assigned installation ID

### 2. HA Dispatch CPU Load

- **Entity ID**: `sensor.ha_dispatch_cpu_load`
- **Shows**: 1-minute CPU load average
- **Unit**: load

### 3. HA Dispatch Memory Used

- **Entity ID**: `sensor.ha_dispatch_memory_used`
- **Shows**: Memory usage percentage
- **Unit**: %

### Verifying Entities

Go to **Developer Tools** -> **States** and search for `sensor.ha_dispatch`. All three sensors should show values (not "unavailable"). Example states:

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

## Server Requirements

- Running HA Dispatch server (Laravel 12 + Filament 4)
- Server must be accessible from your Home Assistant instance over the network
- Default server port: 8080
- API endpoints: `/api/v1/installations/*`

### Verifying Server Availability

Test the registration endpoint before installing the client:

```bash
curl -X POST http://your-server:8080/api/v1/installations/register \
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

## Dependencies

The integration requires the following Python packages (both are typically available in Home Assistant by default):

- `aiohttp>=3.8.0` -- Async HTTP client for server communication
- `psutil>=5.9.0` -- System metrics collection (CPU, memory, disk)

## Next Steps

- [Configuration Guide](configuration.md) -- Understand what gets stored and how config updates work
- [Services Guide](services-guide.md) -- Learn the 6 services available for testing and automation
- [Testing Guide](testing.md) -- Validate the full integration end to end
- [Troubleshooting](troubleshooting.md) -- Fix common problems during installation
