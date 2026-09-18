# Roadmap

## Built (v1.5.0)

The following capabilities are complete and available today.

- **Automatic registration** -- Client self-registers with the server on first setup
- **Status reporting** -- Heartbeat sent to the server every 60 seconds
- **System metrics** -- Processor, memory, disk, and uptime data collected and submitted automatically
- **Configuration management** -- Server-pushed configuration changes are detected and applied without user intervention
- **Alert submission and resolution** -- Full lifecycle alert support (create, describe, resolve)
- **Seven management services** -- Testing, custom metrics, alert creation, alert resolution, on-demand refresh, and update installation
- **Three sensor entities** -- Installation status, processor load, and memory usage visible in Home Assistant
- **Graphical setup** -- No file editing required; the entire configuration is done through the Home Assistant interface
- **Secure credential handling** -- Tokens and identifiers stored using Home Assistant's secure storage
- **Community Store packaging** -- The client is packaged for the Home Assistant Community Store, so users outside a managed fleet can install and update it the standard way
- **Version reporting** -- Each installation reports which client build it is running, so the server can see at a glance which are out of date
- **Client self-update (client side)** -- The client can install a newer version of itself and restart, driven either from the Home Assistant interface or automatically on a schedule. Releases must carry a cryptographic signature that the client checks against a key built into it, so the management server can decide when an update happens but never what software is installed. Completing the feature requires the corresponding server-side work below

## Planned

The following enhancements are under consideration for future releases.

- **Offline metric buffering** -- Store metrics locally when the server is unreachable and submit them automatically once the connection is restored, preventing data gaps during outages
- **Batch submission optimization** -- Send multiple metric data points in a single server request to reduce network overhead and improve efficiency
- **Real-time updates via WebSocket** -- Replace or supplement the current polling model with a persistent connection to the server for instant configuration changes and alert delivery
- **Fleet update control (server side)** -- The dashboard work that turns client self-update into a fleet capability: publishing signed releases, choosing which installations receive them, staged rollouts that start with a small group, and automatically stopping a rollout when updated installations stop reporting in. The client half is complete; this is what makes it usable across many instances. See the [self-update specification](../technical/self-update.md)
- **Additional sensor types** -- Add sensors for disk usage, system uptime, connection status, and warning indicators to provide richer local visibility
- **Alert polling and notifications** -- Retrieve active alerts from the server and surface them as Home Assistant notifications, so users can see server-side alerts directly on their local dashboard

## Not Planned (Currently)

The following items are explicitly out of scope for the foreseeable future.

- **Multi-user client** -- The client is designed for a single server connection per Home Assistant installation. Supporting multiple servers or user accounts from a single client is not planned.
- **Mobile application** -- HA Dispatch is accessed through the server's web dashboard and through Home Assistant's own interface. A standalone mobile app is not on the roadmap.
- **Direct device control from server** -- The server monitors and manages installation-level health and configuration. It does not send commands to individual smart-home devices (lights, locks, thermostats, etc.) through the client. Device control remains within each Home Assistant instance.
