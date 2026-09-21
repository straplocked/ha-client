# Changelog

All notable changes to HA Dispatch Client will be documented in this file.

## [1.7.0] - 2026-09-21

The other half of consent-gated remote updates. The HA Dispatch server can now
plan a Core, OS or add-on update, gate it behind the fleet's risk verdict, and
ask the homeowner to consent to that specific version change. The client had no
code to carry one out. This ships it.

### Added — consent-gated remote updates

- **The client installs consented updates.** Once the homeowner has agreed to a
  specific update on the consent page, the server offers the run to this agent.
  `HADispatchUpdates` polls for it on the same cadence as remote access, takes a
  pre-update backup, applies the update against the local Supervisor, and reports
  every phase back — `started`, `backup`, `apply`, `confirm`.

- **It reports before it touches anything.** As with a client self-update, the
  `started` report lands before the backup or the apply. An installation that
  reports it began a Core update and then goes silent has told us the update
  broke it — which no failure report from a restarted box could.

- **It confirms across the restart it causes.** A Core or OS update reboots Home
  Assistant mid-run and kills this process. The run's target is persisted before
  the apply, and on the next boot the agent confirms `success` or `failed` by
  comparing what is now installed to what was asked for.

- **Consent stays the server's to enforce.** The agent never re-checks it: a run
  only appears once consent is granted and disappears the moment it is revoked,
  denied or expires. There is no path here that updates Home Assistant without a
  technician plan and a homeowner's deliberate consent.

- The Supervisor calls (backup, apply) are isolated behind `SupervisorUpdater`,
  the one seam that needs a real supervised system; the run orchestration is
  fully covered by 17 new tests. Design in `docs/technical/remote-updates.md`.

## [1.6.0] - 2026-09-19

Two halves of the same gap. The server had already built consent-gated
remote access and the component inventory pipeline; the client had no code
for either, so requests were never surfaced to anybody and the Components
tab was empty for every installation. Both are shipped here, together.


### Added — consent-gated remote access

- **The client finally receives consent requests.** The server has been able to
  ask an installation for remote access for some time. The client had no code
  for it at all -- no poll, no prompt, no way to answer -- so every request sat
  waiting for a decision that Home Assistant was never going to ask anybody
  for. That is why "the client never gets consent requests".

  A pending request is now surfaced two ways, both inside Home Assistant:

  - a **persistent notification** carrying who is asking, why, what they will
    be able to do, and for how long;
  - a **fixable Repairs issue** whose repair flow is a native **Approve /
    Decline** dialog.

  `scope_description` is shown verbatim. The server writes it for a homeowner
  on purpose, and rewording it into technical language would defeat the point
  of asking. The customer never has to leave Home Assistant to decide.

  A request that leaves the pending list -- answered here, answered on the web
  consent page, or simply expired -- has its notification *and* its Repairs
  issue cleared on the next poll. A prompt for a request that is already dead
  is worse than no prompt: it teaches people that these prompts mean nothing.

- **An off-switch that works.** While a session is live there is a one-tap
  **End remote access** entry in Repairs, a `revoke_access` service, and the
  server's own consent page. A consent model without a working off-switch is
  theatre.

- **The tunnel.** Consent alone would grant a session that then does nothing,
  so the transport half shipped with it. The client long-polls for requests the
  server has already authorised, runs them against the local Home Assistant,
  and posts the responses back -- concurrently, because the technician is
  waiting on the whole batch. Every connection is outbound; no port is opened.

  Local requests authenticate as a Home Assistant **system user** minted
  through `hass.auth`, which is the mechanism Home Assistant sanctions for an
  integration calling the local API. Which user depends on the granted scope:
  `diagnostic` gets the read-only group, so a read-only session is
  *technically* incapable of changing anything rather than merely promised not
  to; `maintenance` and `full` get admin, which is what those scopes were
  granted for. A session with no consent on record gets nothing. Both users are
  created lazily, so an installation that only ever grants diagnostic access
  never has an admin credential on it. On top of the server's scope check, the
  client relays only `/api/` paths, refuses streaming endpoints, and refuses
  responses over 2 MB.

- **`binary_sensor.ha_dispatch_remote_access_requested`** -- on while somebody
  is waiting for an answer, with the pending requests and live sessions as
  attributes, so the prompt can be routed to a phone or a speaker instead of
  waiting to be noticed.

- Two services: `respond_to_access_request` and `revoke_access`. A service call
  carries the calling user's id, so the audit receipt names who agreed rather
  than saying "somebody".

### Changed — consent-gated remote access

- Live sessions are persisted through Home Assistant's storage helper and
  restored on setup. Restarting Home Assistant is one of the main reasons to
  grant *maintenance* access in the first place, so losing the session across
  the restart would break the feature exactly when it is being used.

- `tests/conftest.py` gained stubs for `persistent_notification`,
  `issue_registry`, `repairs`, `binary_sensor`, `data_entry_flow`,
  `entity_platform`, `network`, `CoordinatorEntity` and `dt.parse_datetime`.
  The notification and issue stubs record what they were told, so the tests
  assert on what the customer actually sees rather than on a call count.

### Notes — consent-gated remote access

- A 404 from any `/access/` path means *this server has no remote access*, not
  that the installation has vanished. It maps to `RemoteAccessUnavailable` and
  never to `InstallationGoneError`: the existing re-enrolment handler would
  otherwise re-register a perfectly healthy installation once a minute, forever.

- A request that disappears from the pending list between two polls was either
  granted or declined on the web consent page, and nothing available to the
  client tells the two apart. The tunnel runs for the window that session could
  occupy -- the server only queues work for a genuinely active session, so
  polling for one that is not costs an idle connection -- but no "access is
  active" prompt is raised for it. Claiming access is live when the customer
  may have just declined it is the same lie as leaving a dead request on
  screen.


### Added — component inventory

- **The client reports what it is running.** The server has had a component
  inventory endpoint, a reconciliation service, a transition ledger and an
  update-risk scorer for some time. The client had never posted a single
  inventory, so the dashboard's Components tab was empty for every
  installation and the risk pipeline had nothing whatsoever to score.

  Every report now carries Home Assistant core, the OS and Supervisor where
  they exist, every Supervisor add-on, every integration with a config entry
  plus every custom integration on disk, and everything HACS has downloaded.

  A whole kind being absent is normal rather than an error -- most
  installations are Core-only, with no Supervisor, no add-ons and no HACS --
  so each source degrades on its own. An unavailable Supervisor helper, a HACS
  that rearranged its internals, or a manifest that will not load costs that
  one source and nothing else.

- **`POST /api/v1/installations/{id}/components`** in the API client, driven by
  `coordinator.async_report_components()`.

### The three rules, all of which are load-bearing

- **Full snapshot, never a delta.** The server retires anything absent --
  omission *is* removal, there is no "deleted" flag. An agent that sent only
  what changed would retire almost the entire inventory on its second report,
  and an agent that skipped a poll would leave it permanently wrong. So
  `components.py` keeps no memory of the previous report: there is nothing to
  diff against, deliberately.

- **Slugs are stable, because they are half a component's identity.** Every
  one is a Home Assistant identifier the user cannot rename -- the integration
  domain, the Supervisor add-on slug, the HACS repository. The config entry
  *title* is used for nothing at all: it is user-editable, and an integration
  with two entries has two of them, so slugging on it would turn one Hue
  integration into two components and retire one of them next poll. A slug
  that moves reads as one component being removed and another installed, which
  corrupts both the change timeline and the risk evidence.

- **`failing` is set only where Home Assistant knows.** A config entry in a
  failed setup state, or an add-on Supervisor could not start. A *stopped*
  add-on is explicitly not a failure: people stop add-ons on purpose, and
  calling that a fault would blame the last upgrade for a deliberate act --
  fleet-wide, for everybody about to install that version. The
  failed-config-entry set is now shared with health reporting, so that
  judgement has one definition rather than two that drift.

A built-in integration ships no manifest version, so it is reported with a
name and no version. Inventing one -- the core version, say -- would put a
fabricated transition into the fleet's evidence.

### Cadence

- Reported on the first coordinator tick after a restart, then every **30
  minutes**. Deliberately not on the 60 s metrics poll: versions change
  rarely, the server's attribution window is two hours wide, and reporting on
  every poll would cost bandwidth without improving the signal.

- **Forced immediately after a self-update**, on both paths. The transition is
  what opens that observation window, so a version discovered half an hour
  late gets credited with half an hour of unrelated faults. The restart path
  reports from `async_confirm_pending()`, which runs before the coordinator's
  first refresh and so replaces that tick's report rather than adding to it.
  The no-restart path (`restart_after_update: false`) reports as soon as the
  files are swapped, since nothing else would notice for half an hour.

- A report that fails to send does not stamp the clock, so the next tick
  retries rather than waiting out the full interval on a transport blip.

### Limits

- Truncated to **750** entries client-side -- under the server's 800 retained
  and far under its 2000 hard limit -- so a very large installation loses rows
  somebody chose for it to lose. Platform first, then everything carrying a
  version sorted by `kind:slug`, then everything without one. A versionless
  row contributes nothing to the transition ledger, so it is the right thing
  to lose. The sort matters as much as the bands: an unstable cut would retire
  and reinstate the same components forever.

- Over-long names and versions are trimmed rather than sent whole, so one bad
  field cannot cost the whole report a 422.

### Changed — component inventory

- Inventory reporting never takes anything else down with it. A collection
  failure is logged and the report skipped; metrics and health still go out.
- `health._FAILED_ENTRY_STATES` is now `health.FAILED_ENTRY_STATES`, shared
  with component reporting.
- `tests/conftest.py` gained stubs for `homeassistant.components.hassio` --
  answering the way a Core-only installation does, because that is the
  majority case and the one most likely to be got wrong -- and for
  `loader.async_get_integrations` / `async_get_custom_components`.

## [1.5.2] - 2026-09-19

### Changed

- Version bump only. This release exists to exercise the self-update path
  end to end: 1.5.1 is the first build that pins a signing key, so it is the
  first that can verify anything, and 1.5.2 is therefore the first release any
  installation can actually install by itself.

  There is no code change. Saying so plainly is better than inventing one —
  the thing under test is the delivery mechanism, not the payload.

## [1.5.1] - 2026-09-19

### Added

- **A release signing key is now pinned.** `RELEASE_SIGNING_KEYS` carries
  `hadc-2026-01`, so the client can verify releases signed by the release
  pipeline. Until now the dict was empty and every install failed closed at
  verification, which was the correct default but meant self-update did
  nothing.

  **This release cannot install itself.** A release is verified by the key
  pinned in the client that is *already running*, and 1.5.0 pinned none. So
  1.5.1 has to be deployed the old way — `deploy.sh`, HACS, or a manual copy —
  on every installation. From 1.5.2 onwards self-update works, because by then
  the running client holds the key.

### Changed

- `tests/test_updater.py` no longer asserts that no key is pinned; it asserts
  that whatever *is* pinned is a well-formed 32-byte Ed25519 public key, and
  that a signature from a different keypair is still rejected. The original
  assertion was right while the repo shipped no keys and became wrong the
  moment a real one existed, but the risk it guarded — a truncated or
  mistyped key failing closed on the whole fleet at once, only at install
  time — is unchanged.

## [1.5.0] - 2026-09-17

### Added

- **The client can now update itself.** Until now the only way to change the
  version on an instance was `deploy.sh` over SSH, one host at a time, and the
  server had no idea which version any installation was running. A fleet tool
  that can observe every instance but ship none of them a fix is half a tool.

  - `client_version` is now sent on registration and on every status heartbeat,
    read from the manifest actually on disk rather than a constant, so it stays
    honest after an update.
  - A Home Assistant `update` entity surfaces available client releases at
    **Settings → Updates**, with release notes and install progress.
  - `updater.py` downloads, verifies, validates, and installs a release, then
    restarts Home Assistant.
  - New `install_update` service for driving the same install from an
    automation.
  - The server can push `auto_update`, `target_version`, `update_window`,
    `update_channel`, and `restart_after_update` through the existing
    `desired_state` channel. `auto_update` defaults to off.

  **Releases must be signed.** The client verifies an Ed25519 signature against
  a public key pinned in `const.py`; the Dispatch server never holds that key.
  Installing an update is remote code execution on the user's machine, so the
  server is allowed to decide *whether* and *when* to offer one, and never
  *what code runs*. An attacker who fully owns the server can still serve
  nothing but releases we signed.

  `RELEASE_SIGNING_KEYS` ships **empty**, so self-update is inert until a key is
  deliberately pinned — every install fails closed at verification. Shipping a
  placeholder key nobody controls the private half of would be worse than
  shipping none.

  Before the swap, the archive is checked for path traversal, absolute paths,
  symlink members, a wrong domain or version, a missing manifest, and runaway
  expansion — all while the working copy is still untouched. The previous
  version is kept as a backup outside `custom_components/`, where Home
  Assistant's loader will not mistake it for a second integration.

  **There is no automatic rollback**, and this is a known limitation rather than
  an omission: a release that fails to import takes its own recovery code down
  with it. Mitigations are hard pre-swap validation, a retained backup, staged
  rollouts, and the server noticing an instance that reports `started` and then
  goes quiet. See `docs/user/updating.md` for the manual recovery steps.

- `hacs.json`, so the integration can be installed and updated through HACS. The
  release archive is the same artifact either way.
- `scripts/release.sh` builds and signs a release; `scripts/generate_signing_key.py`
  creates a keypair and prints the `const.py` entry.
- `tests/test_updater.py` — 37 tests over signature verification, archive
  validation, and the directory swap, including the mid-swap failure that has to
  leave a working integration behind.
- `tests/conftest.py` — shared Home Assistant stubs. The per-module stub sets
  had reached the point where collection order decided whether they worked.

### Changed

- `cryptography>=41.0.0` added to `manifest.json` requirements. It ships with
  Home Assistant core, so this normally resolves as already-satisfied; it is
  declared so a thin install fails loudly rather than silently. The updater
  imports it lazily, so a missing copy means "updates do not work" rather than
  "the integration does not load".

### Known limitations

- Self-update does nothing until a signing key is pinned **and** the server
  learns to serve the `client_release` payload. The server side is designed in
  `docs/technical/self-update.md` but not built.
- The download path, the restart, and boot-time confirmation have no automated
  coverage — they need a running Home Assistant. Exercise them on a canary
  instance before enabling `auto_update` anywhere that matters.
- HACS and self-update both write the same files. Keep `auto_update` off on
  HACS-managed instances; pick one route per installation.

## [1.4.0] - 2026-09-17

### Fixed
- **The integration no longer has to be deleted and re-added after a server
  reset.** A rejected bearer token was caught as a generic
  `aiohttp.ClientError`, turned into `UpdateFailed`, and retried forever. Since
  the client had no way to obtain a new token, manual deletion in Home Assistant
  was the only cure -- needed every time the server's database was rebuilt.

  A 401 on an authenticated endpoint now raises `InstallationAuthError`, which is
  deliberately *not* an `aiohttp.ClientError`: a rejected token is not a
  transient network fault and must not be retried indefinitely. The coordinator
  catches it and re-enrols automatically, persisting the new installation id and
  token to the config entry so they survive a restart.

  Covers both failure shapes. A 404 is the more common one: Laravel resolves the
  route model before the auth middleware runs, so an installation whose record
  was removed -- or a database that was rebuilt, renumbering ids -- answers 404
  regardless of the token presented. `InstallationGoneError` subclasses
  `InstallationAuthError` so one handler covers both.

  Re-enrolment reuses the stored `client_id`, which is this installation's stable
  identity. If the server still holds that id -- record present, token stale --
  registration is refused with a 422, and a fresh id is generated instead
  (`ClientIdTakenError`).

  The cycle that triggered re-enrolment still reports failure rather than
  retrying inline, so a persistently failing server cannot spin.

### Changed
- The registration secret is now stored in the config entry. Unattended
  re-enrolment needs it, and it was previously discarded after setup.

### Notes
- Re-enrolment is refused, loudly, if the stored secret is missing or wrong;
  reconfigure the integration in that case. Nothing is persisted on failure.

## [1.3.0] - 2026-08-09

### Added
- Home Assistant health signal reporting (`health.py`), sent alongside metrics:
  - Unavailable entities, with per-entity detail
  - Entities that have gone quiet relative to their own reporting cadence, based
    on `last_reported` rather than `last_changed`
  - Config entries in a failed or retrying state
  - Battery levels, split into low and critical bands
  - Automations that are disabled or unavailable
- Server-controlled thresholds via `desired_state`: `stale_entity_hours`,
  `battery_low_percent`, `battery_critical_percent`
- Unit tests for health classification (`tests/test_health.py`), which stub the
  handful of Home Assistant symbols involved rather than requiring a full install

### Added (registration secret)
- `register_installation()` accepts a `registration_secret`, sent as the
  `X-Registration-Secret` header. Internet-facing servers require one: registration
  is the only unauthenticated endpoint and it issues a bearer token.
- New optional "Registration Secret" field in the config flow. A rejected secret
  raises `RegistrationSecretError` and surfaces as its own error message, rather
  than being reported as a connection failure.
- Leave the field blank for servers on a trusted network with no secret configured.

### Changed
- `api_client.submit_metrics()` accepts an optional `health` payload. The key is
  omitted entirely when collection fails, which the server reads as "not
  reported" and so leaves existing health state untouched. An empty payload
  would instead mean "everything recovered".

### Notes
- Backward compatible. A server without the health migration ignores the extra
  key and continues accepting metrics as before.
- Item detail is capped at 500 per report. Rollup counts are sent separately and
  are not derived from that list, so they stay accurate during a mass outage.

## [1.2.2] - 2025-11-17

### Changed
- domain fix


## [1.2.1] - 2025-11-16

### Changed
- Fixed Hostname bug


## [1.2.0] - 2025-11-17

### Added
- **Alert API Integration** - Full support for server's Alert API
  - Added `submit_alert` service for structured alert submission
  - Added `resolve_alert` service to resolve alerts by type
  - Added `submit_alerts_batch` API method for batch alert submission
  - Alert deduplication support (server prevents duplicate alerts)
  - Proper alert context with JSON data fields

### Changed
- **Updated `trigger_alert` service** - Now uses Alert API directly instead of metrics
  - Submits structured alerts with severity, type, title, message, context
  - Single alerts: `disk_low`, `cpu_high`, `memory_high`
  - Batch submission when `alert_type: all` is used
  - More explicit logging of alert IDs and actions

### Technical Details
- Added 3 new methods to `api_client.py`:
  - `submit_alert()` - Submit single alert
  - `submit_alerts_batch()` - Submit up to 100 alerts
  - `resolve_alert()` - Resolve alerts by type
- Updated service schemas, services.yaml, and strings.json
- Alerts now follow server's expected structure with proper field validation

## [1.1.1] - 2025-11-16

### Changed
- Testing

## [1.1.0] - 2025-11-16

### Fixed
- **Service Registration** - Fixed critical issue where services weren't appearing in Home Assistant
  - Services now register once per domain (not per config entry)
  - Added `setup_services()` function with duplicate check
  - Added `get_coordinator_for_service()` helper for dynamic coordinator access
  - Added `teardown_services()` for proper cleanup
  - Pattern now matches professional integrations (meross_lan, mqtt, zha)

### Added
- **Unified Deploy Script** - Single `deploy.sh` script handles both install and updates
  - Auto-detects install vs update
  - Creates automatic backups before updates
  - Comprehensive verification
  - Smart restart prompt
  - Version tracking

### Changed
- Consolidated 15+ deployment scripts into one unified `deploy.sh`
- Added VERSION file for tracking releases
- Updated manifest.json to reflect version

### Documentation
- Added DEPLOY_README.md for deployment guide
- Added FIX_SUMMARY.md explaining the service registration fix
- Added ANALYSIS_COMPLETE.md with detailed analysis
- Added cleanup_old_scripts.sh for removing obsolete scripts

## [1.0.0] - 2025-11-15

### Added
- Initial release
- Client registration with HA Dispatch server
- System metrics collection (CPU, memory, disk, uptime)
- Configuration polling and application
- Status reporting
- 4 debug services:
  - `send_test_metrics` - Send test metrics with custom values
  - `trigger_alert` - Trigger alerts for testing
  - `force_update` - Force immediate update
  - `send_custom_metric` - Send custom metrics
- Sensor entities for status display
- Config flow for easy setup
- Bearer token authentication

### Known Issues
- Services not appearing in Developer Tools (fixed in 1.1.0)
