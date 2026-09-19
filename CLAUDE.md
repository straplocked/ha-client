# CLAUDE.md - HA Dispatch Client

## Project Overview

Home Assistant custom integration that connects to a centralized **HA Dispatch server** (Laravel 12 + Filament 4) for monitoring, metrics collection, and alert management across multiple HA instances.

- **Domain:** `ha_dispatch_client`
- **Version:** 1.5.0 (tracked in `VERSION` file and `manifest.json`)
- **Language:** Python 3 (async-first)
- **Framework:** Home Assistant Custom Integration (HACS-compatible, `hacs.json` at root)
- **Dependencies:** `aiohttp>=3.8.0`, `psutil>=5.9.0`, `cryptography>=41.0.0`

## Architecture

```
custom_components/ha_dispatch_client/
├── __init__.py       # Entry setup, service registration (singleton), platform forwarding
├── api_client.py     # HADispatchApiClient - async HTTP with Bearer auth
├── coordinator.py    # HADispatchCoordinator - DataUpdateCoordinator (60s interval)
├── config_flow.py    # UI config flow - server URL input, auto-registration
├── sensor.py         # 3 CoordinatorEntity sensors (status, cpu_load, memory_used)
├── update.py         # HADispatchUpdateEntity - client version as an HA update entity
├── updater.py        # ClientUpdater - signed self-update (download, verify, swap, restart)
├── health.py         # Home Assistant health signal collection
├── const.py          # DOMAIN, config keys, API endpoints, signing keys, entity/attribute keys
├── manifest.json     # Integration metadata
├── services.yaml     # 7 service definitions with UI fields
└── strings.json      # UI text and translations
```

Release tooling lives in `scripts/`: `release.sh` builds and signs a release
archive, `generate_signing_key.py` creates the Ed25519 keypair.

### Key Patterns

- **DataUpdateCoordinator pattern:** `coordinator.py` handles periodic updates (status, config, metrics)
- **Singleton service registration:** Services registered once per domain, not per config entry. Checked via `hass.services.has_service()` before registering. Torn down only when the last config entry unloads.
- **CoordinatorEntity sensors:** All sensors inherit from `HADispatchSensorBase(CoordinatorEntity, SensorEntity)` and read from `self.coordinator.data`
- **Async everything:** All API calls use `aiohttp` sessions from `homeassistant.helpers.aiohttp_client`
- **Config entry storage:** Server URL, installation_id, access_token, client_id stored in config entry data
- **Signed self-update:** `updater.py` replaces the integration's own files and restarts HA. Releases must carry an Ed25519 signature verified against a key pinned in `const.py::RELEASE_SIGNING_KEYS` — the server never holds that key, so it can decide whether/when to offer an update but never what code runs. That dict ships **empty**, so self-update fails closed until a key is deliberately pinned. Never add a placeholder.

### API Endpoints (server-side)

All under `/api/v1/installations/`:
- `POST .../register` - Register new installation
- `POST .../{id}/status` - Report heartbeat
- `GET  .../{id}/config` - Fetch configuration (204 = no update)
- `POST .../{id}/metrics` - Submit metrics
- `POST .../{id}/metrics/batch` - Batch metrics
- `POST .../{id}/alerts` - Submit alert
- `POST .../{id}/alerts/batch` - Batch alerts
- `POST .../{id}/alerts/{type}/resolve` - Resolve alerts by type
- `POST .../{id}/client-update` - Report a self-update outcome (started/success/failed)

### Services (7 total)

| Service | Purpose |
|---------|---------|
| `send_test_metrics` | Test connectivity with mock metric values |
| `trigger_alert` | Quick alert testing (disk_low, cpu_high, memory_high, all) |
| `force_update` | Trigger immediate coordinator refresh |
| `send_custom_metric` | Submit arbitrary metric values |
| `submit_alert` | Full alert submission (severity, type, title, message, context) |
| `resolve_alert` | Resolve all unresolved alerts of a given type |
| `install_update` | Install the client release the server is offering (restarts HA) |

## Development Commands

### Deploy to Home Assistant

```bash
# Set environment or enter interactively
export HA_HOST=homeassistant.local
export HA_USER=straplocked
export HA_PORT=22

./deploy.sh              # Deploys via SSH+tar, auto-detects install vs update
```

### Version Management

```bash
./bump_version.sh        # Increment version in VERSION file
./check_version.sh       # Compare local vs deployed version
```

### Release (signed archive for self-update + HACS)

```bash
python3 scripts/generate_signing_key.py --key-id hadc-2026-01   # once, offline
```

```bash
./scripts/release.sh --key hadc-signing.key --key-id hadc-2026-01 --publish
```

The private signing key must never touch the Dispatch server -- that separation
is the whole basis of the self-update trust model.

### Testing

Automated tests cover the pure logic (health classification, re-enrolment,
and the whole self-update verification/validation/swap path). Home Assistant is
not installed in the dev environment; `tests/conftest.py` stubs the symbols the
integration imports. Add to that conftest when new HA imports appear.

```bash
python3 -m pytest tests/ -q
```

Note `api_client.py` imports `aiohttp` at module scope, so `test_reregistration.py`
needs aiohttp importable even though the HTTP calls are faked.

Everything touching a live Home Assistant is still manual:

```bash
# Check HA logs after deployment
ssh user@ha-host 'ha core logs | grep ha_dispatch_client'

# Test services via Developer Tools > Services in HA UI
# Verify sensors via Developer Tools > States: sensor.ha_dispatch_*
```

### Service Verification Scripts

```bash
./CHECK_SERVICES.sh          # Test service endpoints
./CHECK_SERVICES_DIRECT.sh   # Direct service verification
```

## Code Conventions

- **Classes:** PascalCase with `HADispatch` prefix (e.g., `HADispatchApiClient`, `HADispatchCoordinator`)
- **Functions:** snake_case; async functions use `async_` prefix per HA convention
- **Constants:** UPPER_SNAKE_CASE in `const.py` (e.g., `CONF_SERVER_URL`, `API_REGISTER`)
- **Private methods:** leading underscore (e.g., `_collect_metrics`, `_get_headers`)
- **Logging:** `_LOGGER = logging.getLogger(__name__)` at module level
- **Imports:** stdlib, then third-party (`aiohttp`, `voluptuous`, `psutil`), then HA, then relative
- **Type hints:** Used on public API methods (e.g., `Dict[str, Any]`, `Optional[str]`)
- **Error handling:** `try/except` with specific types; coordinators raise `UpdateFailed`
- **Schemas:** `voluptuous` for service call validation in `__init__.py`

## Important Notes

- Tests live in `tests/` and run under plain `pytest` with stubbed HA modules. Anything needing a live Home Assistant (the update download, restart, and boot-time confirmation) is still manual.
- `reference_integrations/` is not committed. It holds a third-party MIT integration used purely as a reference for HA patterns; `DOWNLOAD_MEROSS_LAN.sh` fetches it on demand. Keep it out of the repo -- this is a public repository and redistributing someone else's project without its LICENSE is not ours to do.
- **Caching caveat:** HA heavily caches `manifest.json` and `strings.json`. Version bumps may require deleting the old integration and reinstalling rather than overwriting. See `docs/user/deployment.md`.
- The `VERSION` file at root and `version` in `manifest.json` must stay in sync.
- The server poll interval (default 60s) can be changed remotely via the server's config push.

## Documentation

All documentation is organized under `docs/` — see `docs/INDEX.md` as the master hub.

| Need | Doc |
|------|-----|
| Full doc index | `docs/INDEX.md` |
| API endpoints | `docs/technical/api-reference.md` |
| Service schemas | `docs/technical/services.md` |
| Remote update design | `docs/technical/self-update.md` (client built; server side outstanding) |
| Data model | `docs/technical/data-model/README.md` |
| Dev guide | `docs/technical/dev-guide/README.md` |
| Quickstart | `docs/user/quickstart.md` |
| Deployment | `docs/user/deployment.md` |
| Updating the client | `docs/user/updating.md` |
| Troubleshooting | `docs/user/troubleshooting.md` |
| Doc update process | `DOC_UPDATE.md` (root) |
| Doc changelog | `docs/DOCS_CHANGELOG.md` |

When working on documentation tasks, check `DOC_UPDATE.md` for the update process and `docs/DOCS_CHANGELOG.md` for recent doc changes.
