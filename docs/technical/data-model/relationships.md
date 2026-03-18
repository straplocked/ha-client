<!-- Split from DATA_MODEL.md -->

# Data Models & Relationships

> See also: [Database Schema](schema.md) | [API Endpoints & Data Flow](api-endpoints.md) | [JSON Examples](json-examples.md) | [Business Rules & Constants](business-rules.md)
>
> Back to [Data Model Overview](README.md)

---

## Data Models

### Installation Model

**PHP Model:** `App\Models\Installation`

#### Relationships

```php
// One-to-many relationships
$installation->configurations()      // All configurations
$installation->metrics()             // All metrics
$installation->alerts()              // All alerts
$installation->unresolvedAlerts()    // Unresolved alerts only

// One-to-one relationships
$installation->activeConfiguration() // Current active config
$installation->latestMetric()        // Most recent metric
```

#### Scopes

```php
Installation::online()        // status = 'online'
Installation::offline()       // status = 'offline'
Installation::withWarnings()  // status = 'warning'
Installation::active()        // is_active = true
```

#### Key Methods

```php
$installation->isOnline(): bool      // Check if online based on last_seen_at
$installation->updateStatus(): void  // Update status based on alerts
```

---

### Configuration Model

**PHP Model:** `App\Models\InstallationConfiguration`

#### Business Logic

- Only one configuration can be `is_active = true` per installation
- `version` field is auto-incremented per installation
- Older configurations remain in database for audit trail
- Client compares local `config_version` with server version

---

### Metric Model

**PHP Model:** `App\Models\InstallationMetric`

#### Business Logic

- All metric fields except `recorded_at` are optional
- Metrics are pruned after retention period (default: 30 days)
- `recorded_at` can be historical (for offline buffering)
- Server updates installation's `last_seen_at` when metrics received

---

### Alert Model

**PHP Model:** `App\Models\InstallationAlert`

#### Alert Lifecycle

1. **Triggered**: Alert created when condition detected (`triggered_at` set)
2. **Acknowledged**: Admin acknowledges alert (optional)
3. **Resolved**: Condition no longer present (`resolved_at` set)
4. **Pruned**: Deleted after retention period (default: 90 days)

#### Severity Escalation

- **info**: Informational only, no action required
- **warning**: Attention needed, system functional
- **critical**: Immediate action required, system impaired

---

## Relationships

### Entity Relationship Diagram

```
┌─────────────────────┐
│    installations    │
│                     │
│  PK: id             │
│  UK: client_id      │
│  UK: access_token   │
└──────┬──────────────┘
       │
       │ 1:N
       │
       ├──────────────────────────┐
       │                          │
       ▼                          ▼
┌──────────────────┐    ┌──────────────────┐
│ configurations   │    │     metrics      │
│                  │    │                  │
│ PK: id           │    │ PK: id           │
│ FK: install_id   │    │ FK: install_id   │
│ UK: (install, v) │    │                  │
└──────────────────┘    └──────────────────┘

       │
       ▼
┌──────────────────┐
│     alerts       │
│                  │
│ PK: id           │
│ FK: install_id   │
│ FK: ack_by       │──┐
└──────────────────┘  │
                      │
                      ▼
               ┌──────────┐
               │  users   │
               │          │
               │ PK: id   │
               └──────────┘
```

### Relationship Details

#### Installation -> Configuration (1:N)

```sql
SELECT * FROM installation_configurations
WHERE installation_id = ?
ORDER BY version DESC;
```

**Active Configuration:**
```sql
SELECT * FROM installation_configurations
WHERE installation_id = ?
  AND is_active = true
ORDER BY version DESC
LIMIT 1;
```

#### Installation -> Metrics (1:N)

```sql
SELECT * FROM installation_metrics
WHERE installation_id = ?
ORDER BY recorded_at DESC;
```

**Latest Metric:**
```sql
SELECT * FROM installation_metrics
WHERE installation_id = ?
ORDER BY recorded_at DESC
LIMIT 1;
```

#### Installation -> Alerts (1:N)

```sql
SELECT * FROM installation_alerts
WHERE installation_id = ?
ORDER BY triggered_at DESC;
```

**Unresolved Alerts:**
```sql
SELECT * FROM installation_alerts
WHERE installation_id = ?
  AND resolved_at IS NULL
ORDER BY severity DESC, triggered_at ASC;
```
