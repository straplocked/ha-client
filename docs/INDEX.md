# HA Dispatch Client — Documentation Index

Central hub for all project documentation. Choose your audience below.

## Quick Links

| Need | Go To |
|------|-------|
| Get started fast | [Quickstart Guide](user/quickstart.md) |
| Install & configure | [Installation](user/installation.md) |
| Deploy to HA | [Deployment Guide](user/deployment.md) |
| Update the client | [Updating the Client](user/updating.md) |
| Use services | [Services Guide](user/services-guide.md) |
| Alert management | [Alerts Guide](user/alerts-guide.md) |
| API endpoints | [API Reference](technical/api-reference.md) |
| Remote access | [Remote Access](technical/remote-access.md) |
| Remote updates | [Remote Updates](technical/remote-updates.md) |
| What an install is running | [Component Inventory](technical/component-inventory.md) |
| Architecture overview | [Architecture](technical/architecture.md) |
| Database schema | [Data Model](technical/data-model/README.md) |
| Development guide | [Dev Guide](technical/dev-guide/README.md) |
| Troubleshooting | [Troubleshooting](user/troubleshooting.md) |
| Executive overview | [Executive Summary](leadership/executive-summary.md) |

---

## User Documentation

For end users installing, configuring, and operating the integration.

| Document | Description |
|----------|-------------|
| [Quickstart](user/quickstart.md) | 5-minute setup guide |
| [Installation](user/installation.md) | Detailed installation and setup |
| [Configuration](user/configuration.md) | Config flow and entity management |
| [Services Guide](user/services-guide.md) | All 9 services with examples |
| [Alerts Guide](user/alerts-guide.md) | Alert submission, resolution, automation |
| [Deployment](user/deployment.md) | Deploy script, versioning, caching tips |
| [Updating the Client](user/updating.md) | Updating from HA, the dashboard, or SSH; recovery |
| [Testing](user/testing.md) | Manual testing procedures |
| [Troubleshooting](user/troubleshooting.md) | Common issues and solutions |

---

## Technical Documentation

For developers working on or extending the integration.

| Document | Description |
|----------|-------------|
| [Architecture](technical/architecture.md) | System architecture and component overview |
| [API Reference](technical/api-reference.md) | Complete API endpoint reference |
| [Services](technical/services.md) | Service schemas, handlers, and patterns |
| [Service Registration](technical/service-registration.md) | Singleton registration pattern analysis |
| [Alert API](technical/alert-api.md) | Alert API endpoint reference |
| [Self-Update](technical/self-update.md) | Remote client update design, signing, and rollout (client built in 1.5.0) |
| [Remote Access](technical/remote-access.md) | Consent-gated remote access: consent surfaces, the tunnel, and the local credential model (built in 1.6.0) |
| [Remote Updates](technical/remote-updates.md) | Consent-gated HA Core/OS/add-on updates: backup, apply, and confirm on boot (built in 1.7.0) |
| [Component Inventory](technical/component-inventory.md) | What the installation is running: where each kind comes from, why slugs must be stable, cadence and truncation (built in 1.6.0) |
| [Data Model](technical/data-model/README.md) | Database schema, relationships, business rules |
| [Dev Guide](technical/dev-guide/README.md) | Full client development guide (12 topics) |

---

## Leadership Documentation

Executive-level overview — no code, business language only.

| Document | Description |
|----------|-------------|
| [Executive Summary](leadership/executive-summary.md) | What HA Dispatch is and why it exists |
| [Capabilities](leadership/capabilities.md) | Feature matrix and current state |
| [Roadmap](leadership/roadmap.md) | What's built, planned, and not planned |
| [Technical Profile](leadership/technical-profile.md) | Stack, performance, and security posture |

---

## Archive

Historical development documents preserved for reference.

| Document | Topic |
|----------|-------|
| [Analysis Complete](archive/analysis-complete.md) | Service registration fix |
| [Alert Integration Analysis](archive/alert-integration-analysis.md) | Alert API gap analysis |
| [Alert Integration Complete](archive/alert-integration-complete.md) | Alert API implementation |
| [Bugfix: Uptime](archive/bugfix-uptime.md) | Negative uptime fix |
| [Features Added v1.1](archive/features-added-v1.1.md) | Debug services addition |
| [Fix Summary: Services](archive/fix-summary-services.md) | Service registration fix |
| [Implementation Summary v1](archive/implementation-summary-v1.md) | MVP implementation |

---

## Other Project Files

| File | Location | Purpose |
|------|----------|---------|
| [README.md](../README.md) | Project root | Project introduction |
| [CHANGELOG.md](../CHANGELOG.md) | Project root | Project changelog |
| [CLAUDE.md](../CLAUDE.md) | Project root | AI assistant instructions |
| [DOC_UPDATE.md](../DOC_UPDATE.md) | Project root | Documentation update process |
| [DOCS_CHANGELOG.md](DOCS_CHANGELOG.md) | docs/ | Documentation-specific changelog |
