<!-- Split from CLIENT_SIDE_DEVELOPMENT_GUIDE.md -->

# Testing Strategy

## Unit Tests

### API Client Tests
```python
import pytest
from unittest.mock import AsyncMock, patch
from .api_client import HADispatchApiClient

@pytest.mark.asyncio
async def test_registration():
    """Test installation registration."""
    session = AsyncMock()
    client = HADispatchApiClient(session, "http://test.com")

    # Mock response
    session.post.return_value.__aenter__.return_value.json = AsyncMock(
        return_value={
            "installation_id": "123",
            "access_token": "token123",
        }
    )

    result = await client.register_installation(
        client_id="test-uuid",
        hostname="test-host",
    )

    assert result["installation_id"] == "123"
    assert result["access_token"] == "token123"
```

### Coordinator Tests
```python
@pytest.mark.asyncio
async def test_coordinator_update():
    """Test data coordinator update."""
    hass = Mock()
    api_client = AsyncMock()
    coordinator = HADispatchCoordinator(hass, api_client, "123")

    # Mock API responses
    api_client.report_status.return_value = {
        "installation_status": "online",
        "config_version": 1,
    }

    data = await coordinator._async_update_data()

    assert data["status"] == "online"
    assert data["config_version"] == 1
```

## Integration Tests

### Config Flow Tests
```python
from homeassistant import config_entries
from .const import DOMAIN

async def test_config_flow(hass):
    """Test configuration flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] == "form"
    assert result["step_id"] == "user"

    # Submit form
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "server_url": "http://test.com",
            "name": "Test Installation",
        },
    )

    assert result["type"] == "create_entry"
```

## Manual Testing Checklist

- [ ] Initial setup via UI config flow
- [ ] Registration with server succeeds
- [ ] Status reports sent every interval
- [ ] Configuration updates applied
- [ ] Metrics collected and submitted
- [ ] Sensors display correct values
- [ ] Offline buffering works
- [ ] Network error handling graceful
- [ ] Re-authentication on token expiry
- [ ] Uninstall cleans up properly

## Server-Side Testing

Test integration against actual HA Dispatch server:

1. **Deploy server**: Use Docker Compose setup
2. **Configure server**: Access admin panel at http://localhost:8080/admin
3. **Install client**: Add to Home Assistant via HACS
4. **Configure client**: Use server URL in config flow
5. **Monitor**: Check server admin panel for installation
6. **Verify metrics**: Confirm metrics appear in server dashboard
7. **Test alerts**: Trigger threshold violation, verify alert generation
8. **Test configuration**: Update config in admin panel, verify client applies

---

## Related Documentation

- [Client Implementation](client-implementation.md) for the code being tested
- [API Reference](../api-reference.md) for endpoint behavior to validate against
- [Deployment & Distribution](deployment.md) for deploying to a test environment
