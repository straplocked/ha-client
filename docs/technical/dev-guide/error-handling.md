<!-- Split from CLIENT_SIDE_DEVELOPMENT_GUIDE.md -->

# Error Handling

## HTTP Status Codes

### 200 OK
- **Meaning**: Request successful
- **Action**: Process response data

### 201 Created
- **Meaning**: Resource created (registration, metric submission)
- **Action**: Process response, store IDs

### 204 No Content
- **Meaning**: No update available (configuration fetch)
- **Action**: No action needed, current version is latest

### 401 Unauthorized
- **Meaning**: Invalid or missing token
- **Action**: Prompt user to reconfigure integration, may need to re-register

### 403 Forbidden
- **Meaning**: Token valid but access denied (inactive installation)
- **Action**: Notify user, check server admin panel

### 422 Unprocessable Entity
- **Meaning**: Validation failed
- **Action**: Log validation errors, fix request format

### 429 Too Many Requests
- **Meaning**: Rate limit exceeded
- **Action**: Respect Retry-After header, reduce request frequency

### 500 Internal Server Error
- **Meaning**: Server error
- **Action**: Retry with exponential backoff, log for debugging

### 503 Service Unavailable
- **Meaning**: Server temporarily unavailable
- **Action**: Retry with exponential backoff

## Network Errors

### Connection Timeout
```python
try:
    response = await session.post(url, json=data, timeout=10)
except asyncio.TimeoutError:
    # Log error, retry on next interval
    _LOGGER.warning("Request timeout, will retry")
```

### Connection Refused
```python
except aiohttp.ClientConnectorError:
    # Server unreachable, buffer metrics if possible
    _LOGGER.error("Cannot connect to server")
```

### SSL Certificate Error
```python
except aiohttp.ClientSSLError:
    # Invalid certificate, warn user
    _LOGGER.error("SSL certificate validation failed")
```

## Retry Strategy

### Exponential Backoff
```python
max_retries = 5
base_delay = 2  # seconds

for attempt in range(max_retries):
    try:
        response = await make_request()
        break
    except Exception as e:
        if attempt < max_retries - 1:
            delay = base_delay * (2 ** attempt)  # 2, 4, 8, 16, 32 seconds
            await asyncio.sleep(delay)
        else:
            _LOGGER.error("Max retries exceeded")
```

### Jitter
Add randomness to prevent thundering herd:
```python
import random
delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
```

---

## Related Documentation

- [API Reference](../api-reference.md) for full endpoint documentation and error response formats
- [Authentication & Security](authentication.md) for handling 401/403 and re-registration
- [Communication Patterns](communication-patterns.md) for error handling in each communication pattern
- [Metrics Collection](metrics-collection.md) for metric-specific error handling and offline buffering
