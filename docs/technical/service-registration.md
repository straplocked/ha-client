# Service Registration Analysis

> **Note:** This document was originally a development analysis. It has been preserved as technical reference because the patterns it describes remain current in the codebase. For the full documentation index, see [docs/INDEX.md](../INDEX.md).

## Current Implementation Review

### What You Have

Your `ha_dispatch_client/__init__.py` contains:
- Service handler functions (6 services)
- Service schemas with voluptuous validation
- `hass.services.async_register()` calls
- `services.yaml` file with UI definitions
- Singleton guard via `hass.services.has_service()` check

### The Pattern

#### Issue #1: Services Registered Per Config Entry (original bug, now fixed)
**Location:** Line 66 in `__init__.py` (original)

```python
# Register services
await async_setup_services(hass, coordinator, entry.data[CONF_INSTALLATION_ID])
```

**Problem:** Services were registered inside `async_setup_entry()`, which means:
- If you have multiple config entries, it tries to register the same service multiple times
- Home Assistant will **silently fail** on duplicate registrations
- Services might not appear at all if registration fails

**Solution:** Move service registration to a standalone function with a singleton guard

#### Issue #2: No Service Unregistration (original bug, now fixed)
**Location:** `async_unload_entry()` function

**Problem:** Services were not unregistered when integration was unloaded
- This can cause issues with reloading
- Services might "stick around" even after unload

**Solution:** Track if services are registered and unregister them on last entry unload

#### Issue #3: Missing Module-Level Setup (addressed)
**Problem:** No `async_setup()` function
- Services should be registered once at the platform level, not per config entry
- Current approach registers per installation

## Applied Fix

### Step 1: Standalone setup_services()

```python
def setup_services(hass: HomeAssistant):
    """Set up services for HA Dispatch Client (called once)."""

    # Check if services are already registered
    if hass.services.has_service(DOMAIN, "send_test_metrics"):
        _LOGGER.debug("Services already registered, skipping")
        return

    # Define all handlers...
    # Register all services...

    _LOGGER.info("HA Dispatch Client services registered successfully")
```

### Step 2: Dynamic Coordinator Resolution

```python
def get_coordinator_for_service(hass: HomeAssistant) -> HADispatchCoordinator | None:
    """Get the first available coordinator for service calls."""
    coordinators = hass.data.get(DOMAIN, {})
    if not coordinators:
        _LOGGER.error("No HA Dispatch installations configured")
        return None
    entry_id = next(iter(coordinators))
    return coordinators[entry_id]
```

### Step 3: Updated Config Entry Setup

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

    # Services are registered once via singleton guard
    setup_services(hass)

    # Forward to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True
```

### Step 4: Service Unregistration on Last Unload

```python
def teardown_services(hass: HomeAssistant):
    """Remove services when last config entry is unloaded."""
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, "send_test_metrics")
        hass.services.async_remove(DOMAIN, "trigger_alert")
        hass.services.async_remove(DOMAIN, "force_update")
        hass.services.async_remove(DOMAIN, "send_custom_metric")
        hass.services.async_remove(DOMAIN, "submit_alert")
        hass.services.async_remove(DOMAIN, "resolve_alert")
        _LOGGER.info("HA Dispatch Client services unregistered")
```

## Reference: How meross_lan Does It

After analyzing the downloaded `meross_lan` integration:

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
   Go to Developer Tools > Services
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

## Related Documentation

- [Services](services.md) -- Complete service reference
- [Architecture](architecture.md) -- System architecture overview
- [Archive: Analysis Complete](../archive/analysis-complete.md) -- Historical analysis of the fix
- [Archive: Fix Summary](../archive/fix-summary-services.md) -- Historical fix summary
