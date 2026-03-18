# Troubleshooting Guide

This guide consolidates all common issues and their solutions for the HA Dispatch Client.

## Cannot Connect Errors

**Symptom:** "Cannot connect" error during the config flow, or connection errors in logs.

**Solutions:**

1. **Verify the server URL is correct.** It should include the protocol and port, e.g., `http://192.168.1.50:8080`. Do not include a trailing slash.

2. **Check network connectivity between Home Assistant and the server:**
   ```bash
   # From the HA host
   ping your-server-ip
   curl http://your-server:8080/api/v1/installations/register
   ```

3. **Ensure the server is running and the API is available:**
   ```bash
   # If using Docker
   docker compose ps
   docker compose logs -f --tail=20
   ```

4. **Check for firewall rules** blocking traffic on the server port (default 8080).

5. **Try using an IP address** instead of a hostname if DNS resolution is unreliable.

## Registration Failures

**Symptom:** Config flow fails after entering the server URL. Logs show registration errors.

**Solutions:**

1. **Check server logs** for validation errors during registration.

2. **Ensure the server database is initialized and migrated:**
   ```bash
   docker compose exec app php artisan migrate:status
   ```

3. **Verify the server is not in maintenance mode:**
   ```bash
   docker compose exec app php artisan up
   ```

4. **Test the registration endpoint directly:**
   ```bash
   curl -X POST http://your-server:8080/api/v1/installations/register \
     -H "Content-Type: application/json" \
     -d '{
       "client_id": "test-uuid-12345",
       "hostname": "test-host",
       "name": "Test Installation"
     }'
   ```
   A 201 response with `installation_id` and `access_token` confirms the server is working.

## Missing Metrics

**Symptom:** Integration is running but no metrics appear on the server dashboard.

**Solutions:**

1. **Check Home Assistant logs** for metric submission errors:
   ```bash
   ha core logs | grep ha_dispatch
   ```

2. **Verify the access token is valid.** If the token was rotated or invalidated on the server, the client will get authentication errors. You may need to remove and re-add the integration.

3. **Check the installation status on the server.** Go to Monitoring -> Installations and verify the installation shows as "online."

4. **Ensure psutil is available in Home Assistant.** It should be included by default, but verify by checking for import errors in the logs.

5. **Check server logs** for API validation errors when metrics are submitted:
   ```bash
   docker compose logs -f --tail=50 | grep -i metric
   ```

## Unavailable Sensors

**Symptom:** One or more sensors show "unavailable" in Developer Tools -> States.

**Solutions:**

1. **Check if the integration is loaded:** Go to Settings -> Devices & Services and confirm HA Dispatch Client appears.

2. **Check the coordinator is updating.** Look for update errors in the logs:
   ```bash
   ha core logs | grep ha_dispatch
   ```
   Errors like "Error communicating with API" indicate the coordinator cannot reach the server.

3. **Restart Home Assistant:**
   ```bash
   ha core restart
   ```

4. **Verify the server is reachable:** Test with `curl` from the HA host.

5. **Re-add the integration** if the config entry is corrupted: Delete the integration from Settings -> Devices & Services, then add it again.

## Services Not Appearing

**Symptom:** The 6 services (`send_test_metrics`, `trigger_alert`, `force_update`, `send_custom_metric`, `submit_alert`, `resolve_alert`) do not appear in Developer Tools -> Services.

**Solutions:**

1. **Verify the integration is loaded:** Go to Settings -> Devices & Services and confirm it is listed.

2. **Look for service registration in logs:**
   ```bash
   ha core logs | grep "debug services registered"
   ```
   If you do not see this message, the `__init__.py` setup may have failed.

3. **Restart Home Assistant.** Services are registered during integration setup. A restart ensures clean initialization.

4. **Check for errors during setup:**
   ```bash
   ha core logs | grep ha_dispatch_client
   ```
   Look for exceptions or import errors that would prevent `async_setup_entry` from completing.

5. **Redeploy the integration.** Ensure `services.yaml` and `__init__.py` are both present and up to date. See the [Deployment Guide](deployment.md).

## Version Not Updating

**Symptom:** After deploying a new version, Home Assistant still shows the old version number.

**Root Cause:** Home Assistant aggressively caches `manifest.json` in memory. Simply replacing files and restarting is not always enough.

**Solutions (try in order):**

1. **Reload the integration:** Settings -> Devices & Services -> HA Dispatch Client -> (three dots menu) -> Reload.

2. **Hard refresh the browser:** `Ctrl+Shift+R` (Windows/Linux) or `Cmd+Shift+R` (Mac).

3. **Clear the browser cache:** Open DevTools (F12), right-click the refresh button, select "Empty Cache and Hard Reload."

4. **Restart Home Assistant:**
   ```bash
   ssh straplocked@homeassistant.local "sudo ha core restart"
   ```

5. **Perform a clean deploy.** This is the nuclear option for manifest changes:
   ```bash
   # Delete the integration folder
   ssh straplocked@homeassistant.local "sudo rm -rf /config/custom_components/ha_dispatch_client"

   # Restart HA to clear memory cache
   ssh straplocked@homeassistant.local "sudo ha core restart"

   # Wait 1-2 minutes, then deploy fresh files
   ./deploy.sh

   # Restart HA again
   ssh straplocked@homeassistant.local "sudo ha core restart"

   # Re-add the integration in the UI
   ```

   See the [Deployment Guide](deployment.md) for full details on clean deployments.

## Performance Issues

**Symptom:** Home Assistant slows down or uses excessive resources after installing the integration.

**Expected resource usage:**
- CPU: less than 1% average
- Memory: less than 50MB

**Solutions:**

1. **Check the poll interval.** The default is 60 seconds. If the server pushed a very short interval (e.g., 5 seconds), this could increase resource usage. Check the `config_version` attribute on `sensor.ha_dispatch_status` and review the configuration on the server.

2. **Monitor resource usage:**
   ```bash
   top -b -n 1 | grep python
   ps aux | grep home-assistant
   ```

3. **Check for error loops.** If the server is unreachable, the coordinator retries every interval. Persistent connection failures generate a lot of log output and network activity. Fix the server connectivity issue to resolve this.

4. **Check the update frequency:**
   ```bash
   ha core logs -f | grep "Submitting metrics"
   ```
   Entries should appear at the configured interval (default: every 60 seconds).

## Integration Will Not Load

**Symptom:** The integration does not appear in the "Add Integration" list or fails to load after installation.

**Solutions:**

1. **Verify all files are present** in `custom_components/ha_dispatch_client/`:
   ```bash
   ls -l /config/custom_components/ha_dispatch_client/
   ```
   Required files: `__init__.py`, `manifest.json`, `const.py`, `config_flow.py`, `api_client.py`, `coordinator.py`, `sensor.py`, `strings.json`, `services.yaml`.

2. **Verify `manifest.json` is valid JSON:**
   ```bash
   python3 -c "import json; json.load(open('/config/custom_components/ha_dispatch_client/manifest.json'))"
   ```

3. **Check that dependencies are available.** The integration requires `aiohttp>=3.8.0` and `psutil>=5.9.0`, both of which are typically included with Home Assistant.

4. **Check Home Assistant startup logs** for import errors or syntax errors:
   ```bash
   ha core logs | grep ha_dispatch_client
   ```

5. **Reinstall the integration files** using `./deploy.sh` and restart Home Assistant.

## Configuration Updates Not Applied

**Symptom:** Configuration changes made on the server are not reflected in the client.

**Solutions:**

1. **Check that the configuration is marked as "active" on the server.**

2. **Verify `config_version` is incrementing.** Each new configuration should have a higher version number.

3. **Check Home Assistant logs** for configuration fetch messages:
   ```bash
   ha core logs | grep -i "configuration"
   ```

4. **Manually increment the config version** on the server and verify the client picks it up within 60 seconds.

5. **Check the coordinator is polling** the config endpoint. Look for periodic status/config fetch messages in the logs.

## Alerts Not Appearing on Server

**Symptom:** `trigger_alert` or `submit_alert` succeeds (no error in logs) but no alerts appear on the server dashboard.

**Solutions:**

1. **Check alert threshold settings on the server:** Settings -> System -> Settings -> Alerts. Verify thresholds are configured.

2. **For `trigger_alert`:** The service sends extreme metric values. The server must have threshold-based alert rules configured to generate alerts from metrics.

3. **For `submit_alert`:** The alert should appear directly. Check the server's Installation Alerts section, filtering by your installation.

4. **Check server logs:**
   ```bash
   docker compose logs | grep -i alert
   ```

5. **Verify the installation is active** on the server and not in a disabled state.

## Service Call Fails

**Symptom:** "Failed to call service" error when invoking a service.

**Solutions:**

1. **Check the integration is active.** Go to Settings -> Devices & Services and verify HA Dispatch Client is listed without errors.

2. **Verify the server URL is correct and accessible.**

3. **Check the access token is valid.** If it was revoked server-side, remove and re-add the integration.

4. **View Home Assistant logs** for the specific error:
   ```bash
   ha core logs | grep ha_dispatch
   ```

## General Debugging Steps

When none of the above sections match your issue:

1. **Check Home Assistant logs:**
   ```bash
   # Via UI
   Settings -> System -> Logs -> Search for "ha_dispatch"

   # Via SSH
   ha core logs | grep ha_dispatch_client
   ```

2. **Check server logs:**
   ```bash
   docker compose logs -f --tail=50
   ```

3. **Test API connectivity directly:**
   ```bash
   curl http://your-server:8080/api/v1/installations/register
   ```

4. **Verify deployed files match the source:**
   ```bash
   ./check_version.sh
   ```

5. **Try a clean deploy** as described in the [Deployment Guide](deployment.md).

## Related Guides

- [Quick Start](quickstart.md) -- Get up and running in 5 minutes
- [Installation Guide](installation.md) -- Installation methods and setup flow
- [Deployment Guide](deployment.md) -- Deploy scripts, versioning, and caching
- [Services Guide](services-guide.md) -- All 6 services with parameters and examples
- [Alerts Guide](alerts-guide.md) -- Alert lifecycle and automation patterns
