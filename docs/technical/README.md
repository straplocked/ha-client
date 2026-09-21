# Technical Documentation

Developer-facing documentation for the HA Dispatch Client integration. These documents cover architecture, API contracts, service definitions, and implementation patterns.

For the full documentation index, see [docs/INDEX.md](../INDEX.md).

## Contents

| Document | Description |
|----------|-------------|
| [Architecture](architecture.md) | System architecture, component overview, data flow, key patterns |
| [Alert API](alert-api.md) | Alert API endpoint reference (submit, batch, resolve) |
| [Services](services.md) | Complete service reference with schemas and parameters |
| [Service Registration](service-registration.md) | Singleton registration pattern analysis |
| [API Reference](api-reference.md) | Full API endpoint reference |
| [Self-Update](self-update.md) | Remote client update design, signing, and rollout |
| [Remote Access](remote-access.md) | Consent-gated remote access: consent surfaces, transport, local credentials |
| [Remote Updates](remote-updates.md) | Consent-gated HA Core/OS/add-on updates: backup, apply, confirm on boot |
| [Component Inventory](component-inventory.md) | What the installation is running: sources, stable slugs, cadence, truncation |
| [Data Model](data-model/README.md) | Database schema, relationships, business rules |
| [Dev Guide](dev-guide/README.md) | Full client development guide (12 topics) |
