# HA Dispatch Client

A Home Assistant custom integration that connects to your [HA Dispatch server](https://github.com/straplocked/ha-dispatch) for centralized monitoring and management.

**Version:** 1.2.2 | **Platform:** Home Assistant (HACS-compatible) | **Language:** Python 3 (async)

## Features

- Automatic registration with HA Dispatch server
- Periodic status reporting (heartbeat)
- System metrics collection (CPU, memory, disk, uptime)
- Configuration updates from server
- Alert submission and resolution
- 6 services for testing and management
- 3 sensor entities for local monitoring
- UI-based setup via config flow

## Quick Install

```bash
cp -r custom_components/ha_dispatch_client /config/custom_components/
```

Then restart Home Assistant, go to **Settings → Devices & Services → Add Integration**, and search for "HA Dispatch Client".

For detailed instructions, see the [Quickstart Guide](docs/user/quickstart.md).

## Entities

| Entity | Shows |
|--------|-------|
| `sensor.ha_dispatch_status` | Installation status (online/offline/warning) |
| `sensor.ha_dispatch_cpu_load` | 1-minute CPU load average |
| `sensor.ha_dispatch_memory_used` | Memory usage percentage |

## Documentation

All documentation lives in [`docs/`](docs/INDEX.md):

| Audience | Start Here |
|----------|------------|
| **Users** | [Quickstart](docs/user/quickstart.md) · [Installation](docs/user/installation.md) · [Services](docs/user/services-guide.md) · [Troubleshooting](docs/user/troubleshooting.md) |
| **Developers** | [Architecture](docs/technical/architecture.md) · [API Reference](docs/technical/api-reference.md) · [Dev Guide](docs/technical/dev-guide/README.md) |
| **Leadership** | [Executive Summary](docs/leadership/executive-summary.md) · [Capabilities](docs/leadership/capabilities.md) |

## Deploy

```bash
export HA_HOST=homeassistant.local HA_USER=straplocked
./deploy.sh
```

See [Deployment Guide](docs/user/deployment.md) for details.

## Dependencies

- `aiohttp>=3.8.0` — Async HTTP client
- `psutil>=5.9.0` — System metrics collection

## License

[MIT](LICENSE)
