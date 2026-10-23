# CLAUDE.md - HA Dispatch Client

## Project Overview

Home Assistant custom integration that connects to a centralized **HA Dispatch server** (Laravel 12 + Filament 4) for monitoring, metrics collection, and alert management across multiple HA instances.

- **Domain:** `ha_dispatch_client`
- **Version:** 1.8.0 (tracked in `VERSION` file and `manifest.json`)
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
├── backups.py      # HADispatchBackups - reports the installation's own backups (+ sha256)
├── screen.py         # ScreenSockets - Remote Screen's loopback WebSocket, frames over the poll
├── remote_ui.py      # HADispatchRemoteUI - the homeowner's own way in, through the Dispatch relay (TLS ends here)
├── http_config.py   # http.yaml (trusted proxies) + the one include line in configuration.yaml
├── repairs.py        # Approve/Deny dialog and the end-access off-switch
├── floorplan.py      # HADispatchFloorplan - plan assets -> the room-first "Home 3D" dashboard
├── www/
│   └── ayvra-room-plan.js  # <ayvra-room-plan> - the room-first custom Lovelace card
├── const.py          # DOMAIN, config keys, API endpoints, signing keys, entity/attribute keys
├── manifest.json     # Integration metadata
├── services.yaml     # 11 service definitions with UI fields
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
- **Installer corrections outrank the matcher, and survive a redeploy:** the
  render pipeline's automatic placement is weak until there is a definitive
  inventory per project, so `floorplan.py::async_plan_devices` runs the
  matcher first and then lays the installer's own corrections over it as a
  **separate layer** -- never folded into `async_match_entity`, because the
  deploy report has to be able to say which placements a human had to fix
  (`matched` is the matcher's conclusion, `bound` is what the plan shows).
  Corrections are keyed by `ha_map.json`'s device slug, the one identifier a
  redeploy cannot change, and re-applied on top of the **freshly downloaded**
  manifests -- applying them before the manifest load loses every one of
  them. One whose device or room the new manifest no longer has is dropped,
  pruned and reported; neither silently retained nor silently discarded.
- **Rooms are the UI metaphor, and the card is a custom one:** the Home 3D
  dashboard draws each floor with `<ayvra-room-plan>`
  (`www/ayvra-room-plan.js`), not a stock card -- tapping a room greys out the
  rest of the floor and opens a modal holding **that room's** controls. There
  are no loose device icons on the plan; a device is placed by its **room**,
  not by coordinates, so one whose room is not on the floor is reported
  `no_room_on_plan` and left off. The card is hand-written, dependency-free ES
  -- **no node, no bundler, no TypeScript, no build step** -- because this repo
  has no JavaScript toolchain and must stay HACS-installable as it sits; its
  expected config shape is documented in `floorplan.py::build_room_plan_card`'s
  docstring and repeated in the card's header, and the two must change
  together. The deploy copies the card into `FLOORPLAN_ASSET_DIR` (only
  `/config/www` is served over HTTP) and registers it as a Lovelace resource
  **with a `?v=<client version>` query string**: Home Assistant caches
  frontend resources as hard as it caches `manifest.json`, so a bare URL
  leaves every wall tablet running the card it downloaded months ago. The row
  is matched ignoring that query string, so a version bump re-points the one
  resource instead of adding another.
- **A room's name is display-only; its `name` is a key:** the sketch numbers a
  room's sub-cells ("Master Bathroom #4", "Foyer #7"), which is survey
  vocabulary, not something to put in front of a homeowner. `floorplan.py::
  room_display_name` strips that suffix into a `display` field and leaves
  `name` alone -- `name` is what a device's room and an installer's correction
  are matched on. `assign_room_labels` then decides which rooms carry a name
  chip at all: a sub-cell with no controls folds into its room (`same_room_as`,
  so a tap there opens the room), and a room under
  `FLOORPLAN_LABEL_MIN_SF` with no controls goes quiet. **Hiding a chip never
  hides a room** -- every room keeps its polygon and its tap, and a room with
  controls is never quietened, because its name is how the controls are found.
- **View rotation is a display-time concern, never a baked one:** each floor's
  default comes from `plan_transform.json`'s `plan_rotation_deg` (an integer,
  one of 0/90/180/270, clockwise; absent or invalid means 0 -- read by
  `floorplan.py::plan_rotation`) and rides the card config as `rotation`. The
  installer at the panel has the last word: the card's rotate control steps
  quarter turns and remembers the choice **per panel** in `localStorage` under
  `ayvra.room-plan.rotation.<dashboard_url_path>.<floor_key>`, because there
  is one tablet per floor and each is mounted on a different wall. Geometry in
  the card config is therefore **always un-rotated** -- the backplate is a PNG
  this client cannot turn, so pre-rotating the polygons would slide every room
  off the picture underneath it. In the card, the image, the polygons, the tap
  targets and the label anchors sit in **one** `.layer` div and that div is
  what rotates, as a single CSS transform, so they cannot come out of
  register; at 90/270 the layer keeps the image-shaped box inside a stage
  sized to the inverted one, and each label is counter-rotated so text stays
  upright. `floorplan.py::rotate_percent_point` is the normative definition of
  a quarter turn in percent space, tested there and cited by the card.
- **Consent-gated remote access:** `remote_access.py` owns consent and session state, `tunnel.py` owns transport, `repairs.py` owns the dialogs. Neither enforces scope -- the server authorises every relayed request before it is queued. What the client adds is a local credential and a local refusal policy. Relayed requests run as a Home Assistant **system user** minted through `hass.auth`: read-only group for `diagnostic`, admin group for `maintenance`/`full`, and **nothing at all** for a session with no consent on record. Both users are created lazily. `scope_description` from the server is shown **verbatim** -- it is written for a homeowner on purpose. Full design: `docs/technical/remote-access.md`. A `screen` session (Remote Screen) carries the frontend itself to a headless browser on the Dispatch side: its own local policy (no `/auth`, no raw socket, writes only to `/api`), the admin user, and `screen.py` swapping our token into the browser's WebSocket `auth` frame so a real credential never leaves the house.

- **Remote UI is the homeowner's path, and nothing of ours is in it:**
  `remote_ui.py` dials out to the Dispatch relay (snitun, the library Nabu
  Casa runs for HA Cloud, already installed by Core) and holds one
  connection; the relay matches a phone's TLS SNI to it and copies bytes. TLS
  terminates **inside Home Assistant's own aiohttp server** with a certificate
  whose key sits in `<config>/ha_dispatch_client/remote_ui/` and never leaves
  it, so Home Assistant applies its own login and MFA and no system user, token
  substitution or local policy is involved. It is **off until that directory
  holds `remote_ui.json`, `fullchain.pem` and `privkey.pem`** -- a server
  cannot make a house reachable. Those three files are the contract between
  slice 1 (hand-placed) and slice 2 (fetched): `async_poll` runs on the
  coordinator cycle, asks `GET .../remote-ui` and writes only what the
  desired state needs -- the key is generated **here** and is never in a
  request body (only the CSR is), the token is re-minted when
  `config_version` moves, and an unchanged state writes nothing. A 404 is
  `RemoteUiUnavailable`, asked once per run; `enabled: false` is a 200 that
  removes the three files and never `http.yaml`. This is the opposite property from the
  technician tunnel (which must be readable to be audited); never route
  technician access through it, and never put a credential of ours in it.
  Design: ha-dispatch `docs/technical/homeowner-remote-access.md`; the
  relay and its runbook live in that repo's `docker/relay/`.
- **The `http:` include is append-only, and the homeowner's `http:` wins:**
  the relay's records are proxied, so Home Assistant must trust the proxy
  ranges the server sends (`trusted_proxies`, plus `use_x_forwarded_for`).
  `http_config.py` writes them to `<config>/ha_dispatch_client/http.yaml`
  (atomically, only when the text differs) and then -- only if
  `configuration.yaml` has **no top-level `http:` key at all** -- appends one
  comment and one `http: !include ha_dispatch_client/http.yaml` line. The file
  is **scanned line by line and appended to, never parsed and re-dumped**:
  comments, `!include` and `!secret` lines must survive byte for byte (the
  original bytes are always a prefix of the new file; a test fails the day a
  writer re-serialises). An `http:` block that is not ours is never touched,
  reported as `http_block: present_unmanaged` for a technician to merge. Order
  matters: `http.yaml` is written **before** anything includes it, because an
  include of a missing file stops Home Assistant loading its config. The
  agent **never restarts Home Assistant**: a rewrite (or a first include) is
  reported `restart_required` until the HA process is newer than the file's
  mtime -- stateless, so an integration reload does not clear it. Status goes
  to `POST .../remote-ui/status` on change and at least every 10 minutes and
  cannot raise. The ranges ride a proxied (Cloudflare) DNS record; never
  write that vendor's name into customer-facing copy.

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
- `POST .../{id}/backups/batch` - Report finished backups, oldest first (once each)
- `GET  .../{id}/access/pending` - Remote access requests awaiting the customer
- `POST .../{id}/access/{session}/respond` - Report grant/deny
- `POST .../{id}/access/{session}/revoke` - End a live session
- `GET  .../{id}/access/poll` - Long-poll for authorised requests (held up to 25s)
- `POST .../{id}/access/exchanges/{request_id}/respond` - Return a local response
- `GET  .../{id}/remote-ui` - Remote UI desired state (`enabled: false` is a 200)
- `POST .../{id}/remote-ui/csr` - Have the house's CSR signed (the key stays home)
- `POST .../{id}/remote-ui/token` - Mint the relay token for a keyset made here
- `POST .../{id}/remote-ui/status` - The house reports (204)

A **404 on any `/access/` path means the server has no remote access**, not
that this installation has vanished. It maps to `RemoteAccessUnavailable`, and
must never map to `InstallationGoneError` -- the re-enrolment handler would
re-register a healthy installation once a minute, forever. The same holds for
`/remote-ui` paths (`RemoteUiUnavailable`).

### Services (11 total)

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
| `assign_floorplan_device` | Correct a Home 3D device's room, pin its entity, or clear the correction |
| `rebuild_floorplan` | Redraw the Home 3D dashboard from the plan files already on disk -- no server round trip |

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
metrics poll). The Home 3D floorplan is covered the same way: that a room's
temperature never binds to an outdoor sensor or to an entity of the wrong
`device_class`, that the installer's corrections survive a redeploy and are
laid over the *new* manifests, that the dashboard is built from
`<ayvra-room-plan>` and each device lands in its own room's controls, that a
quarter turn means what `rotate_percent_point` says it means, and that the
card's Lovelace resource is re-pointed rather than duplicated. Home Assistant
is not installed in the dev environment; `tests/conftest.py` stubs the symbols
the integration imports, including recording versions of
`persistent_notification` and `issue_registry` so tests can assert on what was
actually put in front of the customer, and Supervisor helpers that answer the
way a Core-only install does. Add to that conftest when new HA imports appear.

```bash
python3 -m pytest tests/ -q
```

That currently prints `416 passed, 84 subtests passed`. Two standing limits on
what that number means, both of which have caught people out:

- **`__init__.py` is exercised by no test at all.** `voluptuous` is not
  installed here, so a service cannot be invoked in this environment. The
  service *handlers* are verified only by inspection; the manager methods they
  call are tested directly, and `ServiceWiringTest` reads `__init__.py` to
  prove each service name is registered, torn down, and described in both
  `services.yaml` and `strings.json`. A change to a handler body is unguarded.
- **`www/ayvra-room-plan.js` has no test runner.** The suite is pytest and the
  card is hand-written JS with no toolchain, deliberately. What the suite can
  assert about it, it asserts *as strings* on the shipped file (the
  `localStorage` key template, the four legal angles, every colour coming from
  a theme variable, the grey-out never selecting the focused room). Anything
  about rendering -- registration of the backplate, the polygons and the tap
  targets at 90/270, the focus treatment, hit testing -- needs a real browser.
  `docs/user/deployment.md` carries the manual checks; do not take a green
  suite as evidence the card draws correctly.

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
| Backup reporting | `docs/technical/backup-reporting.md` (which backups, slugs, once-each storage, checksums) |
| Data model | `docs/technical/data-model/README.md` |
| Dev guide | `docs/technical/dev-guide/README.md` |
| Quickstart | `docs/user/quickstart.md` |
| Deployment | `docs/user/deployment.md` |
| Updating the client | `docs/user/updating.md` |
| Troubleshooting | `docs/user/troubleshooting.md` |
| Doc update process | `DOC_UPDATE.md` (root) |
| Doc changelog | `docs/DOCS_CHANGELOG.md` |

When working on documentation tasks, check `DOC_UPDATE.md` for the update process and `docs/DOCS_CHANGELOG.md` for recent doc changes.
