# Capabilities

## Feature Matrix

The table below outlines every capability delivered in the current version (v1.6.0) of the HA Dispatch Client.

| Capability | Description | Status |
|------------|-------------|--------|
| Automatic registration | When installed on a Home Assistant instance, the client registers itself with the HA Dispatch server automatically. No manual account creation or token setup is required. | Delivered |
| Periodic status reporting | The client sends a heartbeat to the server every 60 seconds, confirming the installation is online and healthy. The server uses this to track uptime and detect offline installations. | Delivered |
| System metrics collection | Processor usage (1-minute, 5-minute, and 15-minute averages), memory usage (percentage and megabytes), disk usage (percentage and free space), and system uptime are collected each reporting cycle. | Delivered |
| Configuration updates from server | The client checks for configuration changes from the server every reporting cycle. If the server has pushed a new configuration (such as a different reporting interval), the client applies it automatically. | Delivered |
| Alert submission | The client can send alerts to the server, including severity level, alert type, title, descriptive message, and additional context. This supports both manual and automated alert workflows. | Delivered |
| Alert resolution | The client can tell the server to resolve all outstanding alerts of a given type, closing them out on the dashboard. | Delivered |
| Test metrics service | A built-in service allows operators to send sample data to the server to verify that the connection is working correctly. | Delivered |
| Alert trigger service | A built-in service for quick alert testing. Supports predefined alert scenarios: low disk space, high processor usage, high memory usage, or all three at once. | Delivered |
| Force update service | Triggers an immediate data refresh and server report without waiting for the next scheduled cycle. | Delivered |
| Custom metric service | Allows operators to submit arbitrary metric values to the server for tracking any data point beyond the standard system metrics. | Delivered |
| Full alert submission service | Provides complete control over alert creation, including severity, type, title, message, and contextual data. | Delivered |
| Alert resolution service | Resolves all unresolved alerts of a specified type on the server. | Delivered |
| Status sensor | A sensor entity within Home Assistant that displays the current installation status (online, offline, or warning) along with the configuration version and server-assigned installation identifier. | Delivered |
| Processor load sensor | A sensor entity that displays the current 1-minute processor load average, updated every reporting cycle. | Delivered |
| Memory usage sensor | A sensor entity that displays current memory usage as a percentage, updated every reporting cycle. | Delivered |
| Graphical setup | The entire setup process is handled through the Home Assistant user interface. Users enter the server address, optionally provide a friendly name, and the rest is automatic. No configuration files need to be edited. | Delivered |
| Consent for remote support | When a technician asks to access an installation, the request appears inside that Home Assistant as a notification and as a one-tap Approve or Decline dialog. It says who is asking, why, what they will be able to do, and for how long, in plain language. Nothing happens unless the homeowner agrees, and the prompt disappears the moment the request is answered or expires. | Delivered |
| Remote support off-switch | While a support session is live, the homeowner can end it immediately from inside Home Assistant. Ending it closes the session on the spot. | Delivered |
| Remote support session | Once access has been approved, the client carries the technician's individual requests to the local Home Assistant and returns the answers. The connection is always outbound, so no ports are opened on the customer's network, and read-only support sessions are technically incapable of changing anything. | Delivered |
| Remote access request sensor | A sensor entity that turns on while somebody is waiting for an answer, so the prompt can be routed to a phone, a light, or a speaker rather than waiting to be noticed. | Delivered |
| Version inventory reporting | Every half hour the client tells the server exactly what the installation is running -- Home Assistant itself, the operating system, add-ons, integrations, and Community Store downloads -- and flags anything Home Assistant reports as broken. This is what fills the dashboard's Components view, and it is the raw material the fleet's update advice is calculated from. | Delivered |
| Prompt reporting after an update | When the client updates itself it reports the new inventory straight away rather than waiting for the next half-hourly cycle. Advice about whether a version is safe depends on knowing when that version was actually installed. | Delivered |
| Secure credential storage | Server credentials (access tokens, installation identifiers) are stored using Home Assistant's built-in secure configuration storage. | Delivered |

## Summary

- **9 services** for testing, monitoring, management, and remote support
- **4 entities** for at-a-glance local monitoring, including a remote access request indicator
- **Fully automated** registration, reporting, and configuration management
- **No manual configuration files** -- everything is set up through the user interface
