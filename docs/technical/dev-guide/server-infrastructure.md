<!-- Split from CLIENT_SIDE_DEVELOPMENT_GUIDE.md -->

# Server Infrastructure

## Technology Stack

### Backend Framework: Laravel 12
- **Eloquent ORM**: Database models and relationships
- **Laravel Sanctum**: API token authentication
- **Laravel Broadcasting**: Real-time WebSocket events
- **Laravel Queue**: Background job processing
- **Laravel Scheduler**: Periodic tasks
- **Middleware**: Custom authentication for installation tokens

### Frontend/Admin: Filament 4
- **Resources**: CRUD operations for installations, alerts, users
- **Widgets**: Dashboard with real-time monitoring
- **Notifications**: In-app alert system
- **Charts**: Metrics visualization with Chart.js
- **Forms**: Configuration and settings management
- **Shield Integration**: Role-based permissions

### Infrastructure
- **MySQL 8**: Primary database
- **Nginx**: Web server and reverse proxy
- **PHP 8.3-FPM**: Application runtime
- **Docker Compose**: Containerized deployment
- **Queue Worker**: Background job processor
- **Node.js**: Frontend asset compilation

## Key Laravel Packages
- `laravel/sanctum` - API authentication
- `beyondcode/laravel-websockets` - WebSocket server
- `filament/filament` - Admin panel framework
- `spatie/laravel-permission` - Role and permission management
- `bezhansalleh/filament-shield` - Filament permission integration

## Default Settings (Configurable via Admin Panel)

### Alert Thresholds
- CPU Load Warning: 80%
- Memory Usage Warning: 90%
- Disk Free Warning: 10%
- Offline Threshold: 5 minutes

### Data Retention
- Metrics Retention: 30 days
- Alerts Retention: 90 days
- Soft Deletes Retention: 30 days

### Monitoring Intervals
- Default Poll Interval: 60 seconds
- Health Check Frequency: 300 seconds (5 minutes)
- Metrics Prune Time: 03:00 daily

### Application Settings
- Brand Name: "HA Dispatch"
- Timezone: UTC
- Dashboard Refresh Rate: 30 seconds

## Scheduled Jobs
- **CheckInstallationHealthJob**: Every 5 minutes (marks offline installations)
- **PruneOldMetricsJob**: Daily at 03:00 (deletes old metrics)
- **PruneOldAlertsJob**: Daily at 03:30 (deletes old resolved alerts)
- **PruneOldSoftDeletesJob**: Weekly Sunday 04:00 (permanent deletion)

---

## Related Documentation

- [System Architecture](system-architecture.md) for the high-level overview
- [Authentication & Security](authentication.md) for token and middleware details
- [Configuration Management](configuration.md) for how server pushes configuration to clients
