# Configuration Guide

This guide explains how the config flow works, what gets stored, entity configuration, and how server-side configuration updates are applied.

## How the Config Flow Works

When you add the HA Dispatch Client integration via **Settings** -> **Devices & Services** -> **Add Integration**, the config flow performs these steps:

### Step 1: User Input

Onboarding starts in HA Dispatch: your installer makes a one-time **pairing
code** there (it looks like `ABCD-1234` and lasts 30 minutes). The dialog asks
for that code and nothing else:

| Field | Required | Description |
|-------|----------|-------------|
| Pairing code | Yes | The code your installer gave you. Upper or lower case, with or without the dash; an `O` is read as a zero and an `I` or `L` as a one |

The server is `https://app.hadispatch.app`, and the installation is named
after your Home Assistant's location name. Once paired it appears in your
installer's fleet, assigned to them.

**Your own server (Advanced Mode).** With Advanced Mode switched on in your
user profile, the dialog first offers a choice: a pairing code, or **a server
of my own**. The second asks for:

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| Server URL | Yes | `https://app.hadispatch.app` | The base URL of your HA Dispatch server |
| Name | No | Your HA location name | A friendly name for this installation |
| Pairing code | No | -- | A code made on that server |
| Registration secret | No | -- | The server's enrolment secret, when no pairing code is given |

### Step 2: Client ID Generation

The integration generates a UUID v4 as the `client_id`. This uniquely identifies this Home Assistant instance and is sent to the server during registration.

### Step 3: Registration

The integration calls `POST /api/v1/installations/register` with:

- `client_id` -- The generated UUID
- `hostname` -- Automatically detected as `<hostname>.local:<port>` (e.g., `homeassistant.local:8123`)
- `name` -- The friendly name from user input, or the HA location name as a fallback
- `pairing_code` -- The code, normalised (uppercase, no dash), when one was given

The pairing code is never stored: it is single use and spent by this call.

### Step 4: Store Config Entry

On successful registration, the server returns an `installation_id` and `access_token`. The integration stores all values in a Home Assistant config entry.

### Error Handling

| Error | Cause | Resolution |
|-------|-------|------------|
| `invalid_pairing_code` | The code is wrong, has expired (30 minutes) or was already used | Ask your installer for a new code |
| `too_many_attempts` | Too many tries in a row; the server is throttling | Wait a minute, then try again |
| `invalid_registration_secret` | (Own server) the secret is missing or wrong | Check the value with whoever runs the server |
| `cannot_connect` | Server unreachable or returned a connection error | Verify the server URL and network connectivity |
| `unknown` | Unexpected response from the server (missing fields, timeout, etc.) | Check Home Assistant logs and server logs for details |

## What Gets Stored

The config entry stores four values in Home Assistant's internal configuration storage:

| Key | Description | Example |
|-----|-------------|---------|
| `server_url` | Base URL of the HA Dispatch server | `http://192.168.1.50:8080` |
| `client_id` | UUID v4 generated during setup | `a1b2c3d4-e5f6-7890-abcd-ef1234567890` |
| `installation_id` | Server-assigned numeric ID | `42` |
| `access_token` | Bearer token for API authentication | `eyJhbGciOi...` |

These values are stored securely in Home Assistant's `.storage/` directory and are not exposed to the user interface after initial setup.

### Viewing Stored Values

The `installation_id` is visible as an attribute on the `sensor.ha_dispatch_status` entity. You can see it in **Developer Tools** -> **States**.

The `server_url` can be seen by going to **Settings** -> **Devices & Services** -> **HA Dispatch Client** and viewing the integration entry.

The `access_token` and `client_id` are not displayed in the UI. They are used internally for API authentication.

## Entity Configuration

The integration creates three sensor entities automatically during setup. No additional entity configuration is needed.

| Entity | Type | Source |
|--------|------|--------|
| `sensor.ha_dispatch_status` | String (online/offline/warning) | Coordinator status report |
| `sensor.ha_dispatch_cpu_load` | Numeric (load average) | `psutil` 1-minute load average |
| `sensor.ha_dispatch_memory_used` | Numeric (percentage) | `psutil` virtual memory percent |

All sensors inherit from `CoordinatorEntity` and update whenever the `HADispatchCoordinator` completes a data refresh cycle.

### Options Flow

The integration includes a placeholder options flow accessible via **Settings** -> **Devices & Services** -> **HA Dispatch Client** -> **Configure**. Currently, no user-configurable options are exposed. Future versions may add options such as custom scan intervals or feature toggles.

## How Config Updates Work from Server

The HA Dispatch server can push configuration changes to connected installations. This is how the update cycle works:

### The Update Cycle

1. Every 60 seconds (default), the coordinator fetches the latest status from the server by calling `GET /api/v1/installations/{id}/config`.
2. The server responds with either:
   - **200 OK** with a new configuration payload (if `config_version` has changed)
   - **204 No Content** (if no configuration update is pending)
3. If a new configuration is received, the coordinator applies the changes.

### What Can Be Updated Remotely

The server can push a `desired_state` JSON object. Example:

```json
{
  "report_interval": 30,
  "thresholds": {
    "disk_free_percent_warn": 15,
    "cpu_load_warn": 70,
    "memory_used_percent_warn": 85
  },
  "features": {
    "metrics_enabled": true,
    "alerts_enabled": true
  }
}
```

When the client receives this:
- The `report_interval` value changes how frequently the coordinator polls (e.g., from 60s to 30s).
- The `config_version` attribute on `sensor.ha_dispatch_status` increments.

### Verifying Config Updates

Check Home Assistant logs for:
```
Received new configuration version: 2
Applied configuration version 2
Updated poll interval to 30 seconds
```

You can also verify by looking at the `config_version` attribute on `sensor.ha_dispatch_status` in **Developer Tools** -> **States**.

## Related Guides

- [Installation Guide](installation.md) -- Installation methods and setup flow
- [Deployment Guide](deployment.md) -- How to deploy updates to Home Assistant
- [Troubleshooting](troubleshooting.md) -- Fixing configuration-related issues
