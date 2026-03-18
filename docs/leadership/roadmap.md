# Roadmap

## Built (MVP -- v1.2.2)

The following capabilities are complete and available today.

- **Automatic registration** -- Client self-registers with the server on first setup
- **Status reporting** -- Heartbeat sent to the server every 60 seconds
- **System metrics** -- Processor, memory, disk, and uptime data collected and submitted automatically
- **Configuration management** -- Server-pushed configuration changes are detected and applied without user intervention
- **Alert submission and resolution** -- Full lifecycle alert support (create, describe, resolve)
- **Six management services** -- Testing, custom metrics, alert creation, alert resolution, and on-demand refresh
- **Three sensor entities** -- Installation status, processor load, and memory usage visible in Home Assistant
- **Graphical setup** -- No file editing required; the entire configuration is done through the Home Assistant interface
- **Secure credential handling** -- Tokens and identifiers stored using Home Assistant's secure storage

## Planned

The following enhancements are under consideration for future releases.

- **Offline metric buffering** -- Store metrics locally when the server is unreachable and submit them automatically once the connection is restored, preventing data gaps during outages
- **Batch submission optimization** -- Send multiple metric data points in a single server request to reduce network overhead and improve efficiency
- **Real-time updates via WebSocket** -- Replace or supplement the current polling model with a persistent connection to the server for instant configuration changes and alert delivery
- **HACS distribution** -- Package the client for the Home Assistant Community Store, making installation and updates a one-click process for any user
- **Additional sensor types** -- Add sensors for disk usage, system uptime, connection status, and warning indicators to provide richer local visibility
- **Alert polling and notifications** -- Retrieve active alerts from the server and surface them as Home Assistant notifications, so users can see server-side alerts directly on their local dashboard

## Not Planned (Currently)

The following items are explicitly out of scope for the foreseeable future.

- **Multi-user client** -- The client is designed for a single server connection per Home Assistant installation. Supporting multiple servers or user accounts from a single client is not planned.
- **Mobile application** -- HA Dispatch is accessed through the server's web dashboard and through Home Assistant's own interface. A standalone mobile app is not on the roadmap.
- **Direct device control from server** -- The server monitors and manages installation-level health and configuration. It does not send commands to individual smart-home devices (lights, locks, thermostats, etc.) through the client. Device control remains within each Home Assistant instance.
