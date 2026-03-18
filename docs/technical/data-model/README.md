<!-- Split from DATA_MODEL.md -->

# HA Dispatch - Data Model Reference

**Version:** 1.0
**Date:** November 16, 2025
**Purpose:** Client-side application development reference

---

## Overview

HA Dispatch is a centralized management system for multiple Home Assistant installations. The data model follows a **controller-agent architecture**:

- **Server (Controller)**: Laravel 12 + MySQL 8 backend
- **Client (Agent)**: Home Assistant HACS integration (to be built)

### Core Entities

1. **Installation** - Represents a registered Home Assistant instance
2. **Configuration** - Versioned configuration for an installation
3. **Metric** - Time-series system metrics from installations
4. **Alert** - Generated alerts based on thresholds and conditions
5. **Setting** - Global system settings

---

## Topic Files

| File | Description |
|------|-------------|
| [schema.md](schema.md) | Database schema definitions for all 5 tables (installations, configurations, metrics, alerts, settings) with CREATE TABLE statements and field descriptions |
| [relationships.md](relationships.md) | PHP data models, Eloquent relationships, scopes, entity-relationship diagram, and SQL query examples |
| [api-endpoints.md](api-endpoints.md) | All API endpoint documentation with request/response formats, plus data flow diagrams (registration, periodic reporting, metrics collection, offline buffering) |
| [json-examples.md](json-examples.md) | Complete JSON examples for registration, configuration, metric submission, and alert context objects |
| [business-rules.md](business-rules.md) | Business rules for registration, status, configuration, metrics, alerts, authentication, and data retention; enumerations and constants; client implementation checklist |

---

**Document Version:** 1.0
**Last Updated:** November 16, 2025
**For Questions:** Refer to existing codebase in `/src/app/Models/` and `/src/app/Http/Controllers/Api/`
