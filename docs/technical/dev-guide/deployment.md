<!-- Split from CLIENT_SIDE_DEVELOPMENT_GUIDE.md -->

# Deployment & Distribution

## HACS Repository Structure

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

## hacs.json

```json
{
  "name": "HA Dispatch Client",
  "hacs": "1.6.0",
  "domains": ["sensor", "binary_sensor"],
  "iot_class": "Cloud Polling",
  "homeassistant": "2024.1.0"
}
```

## info.md

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
3. Go to Settings > Devices & Services > Add Integration
4. Search for "HA Dispatch"
5. Enter your HA Dispatch server URL
6. Integration will automatically register and start reporting

## Requirements

- HA Dispatch server instance
- Home Assistant 2024.1.0 or newer
- Python packages: aiohttp, psutil
```

## Release Process

1. **Version Bump**: Update `manifest.json` version
2. **Changelog**: Document changes in README
3. **Git Tag**: Create version tag (e.g., `v1.0.0`)
4. **GitHub Release**: Create release with tag
5. **HACS Discovery**: Repository will appear in HACS default store (after validation)

## User Installation

### Via HACS
1. Open HACS in Home Assistant
2. Go to Integrations
3. Search for "HA Dispatch Client"
4. Click Install
5. Restart Home Assistant
6. Go to Settings > Devices & Services
7. Click "Add Integration"
8. Search for "HA Dispatch"
9. Follow configuration flow

### Manual Installation
1. Download repository
2. Copy `custom_components/ha_dispatch_client` to `config/custom_components/`
3. Restart Home Assistant
4. Follow configuration flow

---

## Related Documentation

- [Client Implementation](client-implementation.md) for the integration file structure and code
- [Testing Strategy](testing-strategy.md) for pre-release testing
- [Configuration Management](configuration.md) for post-install configuration flow
