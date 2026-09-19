# Documentation Changelog

Documentation-specific changelog, separate from the project [CHANGELOG.md](../CHANGELOG.md).

Format follows [Keep a Changelog](https://keepachangelog.com/).

---

## [2026-09-19] — Component Inventory (v1.7.0)

### Added
- `docs/technical/component-inventory.md` — why the inventory exists, the three rules the server's reconciliation model forces (full snapshot, stable slugs, `failing` only where Home Assistant knows), where each kind is read from and when it is legitimately absent, the cadence and both post-update paths, the truncation order, and what happens when a report fails

### Changed
- `docs/technical/api-reference.md` — new Endpoint 12 (`POST .../components`) with the implementation notes that matter: omission *is* removal, slugs must not derive from anything renameable, 30-minute cadence with a forced report after an update, and client-side truncation at 750
- `docs/technical/architecture.md` — `components.py` added to the file tree and given its own responsibilities section; `report_components()` added to the API client table; the coordinator cycle now lists all six steps and states plainly that the inventory does not ride the 60 s cadence
- `docs/technical/README.md`, `docs/INDEX.md` — added Component Inventory
- `docs/leadership/capabilities.md` — version v1.6.0 → v1.7.0; two new delivered capabilities (version inventory reporting, prompt reporting after an update)
- `docs/leadership/roadmap.md` — Built moved to v1.7.0 and gained version inventory reporting, noting that the Components view was empty for every installation until now
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
