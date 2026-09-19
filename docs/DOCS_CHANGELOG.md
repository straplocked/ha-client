# Documentation Changelog

Documentation-specific changelog, separate from the project [CHANGELOG.md](../CHANGELOG.md).

Format follows [Keep a Changelog](https://keepachangelog.com/).

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
