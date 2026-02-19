# Features Added - Debug Services & Bug Fixes

## 🎉 What's New in v1.1.0

### 1. ✅ Fixed Negative Uptime Bug

**Problem Solved:**
- Client was sending negative uptime values (e.g., `-1761609031`)
- Server returned 422 validation errors
- Metrics submission was failing

**What Was Fixed:**
- Changed uptime calculation from `loop.time()` to `time.time()`
- Added sanity check for negative values
- Uptime is now always positive and correct

**Impact:**
- ✅ No more validation errors
- ✅ Metrics submit successfully
- ✅ Server accepts all metrics

---

### 2. 🛠️ Four New Debug Services

All services are accessible via **Developer Tools → Services** in Home Assistant!

#### Service 1: Send Test Metrics
**Name:** `ha_dispatch_client.send_test_metrics`

Send custom test metrics to verify server connectivity.

```yaml
service: ha_dispatch_client.send_test_metrics
data:
  cpu_load: 80        # 0-100%
  memory_used: 90     # 0-100%
  disk_free: 20       # 0-100%
```

**Use Cases:**
- Quick connectivity test
- Verify metrics submission works
- Test with normal values (won't trigger alerts)

---

#### Service 2: Trigger Alert
**Name:** `ha_dispatch_client.trigger_alert`

Send extreme metrics to trigger alerts on server for testing.

```yaml
service: ha_dispatch_client.trigger_alert
data:
  alert_type: disk_low  # Options: disk_low, cpu_high, memory_high, all
```

**What Each Type Does:**
- `disk_low` - Sends 5% disk free (triggers low disk alert)
- `cpu_high` - Sends 850% CPU load (triggers high CPU alert)
- `memory_high` - Sends 95% memory usage (triggers high memory alert)
- `all` - Sends all extreme values at once

**Use Cases:**
- Test alert generation
- Verify alert notifications (email/SMS)
- Validate alert thresholds

---

#### Service 3: Force Update
**Name:** `ha_dispatch_client.force_update`

Force immediate coordinator update instead of waiting 60 seconds.

```yaml
service: ha_dispatch_client.force_update
data: {}
```

**Use Cases:**
- Get instant feedback after changes
- Test immediately without waiting
- Verify connection status right now

---

#### Service 4: Send Custom Metric
**Name:** `ha_dispatch_client.send_custom_metric`

Send completely custom metric values for advanced testing.

```yaml
service: ha_dispatch_client.send_custom_metric
data:
  cpu_load_1m: 2.5
  memory_used_percent: 67.8
  disk_free_percent: 35.5
  warnings: "low_disk,custom_warning"
```

**Use Cases:**
- Test specific threshold values
- Find exact alert boundaries
- Send custom warning tags
- Advanced debugging scenarios

---

## 📦 Files Added/Modified

### New Files
- ✅ `services.yaml` - Service definitions
- ✅ `DEBUG_SERVICES.md` - Complete documentation (24 pages!)
- ✅ `BUGFIX_UPTIME.md` - Bug fix details
- ✅ `CHANGELOG.md` - Version history
- ✅ `UPDATE_WITH_DEBUG.sh` - Update script

### Modified Files
- ✅ `coordinator.py` - Fixed uptime calculation
- ✅ `__init__.py` - Added service registration & handlers
- ✅ `strings.json` - Added service descriptions

### Total Project Size
- **Integration files**: 9
- **Documentation files**: 12  
- **Scripts**: 6
- **Total**: 25 files

---

## 🚀 How to Apply

### Step 1: Run Update Script

In your terminal:

```bash
cd ~/Documents/ha-client
./UPDATE_WITH_DEBUG.sh
```

The script will:
1. ✅ Connect to your Home Assistant
2. ✅ Backup existing files
3. ✅ Upload all updated files
4. ✅ Verify installation
5. ✅ Offer to restart HA

### Step 2: Restart Home Assistant

Either:
- Let the script restart it for you, OR
- Manual: Settings → System → Restart

### Step 3: Verify Services

After restart:
1. Go to **Developer Tools** → **Services**
2. Search: `ha_dispatch_client`
3. You should see **4 services**:
   - send_test_metrics
   - trigger_alert
   - force_update
   - send_custom_metric

---

## ✅ Quick Test

Try this first service call to verify everything works:

```yaml
service: ha_dispatch_client.send_test_metrics
data: {}  # Uses default values
```

**Expected result:**
- Check HA logs: "Test metrics sent successfully"
- Check server admin panel: New metric appears
- No errors

---

## 📚 Documentation

| File | Purpose |
|------|---------|
| `DEBUG_SERVICES.md` | **Complete guide** to all 4 services with examples |
| `BUGFIX_UPTIME.md` | Technical details of uptime bug fix |
| `CHANGELOG.md` | Version history and upgrade guide |
| `README.md` | Main integration documentation |

---

## 🎯 Common Use Cases

### Test Connectivity
```yaml
service: ha_dispatch_client.send_test_metrics
data: {}
```

### Trigger Low Disk Alert
```yaml
service: ha_dispatch_client.trigger_alert
data:
  alert_type: disk_low
```

### Test All Alerts
```yaml
service: ha_dispatch_client.trigger_alert
data:
  alert_type: all
```

### Force Immediate Update
```yaml
service: ha_dispatch_client.force_update
data: {}
```

### Custom Test Scenario
```yaml
service: ha_dispatch_client.send_custom_metric
data:
  disk_free_percent: 9.5  # Just below 10% threshold
  warnings: "test_alert"
```

---

## 🔍 How to Access Services

### Method 1: Developer Tools (Easiest)
1. Open Home Assistant web UI
2. Go to **Developer Tools** → **Services**
3. Search: `ha_dispatch_client`
4. Select a service
5. Fill in parameters (or leave blank for defaults)
6. Click **Call Service**

### Method 2: Automations
```yaml
automation:
  - alias: "Test HA Dispatch Daily"
    trigger:
      - platform: time
        at: "09:00:00"
    action:
      - service: ha_dispatch_client.send_test_metrics
        data: {}
```

### Method 3: Scripts
```yaml
script:
  test_alerts:
    sequence:
      - service: ha_dispatch_client.trigger_alert
        data:
          alert_type: disk_low
```

---

## 🐛 Troubleshooting

### Services Don't Appear

**Problem**: Can't find services in Developer Tools

**Solution:**
1. Restart Home Assistant
2. Check Settings → Devices & Services → HA Dispatch Client
3. View logs: Search for "HA Dispatch debug services registered"

### Service Call Fails

**Problem**: "Failed to call service" error

**Solution:**
1. Check HA logs for details
2. Verify integration is active
3. Test server connectivity
4. Check access token is valid

### Metrics Not on Server

**Problem**: Service succeeds but no data appears

**Solution:**
1. Check server is running
2. Verify server URL is correct
3. Check server logs for errors
4. Verify installation is active on server

---

## 📊 What You Can Now Do

✅ **Test connectivity** anytime with one service call
✅ **Trigger alerts** manually to verify alert system works  
✅ **Force updates** for instant feedback during testing  
✅ **Send custom metrics** for advanced troubleshooting  
✅ **Validate thresholds** by testing exact boundary values  
✅ **Demo the system** to stakeholders with realistic data  
✅ **Debug issues** with detailed metric control  

---

## 🎊 Summary

**Version**: 1.1.0  
**Release Date**: November 16, 2025  
**Changes**: Bug fix + 4 new debug services  
**Status**: Ready to deploy!  

**To Update**: Run `./UPDATE_WITH_DEBUG.sh`  
**Documentation**: See `DEBUG_SERVICES.md` for detailed examples  
**Support**: Check logs and server admin panel for verification  

---

**Enjoy the new debugging capabilities!** 🚀

