# Executive Summary

## The Problem

Home Assistant is a powerful platform for smart-home automation, but organizations and enthusiasts running multiple Home Assistant installations face a common challenge: **there is no built-in way to monitor and manage all of them from one place.** Each installation operates independently, with its own dashboard, its own logs, and its own alerts. As the number of installations grows, so does the operational burden of keeping them healthy, up to date, and responsive.

## The Solution

**HA Dispatch** is a centralized monitoring and management platform purpose-built for multi-installation Home Assistant environments. It consists of two components:

- **HA Dispatch Server** -- A web-based dashboard and data platform that collects information from every connected Home Assistant installation. It provides a single view of system health, performance metrics, configuration status, and alerts.
- **HA Dispatch Client** -- A lightweight plugin installed on each Home Assistant instance. It automatically registers with the server, reports system health every 60 seconds, and relays alerts and metrics without any manual intervention.

## How It Works (Plain Language)

1. A user installs the HA Dispatch Client on a Home Assistant instance and provides the server address.
2. The client automatically registers itself with the server and receives secure credentials.
3. Every 60 seconds, the client sends a health check to the server along with system performance data (processor usage, memory usage, disk space, uptime).
4. The server stores this data, displays it on a management dashboard, and can generate alerts when something looks wrong (high processor usage, low disk space, etc.).
5. The server can also push configuration changes back to each client, such as adjusting how frequently it reports in.

## Current Status

| Area | Status |
|------|--------|
| Version | 1.2.2 (MVP complete) |
| Services available | 6 (testing, alerts, metrics, management) |
| Sensor entities | 3 (status, processor load, memory usage) |
| Setup experience | Fully graphical -- no manual file editing required |
| Server communication | Automated registration, heartbeat, metrics, alerts |
| Deployment | Manual installation or script-based deployment |

## Value Proposition

- **Single pane of glass** -- See the health and status of every Home Assistant installation from one dashboard.
- **Proactive alerting** -- Know about problems (high resource usage, offline installations) before they affect end users.
- **Zero-touch operation** -- After initial setup, the client runs silently in the background with no ongoing maintenance.
- **Centralized configuration** -- Push settings changes to any installation from the server without logging into each one individually.
- **Lightweight footprint** -- The client uses minimal system resources and does not interfere with normal Home Assistant operation.
