# Deployment Guide

This guide consolidates everything about deploying the HA Dispatch Client to your Home Assistant instance: using the deploy script, managing versions, handling caching issues, and troubleshooting deployment problems.

## Quick Deploy

The unified `deploy.sh` script handles both fresh installations and updates in a single command:

```bash
./deploy.sh
```

The script will:
- Detect if this is a fresh install or an update
- Prompt for SSH credentials (or use environment variables)
- Create a backup if updating
- Deploy all files
- Verify the installation
- Offer to restart Home Assistant

### Environment Variables

Skip interactive prompts by setting environment variables:

```bash
HA_HOST="192.168.1.100" HA_USER="root" HA_PASS="mypassword" ./deploy.sh
```

| Variable | Default | Description |
|----------|---------|-------------|
| `HA_HOST` | `homeassistant.local` | Hostname or IP of your HA instance |
| `HA_USER` | `straplocked` | SSH username |
| `HA_PORT` | `22` | SSH port |
| `HA_PASS` | (prompted) | SSH password |

### What Happens During Deployment

**For fresh installs:**
1. Creates `/config/custom_components/` directory
2. Uploads all integration files
3. Verifies installation
4. Prompts to restart
5. Shows setup instructions

**For updates:**
1. Creates timestamped backup (e.g., `ha_dispatch_client_backup_20251116-143022`)
2. Uploads updated files
3. Verifies deployment
4. Prompts to restart
5. Shows restore instructions

### After Deployment

**First time install:**
1. Restart Home Assistant
2. Go to Settings -> Devices & Services
3. Add Integration -> Search "HA Dispatch Client"
4. Enter server URL

**After update:**
1. Restart Home Assistant
2. Services are automatically available
3. Test with: Developer Tools -> Services

## Advanced Options

### Deploy to a Different Host

```bash
HA_HOST="ha-test.local" ./deploy.sh
```

### Deploy with a Custom Port

```bash
HA_PORT="22222" ./deploy.sh
```

### Non-Interactive Deploy

```bash
HA_HOST="192.168.1.100" HA_USER="root" HA_PASS="mypassword" ./deploy.sh
```

### Script Features

- **Auto-detection**: Knows if it is an install or update
- **Safe backups**: Always backs up before updating
- **Verification**: Counts files to ensure complete deployment
- **Feature detection**: Checks for services and key components
- **Permission handling**: Uses sudo automatically when needed
- **Smart restart**: Offers to restart HA after deployment

## Version Management

### Version Format

The project uses [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`

| Component | When to increment | Example |
|-----------|-------------------|---------|
| MAJOR | Breaking changes | 2.0.0 |
| MINOR | New features (backward compatible) | 1.2.0 |
| PATCH | Bug fixes (backward compatible) | 1.1.1 |

### Bumping the Version

```bash
./bump_version.sh
```

The script prompts you to choose:
- **Patch** (bug fixes)
- **Minor** (new features)
- **Major** (breaking changes)
- **Custom** (enter manually)

It automatically updates:
- `VERSION` file
- `custom_components/ha_dispatch_client/manifest.json`
- `CHANGELOG.md`

### Version Workflow

```bash
# 1. Make changes and test
nano custom_components/ha_dispatch_client/coordinator.py
./deploy.sh
# Verify fix on HA

# 2. Bump version
./bump_version.sh
# Select: 1 (Patch)
# Enter: "Fixed uptime calculation bug"

# 3. Deploy new version
./deploy.sh
# Shows: Version: 1.1.1

# 4. Commit changes
git add VERSION CHANGELOG.md custom_components/
git commit -m "v1.1.1: Fixed uptime calculation bug"
```

### Checking Versions

```bash
# Check current local version
cat VERSION

# Compare local vs deployed version
./check_version.sh

# Check what is deployed on the server
ssh straplocked@homeassistant.local "grep version /config/custom_components/ha_dispatch_client/manifest.json"
```

### Files Involved

| File | Purpose |
|------|---------|
| `VERSION` | Single line file with current version number |
| `manifest.json` | Integration metadata including the `version` field |
| `CHANGELOG.md` | Documents all changes per version |

The `VERSION` file and the `version` field in `manifest.json` must always stay in sync. The `bump_version.sh` script handles this automatically.

## Checking the Home 3D room plan by hand

The room-first plan card is JavaScript, so the automated suite can only prove
that the file was copied, that its Lovelace resource is registered once with a
version stamp, and that the dashboard config is shaped the way the card
expects. These four checks are the part only a person can do, and are worth
running after a floorplan deploy on a real installation:

1. **The card loaded at all.** Open the *Home 3D* dashboard. Rooms outlined on
   the plan means the resource loaded; a "Custom element doesn't exist:
   ayvra-room-plan" box means it did not -- check
   Settings -> Dashboards -> Resources for
   `/local/ha_dispatch/floorplan/ayvra-room-plan.js?v=...`, and that the file
   is in `/config/www/ha_dispatch/floorplan/`. If the file is there and the
   resource is listed but the browser still 404s it, `/config/www` did not
   exist when Home Assistant started -- it only registers `/local/` at
   startup -- so restart once. This affects the plan images the same way.
2. **A room taps.** Tapping inside a room dims the rest of the floor and opens
   that room's panel. Tapping the plan *outside* any room does nothing, which
   is correct -- rooms are the only hit targets.
3. **The controls are live.** A light or switch in the panel toggles the real
   device, a thermostat's +/- changes the real setpoint, and a sensor shows a
   reading. Change something from another client and the panel follows it.
4. **It closes both ways.** Escape closes the panel on a laptop; a tap outside
   the panel closes it on a tablet. Both must work -- the installer's laptop
   has a keyboard and the wall tablet does not.

A room that reports "No devices are assigned to this room yet" is not a bug:
the automatic placement is a best guess from an insurance estimate. Use the
`assign_floorplan_device` service to put the device in the right room (see
`docs/user/services-guide.md`); the plan redraws immediately.

## Caching and Clean Deploy

Home Assistant aggressively caches certain files in memory. After deploying changes to these files, a normal restart may not be enough.

### Which Files Are Cached

| File | Caching Behavior | Notes |
|------|-----------------|-------|
| `manifest.json` | Heavily cached | Needs restart or clean deploy |
| `strings.json` | Heavily cached | Needs restart |
| Python files (`*.py`) | Moderately cached | Reload usually works |
| `services.yaml` | Moderately cached | Reload usually works |
| `www/ayvra-room-plan.js` | Cached by the **browser**, not HA | Served from `/local/`; the deploy registers it with a `?v=<version>` stamp so a new version is fetched, but a browser already holding the old URL may need one hard refresh |

**Rule of thumb:** If you change `manifest.json`, expect to need a clean deploy.

### When to Use Normal Deploy vs Clean Deploy

**Normal deploy** (`./deploy.sh`) is sufficient for:
- Code changes (`__init__.py`, `coordinator.py`, etc.)
- Adding new files
- Bug fixes
- Service changes
- Most updates

**Clean deploy** is required for:
- Version bumps in `manifest.json`
- Domain name changes
- Requirement changes
- When the integration does not reload properly

### Clean Deploy Procedure

When `manifest.json` changes (especially the version), follow this sequence:

```bash
# 1. Delete the integration folder on the HA server
ssh straplocked@homeassistant.local "sudo rm -rf /config/custom_components/ha_dispatch_client"

# 2. Restart Home Assistant to clear the memory cache
ssh straplocked@homeassistant.local "sudo ha core restart"

# 3. Wait for restart (1-2 minutes), then deploy fresh files
./deploy.sh

# 4. Restart again to load the new manifest cleanly
ssh straplocked@homeassistant.local "sudo ha core restart"

# 5. Re-add the integration in the UI
# Settings -> Devices & Services -> + Add Integration -> "HA Dispatch Client"
```

**Why this works:**
- Deleting files ensures no old manifest is cached.
- The first restart clears Home Assistant's memory cache.
- `deploy.sh` installs fresh files.
- The second restart loads the new manifest cleanly.
- Re-adding creates a fresh config entry.

### Quick Clean Deploy Script

```bash
#!/bin/bash
# clean_deploy.sh
HA_HOST="${1:-homeassistant.local}"
HA_USER="${2:-straplocked}"
HA_PORT="${3:-22}"

echo "Clean deployment (removes old files first)"

# Delete old files
echo "Removing old files..."
ssh -p $HA_PORT $HA_USER@$HA_HOST "sudo rm -rf /config/custom_components/ha_dispatch_client"

# Deploy new files
echo "Deploying fresh files..."
./deploy.sh

echo "Clean deployment complete!"
echo ""
echo "IMPORTANT: You must now:"
echo "1. Delete the integration in HA UI"
echo "   Settings -> Devices & Services -> HA Dispatch Client -> Delete"
echo "2. Restart HA: sudo ha core restart"
echo "3. Re-add the integration with your server URL"
```

## Troubleshooting

### Connection Failed

```bash
# Check if HA is reachable
ping homeassistant.local

# Try with IP address instead
HA_HOST="192.168.1.100" ./deploy.sh
```

### Permission Denied

```bash
# Verify you have sudo access
ssh straplocked@homeassistant.local "sudo -v"
```

### Version Not Updating After Deploy

Try these steps in order:

1. **Reload integration**: Settings -> Devices & Services -> HA Dispatch Client -> (three dots) -> Reload
2. **Hard refresh browser**: `Ctrl+Shift+R` (Windows/Linux) or `Cmd+Shift+R` (Mac)
3. **Clear browser cache**: Open DevTools (F12), right-click refresh button, select "Empty Cache and Hard Reload"
4. **Restart Home Assistant**: `ssh straplocked@homeassistant.local "sudo ha core restart"`
5. **Clean deploy**: Follow the clean deploy procedure above.

### Restore a Backup

If something goes wrong after an update:

```bash
# List backups
ssh straplocked@homeassistant.local "ls -lh /config/custom_components/ | grep backup"

# Restore (replace TIMESTAMP with your backup timestamp)
ssh straplocked@homeassistant.local "
    sudo rm -rf /config/custom_components/ha_dispatch_client &&
    sudo mv /config/custom_components/ha_dispatch_client_backup_TIMESTAMP /config/custom_components/ha_dispatch_client &&
    sudo ha core restart
"
```

### Clean Up Old Backups

```bash
# Remove all old backups except the latest
ssh straplocked@homeassistant.local "
    cd /config/custom_components &&
    ls -t ha_dispatch_client_backup_* | tail -n +2 | xargs sudo rm -rf
"
```

### Verify Deployment

```bash
# List deployed files
ssh straplocked@homeassistant.local "ls -lh /config/custom_components/ha_dispatch_client/"

# Check deployed version
ssh straplocked@homeassistant.local "grep version /config/custom_components/ha_dispatch_client/manifest.json"

# View HA logs for the integration
ssh straplocked@homeassistant.local "tail -f /config/home-assistant.log | grep ha_dispatch"
```

## File Locations

| Location | Path |
|----------|------|
| Local source | `custom_components/ha_dispatch_client/` |
| Remote (deployed) | `/config/custom_components/ha_dispatch_client/` |
| Backups | `/config/custom_components/ha_dispatch_client_backup_*/` |

## Best Practices

1. **Test locally first** -- Make changes and verify them before deploying.
2. **Increment the version** -- Use `./bump_version.sh` to keep versions in sync.
3. **Update the changelog** -- Document what changed in each version.
4. **Check logs after deploy** -- Look for errors in Home Assistant logs.
5. **Verify services** -- Test services in Developer Tools -> Services after every deploy.

## Quick Command Reference

```bash
# Deploy with defaults
./deploy.sh

# Deploy to specific host
HA_HOST="192.168.1.50" ./deploy.sh

# Deploy non-interactively
HA_PASS="mypassword" ./deploy.sh

# Deploy to different user/port
HA_USER="root" HA_PORT="22222" ./deploy.sh

# Bump version
./bump_version.sh

# Check current version
cat VERSION

# Compare local vs deployed
./check_version.sh
```

## Related Guides

- [Installation Guide](installation.md) -- First-time setup and entity descriptions
- [Configuration Guide](configuration.md) -- What gets stored during setup
- [Troubleshooting](troubleshooting.md) -- General troubleshooting for all issues
