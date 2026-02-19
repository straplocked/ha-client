# Quick Start Guide

Get your HA Dispatch Client up and running in 5 minutes!

## Prerequisites

- ✅ HA Dispatch server running and accessible
- ✅ Home Assistant instance (2024.1.0+)
- ✅ SSH/terminal access to Home Assistant

## Step 1: Install (1 minute)

### Easy Method
```bash
cd /home/straplocked/Documents/ha-client
./install.sh /path/to/homeassistant/config
```

### Manual Method
```bash
cp -r /home/straplocked/Documents/ha-client/custom_components/ha_dispatch_client \
     /path/to/homeassistant/config/custom_components/
```

## Step 2: Restart Home Assistant (1 minute)

```bash
# Via CLI
ha core restart

# Or via UI: Settings → System → Restart
```

Wait for restart to complete.

## Step 3: Add Integration (2 minutes)

1. Open Home Assistant web UI
2. Go to **Settings** → **Devices & Services**
3. Click **+ Add Integration** (bottom right)
4. Search for "**HA Dispatch**"
5. Click "**HA Dispatch Client**"
6. Enter your server URL: `http://your-server:8080`
7. Enter installation name (optional): `My Home Assistant`
8. Click **Submit**

✅ Done! Integration should configure successfully.

## Step 4: Verify (1 minute)

### Check Entities
Go to **Developer Tools** → **States**

Search for:
- `sensor.ha_dispatch_status` → Should show "online"
- `sensor.ha_dispatch_cpu_load` → Should show a number
- `sensor.ha_dispatch_memory_used` → Should show percentage

### Check Server
1. Open server admin: `http://your-server:8080/admin`
2. Go to **Monitoring** → **Installations**
3. Find your installation → Should show **Online** (green)

## Troubleshooting

### "Cannot connect" error
- Check server URL is correct
- Verify server is running: `curl http://your-server:8080/api/v1/installations/register`
- Check network connectivity

### Entities show "Unavailable"
- Check logs: `ha core logs | grep ha_dispatch`
- Restart Home Assistant
- Verify server is accessible

### Integration not found
- Verify files copied to correct location
- Check `custom_components/ha_dispatch_client/manifest.json` exists
- Restart Home Assistant

## What's Next?

- Read **README.md** for detailed documentation
- Follow **TESTING.md** for comprehensive testing
- Review **IMPLEMENTATION_SUMMARY.md** for technical details

## Need Help?

Check the logs:
```bash
ha core logs | grep ha_dispatch_client
```

Look for errors or warnings and consult the troubleshooting sections in README.md or TESTING.md.

---

**That's it!** Your Home Assistant is now reporting to HA Dispatch! 🎉

The integration will:
- ✅ Send status updates every 60 seconds
- ✅ Report system metrics (CPU, memory, disk)
- ✅ Automatically update when you change config on server
- ✅ Show status in 3 sensor entities

