# Service Registration Fix - Summary

> **Note:** This is a historical development document. For current documentation, see [docs/INDEX.md](../INDEX.md).

## Problem Identified

Your `ha_dispatch_client/__init__.py` was registering services inside `async_setup_entry()`, which:

1. **Runs every time a config entry loads** (not just once)
2. **Tries to register the same service multiple times** if you reload or have multiple entries
3. **Home Assistant silently fails** on duplicate service registration
4. **Services never appear** in Developer Tools > Services

## How meross_lan Does It

After analyzing the downloaded `meross_lan` integration, here's the correct pattern:

### Key Points:

1. **Singleton Pattern**: Services registered in a singleton class (`ComponentApi`)
2. **One-Time Registration**: Services registered in `__init__` of the singleton
3. **Check Before Register**: Uses `hass.services.has_service()` to prevent duplicates
4. **Cleanup on Last Unload**: Only removes services when NO coordinators remain

### Code Location (meross_lan):

```python
# In component_api.py, lines 527-532
hass.services.async_register(
    mlc.DOMAIN,
    mlc.SERVICE_REQUEST,
    _async_service_request,
    supports_response=SupportsResponse.OPTIONAL,
)
```

This happens in `ComponentApi.__init__()` which is called ONCE via the `get()` singleton method.

## The Fix Applied

### Changes Made:

1. **Moved service registration OUT of `async_setup_entry()`**
   - Created standalone `setup_services()` function
   - Called once when first config entry loads

2. **Added duplicate check**
   ```python
   if hass.services.has_service(DOMAIN, "send_test_metrics"):
       _LOGGER.debug("Services already registered, skipping")
       return
   ```

3. **Created helper function to get coordinator**
   ```python
   def get_coordinator_for_service(hass: HomeAssistant):
       """Get the first available coordinator for service calls."""
       coordinators = hass.data.get(DOMAIN, {})
       # Returns first coordinator
   ```

4. **Added proper cleanup**
   ```python
   def teardown_services(hass: HomeAssistant):
       """Remove services when last config entry is unloaded."""
       if not hass.data.get(DOMAIN):  # No coordinators left
           # Remove all services
   ```

### New Flow:

```
+-----------------------------------------------------+
| First Config Entry Loads                             |
|  -> async_setup_entry()                              |
|  -> setup_services()                                 |
|     -> Check if services already registered          |
|     -> If not, register all 4 services               |
|     -> Services now available in UI!                 |
+-----------------------------------------------------+

+-----------------------------------------------------+
| Second/Additional Config Entries Load                |
|  -> async_setup_entry()                              |
|  -> setup_services()                                 |
|     -> Services already registered, skip             |
+-----------------------------------------------------+

+-----------------------------------------------------+
| Config Entry Unloads                                 |
|  -> async_unload_entry()                             |
|  -> Remove coordinator                               |
|  -> teardown_services()                              |
|     -> Check if any coordinators remain              |
|     -> If not, unregister all services               |
+-----------------------------------------------------+
```

## Files Modified

- `__init__.py.fixed` - New corrected version

## Comparison

### OLD (Broken):

```python
async def async_setup_entry(...):
    # ... create coordinator ...

    # WRONG: Registers every time entry loads
    await async_setup_services(hass, coordinator, installation_id)

async def async_setup_services(hass, coordinator, installation_id):
    # No duplicate check
    hass.services.async_register(DOMAIN, "send_test_metrics", ...)
```

### NEW (Fixed):

```python
async def async_setup_entry(...):
    # ... create coordinator ...

    # RIGHT: Checks and registers only once
    setup_services(hass)

def setup_services(hass):
    # Duplicate check
    if hass.services.has_service(DOMAIN, "send_test_metrics"):
        return

    # Gets coordinator dynamically
    coordinator = get_coordinator_for_service(hass)

    # Register services
    hass.services.async_register(DOMAIN, "send_test_metrics", ...)
```

## Next Steps

1. **Backup current version** (already done: `__init__.py.fixed`)
2. **Apply the fix**: Replace `__init__.py` with fixed version
3. **Upload to HA server**: Use `UPDATE_WITH_DEBUG.sh` script
4. **Restart Home Assistant**
5. **Verify services appear**: Developer Tools > Services > Search "ha_dispatch"
6. **Test services**: Try `ha_dispatch_client.send_test_metrics`

## Testing Checklist

After deploying the fix:

- [ ] Services appear in Developer Tools > Services
- [ ] All 4 services visible: `send_test_metrics`, `trigger_alert`, `force_update`, `send_custom_metric`
- [ ] Can call `send_test_metrics` successfully
- [ ] Check Home Assistant logs for "HA Dispatch Client services registered successfully"
- [ ] Services still work after HA restart
- [ ] Services still work if integration reloaded
- [ ] No duplicate registration errors in logs

## Why This Matters

This pattern is critical for any Home Assistant integration that provides services:

1. **Services are domain-level**, not config-entry-level
2. **Must register once** for the entire domain
3. **Must handle multiple config entries** gracefully
4. **Must cleanup properly** when last entry unloads

This is the same pattern used by professional integrations like `meross_lan`, `mqtt`, `zha`, etc.

---

**Author**: AI Assistant
**Date**: November 16, 2025
**Based on**: meross_lan v5.6.0 analysis
