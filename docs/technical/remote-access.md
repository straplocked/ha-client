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

A `screen` session (below) is judged by its own rule instead: reads of any
path except `/auth…` and `/api/websocket`, writes only under `/api/`, and a
12 MB response cap (`ACCESS_SCREEN_MAX_RESPONSE_BYTES`) because frontend
bundles are larger than any REST answer.

## Remote Screen

Dispatch can render this house's own frontend in a headless browser on its
side, so a technician sees the real dashboards at a chosen device size. The
agent's part (`screen.py`, `tunnel.py`, `remote_access.py`):

- **Learning the session.** A screen opened under the homeowner's standing
  enhanced permissions is never pending. `/access/pending` lists it under
  `active`; `_reconcile_active()` marks it live, announces it with the usual
  "access is active" notification and off-switch, and ends it when the server
  stops listing it. A server without the list changes nothing.
- **HTTP.** Relayed like any session, with the screen policy above and the
  admin system user's credential.
- **The WebSocket.** The poll response carries `frames` beside `requests`.
  `ScreenSockets` holds one loopback socket to `/api/websocket` per screen
  session, applies frames in order (`open`, `frame`, `close`), and posts what
  Home Assistant sends back to `/access/frames`, batched every 30 ms.
- **The credential stays here.** The browser on the Dispatch side holds a
  placeholder. Its `auth` frame is rewritten with our own token
  (`substitute_auth`) before it reaches Home Assistant; nothing else is
  rewritten. Frames for a session without screen consent here are dropped.

The full design is ha-dispatch `docs/technical/remote-screen.md`.

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

## Remote UI: the homeowner's own way in

**Status:** slice 1 (hand-placed files) and slice 2 (the agent fetches its
certificate, token and desired state from Dispatch, manages the `http:` include,
reports its readiness and warns when login lockout is off) built on the agent
side; not yet in a release. Needs the matching Dispatch release: until the
server lists the health item's type it drops it (see "The login-lockout health
item").

Everything above is remote access for a **technician**: the server authorises
every request and writes it on a receipt, which requires that the traffic can
be read. Remote UI is remote access for the **homeowner** -- the stock
companion app on their phone reaching their own Home Assistant through
Dispatch's infrastructure with no port forward -- and its value depends on the
traffic **not** being readable by anyone of ours. The two share nothing but the
word "remote". Design and reasoning: ha-dispatch
`docs/technical/homeowner-remote-access.md`.

### How it works

`remote_ui.py` dials out to the Dispatch relay and holds one multiplexed
connection open. The relay is snitun's SNI passthrough server (the library
Nabu Casa runs for HA Cloud; Core already installs it through hass-nabucasa).
When the phone connects to `https://<label>.remote.hadispatch.app`, the relay
matches the TLS SNI to this house's connection and copies bytes. The TLS
session terminates **here**, inside Home Assistant's own aiohttp runner, with a
certificate whose private key sits on this disk. Home Assistant then sees an
ordinary request from an ordinary client and applies its own login, its own
MFA and its own permissions. No system user, no token substitution, no local
refusal policy: nothing of ours is in the path, and that is the design.

### The files, and the off-switch

Off until `<config>/ha_dispatch_client/remote_ui/` holds all three:

| File | What it is | Hand-placed (slice 1) | Fetched (slice 2) |
|---|---|---|---|
| `remote_ui.json` | The relay to dial, the token to present, the channel's AES key and IV, the token's expiry | ha-dispatch `docker/relay/mint_token.py mint` | `POST .../remote-ui/token`, for a keyset made here |
| `fullchain.pem` | The house's certificate for its hostname | A manual ACME run | `POST .../remote-ui/csr`, signed from a CSR made here |
| `privkey.pem` | Its private key. Never leaves this directory | The same run | Generated here (EC P-256), mode 0600 |

The files are the contract between the two slices, and the dial loop neither
knows nor cares which wrote them.

No directory means no attempt, logged at debug. A directory with a missing or
unusable file is an **error** logged once, and still no attempt: a server that
wants a house reachable cannot make it so, and a typo cannot make it
half-reachable. `parse_remote_ui_config` checks every field the dial needs so a
mistake fails at load with a reason. An expired token is not presented.

### Two things the first real house taught (2026-10-23)

- **At boot, Home Assistant's HTTP server does not exist yet.** Config entries
  are set up before it starts, so `hass.http.runner` is `None` during
  `async_load`. The manager now waits for `EVENT_HOMEASSISTANT_STARTED` once
  and loads again, the way hass-nabucasa waits for the runner.
- **A reload used to leave a dead listener behind.** The snitun Home Assistant
  bundles (0.45) serves TLS on a loopback site; its `stop()` closes the socket
  but not the asyncio server behind it, and the next client in the same
  process could inherit a listener that never accepted. The symptom was a
  house that dialled, a relay that held the handshake open, and a loopback
  accept queue that only grew. `async_unload` now closes the site first.
  Verified: a fresh process served `401: Unauthorized` through the relay at
  once.

### The relay protocol is the house's, not the relay's

The agent imports whatever snitun Home Assistant bundles through
hass-nabucasa, and that lags the relay: Home Assistant 2026.6 ships snitun
0.45.1 (relay protocol 1) while the relay image speaks 2. A token minted for
a newer protocol than the house speaks **connects and then hangs every
channel**, silently, which is how the first lab dial failed on 2026-10-23.
So `installed_relay_protocol()` reads the installed snitun's
`PROTOCOL_VERSION`; the provisioner sends it with the mint request and
refuses a token minted above it; and the dial loop refuses to present a
hand-placed token minted above it, with an error that says which number to
re-mint for. The "dialled" log line names the snitun version and protocol.

### Dial, hold, redial

The connection is dialled on setup and held. When it drops -- a relay restart,
the house's internet, Home Assistant itself restarting -- the agent waits a
random 1 to 15 seconds and dials again, so a relay restart is followed by a
trickle of reconnects rather than a storm. A refused dial is retried the same
way and remembered in `last_error`. `async_unload` hangs up and leaves the
files: an integration reload is not the homeowner turning their access off.

### How the files get there: the provisioner

`HADispatchRemoteUI.async_poll` runs on the coordinator's 60 s cycle, in the
same arrangement as `backups.async_tick()`: absent unless setup wired a
coordinator, and unable to raise. It asks `GET .../remote-ui` for the desired
state and acts on the answer:

| The server says | The agent does |
|---|---|
| **404** | The server predates Remote UI. Remembered for the rest of the run and said once at info; hand-placed files keep working untouched. Never mapped to "installation gone" (see `RemoteUiUnavailable`). |
| `enabled: false` | Hangs up if a dial is live, deletes the three files **if the provisioner wrote them** (its `remote_ui.json` carries `"managed_by": "dispatch"`; a hand-placed slice-1 set is left in place and logged once, since a private key this client did not create is not its to delete), forgets the applied version. Leaves `ha_dispatch_client/http.yaml` alone -- a stale proxy list is harmless and removing the include is a config edit nobody asked for. Idempotent: off twice writes and logs nothing. |
| `enabled: true` | Inspects the directory and writes only what is needed (`plan_provisioning`): a key if there is none; a certificate if there is none, it is not this key's, it does not name the hostname, it has under 60 days left, or a renewal was requested; a token if `remote_ui.json` is missing, names another hostname or relay, has under 48 h left -- **or `config_version` moved**, since the token names the hostname and the relay. Then (re)loads through `async_load`, the one path that knows how to dial. |

What leaves the house is the **certificate signing request** -- the public
key and a signature made with the private one -- and the AES keyset the
relay channel will use. The private key is generated here, written with mode
0600, and is in no request body; the test suite's guard for this slice
asserts exactly that. A certificate the server already holds for this very
key (a lost or hand-replaced file) is written back rather than spent on a
second signing. `remote_ui.json` is written in exactly the slice-1 shape,
through the same validator the loader uses, atomically (temp file, then
replace). An unchanged desired state writes nothing: the mtimes do not move.

A step the server declines -- `409` not switched on, `422` CSR names the wrong
host, `429` signed in the last 24 h -- or that fails on the wire is backed off
for five minutes, not retried every poll, and the version is not recorded so
the next attempt picks up what is left (a key already generated is kept).

Renewal needs no flag: Dispatch's sweep bumps `config_version` when a
certificate is inside its last 60 days, the agent re-reads the desired state
and `plan_provisioning` finds the certificate on disk short of the same
60-day horizon, so a new CSR goes. A `renew_requested` field is honoured if
a server ever sends one, but the current server does not.

### The `http:` block, and the restart it owes

The relay's DNS records are proxied, so a request reaches Home Assistant from
the proxy's address with the phone's address in `X-Forwarded-For`, and Home
Assistant refuses that unless it has been told which peers it may believe.
Every house therefore needs `use_x_forwarded_for: true` and the server's
`trusted_proxies` in its `http:` configuration. `http_config.py` owns that,
and is deliberately timid about it, because `configuration.yaml` is the
homeowner's file:

| Step | What happens |
|---|---|
| The list arrives | When `trusted_proxies` is first seen, or changes, `ha_dispatch_client/http.yaml` is written (atomically, and **only if its text differs**). Malformed entries are dropped rather than written, since a bad address there would stop Home Assistant starting. An empty list writes nothing. |
| The include | `configuration.yaml` is **scanned line by line**. If no top-level `http:` key exists, a comment and `http: !include ha_dispatch_client/http.yaml` are **appended**; the original bytes are always a prefix of the new file, so comments, `!include`, `!secret`, line endings and a byte-order mark all survive. It is never parsed and re-dumped. A symlinked file stays a symlink. |
| The homeowner has an `http:` block | Anything that is not our exact include line: left untouched, reported `http_block: present_unmanaged`. `http.yaml` is still written, as the text a technician merges from; since nothing reads it, no restart is owed. |
| No `configuration.yaml` | Not invented. Reported `missing`. |

`http.yaml` is written **before** the include line goes in: an `!include` of a
file that is not there stops Home Assistant loading its configuration. When
the include is newly added, `http.yaml` is rewritten even if its text is
identical, so its mtime stays a faithful record of the last change a restart
must pick up.

The `http:` block is read only at start, so a change is not live until Home
Assistant restarts, and **the agent never restarts it**. `restart_required` is
true while `http.yaml` is newer than the Home Assistant process (`psutil`'s
process start): no state of ours to lose, an integration reload does not clear
it, and a restart does. Switching Remote UI off leaves `http.yaml` and the
include in place -- a stale proxy list is harmless, and removing the line is a
config edit the homeowner did not ask for.

### Reporting readiness

`async_report_status` ends every `async_poll`. It posts to
`POST .../remote-ui/status` when the payload differs from the last one sent,
and at least every ten minutes regardless (the heartbeat that distinguishes a
quiet house from a silent one). Nothing is sent for a house that is off or a
server that predates the feature, and it cannot raise.

| Field | From |
|---|---|
| `connected`, `dials`, `drops`, `last_error` | The dial loop. `last_error` is cut to 500 characters, the server's validation limit, so a verbose dial error cannot cost the house its report |
| `http_block` | `managed`, `present_unmanaged` or `missing` (above) |
| `ranges_version` | The desired state the server last sent |
| `restart_required` | Above |
| `certificate_expires_at` | Parsed from `fullchain.pem` on load, ISO 8601 UTC; `null` if unreadable |
| `token_valid` | The `valid` timestamp in `remote_ui.json`, `null` if not dialling |

A `404` on the status path is remembered like any other; a `409` (not yet
switched on) is not retried every minute.

### The login-lockout health item

A house that is reachable from the internet should be locking out someone who
guesses passwords. Home Assistant's `ip_ban_enabled` does that, and with the
relay in front it only works against the right address because of the
`trusted_proxies` the include above supplies: the address that gets banned is
the real client's, not the relay's.

`collect_health` takes the Remote UI manager and, when it reports `enabled`
(the three files are in place and a token is current) and the `http`
component is **not** locking out failed logins, adds one item to the health
payload:

| Field | Value |
|---|---|
| `type`, `key` | `remote_ui_brute_force_protection_off` |
| `label` | Remote access is on but login lockout is off |
| `severity` | `warning` |
| `detail.text` | For a technician: set `ip_ban_enabled: true` and a `login_attempts_threshold` in the `http:` block, then restart |

Home Assistant exposes no public getter for the setting. The `http` component
installs its `ban_middleware` on the web app only when `ip_ban_enabled` is
true, so `health.py::_ip_ban_enabled` takes that middleware's presence as the
setting being on. If there is no `hass.http`, no app or no middleware list, it
**cannot tell**, and cannot-tell emits nothing -- never "off". The item is not
part of the rollup counts, sits just behind failed integrations so the item
cap does not cut it, and clears by absence like every other item. This
detection has been exercised against stubs only; confirm it on a live Home
Assistant (set `ip_ban_enabled: true`, restart, expect the item to go).

### What is still manual

- **Restarting Home Assistant** after `restart_required`: the agent never does.
- **Merging an existing `http:` block** (`present_unmanaged`): a technician
  adds `use_x_forwarded_for: true` and the `trusted_proxies` from
  `ha_dispatch_client/http.yaml` to the homeowner's own block. Remote UI does
  not work for that house until then.
- **Turning on login lockout**, per the health item above.
- **Hand-placing the files** when the server predates Remote UI (the 404 case).
- **Checking against a live Home Assistant**: the whole Remote UI path runs
  under stubs in the suite; the dial, the TLS termination and the ban detection
  need a real house.

## What is not supported

WebSockets are carried only for `screen` sessions, as frames. Other scopes
remain REST-only, and streaming endpoints are not carried at all.

---

## See also

- [API Reference](api-reference.md) — endpoints 7–11
- [Architecture](architecture.md) — where these modules sit
- [Services](services.md) — service schemas
