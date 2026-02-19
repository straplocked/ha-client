# Deploy Script - Quick Reference

## One Command to Deploy Everything 🚀

This unified script handles both **fresh installation** and **updates**.

### Basic Usage

```bash
cd /home/straplocked/Documents/ha-client
./deploy.sh
```

That's it! The script will:
- ✅ Detect if it's an install or update
- ✅ Prompt for SSH password
- ✅ Create backup (if updating)
- ✅ Deploy all files
- ✅ Verify installation
- ✅ Offer to restart HA

### Advanced Usage

#### Use Environment Variables (skip prompts)

```bash
HA_HOST="192.168.1.100" HA_USER="root" HA_PASS="mypassword" ./deploy.sh
```

#### Custom Port

```bash
HA_PORT="22222" ./deploy.sh
```

#### Deploy to Different Host

```bash
HA_HOST="ha-test.local" ./deploy.sh
```

### What It Does

#### For Fresh Install:
1. Creates `/config/custom_components/` directory
2. Uploads all integration files
3. Verifies installation
4. Prompts to restart
5. Shows setup instructions

#### For Updates:
1. Creates timestamped backup (e.g., `ha_dispatch_client_backup_20251116-143022`)
2. Uploads updated files
3. Verifies deployment
4. Prompts to restart
5. Shows restore instructions

### Features

- **Auto-detection**: Knows if it's install or update
- **Safe backups**: Always backs up before updating
- **Verification**: Counts files to ensure complete deployment
- **Feature detection**: Checks for services and key components
- **Permission handling**: Uses sudo automatically when needed
- **Smart restart**: Offers to restart HA after deployment
- **Helpful output**: Shows what was deployed and what to do next

### After Deployment

#### First Time Install:
1. Restart Home Assistant
2. Go to Settings → Devices & Services
3. Add Integration → Search "HA Dispatch Client"
4. Enter server URL

#### After Update:
1. Restart Home Assistant
2. Services automatically available
3. Test with: Developer Tools → Services

### Troubleshooting

#### Connection Failed
```bash
# Check if HA is reachable
ping homeassistant.local

# Try with IP address instead
HA_HOST="192.168.1.100" ./deploy.sh
```

#### Permission Denied
```bash
# Make sure you have sudo access
ssh straplocked@homeassistant.local "sudo -v"
```

#### Restore Backup
If something goes wrong:
```bash
# List backups
ssh straplocked@homeassistant.local "ls -lh /config/custom_components/ | grep backup"

# Restore (replace timestamp with your backup)
ssh straplocked@homeassistant.local "
    sudo rm -rf /config/custom_components/ha_dispatch_client &&
    sudo mv /config/custom_components/ha_dispatch_client_backup_TIMESTAMP /config/custom_components/ha_dispatch_client &&
    sudo ha core restart
"
```

### Clean Up Old Backups

```bash
# Remove all old backups (keep latest)
ssh straplocked@homeassistant.local "
    cd /config/custom_components &&
    ls -t ha_dispatch_client_backup_* | tail -n +2 | xargs sudo rm -rf
"
```

## Old Scripts (Can Be Deleted)

Once you confirm `deploy.sh` works, you can delete these old scripts:

### Install Scripts (REPLACED by deploy.sh)
- `INSTALL_REMOTE.sh`
- `INSTALL_REMOTE_SUDO.sh`
- `INSTALL_REMOTE_INTERACTIVE.sh`
- `INSTALL_REMOTE_TAR.sh`
- `install.sh`

### Update Scripts (REPLACED by deploy.sh)
- `UPDATE_REMOTE.sh`
- `UPDATE_WITH_DEBUG.sh`
- `UPDATE_WITH_DEBUG_INTERACTIVE.sh`
- `PUSH_SERVICE_FIX.sh`

### Download Scripts (Keep if needed for reference)
- `DOWNLOAD_MEROSS_LAN.sh`
- `DOWNLOAD_MEROSS_LAN_AUTO.sh`

### Total: ~10 old scripts → 1 new script ✅

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

# View deploy script help
head -20 deploy.sh
```

## File Locations

**Local:** `/home/straplocked/Documents/ha-client/custom_components/ha_dispatch_client/`  
**Remote:** `/config/custom_components/ha_dispatch_client/`  
**Backups:** `/config/custom_components/ha_dispatch_client_backup_*/`

## Support

If you encounter issues:
1. Check Home Assistant logs: Settings → System → Logs
2. Check SSH connection: `ssh straplocked@homeassistant.local`
3. Verify files deployed: `ssh straplocked@homeassistant.local "ls -lh /config/custom_components/ha_dispatch_client/"`
4. Review backup if needed (see Restore Backup above)

