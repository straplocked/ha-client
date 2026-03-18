<!-- Split from CLIENT_SIDE_DEVELOPMENT_GUIDE.md -->

# Client-Side Development Guide

**Version:** 1.0
**Date:** November 16, 2025
**Server Version:** Laravel 12 + Filament 4
**Target Platform:** Home Assistant (HACS Integration)

---

## Contents

| Document | Description |
|----------|-------------|
| [System Architecture](system-architecture.md) | High-level overview of the controller-agent architecture, component responsibilities, and key design principles |
| [Server Infrastructure](server-infrastructure.md) | Server technology stack (Laravel 12, Filament 4, MySQL), default settings, and scheduled jobs |
| [Authentication & Security](authentication.md) | Registration flow, token-based authentication, HTTPS requirements, rate limiting, and security best practices |
| [API Reference](../api-reference.md) | Full REST API endpoint documentation with request/response schemas and example implementations |
| [Communication Patterns](communication-patterns.md) | Sequence diagrams for registration, status polling, metrics submission, and WebSocket alert notification |
| [Client Implementation](client-implementation.md) | Complete HACS integration code: manifest, constants, config flow, API client, coordinator, and sensor entities |
| [Configuration Management](configuration.md) | Server-to-client configuration push/pull flow, threshold parameters, feature flags, and custom settings |
| [Metrics Collection](metrics-collection.md) | CPU, memory, disk, and uptime metric collection methods, submission strategies, and offline buffering |
| [Alert Handling](alerts.md) | Server-side alert generation, alert types and severity levels, and future client-side alert handling |
| [WebSocket Integration](websocket.md) | WebSocket server details, channel subscription, event types, and Python implementation example |
| [Error Handling](error-handling.md) | HTTP status code reference, network error handling, and exponential backoff with jitter |
| [Testing Strategy](testing-strategy.md) | Unit tests, integration tests, manual testing checklist, and server-side testing workflow |
| [Deployment & Distribution](deployment.md) | HACS repository structure, hacs.json configuration, release process, and user installation instructions |

## Cross-Referenced Documentation

- **[API Reference](../api-reference.md)** -- Top-level technical doc covering all REST endpoints
- **[Data Models](../data-model/)** -- Database schemas for Installation, Configuration, Metric, and Alert models

## Development Approach

1. **Start Simple**: Basic registration and status reporting
2. **Add Metrics**: System metrics collection and submission
3. **Configuration**: Implement config polling and application
4. **Entities**: Create sensor entities for display
5. **Polish**: Error handling, offline buffering, UI improvements
6. **Publish**: Package for HACS and release

## Server Summary

The HA Dispatch server is a fully functional Laravel 12 + Filament 4 application providing:

- REST API with token authentication
- Real-time WebSocket server
- Comprehensive settings management via admin panel
- Role-based access control
- Automated health checks and data pruning

## Resources

- **Home Assistant Docs**: https://developers.home-assistant.io/
- **HACS Docs**: https://hacs.xyz/docs/publish/start
- **Laravel Docs**: https://laravel.com/docs/12.x
- **Filament Docs**: https://filamentphp.com/docs/4.x
