<!-- Split from CLIENT_SIDE_DEVELOPMENT_GUIDE.md -->

# Authentication & Security

## Authentication Flow

### 1. Initial Registration (No Authentication)
```http
POST /api/v1/installations/register
Content-Type: application/json

{
  "client_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "hostname": "homeassistant.local",
  "name": "Home Assistant Main",
  "ha_version": "2025.11.0",
  "os_info": "Home Assistant OS 11.1"
}
```

**Response:**
```json
{
  "installation_id": "12345",
  "access_token": "randombase64string...",
  "poll_interval_seconds": 60,
  "config_version": 1
}
```

### 2. Authenticated Requests
All subsequent requests must include the Bearer token:

```http
Authorization: Bearer {access_token}
```

## Security Implementation

### Token Generation
- 64-character random string
- Generated during registration
- Stored hashed in database (SHA-256)
- Unique per installation
- Long-lived (no expiration by default)

### Token Storage (Client Side)
- Store in Home Assistant's configuration storage
- Encrypt if possible
- Never log token in plain text
- Regenerate if compromised (requires re-registration)

### Request Authentication Middleware
- Custom middleware: `auth.installation`
- Validates Bearer token
- Loads Installation model into request
- Returns 401 if invalid/missing
- Returns 403 if installation is inactive

### HTTPS Requirements
- **PRODUCTION**: Always use HTTPS
- **DEVELOPMENT**: HTTP acceptable for local testing
- **WEBSOCKETS**: Use WSS in production

### Rate Limiting
- Default: 60 requests/minute per installation
- Configurable via admin panel
- 429 status code when exceeded
- Retry-After header included

## Security Best Practices for Client

1. **Never hardcode tokens**: Always use configuration storage
2. **Validate server certificates**: Prevent MITM attacks
3. **Use secure storage**: Encrypt sensitive data
4. **Handle 401/403 properly**: Prompt for re-registration
5. **Implement exponential backoff**: On rate limit or server errors
6. **Sanitize metrics data**: Don't send sensitive HA data
7. **Verify server identity**: Check SSL certificate in production

---

## Related Documentation

- [API Reference](../api-reference.md) for full endpoint documentation including registration
- [Error Handling](error-handling.md) for handling 401/403/429 responses
- [Communication Patterns](communication-patterns.md) for the registration flow diagram
