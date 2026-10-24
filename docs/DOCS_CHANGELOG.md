# Documentation Changelog

Documentation-specific changelog, separate from the project [CHANGELOG.md](../CHANGELOG.md).

Format follows [Keep a Changelog](https://keepachangelog.com/).

---

## [2026-10-23] — Remote UI: the house tells its owner; 1.9.0

### Added
- `docs/user/updating.md`: a "Release notes" section, opening with 1.9.0 in the product's voice (your home, reachable from anywhere; your login stays yours; off in one tap) and the technical note that the house needs nothing but this update
- `docs/technical/remote-access.md`: "Telling the homeowner" (the `announce` block, the one notification under `remote_ui_on`, the enhanced-versus-tap wording, update-in-place and dismissal, why there is no Repairs issue) and the two deployment modes at the head of the `http:` section, with `not_needed`
- `CHANGELOG.md`: the 1.9.0 entry

### Changed
- `docs/technical/api-reference.md`: Endpoint 14 carries `announce`, and `trusted_proxies: []` is named as DNS-only mode; Endpoint 17's `http_block` enum gains `not_needed`
- `CLAUDE.md`: version 1.9.0 (the header had still said 1.8.0 at 1.8.1), the Remote UI pattern's announcement and DNS-only bullets, and the test count
- `CLAUDE.md`: the architecture tree now lists `updates.py` (`HADispatchUpdates`, shipped since 1.7.0 and in every release archive, but absent from the tree), and the comment columns line up again

## [2026-10-22] — Remote UI slice 2: integration pass

### Added
- `docs/technical/api-reference.md`: Endpoints 14-17 (`GET .../remote-ui`, `POST .../remote-ui/csr`, `.../token`, `.../status`) with the contract's bodies, the 404 rule and the refusal codes. They were listed in `CLAUDE.md` and described in `remote-access.md` but absent from the reference

### Changed
- `docs/technical/remote-access.md`: the status table says `last_error` is cut to 500 characters (the server's validation limit, now applied by the agent); the provisioner section says how renewal is actually signalled -- a `config_version` bump met by the certificate's own 60-day horizon, no flag
- `CLAUDE.md`: the test count (423 → 424)

## [2026-10-22] — Remote UI slice 2: the login-lockout health item

### Added
- `docs/technical/remote-access.md`: "The login-lockout health item" (when it appears, what it says, how `ip_ban_enabled` is detected and why cannot-tell emits nothing) and "What is still manual" (restart, merging an existing `http:` block, turning on lockout, hand-placed files, live verification). The Remote UI status line no longer lists anything as unbuilt
- `docs/user/troubleshooting.md`: a "Remote Access" section (restart required, an existing `http:` block, an expired token, the lockout warning)

### Changed
- `docs/technical/architecture.md`: the `remote_ui.py` paragraph says the files are fetched when Dispatch supports it and hand-placed otherwise, and names the health item
- `CLAUDE.md`: the Remote UI bullets (fetched versus hand-placed, never re-serialise `configuration.yaml`, the health item) and the test count (416 → 423)

## [2026-10-22] — Remote UI slice 2: the http include and the readiness report

### Added
- `docs/technical/remote-access.md`: "The `http:` block, and the restart it owes" (what `http.yaml` holds, the scan-and-append rule for `configuration.yaml`, why the file is written before the include, `present_unmanaged`, and how `restart_required` clears without any state of ours) and "Reporting readiness" (the status payload field by field, on change and every ten minutes). The unbuilt list is now only the `ip_ban_enabled` health item
- `docs/technical/architecture.md`: `http_config.py` given its own section and a line in the module tree

### Changed
- `CLAUDE.md`: a pattern bullet for the append-only include and the homeowner's `http:` winning, `http_config.py` in the tree, and the test count (391 → 416, 76 → 84 subtests)

## [2026-10-22] — Remote UI slice 2: the agent fetches its files

### Changed
- `docs/technical/remote-access.md`: the Remote UI section's "What slice 2 changes" became what it does — the provisioner's three answers to `GET .../remote-ui` (404, off, on), what leaves the house (the CSR and the AES keyset, never the key), why a moved `config_version` re-mints the token, the back-off on a refusal, and what from the contract is still unbuilt (status reporting, `http.yaml`, the health item). The files table gained a "Fetched" column beside "Hand-placed"
- `CLAUDE.md`: the four `/remote-ui` endpoints, the 404 rule extended to them, the slice-1/slice-2 file contract in the Remote UI pattern, and the test count (374 → 391, 69 → 76 subtests)

## [2026-10-20] — Room-first Home 3D plan (v1.8.0)

### Changed
- `docs/technical/architecture.md`: `floorplan.py` and `www/ayvra-room-plan.js` given their own section — it was the integration's largest module and had never been listed. Documents the three layers and the order that makes them work: matching, then the installer's corrections as a separate layer over it, then a card that reads the planning outcome rather than re-resolving entities
- `docs/technical/architecture.md`, `docs/INDEX.md`, `docs/leadership/capabilities.md`: the service count went 9 → 10 with `assign_floorplan_device`, and these three were still saying 9. The service table in `architecture.md` gained the missing row
- `docs/user/deployment.md`: added what has already been measured in a headless browser (tap-target and backplate registration at all four rotations, the focus layer's transform and box matching the plan layer's, the grey-out never reaching the focused room) so it is not re-done, and narrowed the remaining manual checks to what genuinely still needs a human at a real tablet and an old WebView
- `CLAUDE.md`: the Testing section described a suite that predates the floorplan work. Now lists what the floorplan tests cover, quotes what pytest actually prints, and states the two standing limits plainly — `__init__.py` is exercised by no test because `voluptuous` is absent, and the card's JS has no test runner, so a green suite is not evidence the card draws correctly

## [2026-10-09] — Backup checksums from network shares (v1.7.9)

### Changed
- `docs/technical/backup-reporting.md`, `docs/technical/api-reference.md`: copies on a Supervisor backup mount (NAS) are hashed too; checksum failures are logged as warnings

## [2026-10-08] — Backup Reporting (v1.7.8)

### Added
- `docs/technical/backup-reporting.md`: source, what is reported under which slug and why, the once-each storage, local-only checksums, and cadence

### Changed
- `docs/technical/api-reference.md`: Endpoint 13 (`POST .../backups/batch`)
- `docs/INDEX.md`, `CLAUDE.md`: added Backup Reporting

## [2026-09-19] — Component Inventory (v1.6.0)

### Added
- `docs/technical/component-inventory.md` — why the inventory exists, the three rules the server's reconciliation model forces (full snapshot, stable slugs, `failing` only where Home Assistant knows), where each kind is read from and when it is legitimately absent, the cadence and both post-update paths, the truncation order, and what happens when a report fails

### Changed
- `docs/technical/api-reference.md` — new Endpoint 12 (`POST .../components`) with the implementation notes that matter: omission *is* removal, slugs must not derive from anything renameable, 30-minute cadence with a forced report after an update, and client-side truncation at 750
- `docs/technical/architecture.md` — `components.py` added to the file tree and given its own responsibilities section; `report_components()` added to the API client table; the coordinator cycle now lists all six steps and states plainly that the inventory does not ride the 60 s cadence
- `docs/technical/README.md`, `docs/INDEX.md` — added Component Inventory
- `docs/leadership/capabilities.md` — two new delivered capabilities (version inventory reporting, prompt reporting after an update)
- `docs/leadership/roadmap.md` — Built gained version inventory reporting, noting that the Components view was empty for every installation until now
- `CLAUDE.md` — version, file tree, endpoint list, key patterns, and testing note

---

## [2026-09-19] — Consent-Gated Remote Access (v1.6.0)

### Added
- `docs/technical/remote-access.md` — client-side design for consent-gated remote access: the two consent surfaces, how a prompt is cleared, how live sessions are tracked (including the one judgement call, for a request answered on the web page), the transport loop, and the local credential decision with what each scope can reach

### Changed
- `docs/technical/api-reference.md` — new endpoints 7–11 (`access/pending`, `access/{session}/respond`, `access/{session}/revoke`, `access/poll`, `access/exchanges/{id}/respond`), each with the client implementation notes that matter, including the 404-is-not-a-vanished-installation trap
- `docs/technical/architecture.md` — file tree brought current (it still listed neither `update.py`/`updater.py`/`health.py` from 1.5.0 nor the new modules), new sections for `binary_sensor.py` and the remote access modules, service count 6 → 9
- `docs/technical/services.md` — added `respond_to_access_request` and `revoke_access`; service count 6 → 9
- `docs/user/services-guide.md` — 7 services → 9, with the two new services written for the person using them and a pointer to the Repairs dialog as the normal route
- `docs/INDEX.md`, `docs/technical/README.md` — added Remote Access; corrected the stale "All 6 services" label
- `docs/leadership/capabilities.md` — version v1.2.2 → v1.6.0; four new delivered capabilities covering consent, the off-switch, the support session, and the request sensor; summary counts updated
- `docs/leadership/roadmap.md` — Built moved to v1.6.0 and gained consent-gated remote support; service and entity counts updated
- `CLAUDE.md` — version, file tree, service list, endpoint list, platform list, and the remote access documentation entry

---

## [2026-09-18] — Server Side Built

### Changed
- `docs/technical/self-update.md` — status moved to implemented; phase table updated (layer 6 done); the remaining pre-launch steps now reflect that the server exists, leaving only "generate a key, cut a release, publish, roll out". Points at the HA Dispatch repository's `docs/technical/client-updates.md` as authoritative for the server half.

---

## [2026-09-17] — Self-Update Implementation (v1.5.0)

### Added
- `docs/user/updating.md` — operator guide for updating from Home Assistant, the dashboard, or SSH, including the manual recovery path and the explicit warning that there is no automatic rollback

### Changed
- `docs/technical/self-update.md` — status moved from proposed to client-implemented; phase table now reflects what shipped; `SPECIFIC_VERSION` removed from the update entity's advertised features with the reason; test coverage section rewritten to describe actual tests
- `docs/technical/api-reference.md` — `client_version` added to register and status request bodies; `client_release` response block documented; new Endpoint 6 for `client-update` reporting
- `docs/technical/services.md` — added `install_update`
- `docs/user/services-guide.md` — 6 services → 7, added `install_update`, plus a note distinguishing it from `force_update`
- `docs/user/README.md`, `docs/INDEX.md` — added the updating guide
- `docs/leadership/roadmap.md` — client self-update, version reporting, and HACS packaging moved to Built; remaining server-side work restated as "Fleet update control"
- `CLAUDE.md` — version, dependencies, file tree, endpoint and service lists, release commands; corrected the stale "no automated tests exist" note

---

## [2026-09-17] — Self-Update Design Specification

### Added
- `docs/technical/self-update.md` — design spec for remote client updates: version reporting, Home Assistant `update` entity, signed installer, dashboard-driven rollout, threat model, and phased implementation plan. Proposed, not implemented.

### Changed
- `docs/INDEX.md` — added Self-Update to the technical documentation table
- `docs/technical/README.md` — added Self-Update to the contents table
- `docs/leadership/roadmap.md` — added "Remote client updates" under Planned

---

## [2026-03-18] — Full Documentation Restructure

### Added
- `docs/` directory with audience-specific structure (technical, user, leadership, archive)
- `docs/INDEX.md` — master documentation hub with quick links
- `docs/DOCS_CHANGELOG.md` — this file
- `DOC_UPDATE.md` — documentation update process with run tracking
- `docs/technical/architecture.md` — system architecture overview
- `docs/technical/api-reference.md` — complete API endpoint reference
- `docs/technical/services.md` — all 6 services documented
- `docs/technical/service-registration.md` — singleton pattern analysis
- `docs/technical/alert-api.md` — alert API endpoint reference
- `docs/technical/data-model/` — data model split into 5 topic files
- `docs/technical/dev-guide/` — development guide split into 12 topic files
- `docs/user/quickstart.md` — 5-minute setup guide
- `docs/user/installation.md` — detailed installation and setup
- `docs/user/configuration.md` — config flow walkthrough
- `docs/user/services-guide.md` — consolidated services guide
- `docs/user/alerts-guide.md` — alert management guide
- `docs/user/deployment.md` — consolidated deployment guide
- `docs/user/testing.md` — testing procedures
- `docs/user/troubleshooting.md` — consolidated troubleshooting
- `docs/leadership/executive-summary.md` — executive overview
- `docs/leadership/capabilities.md` — feature matrix
- `docs/leadership/roadmap.md` — project roadmap
- `docs/leadership/technical-profile.md` — stack and performance profile
- `docs/archive/` — 7 historical development documents preserved

### Split
- `CLIENT_SIDE_DEVELOPMENT_GUIDE.md` (85K) → 12 files in `docs/technical/dev-guide/` + `docs/technical/api-reference.md`
- `DATA_MODEL.md` (37K) → 5 files in `docs/technical/data-model/`

### Moved
- `QUICKSTART.md` → `docs/user/quickstart.md`
- `TESTING.md` → `docs/user/testing.md`
- `ALERT_API_DOCUMENTATION.md` → `docs/technical/alert-api.md`
- `SERVICE_REGISTRATION_ANALYSIS.md` → `docs/technical/service-registration.md`
- `ANALYSIS_COMPLETE.md` → `docs/archive/analysis-complete.md`
- `ALERT_INTEGRATION_ANALYSIS.md` → `docs/archive/alert-integration-analysis.md`
- `ALERT_API_INTEGRATION_COMPLETE.md` → `docs/archive/alert-integration-complete.md`
- `BUGFIX_UPTIME.md` → `docs/archive/bugfix-uptime.md`
- `FEATURES_ADDED.md` → `docs/archive/features-added-v1.1.md`
- `FIX_SUMMARY.md` → `docs/archive/fix-summary-services.md`
- `IMPLEMENTATION_SUMMARY.md` → `docs/archive/implementation-summary-v1.md`

### Consolidated
- `DEPLOY_README.md` + `DEPLOYMENT_TIPS.md` + `VERSION_TRACKING.md` → `docs/user/deployment.md`
- `DEBUG_SERVICES.md` + alert service docs → `docs/user/services-guide.md`
- Troubleshooting sections from multiple files → `docs/user/troubleshooting.md`

### Changed
- `README.md` — slimmed to ~60 lines with links to `docs/`
- `CLAUDE.md` — added Documentation section

### Removed
- 14 root-level .md files (moved to docs/)

### Archived
- 7 historical development documents moved to `docs/archive/` with archive headers
