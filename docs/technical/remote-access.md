# Consent-Gated Remote Access (client side)

**Status:** implemented in v1.6.0.

A technician on the HA Dispatch dashboard asks for access to an installation.
Nothing happens until the person who owns that Home Assistant says yes, in
Home Assistant, on the screen they already trust. If they say yes, the client
relays individual authorised HTTP requests to the local REST API and posts the
responses back.

The server half is complete and is the authority on behaviour. See the
HA Dispatch repository's `docs/client-integration/remote-access.md` (the agent
spec) and `docs/technical/remote-access.md` (the controller design).

---

## The two jobs

| Job | Module | What it does |
|-----|--------|--------------|
| Consent | `remote_access.py` | Polls for pending requests, shows them, reports the answer, tracks which sessions are live |
| Transport | `tunnel.py` | Long-polls for authorised requests, runs them locally, posts the responses back |

Neither module enforces scope. The controller authorises every request before
it is queued. What the client adds is a local credential and a local refusal
policy, which is the one thing the server cannot supply.

---

## Consent

### Cadence

`GET /api/v1/installations/{id}/access/pending` is called from
`HADispatchCoordinator._async_update_data`, on the coordinator's existing
cadence — 60 s by default, server-tunable. The spec asks for 30–60 s.

`HADispatchRemoteAccess.async_poll_pending()` cannot raise. A customer's
Home Assistant must keep reporting metrics even when remote access is broken.

### What the customer sees

Two surfaces, both raised the moment a request appears:

1. **A persistent notification** carrying who is asking (`requested_by`), why
   (`reason`), what they will be able to do (`scope_description`) and for how
   long (`duration_minutes`), plus the web consent link as a fallback.
2. **A fixable Repairs issue** (`is_fixable=True`, translation key
   `access_request`) whose repair flow is a native **Approve / Deny** menu.
   `repairs.py` maps those two buttons onto the two decisions and nothing else.

`scope_description` is passed through **verbatim**. The server writes it for a
homeowner on purpose; replacing it with technical wording defeats the point of
asking. `tests/test_remote_access.py` asserts both the exact notification text
and that the scope slug (`maintenance`) never appears in it.

### Reporting the answer

`POST .../access/{session}/respond` with `{"decision": "grant"|"deny"}`, plus
`note` and `responder` when they are known.

`responder` names the Home Assistant user who acted, so the audit receipt can
say who agreed instead of "somebody". It is reliable from a **service call**,
which carries `call.context.user_id`; from a Repairs dialog Home Assistant does
not guarantee a user id, so it is best-effort and simply omitted when unknown.
Nothing is ever invented.

A **422** means the request was already answered or has lapsed. That is a
normal outcome, not an error: the prompt comes down and nothing is retried.

### Clearing the prompt

Anything that leaves the pending list has its notification **and** its Repairs
issue cleared on the next poll, however it left — answered here, answered on
the web consent page, or expired. A prompt for a request that is already dead
is worse than no prompt: it teaches people that these prompts mean nothing.

A transient poll failure clears nothing. Leaving a valid prompt up is the safe
failure; taking down a live one is not.

### The off-switch

Three ways, because a consent model without a working off-switch is theatre:

- a fixable Repairs issue, `access_active_<session>`, raised while the session
  is live — one tap, no YAML;
- the `ha_dispatch_client.revoke_access` service, with or without a session id;
- the web consent page, which is the server's own.

All of them end in `POST .../access/{session}/revoke`.

### Which sessions are live

`pending` lists requests awaiting an answer. It does not list granted sessions,
so the client keeps its own record:

| How the request left `pending` | Live? | Prompt raised? |
|---|---|---|
| Granted here | yes, until `expires_at` from the response | yes — "Remote access is active" |
| Denied here | no | no |
| Vanished without our answer | **assumed** live until `requested_at + duration` | **no** |

The third row is a deliberate trade-off, and it is the one thing in this
feature that is a judgement call rather than a specification.

A request that disappears between two polls was either granted or denied on the
web consent page, and nothing available to the client distinguishes the two.
Guessing "denied" would leave a session the customer really did grant with no
agent on the other end, so the tunnel runs — the server only ever queues work
for a genuinely active session, so polling for one that is not costs an idle
outbound connection and nothing else. But no "access is active" prompt is
raised for it, because claiming access is live when the customer may have just
declined it is the same lie as leaving a dead request on screen.

Sessions whose consent was confirmed here carry `confirmed_here: true` in the
binary sensor's `live_sessions` attribute. Assumed ones carry `false`.

### Surviving a restart

Live sessions are persisted through Home Assistant's storage helper
(`ha_dispatch_client.remote_access`) and restored on setup. Restarting Home
Assistant is one of the main reasons to grant *maintenance* access in the first
place, so losing the session across the restart would break the feature exactly
when it is being used. Expired entries are dropped on load.

---

## Transport

Only while at least one session is live. `HADispatchTunnel.async_sync()` starts
and stops the loop to match, and is idempotent.

```
while a session is live:
    requests = GET .../access/poll        # held up to 25 s
    run them concurrently
    POST .../access/exchanges/{id}/respond  with status/headers/body or {"error"}
```

A batch is answered with `asyncio.gather`, not in sequence — the technician is
waiting on all of it, and the server abandons an exchange after about 30 s.
A single request is capped at `ACCESS_EXECUTE_TIMEOUT` (20 s) so the response
still has time to get back.

### Local authentication — the design decision

The relay strips `Cookie` and `Authorization` before a request reaches the
client, on purpose: the technician's Dispatch session has no business
authenticating against a customer's Home Assistant. So the client has to supply
its own local credential, and *which* credential is the whole security story.

**What was chosen:** Home Assistant **system users** minted through
`hass.auth`, one per privilege level, each holding a refresh token. Relayed
requests go over loopback to Home Assistant's own REST API with a short-lived
access token derived from that refresh token.

**Why:**

- It is the mechanism Home Assistant itself sanctions for an integration that
  needs to call the local API — the same path Supervisor uses to talk to Core.
  No private interfaces, no reimplementation of Home Assistant's auth.
- Every relayed request passes through Home Assistant's own authentication and
  permission checks on the way in. A bug in this integration cannot hand out
  more than the token's group already allows, because the enforcement is not
  ours to get wrong.
- Going over HTTP rather than reaching directly into `hass.states` and
  `hass.services` means we are not reimplementing — and then slowly diverging
  from — the REST API's semantics, and every request is logged, rate limited
  and permission checked like any other API call.

**What it can reach**, by scope:

| Scope | Home Assistant group | What that allows |
|---|---|---|
| `diagnostic` | `system-read-only` | Reads only. Home Assistant itself refuses any write, so a read-only session cannot change anything even if the server were compromised and queued a `POST` |
| `maintenance` | `system-admin` | Service calls, reloads and restarts — which is precisely what the scope was granted for |
| `full` | `system-admin` | As above |
| *unknown* | none | **Refused.** No record of consent means no basis for choosing a credential |

Both users are created **lazily**. An installation that only ever grants
diagnostic access never has an admin-capable credential on it at all. The
refresh token from a previous run is reused rather than accumulating one per
restart.

### Local refusal policy

Applied on top of the scope the server already enforced — the spec's "you
should still refuse anything your own configuration forbids":

- only paths under `/api/` are relayed, which keeps the frontend and the auth
  endpoints at `/auth/token` out of reach no matter what the server queues;
- `/api/stream` and `/api/websocket` are refused — they cannot work over a
  request/response relay and would hold the exchange open until it timed out;
- responses over `ACCESS_MAX_RESPONSE_BYTES` (2 MB) are refused rather than
  posted, since they cross as a database row on the server.

### Body encoding

Bodies cross as strings. Textual content types (`text/*`, `application/json`,
`+json`, `+xml`, …) are decoded as UTF-8 with replacement. Anything else is
base64 encoded, keeps its original `Content-Type`, and is labelled
`Content-Transfer-Encoding: base64` so the far end can tell.

---

## Entities and services

| Thing | Name | Purpose |
|---|---|---|
| Binary sensor | `HA Dispatch Remote Access Requested` | On while a request is waiting. Carries the pending requests and live sessions as attributes, so automations and dashboard buttons have something to act on |
| Service | `ha_dispatch_client.respond_to_access_request` | Approve or decline from an automation |
| Service | `ha_dispatch_client.revoke_access` | End one live session, or all of them |
| Repairs issue | `access_request_<session>` | The Approve / Deny dialog |
| Repairs issue | `access_active_<session>` | The one-tap off-switch |

---

## Failure modes

| Situation | Behaviour |
|---|---|
| Server predates remote access (404) | `RemoteAccessUnavailable`, asked once, then never again for this run. **Not** mapped to `InstallationGoneError` — doing so would re-enrol a perfectly healthy installation once a minute forever |
| Server unreachable | Logged at debug; prompts left exactly as they are |
| Decision refused (422) | Prompt cleared, nothing retried |
| Local request fails | `{"error": ...}` posted so the technician sees the reason rather than a 504 |
| Cannot post the response | Logged; the exchange times out on the server and the technician sees a 504, which is the truth |
| Integration unloaded | Tunnel stops. Live sessions stay on disk and stay granted — a reload is not the customer withdrawing consent |

---

## What is not supported

WebSockets are not relayed, so Home Assistant's own frontend cannot be proxied
end to end. The REST API, which covers most remote repairs, works fully.

---

## See also

- [API Reference](api-reference.md) — endpoints 7–11
- [Architecture](architecture.md) — where these modules sit
- [Services](services.md) — service schemas
