# HA Dispatch Client - MVP

A minimal viable Home Assistant custom component that connects to your HA Dispatch server for centralized monitoring and management.

## Features

- ✅ Automatic registration with HA Dispatch server
- ✅ Periodic status reporting (heartbeat)
- ✅ System metrics collection (CPU, memory, disk)
- ✅ Configuration updates from server
- ✅ Three sensor entities for monitoring
- ✅ UI-based setup via config flow

## Installation

### Method 1: Copy to Home Assistant

1. Copy the `custom_components/ha_dispatch_client` directory to your Home Assistant's `config/custom_components/` directory:

```bash
# If your HA config directory is at /config
cp -r custom_components/ha_dispatch_client /config/custom_components/
```

2. Restart Home Assistant

### Method 2: Manual Installation

1. SSH or access your Home Assistant instance
2. Navigate to your config directory (usually `/config`)
3. Create the directory: `mkdir -p custom_components/ha_dispatch_client`
4. Copy all files from this repo's `custom_components/ha_dispatch_client/` to that directory
5. Restart Home Assistant

## Setup

1. Go to **Settings** → **Devices & Services** → **Add Integration**
2. Search for "HA Dispatch Client"
3. Enter your HA Dispatch server URL (e.g., `http://your-server:8080`)
4. Optionally, provide a friendly name for this installation
5. Click Submit

The integration will:
- Generate a unique client ID
- Register with your server
- Start sending status updates and metrics every 60 seconds

## Entities

After setup, you'll have three new sensor entities:

### 1. HA Dispatch Status
- **Entity ID**: `sensor.ha_dispatch_status`
- **Shows**: Current installation status (online/offline/warning)
- **Attributes**: 
  - `config_version`: Current configuration version
  - `installation_id`: Server-assigned ID

### 2. HA Dispatch CPU Load
- **Entity ID**: `sensor.ha_dispatch_cpu_load`
- **Shows**: 1-minute CPU load average
- **Unit**: load

### 3. HA Dispatch Memory Used
- **Entity ID**: `sensor.ha_dispatch_memory_used`
- **Shows**: Memory usage percentage
- **Unit**: %

## Server Requirements

- Running HA Dispatch server (Laravel 12 + Filament 4)
- Server must be accessible from your Home Assistant instance
- Default server port: 8080
- API endpoints: `/api/v1/installations/*`

## Configuration

The integration stores:
- Server URL
- Client ID (UUID)
- Installation ID (from server)
- Access token (for authentication)

These are stored securely in Home Assistant's configuration storage.

## Monitoring

### Check Logs

View Home Assistant logs for integration activity:

```bash
# In Home Assistant
Settings → System → Logs

# Or via CLI
ha core logs | grep ha_dispatch_client
```

### Check API Communication

The integration logs:
- Registration attempts
- Status report success/failures
- Metrics submission
- Configuration updates
- Errors and warnings

## Troubleshooting

### Cannot Connect Error

- Verify server URL is correct and accessible
- Check network connectivity between HA and server
- Ensure server is running and API is available
- Test with: `curl http://your-server:8080/api/v1/installations/register`

### Registration Failed

- Check server logs for validation errors
- Ensure server database is initialized
- Verify server is not in maintenance mode

### No Metrics Appearing on Server

- Check Home Assistant logs for submission errors
- Verify access token is valid
- Check server's installations panel to see if installation is online
- Ensure psutil package is available in HA (it should be by default)

### Sensors Show "Unavailable"

- Check if integration is loaded (Settings → Devices & Services)
- Verify coordinator is updating (check logs)
- Restart Home Assistant if needed

## Development

### Project Structure

```
custom_components/ha_dispatch_client/
├── __init__.py           # Integration setup and teardown
├── manifest.json         # Integration metadata
├── const.py             # Constants
├── config_flow.py       # UI configuration flow
├── api_client.py        # API client for server communication
├── coordinator.py       # Data update coordinator
├── sensor.py            # Sensor entities
└── strings.json         # UI text translations
```

### Testing

1. Install in Home Assistant
2. Configure with your server URL
3. Check Developer Tools → States for new entities
4. Monitor logs for successful API calls
5. Verify data appears in server's admin panel

### Dependencies

- `aiohttp>=3.8.0` - Async HTTP client
- `psutil>=5.9.0` - System metrics collection

## MVP Limitations

This is a minimal viable product. The following features are NOT included:

- ❌ Offline metric buffering
- ❌ Batch metric submission
- ❌ WebSocket support for real-time updates
- ❌ Binary sensors or switch entities
- ❌ Advanced configuration options
- ❌ Alert polling/notifications
- ❌ Custom metrics beyond system stats

These can be added in future iterations.

## Next Steps

### For Testing

1. Ensure HA Dispatch server is running
2. Install this integration in Home Assistant
3. Configure with server URL
4. Verify entities appear and update
5. Check server admin panel for installation data

### For Production

1. Test thoroughly in development environment
2. Update manifest.json with real documentation URLs
3. Add proper error handling for edge cases
4. Consider adding offline buffering
5. Add more sensor types if needed
6. Package for HACS distribution

## Support

For issues related to:
- **Server**: Check HA Dispatch server logs and documentation
- **Client**: Check Home Assistant logs and this integration's code
- **API**: Refer to CLIENT_SIDE_DEVELOPMENT_GUIDE.md and DATA_MODEL.md

## License

Private use only (as specified in requirements).

## Version

**1.0.0** - Minimal Viable Product
- Basic registration and status reporting
- System metrics collection
- Three sensor entities
- UI-based configuration

