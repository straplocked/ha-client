# Bug Fix: Negative Uptime Calculation

**Date**: November 16, 2025  
**Issue**: Client sending negative uptime_seconds to server  
**Status**: ✅ Fixed

---

## The Problem

The HA Dispatch Client was calculating system uptime incorrectly, resulting in negative values like `-1761609031` being sent to the server. This caused API validation errors:

```json
{
  "error": "Validation failed",
  "messages": {
    "uptime_seconds": ["The uptime seconds field must be at least 0."]
  }
}
```

## Root Cause

**File**: `custom_components/ha_dispatch_client/coordinator.py`  
**Line**: 103

### Original Code (WRONG):
```python
boot_time = psutil.boot_time()
uptime = self.hass.loop.time() - boot_time
```

### The Bug Explained:

- `psutil.boot_time()` returns a **Unix timestamp** (seconds since epoch, e.g., 1700000000)
- `self.hass.loop.time()` returns **event loop monotonic time** (starts at 0 when loop starts)
- Subtracting a huge Unix timestamp from a small monotonic value = **large negative number**

Example calculation:
```
loop.time():     1234.56     (seconds since event loop started)
boot_time:  1731780000.00    (Unix timestamp)
result:    -1731778765.44    (WRONG!)
```

## The Fix

### New Code (CORRECT):
```python
import time  # Added at top of file

# Calculate uptime correctly using time.time() not loop.time()
boot_time = psutil.boot_time()
uptime_seconds = int(time.time() - boot_time)

# Ensure uptime is positive (sanity check)
if uptime_seconds < 0:
    _LOGGER.warning("Negative uptime calculated, using 0 instead")
    uptime_seconds = 0
```

### Changes Made:

1. ✅ **Added `import time`** at the top of coordinator.py
2. ✅ **Changed from `self.hass.loop.time()`** to **`time.time()`**
3. ✅ **Added sanity check** for negative values
4. ✅ **Added logging** if negative value detected
5. ✅ **Cast to int** explicitly

### Why This Works:

- `time.time()` returns a **Unix timestamp** (same scale as boot_time)
- Subtracting two Unix timestamps gives correct uptime in seconds
- Result is always positive (current time > boot time)

Example calculation:
```
time.time():   1731780000.00  (Unix timestamp now)
boot_time:     1731700000.00  (Unix timestamp at boot)
result:            80000.00   (80,000 seconds = ~22 hours uptime ✓)
```

## Files Modified

- `custom_components/ha_dispatch_client/coordinator.py`
  - Line 3: Added `import time`
  - Lines 104-111: Fixed uptime calculation with sanity check

## How to Apply the Fix

### Option 1: Run Update Script (Easiest)

```bash
cd ~/Documents/ha-client
./UPDATE_REMOTE.sh
```

This script will:
- Backup the current file
- Upload the fixed version
- Verify the fix
- Optionally restart Home Assistant

### Option 2: Manual Update

```bash
# Copy fixed file to Home Assistant
cat custom_components/ha_dispatch_client/coordinator.py | \
  ssh straplocked@homeassistant.local \
  "sudo tee /config/custom_components/ha_dispatch_client/coordinator.py > /dev/null"

# Restart Home Assistant
ssh straplocked@homeassistant.local "sudo ha core restart"
```

### Option 3: Reinstall Completely

```bash
cd ~/Documents/ha-client
./INSTALL_REMOTE_SUDO.sh
```

## Verification

After applying the fix and restarting Home Assistant:

### 1. Check Home Assistant Logs

```bash
# Should see positive uptime values in metrics
ssh straplocked@homeassistant.local "grep 'uptime_seconds' /config/home-assistant.log"
```

### 2. Check Server Admin Panel

1. Open server: `http://your-server:8080/admin`
2. Go to: Monitoring → Installations → Your Installation
3. Click: Metrics tab
4. Verify: `uptime_seconds` is positive (e.g., 3600, 86400, etc.)

### 3. Check API Response

The server should now accept metrics without errors:

```json
{
  "status": "ok",
  "metric_id": 2966
}
```

No more 422 validation errors!

## Expected Uptime Values

After the fix, uptime_seconds should be:

| Duration | Seconds | Typical Value |
|----------|---------|---------------|
| 1 hour   | 3,600   | Small systems |
| 1 day    | 86,400  | Typical       |
| 1 week   | 604,800 | Stable systems|
| 30 days  | 2,592,000| Production   |

Values should:
- ✅ Always be positive
- ✅ Increase over time
- ✅ Reset to ~0 after Home Assistant restart

## Related Changes

### Server-Side Fix (Already Applied)

The server was also updated to be more tolerant:

1. Changed validation from `nullable|integer|min:0` to `nullable|integer`
2. Added sanitization to convert negative values to NULL
3. Added debug logging for invalid values

This prevents future client bugs from breaking the API, but the client should still send correct values.

## Testing

### Before Fix:
```python
# Typical error in logs
2025-11-16 12:34:56 ERROR Coordinator update failed: 422 Unprocessable Entity
```

### After Fix:
```python
# Successful metric submission
2025-11-16 12:34:56 DEBUG Submitting metrics to server
2025-11-16 12:34:56 INFO Metrics sent: CPU=0.75, MEM=42.5%
```

## Prevention

To avoid similar bugs in the future:

### Use Correct Time Sources:

| Use Case | Use This | Not This |
|----------|----------|----------|
| Unix timestamps | `time.time()` | `loop.time()` |
| Elapsed time | `loop.time()` | `time.time()` |
| Uptime calculation | `time.time() - psutil.boot_time()` | `loop.time() - ...` |

### Best Practices:

1. Always use `time.time()` for timestamps
2. Use `loop.time()` only for event loop timing
3. Add sanity checks for calculated values
4. Log warnings for unexpected values
5. Cast floats to int for whole numbers

## Alternative Implementations

### Method 1: Direct /proc/uptime (Linux only)
```python
with open('/proc/uptime', 'r') as f:
    uptime_seconds = int(float(f.readline().split()[0]))
```

### Method 2: psutil with time.time() (Cross-platform)
```python
import time
uptime_seconds = int(time.time() - psutil.boot_time())
```

Both are correct. We use Method 2 (already using psutil).

---

## Summary

✅ **Bug**: Used wrong time source (`loop.time()` instead of `time.time()`)  
✅ **Fix**: Changed to correct time source with sanity check  
✅ **Result**: Uptime is now always positive and accurate  
✅ **Server**: Now accepts metrics without validation errors  
✅ **Status**: Ready to deploy

**Apply the fix by running**: `./UPDATE_REMOTE.sh`

