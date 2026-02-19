# Deployment Tips & Tricks

## When Manifest Changes Don't Take Effect

**Problem:** After deploying a new `manifest.json` (e.g., version bump), Home Assistant still shows the old version.

**Root Cause:** Home Assistant aggressively caches `manifest.json` in memory. Simply deploying new files and restarting isn't always enough!

### ✅ Solution: Clean Deployment

When you change `manifest.json` (especially version), use this sequence:

```bash
# 1. SSH to HA server and delete the integration folder
ssh straplocked@homeassistant.local "sudo rm -rf /config/custom_components/ha_dispatch_client"

# 2. Restart Home Assistant
ssh straplocked@homeassistant.local "sudo ha core restart"

# 3. Wait for restart (1-2 minutes), then deploy
./deploy.sh

# 4. Restart again
ssh straplocked@homeassistant.local "sudo ha core restart"

# 5. Re-add the integration in the UI
# Settings → Devices & Services → + Add Integration → "HA Dispatch Client"
```

### Why This Works

- **Deleting files** ensures no old manifest is cached
- **First restart** clears Home Assistant's memory cache
- **Deploy** installs fresh files
- **Second restart** loads the new manifest cleanly
- **Re-adding** creates a fresh config entry

## Quick Clean Deploy Script

```bash
#!/bin/bash
# clean_deploy.sh - Nuclear option deployment

HA_HOST="${1:-homeassistant.local}"
HA_USER="${2:-straplocked}"
HA_PORT="${3:-22}"

echo "🧹 Clean deployment (removes old files first)"
echo ""

# Delete old files
echo "Removing old files..."
ssh -p $HA_PORT $HA_USER@$HA_HOST "sudo rm -rf /config/custom_components/ha_dispatch_client"

# Deploy new files
echo "Deploying fresh files..."
./deploy.sh

echo ""
echo "✅ Clean deployment complete!"
echo ""
echo "⚠️  IMPORTANT: You must now:"
echo "1. Delete the integration in HA UI"
echo "   Settings → Devices & Services → HA Dispatch Client → Delete"
echo "2. Restart HA: sudo ha core restart"
echo "3. Re-add the integration with your server URL"
```

## When to Use Clean Deploy vs Normal Deploy

### Use **Normal Deploy** (`./deploy.sh`) for:
- ✅ Code changes (`__init__.py`, `coordinator.py`, etc.)
- ✅ Adding new files
- ✅ Bug fixes
- ✅ Service changes
- ✅ Most updates

### Use **Clean Deploy** for:
- 🧹 Version bumps in `manifest.json`
- 🧹 Domain name changes
- 🧹 Requirement changes
- 🧹 When integration doesn't reload properly
- 🧹 "Nuclear option" troubleshooting

## Troubleshooting Version Not Updating

If version still shows old after normal deploy:

### Try 1: Reload Integration
Settings → Devices & Services → HA Dispatch Client → ⋮ → Reload

### Try 2: Hard Refresh Browser
- Chrome/Firefox: `Ctrl+Shift+R`
- Mac: `Cmd+Shift+R`

### Try 3: Clear Browser Cache
1. Open DevTools (F12)
2. Right-click refresh button
3. Select "Empty Cache and Hard Reload"

### Try 4: Restart Home Assistant
```bash
ssh straplocked@homeassistant.local "sudo ha core restart"
```

### Try 5: Clean Deploy (Nuclear Option)
Follow the clean deployment sequence above.

## Best Practices

1. **Test locally first** - Make changes, test, THEN deploy
2. **Increment version** - Use `./bump_version.sh` for tracking
3. **Read CHANGELOG** - Document what changed
4. **Check logs** - After deploy, check HA logs for errors
5. **Verify services** - Test in Developer Tools → Services

## Home Assistant Caching Behavior

Home Assistant caches these files heavily:
- ✅ `manifest.json` - **Heavily cached** (needs restart/reload)
- ✅ `strings.json` - **Heavily cached** (needs restart)
- ⚠️ Python files (`*.py`) - **Moderately cached** (reload usually works)
- ✅ `services.yaml` - **Moderately cached** (reload usually works)

**Rule of Thumb:** If you change `manifest.json`, expect to need a clean deploy!

## Version History Tracking

After deploying a new version:

1. Check it shows correctly in HA UI
2. Note it in your local tracking:
   ```bash
   echo "Deployed v$(cat VERSION) on $(date)" >> DEPLOYMENT_LOG.md
   ```

## Related Commands

```bash
# Check what's deployed on server
ssh straplocked@homeassistant.local "grep version /config/custom_components/ha_dispatch_client/manifest.json"

# Compare local vs deployed
./check_version.sh

# View HA logs for our integration
ssh straplocked@homeassistant.local "tail -f /config/home-assistant.log | grep ha_dispatch"

# List all custom integrations
ssh straplocked@homeassistant.local "ls -lh /config/custom_components/"
```

## Lessons Learned

**2025-11-16:** Version 1.1.0 deployment required clean deploy. Simply updating files and restarting wasn't enough. Had to:
1. Delete `/config/custom_components/ha_dispatch_client`
2. Restart HA
3. Deploy fresh files
4. Restart HA again
5. Re-add integration in UI

This is now the recommended approach for manifest changes!

