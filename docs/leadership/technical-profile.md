# Technical Profile

A lightweight overview of the technology, performance characteristics, security posture, and compatibility of the HA Dispatch Client.

## Technology Stack

| Layer | Technology | Role |
|-------|-----------|------|
| Language | Python 3 (asynchronous) | All client logic is written in Python using modern asynchronous patterns for efficient, non-blocking operation |
| Platform | Home Assistant Custom Integration | The client runs as a native Home Assistant plugin, following all official development standards |
| Server | Laravel 12 with Filament 4 | The HA Dispatch server that receives data from all clients and provides the management dashboard |
| Communication | HTTP/HTTPS with bearer token authentication | All data exchange between client and server uses standard web protocols with secure token-based identity verification |

## Performance

The client is designed to have a negligible impact on the Home Assistant host system.

| Metric | Typical Value |
|--------|---------------|
| Memory usage | 10 to 20 megabytes |
| Processor usage | Less than 1 percent on average, with brief spikes during data collection |
| Network traffic | Approximately 1 kilobyte per minute |
| Disk activity | Minimal -- limited to reading and writing configuration data |
| Reporting interval | Every 60 seconds (adjustable from the server) |

## Security

| Measure | Description |
|---------|-------------|
| Authentication | Every request to the server includes a bearer token issued during registration. No data is sent without identity verification. |
| Credential storage | Access tokens, installation identifiers, and server details are stored in Home Assistant's built-in secure configuration storage -- not in plain-text files. |
| Transport encryption | The client supports HTTPS for encrypted communication between the client and the server. HTTPS is recommended for all production deployments. |
| Data privacy | No sensitive device data, user credentials, or personal information is sent to the server. Only system-level performance metrics and installation health data are transmitted. |
| Logging hygiene | Authentication tokens and credentials are never written to log files. |

## Dependencies

The client relies on two external libraries, both widely used and well maintained.

| Library | Purpose |
|---------|---------|
| aiohttp (version 3.8.0 or later) | Handles all network communication with the server using efficient asynchronous requests |
| psutil (version 5.9.0 or later) | Collects system performance data (processor, memory, disk, uptime) from the host machine |

## Compatibility

| Requirement | Minimum Version |
|-------------|-----------------|
| Home Assistant | 2024.1.0 or later |
| HA Dispatch Server | Any current instance |
| Python | 3.11 or later (included with Home Assistant) |
| Operating system | Any platform supported by Home Assistant (Linux, macOS, Windows) |
