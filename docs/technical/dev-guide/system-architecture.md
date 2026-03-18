<!-- Split from CLIENT_SIDE_DEVELOPMENT_GUIDE.md -->

# System Architecture

## Executive Summary

**HA Dispatch** is a centralized management system for multiple Home Assistant installations. The system uses a **controller-agent architecture** where:

- **Server (Controller)**: Laravel 12 + Filament 4 application managing all installations
- **Client (Agent)**: HACS integration installed on each Home Assistant instance

### Key Principles

1. **Client-Initiated Communication**: All communication is initiated by the client to avoid firewall/NAT issues
2. **Token-Based Authentication**: Each installation has a unique API token
3. **Configuration Push/Pull**: Server defines desired state, client polls and applies
4. **Metrics Reporting**: Client sends periodic system metrics to server
5. **Alert Generation**: Server monitors metrics and generates alerts based on configurable thresholds

---

## High-Level Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    HA Dispatch Server                        │
│              (Laravel 12 + Filament 4 + MySQL)              │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │  REST API    │  │  WebSockets  │  │  Admin Panel │     │
│  │  (Sanctum)   │  │  (Port 6001) │  │  (Filament)  │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │         MySQL Database                                │  │
│  │  - Installations  - Configurations                    │  │
│  │  - Metrics        - Alerts                            │  │
│  │  - Settings       - Users/Roles                       │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            ▲
                            │ HTTPS (Port 8080)
                            │ WSS (Port 6001)
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│ Home Assistant│   │ Home Assistant│   │ Home Assistant│
│  Installation │   │  Installation │   │  Installation │
│      #1       │   │      #2       │   │      #3       │
│               │   │               │   │               │
│  ┌─────────┐  │   │  ┌─────────┐  │   │  ┌─────────┐  │
│  │ HACS    │  │   │  │ HACS    │  │   │  │ HACS    │  │
│  │ Client  │  │   │  │ Client  │  │   │  │ Client  │  │
│  └─────────┘  │   │  └─────────┘  │   │  └─────────┘  │
└───────────────┘   └───────────────┘   └───────────────┘
```

## Component Responsibilities

### Server Responsibilities
- **Installation Registry**: Track all registered Home Assistant installations
- **Configuration Management**: Store and distribute configuration to clients
- **Metrics Storage**: Store time-series metrics from all installations
- **Alert Management**: Generate alerts based on thresholds and conditions
- **User Management**: Role-based access control for administrators
- **Settings Management**: Global defaults and operational parameters
- **API Gateway**: Expose RESTful and WebSocket endpoints
- **Background Jobs**: Health checks, data pruning, alert generation

### Client Responsibilities
- **Self-Registration**: Register with server and obtain API token
- **Status Reporting**: Send periodic heartbeat/status updates
- **Metrics Collection**: Gather system metrics and send to server
- **Configuration Polling**: Check for configuration updates
- **Configuration Application**: Apply received configuration locally
- **Entity Exposure**: Expose status/config as Home Assistant entities
- **Error Handling**: Graceful handling of network/server issues

---

## Related Documentation

- [Server Infrastructure](server-infrastructure.md) for details on the server technology stack
- [Authentication & Security](authentication.md) for the authentication flow
- [Communication Patterns](communication-patterns.md) for client-server interaction diagrams
- [API Reference](../api-reference.md) for full endpoint documentation
