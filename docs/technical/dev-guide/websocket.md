<!-- Split from CLIENT_SIDE_DEVELOPMENT_GUIDE.md -->

# WebSocket Integration

## WebSocket Server

- **URL**: `ws(s)://server:6001/app/{app_key}`
- **Protocol**: Laravel WebSockets (Pusher protocol)
- **Port**: 6001 (default)

## Channel Subscription

### Installation-Specific Channel
```javascript
channel = `installations.${installation_id}`
```

**Events**:
- `InstallationStatusUpdated`: Status changed (online/offline/warning)
- `ConfigurationChanged`: New configuration version available
- `AlertCreated`: New alert generated for this installation
- `AlertResolved`: Alert resolved

### Global Admin Channel (Future)
```javascript
channel = `admin`
```

**Events**:
- `InstallationRegistered`: New installation registered
- `SystemAlert`: System-wide notification

## WebSocket Implementation (Python)

**Note**: WebSocket integration is optional. Polling-based approach is fully functional.

```python
import asyncio
import websockets
import json

async def connect_websocket(server_url, installation_id):
    """Connect to WebSocket server."""
    ws_url = server_url.replace('http', 'ws') + f':6001/app/ha-dispatch'

    async with websockets.connect(ws_url) as websocket:
        # Subscribe to channel
        subscribe_message = {
            "event": "pusher:subscribe",
            "data": {
                "channel": f"installations.{installation_id}"
            }
        }
        await websocket.send(json.dumps(subscribe_message))

        # Listen for events
        async for message in websocket:
            data = json.loads(message)
            await handle_websocket_event(data)

async def handle_websocket_event(data):
    """Handle incoming WebSocket event."""
    event = data.get('event')

    if event == 'ConfigurationChanged':
        # Fetch new configuration
        await fetch_configuration()
    elif event == 'AlertCreated':
        # Show notification
        await create_notification(data['data'])
    elif event == 'InstallationStatusUpdated':
        # Update entity state
        await update_status(data['data'])
```

---

## Related Documentation

- [Communication Patterns](communication-patterns.md) for the WebSocket alert notification flow (Pattern 4)
- [Alert Handling](alerts.md) for alert types and lifecycle
- [Server Infrastructure](server-infrastructure.md) for the WebSocket server setup
