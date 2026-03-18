# Capabilities

## Feature Matrix

The table below outlines every capability delivered in the current version (v1.2.2) of the HA Dispatch Client.

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
| Secure credential storage | Server credentials (access tokens, installation identifiers) are stored using Home Assistant's built-in secure configuration storage. | Delivered |

## Summary

- **6 services** for testing, monitoring, and management
- **3 sensor entities** for at-a-glance local monitoring
- **Fully automated** registration, reporting, and configuration management
- **No manual configuration files** -- everything is set up through the user interface
