# System Architecture

## Overview

**HA Dispatch Client** is a Home Assistant custom integration (HACS-compatible) that connects to a centralized **HA Dispatch server** (Laravel 12 + Filament 4) for monitoring, metrics collection, and alert management across multiple Home Assistant instances.

- **Domain:** `ha_dispatch_client`
- **Language:** Python 3 (async-first)
- **Dependencies:** `aiohttp>=3.8.0`, `psutil>=5.9.0`

---

## High-Level Architecture

The system uses a **controller-agent architecture** where:

- **Server (Controller):** Laravel 12 + Filament 4 application managing all installations
- **Client (Agent):** HACS integration installed on each Home Assistant instance

All communication is **client-initiated** to avoid firewall/NAT issues. The client polls the server at a configurable interval (default 60 seconds).

```
+-------------------------------------------------------------+
|                    HA Dispatch Server                        |
|              (Laravel 12 + Filament 4 + MySQL)              |
|                                                             |
|  +--------------+  +--------------+  +--------------+      |
|  |  REST API    |  |  WebSockets  |  |  Admin Panel |      |
|  |  (Sanctum)   |  |  (Port 6001) |  |  (Filament)  |      |
|  +--------------+  +--------------+  +--------------+      |
|                                                             |
|  +-------------------------------------------------------+  |
|  |         MySQL Database                                 |  |
|  |  - Installations  - Configurations                     |  |
|  |  - Metrics        - Alerts                             |  |
|  |  - Settings       - Users/Roles                        |  |
|  +-------------------------------------------------------+  |
+-------------------------------------------------------------+
                            ^
                            | HTTPS (Port 8080)
                            |
        +-------------------+-------------------+
        |                   |                   |
        v                   v                   v
+---------------+   +---------------+   +---------------+
| Home Assistant|   | Home Assistant|   | Home Assistant|
|  Instance #1  |   |  Instance #2  |   |  Instance #3  |
|               |   |               |   |               |
|  +---------+  |   |  +---------+  |   |  +---------+  |
|  |  HACS   |  |   |  |  HACS   |  |   |  |  HACS   |  |
|  |  Client  |  |   |  |  Client  |  |   |  |  Client  |  |
|  +---------+  |   |  +---------+  |   |  +---------+  |
+---------------+   +---------------+   +---------------+
```

---

## Component Overview

### File Structure

```
custom_components/ha_dispatch_client/
├── __init__.py       # Entry setup, service registration (singleton), platform forwarding
├── api_client.py     # HADispatchApiClient - async HTTP with Bearer auth
├── coordinator.py    # HADispatchCoordinator - DataUpdateCoordinator (60s interval)
├── config_flow.py    # UI config flow - server URL input, auto-registration
├── sensor.py         # 3 CoordinatorEntity sensors (status, cpu_load, memory_used)
├── binary_sensor.py  # Pending remote access consent indicator
├── update.py         # HADispatchUpdateEntity - client version as an HA update entity
├── updater.py        # ClientUpdater - signed self-update (download, verify, swap, restart)
├── health.py         # Home Assistant health signal collection
├── remote_access.py  # HADispatchRemoteAccess - consent surfaces and session state
├── tunnel.py         # HADispatchTunnel - relays authorised requests to the local API
├── repairs.py        # Approve / Deny and End-access repair flows
├── const.py          # DOMAIN, config keys, API endpoints, signing keys, entity/attribute keys
├── manifest.json     # Integration metadata
├── services.yaml     # 9 service definitions with UI fields
└── strings.json      # UI text, translations, and repair flow copy
```

### Component Responsibilities

#### `api_client.py` -- HADispatchApiClient

Async HTTP client providing methods for every server API endpoint:

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `register_installation()` | `POST .../register` | Initial registration |
| `report_status()` | `POST .../{id}/status` | Heartbeat updates |
| `fetch_configuration()` | `GET .../{id}/config` | Configuration polling |
| `submit_metrics()` | `POST .../{id}/metrics` | Single metric submission |
| `submit_metrics_batch()` | `POST .../{id}/metrics/batch` | Batch metrics |
| `submit_alert()` | `POST .../{id}/alerts` | Submit single alert |
| `submit_alerts_batch()` | `POST .../{id}/alerts/batch` | Batch alerts |
| `resolve_alert()` | `POST .../{id}/alerts/{type}/resolve` | Resolve alerts by type |

Uses `aiohttp` sessions from `homeassistant.helpers.aiohttp_client` with Bearer token authentication.

#### `coordinator.py` -- HADispatchCoordinator

Implements the Home Assistant `DataUpdateCoordinator` pattern. Every update cycle (default 60s):

1. Reports status (heartbeat) to the server
2. Checks for configuration updates
3. Collects system metrics via `psutil` (CPU, memory, disk, uptime)
4. Submits metrics to the server

The poll interval can be changed remotely via the server's configuration push.

#### `config_flow.py`

UI-based setup flow (no YAML required):

1. User enters server URL
2. Client generates a UUID `client_id`
3. Client calls `POST /register` on the server
4. Server returns `installation_id` and `access_token`
5. Credentials stored securely in config entry data

#### `sensor.py`

Three `CoordinatorEntity` sensors that read from `self.coordinator.data`:

| Sensor | Entity ID | State | Unit |
|--------|-----------|-------|------|
| Status | `sensor.ha_dispatch_status` | online/offline/warning | -- |
| CPU Load | `sensor.ha_dispatch_cpu_load` | 1-min load average | -- |
| Memory Used | `sensor.ha_dispatch_memory_used` | Usage percentage | % |

All sensors are grouped under a single device entry.

#### `binary_sensor.py`

One entity, `binary_sensor.ha_dispatch_remote_access_requested`, on while
somebody is waiting for the customer to approve remote access. The pending
requests and any live sessions ride along as attributes, so an automation can
route the prompt to a phone or a speaker rather than waiting to be noticed.

#### `remote_access.py`, `tunnel.py`, `repairs.py`

Consent-gated remote access. `remote_access.py` polls for pending requests,
raises the notification and the fixable Repairs issue, reports the decision,
and tracks which sessions are live. `tunnel.py` long-polls for authorised
requests and runs them against the local REST API using a Home Assistant system
user scoped to the session's privilege level. `repairs.py` is the Approve /
Deny dialog and the one-tap off-switch.

Neither enforces scope -- the server does that before anything is queued. See
[Remote Access](remote-access.md).

#### `__init__.py`

Entry point that:
- Creates the API client from stored config entry credentials
- Initializes the coordinator and triggers first refresh
- Registers 9 services using the singleton pattern
- Wires the updater and the remote access manager before the first refresh
- Forwards setup to the sensor, binary_sensor, and update platforms
- Handles teardown on unload

---

## Key Patterns

### DataUpdateCoordinator

The coordinator handles all periodic server communication. Sensors subscribe to coordinator updates and read from `self.coordinator.data` rather than making their own API calls. This ensures a single update cycle per interval rather than one per entity.

```
Coordinator._async_update_data()
  -> report_status()
  -> fetch_configuration()
  -> _collect_metrics()
  -> submit_metrics()
  -> return data dict
       |
       +-> StatusSensor reads data["status"]
       +-> CpuSensor reads data["cpu_load"]
       +-> MemorySensor reads data["memory_used"]
```

### Singleton Service Registration

Services belong to the **domain**, not to individual config entries. The integration uses a singleton guard pattern:

1. `setup_services(hass)` checks `hass.services.has_service(DOMAIN, "send_test_metrics")` before registering
2. Service handlers call `get_coordinator_for_service(hass)` to dynamically resolve the active coordinator
3. `teardown_services(hass)` only removes services when the **last** config entry unloads

This matches the pattern used by professional integrations like `meross_lan`, `mqtt`, and `zha`.

For detailed analysis, see [Service Registration](service-registration.md).

### CoordinatorEntity Sensors

All sensors inherit from `HADispatchSensorBase(CoordinatorEntity, SensorEntity)`:
- State is derived from `self.coordinator.data`
- Entities automatically update when the coordinator completes a cycle
- No direct API calls from sensor entities

### Config Entry Storage

The following values are stored in config entry data (encrypted at rest by HA):
- `server_url` -- Base URL of the HA Dispatch server
- `installation_id` -- Server-assigned installation ID
- `access_token` -- Bearer token for API authentication
- `client_id` -- UUID generated during registration

---

## Data Flow

### Normal Update Cycle (every 60s)

```
+------------------+      +------------------+      +------------------+
| 1. Report Status |----->| 2. Check Config  |----->| 3. Collect       |
|    (heartbeat)   |      |    (poll server)  |      |    Metrics       |
|    POST /status  |      |    GET /config    |      |    (psutil)      |
+------------------+      +------------------+      +------------------+
                                                           |
                                                           v
                                                    +------------------+
                                                    | 4. Submit        |
                                                    |    Metrics       |
                                                    |    POST /metrics |
                                                    +------------------+
                                                           |
                                                           v
                                                    +------------------+
                                                    | 5. Update        |
                                                    |    Sensor States |
                                                    +------------------+
```

### Registration Flow (one-time setup)

```
User enters Server URL in UI
         |
         v
Client generates UUID client_id
         |
         v
POST /api/v1/installations/register
  { server_url, client_id, ha_version, os_info }
         |
         v
Server returns { installation_id, access_token }
         |
         v
Credentials stored in config entry
         |
         v
Coordinator starts periodic updates
```

---

## API Endpoints

All endpoints are under `/api/v1/installations/` and use Bearer token authentication.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `.../register` | Register new installation |
| POST | `.../{id}/status` | Report heartbeat |
| GET  | `.../{id}/config` | Fetch configuration (204 = no update) |
| POST | `.../{id}/metrics` | Submit metrics |
| POST | `.../{id}/metrics/batch` | Batch metrics |
| POST | `.../{id}/alerts` | Submit alert |
| POST | `.../{id}/alerts/batch` | Batch alerts |
| POST | `.../{id}/alerts/{type}/resolve` | Resolve alerts by type |

For full API documentation, see [Alert API](alert-api.md) and [API Reference](api-reference.md).

---

## Services

The integration exposes 9 services via `Developer Tools > Services`:

| Service | Purpose |
|---------|---------|
| `send_test_metrics` | Test connectivity with mock metric values |
| `trigger_alert` | Quick alert testing (disk_low, cpu_high, memory_high, all) |
| `force_update` | Trigger immediate coordinator refresh |
| `send_custom_metric` | Submit arbitrary metric values |
| `submit_alert` | Full alert submission (severity, type, title, message, context) |
| `resolve_alert` | Resolve all unresolved alerts of a given type |
| `install_update` | Install the client release the server is offering |
| `respond_to_access_request` | Approve or decline a remote access request |
| `revoke_access` | End a live remote access session |

For complete service documentation, see [Services](services.md).

---

## Related Documentation

- [Services](services.md) -- Complete service reference with schemas
- [Remote Access](remote-access.md) -- Consent, the tunnel, and the local credential model
- [Alert API](alert-api.md) -- Alert endpoint reference
- [Service Registration](service-registration.md) -- Singleton pattern analysis
- [Data Model](data-model/README.md) -- Database schema and relationships
- [Dev Guide](dev-guide/README.md) -- Full client development guide
