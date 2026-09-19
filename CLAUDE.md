# CLAUDE.md - HA Dispatch Client

## Project Overview

Home Assistant custom integration that connects to a centralized **HA Dispatch server** (Laravel 12 + Filament 4) for monitoring, metrics collection, and alert management across multiple HA instances.

- **Domain:** `ha_dispatch_client`
- **Version:** 1.6.0 (tracked in `VERSION` file and `manifest.json`)
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
├── binary_sensor.py  # Pending remote access consent indicator
├── update.py         # HADispatchUpdateEntity - client version as an HA update entity
├── updater.py        # ClientUpdater - signed self-update (download, verify, swap, restart)
├── health.py         # Home Assistant health signal collection
├── components.py     # Component inventory - core, OS, Supervisor, add-ons, integrations, HACS
├── remote_access.py  # HADispatchRemoteAccess - consent surfaces and live session state
├── tunnel.py         # HADispatchTunnel - relays authorised requests to the local API
├── repairs.py        # Approve/Deny dialog and the end-access off-switch
├── const.py          # DOMAIN, config keys, API endpoints, signing keys, entity/attribute keys
├── manifest.json     # Integration metadata
├── services.yaml     # 9 service definitions with UI fields
└── strings.json      # UI text, translations, and repair flow copy
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
- **Component inventory is a full snapshot, never a delta:** `components.py` reports everything the installation is running on every report. The server retires anything absent -- omission *is* removal -- so an agent that sent only what changed would retire almost the whole inventory on its second report. There is deliberately no memory of the previous report in that module. Slugs must be **stable** because the server keys on `(kind, slug)`: they come from the integration domain, the Supervisor add-on slug, or the HACS repository, and **never** from a config entry title or a display name, which users rename. `failing` is set only where Home Assistant actually knows -- a config entry in a failed setup state, or an add-on Supervisor could not start; a *stopped* add-on is not a failure. Cadence is 30 minutes, **not** the 60 s metrics poll, with a forced report immediately after a self-update because the transition is what opens the fleet's observation window. A whole kind being absent is normal: most installs are Core-only. Full design: `docs/technical/component-inventory.md`.
- **Consent-gated remote access:** `remote_access.py` owns consent and session state, `tunnel.py` owns transport, `repairs.py` owns the dialogs. Neither enforces scope -- the server authorises every relayed request before it is queued. What the client adds is a local credential and a local refusal policy. Relayed requests run as a Home Assistant **system user** minted through `hass.auth`: read-only group for `diagnostic`, admin group for `maintenance`/`full`, and **nothing at all** for a session with no consent on record. Both users are created lazily. `scope_description` from the server is shown **verbatim** -- it is written for a homeowner on purpose. Full design: `docs/technical/remote-access.md`.

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
- `POST .../{id}/components` - Report the full component inventory (snapshot, not a delta)
- `POST .../{id}/client-update` - Report a self-update outcome (started/success/failed)
- `GET  .../{id}/access/pending` - Remote access requests awaiting the customer
- `POST .../{id}/access/{session}/respond` - Report grant/deny
- `POST .../{id}/access/{session}/revoke` - End a live session
- `GET  .../{id}/access/poll` - Long-poll for authorised requests (held up to 25s)
- `POST .../{id}/access/exchanges/{request_id}/respond` - Return a local response

A **404 on any `/access/` path means the server has no remote access**, not
that this installation has vanished. It maps to `RemoteAccessUnavailable`, and
must never map to `InstallationGoneError` -- the re-enrolment handler would
re-register a healthy installation once a minute, forever.

### Services (9 total)

| Service | Purpose |
|---------|---------|
| `send_test_metrics` | Test connectivity with mock metric values |
| `trigger_alert` | Quick alert testing (disk_low, cpu_high, memory_high, all) |
| `force_update` | Trigger immediate coordinator refresh |
| `send_custom_metric` | Submit arbitrary metric values |
| `submit_alert` | Full alert submission (severity, type, title, message, context) |
| `resolve_alert` | Resolve all unresolved alerts of a given type |
| `install_update` | Install the client release the server is offering (restarts HA) |
| `respond_to_access_request` | Approve or decline a remote access request |
| `revoke_access` | End a live remote access session (omit the id to end all) |

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
the whole self-update verification/validation/swap path, consent-gated
remote access -- what the customer is shown, that the decision reaching the
server is the one they made, and that a request leaving the pending list clears
both its notification and its Repairs issue -- and component inventory: that
every report is a full snapshot rather than a delta, that a slug survives
anything a user can rename, and that the inventory does not ride the 60 s
metrics poll). Home Assistant is not installed in the dev environment;
`tests/conftest.py` stubs the symbols the integration imports, including
recording versions of `persistent_notification` and `issue_registry` so tests
can assert on what was actually put in front of the customer, and Supervisor
helpers that answer the way a Core-only install does. Add to that conftest when
new HA imports appear.

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
| Remote access design | `docs/technical/remote-access.md` (consent, tunnel, local credentials) |
| Component inventory | `docs/technical/component-inventory.md` (sources, stable slugs, cadence, truncation) |
| Data model | `docs/technical/data-model/README.md` |
| Dev guide | `docs/technical/dev-guide/README.md` |
| Quickstart | `docs/user/quickstart.md` |
| Deployment | `docs/user/deployment.md` |
| Updating the client | `docs/user/updating.md` |
| Troubleshooting | `docs/user/troubleshooting.md` |
| Doc update process | `DOC_UPDATE.md` (root) |
| Doc changelog | `docs/DOCS_CHANGELOG.md` |

When working on documentation tasks, check `DOC_UPDATE.md` for the update process and `docs/DOCS_CHANGELOG.md` for recent doc changes.
