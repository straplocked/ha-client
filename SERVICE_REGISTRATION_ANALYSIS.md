# Service Registration Issue Analysis

## Current Implementation Review

### What You Have ✅

Your `ha_dispatch_client/__init__.py` contains:
- ✅ Service handler functions (4 services)
- ✅ Service schemas with voluptuous validation
- ✅ `hass.services.async_register()` calls
- ✅ `services.yaml` file with UI definitions
- ✅ Services called from within `async_setup_entry()`

### Potential Issues 🔍

#### Issue #1: Services Registered Per Config Entry
**Location:** Line 66 in `__init__.py`

```python
# Register services
await async_setup_services(hass, coordinator, entry.data[CONF_INSTALLATION_ID])
```

**Problem:** Services are registered inside `async_setup_entry()`, which means:
- If you have multiple config entries, it tries to register the same service multiple times
- Home Assistant will **silently fail** on duplicate registrations
- Services might not appear at all if registration fails

**Solution:** Move service registration to module-level setup

#### Issue #2: No Service Unregistration
**Location:** `async_unload_entry()` function

**Problem:** Services are not unregistered when integration is unloaded
- This can cause issues with reloading
- Services might "stick around" even after unload

**Solution:** Track if services are registered and unregister them

#### Issue #3: Missing Module-Level Setup
**Problem:** No `async_setup()` function
- Services should be registered once at the platform level, not per config entry
- Current approach registers per installation

## Recommended Fix

### Step 1: Add Module-Level Setup

```python
async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the HA Dispatch Client component."""
    # Initialize domain data storage
    hass.data.setdefault(DOMAIN, {})
    
    # Register services once at the integration level
    await async_setup_services(hass)
    
    return True
```

### Step 2: Modify Service Registration

```python
async def async_setup_services(hass: HomeAssistant):
    """Register integration services once for all installations."""
    
    # Check if services are already registered
    if hass.services.has_service(DOMAIN, "send_test_metrics"):
        _LOGGER.debug("Services already registered")
        return
    
    async def handle_send_test_metrics(call: ServiceCall):
        """Handle send_test_metrics service call."""
        # Get the first coordinator (or allow targeting via service data)
        coordinators = hass.data[DOMAIN]
        if not coordinators:
            _LOGGER.error("No installations configured")
            return
        
        # Use first coordinator
        entry_id = list(coordinators.keys())[0]
        coordinator = coordinators[entry_id]
        
        cpu_load = call.data.get("cpu_load", 75.0)
        memory_used = call.data.get("memory_used", 85.0)
        disk_free = call.data.get("disk_free", 15.0)
        
        metrics = {
            "cpu_load_1m": round(cpu_load / 100, 2),
            "cpu_load_5m": round(cpu_load / 100, 2),
            "cpu_load_15m": round(cpu_load / 100, 2),
            "memory_used_percent": round(memory_used, 2),
            "memory_used_mb": round(memory_used * 80, 2),
            "disk_used_percent": round(100 - disk_free, 2),
            "disk_free_percent": round(disk_free, 2),
            "disk_free_mb": int(disk_free * 100),
            "uptime_seconds": 3600,
            "warnings": [],
        }
        
        _LOGGER.info("Sending test metrics: CPU=%.1f%%, Memory=%.1f%%, Disk Free=%.1f%%", 
                     cpu_load, memory_used, disk_free)
        
        await coordinator.api_client.submit_metrics(
            coordinator.installation_id, 
            metrics
        )
        _LOGGER.info("Test metrics sent successfully")
    
    # Register all services
    hass.services.async_register(
        DOMAIN, 
        "send_test_metrics", 
        handle_send_test_metrics, 
        schema=SERVICE_SEND_TEST_METRICS_SCHEMA
    )
    
    # ... (register other services similarly)
    
    _LOGGER.info("HA Dispatch services registered")
```

### Step 3: Update Config Entry Setup

```python
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HA Dispatch Client from a config entry."""
    # Create API client
    session = aiohttp_client.async_get_clientsession(hass)
    api_client = HADispatchApiClient(
        session=session,
        server_url=entry.data[CONF_SERVER_URL],
        token=entry.data[CONF_ACCESS_TOKEN],
    )

    # Create coordinator
    coordinator = HADispatchCoordinator(
        hass=hass,
        api_client=api_client,
        installation_id=entry.data[CONF_INSTALLATION_ID],
    )

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Services are registered in async_setup(), not here!
    # Just forward to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True
```

### Step 4: Add Service Unregistration

```python
async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Remove coordinator
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        
        # If this was the last entry, unregister services
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, "send_test_metrics")
            hass.services.async_remove(DOMAIN, "trigger_alert")
            hass.services.async_remove(DOMAIN, "force_update")
            hass.services.async_remove(DOMAIN, "send_custom_metric")
            _LOGGER.info("HA Dispatch services unregistered")

    return unload_ok
```

## Alternative Approach: Entity-Specific Services

If you want services to target specific installations:

```yaml
# services.yaml
send_test_metrics:
  name: Send Test Metrics
  description: Send test metrics to the server
  target:
    entity:
      integration: ha_dispatch_client
      domain: sensor
  fields:
    cpu_load:
      # ... (rest of field definition)
```

Then in your service handler:

```python
async def handle_send_test_metrics(call: ServiceCall):
    """Handle send_test_metrics service call."""
    # Get target entities if specified
    target_entities = call.data.get("entity_id")
    
    # Find the coordinator for the targeted entity
    # ... implementation
```

## Quick Debug Checks

1. **Check if services are visible:**
   ```bash
   # In Home Assistant
   Go to Developer Tools → Services
   Search for "ha_dispatch"
   ```

2. **Check Home Assistant logs:**
   ```bash
   # Look for registration messages
   grep "HA Dispatch" /config/home-assistant.log
   ```

3. **Check if services are actually registered:**
   ```python
   # In Home Assistant Python console
   hass.services.services.get('ha_dispatch_client')
   ```

## What to Check in meross_lan

When you download meross_lan, look for:

1. Do they have `async_setup()` function?
2. Where are services registered?
3. Do they use `async_setup_entry()` for services?
4. How do they handle multiple devices/entries?
5. How are services unregistered?

Compare their pattern with yours to identify the exact issue.

---

**Next Steps:**
1. Download meross_lan integration
2. Compare service registration patterns
3. Apply fix to ha_dispatch_client
4. Push updated files to Home Assistant
5. Restart and test

