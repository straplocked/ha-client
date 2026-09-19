# Client Self-Update — Design Specification

**Status:** Implemented. Client in 1.5.0; server side built in the HA Dispatch repository.
**Author:** Design spec, 2026-09-17
**Related:** [Architecture](architecture.md) · [API Reference](api-reference.md) · [Updating the client](../user/updating.md)

> **One step remains before anything ships.** Both halves are built and tested,
> but self-update stays inert until a signing key is generated and pinned in
> `const.py` ([§7](#7-signing-and-verification)) and a signed release is cut.
> Until then the update entity shows "up to date" and any install attempt fails
> closed at the verification step. That is the intended behaviour, not a bug.
>
> The server's half — release mirroring, staged rollouts, halt-on-silence, and
> the rollout console — lives in the HA Dispatch repository at
> `docs/technical/client-updates.md`. Where that document and this one differ
> about the server, that one is authoritative.

---

## 1. Problem

The client version is baked into `custom_components/ha_dispatch_client/manifest.json`. The only way to change it on a running instance is [`deploy.sh`](../../deploy.sh) — SSH + tar, one host at a time, with a manual "delete and reinstall" dance when HA caches `manifest.json` (see [Deployment](../user/deployment.md)).

Two consequences:

1. **The server is blind to client versions.** `report_status` sends `ha_version` and `os_info` but never the client's own version, so the dashboard cannot tell which installations are outdated.
2. **There is no delivery path.** A fleet-management product that can observe every instance but cannot ship any of them a fix is only half a product. Shipping a security fix today means N SSH sessions.

This spec covers closing that loop: version visibility, a native Home Assistant update entity, a signed self-installer, and dashboard-driven rollout.

## 2. Goals

- The dashboard knows every installation's client version.
- An operator can update from the Home Assistant UI (Settings → Updates), with no shell access.
- An operator can push a version to one installation, a group, or the whole fleet from the Dispatch dashboard.
- A compromised or impersonated Dispatch server **cannot** execute arbitrary code on client instances.
- One release artifact serves both this mechanism and HACS.

## 3. Non-goals

- Updating Home Assistant core itself. Out of scope; HA has its own updater.
- Automatic rollback of a release that fails to import. See [§9](#9-failure-handling-and-rollback) for why this is not achievable in-band and what replaces it.
- Delta/patch updates. Full-archive replacement only.
- Updating the server from the client.

## 4. Architecture

Five layers, independently shippable in order:

| Layer | Scope | Where |
|-------|-------|-------|
| 1 | Report `client_version` in register + status | Client + server |
| 2 | `update` entity in Home Assistant | Client |
| 3 | Signed installer (download, verify, swap, restart) | Client |
| 4 | Dashboard rollout controls via `desired_state` | Server |
| 5 | HACS packaging | Release process |

Layer 4 rides entirely on the existing configuration-push channel ([`coordinator.py:192`](../../custom_components/ha_dispatch_client/coordinator.py) `_apply_configuration`). No new client transport is needed — just more keys in `desired_state`.

### Trust model in one line

**Artifacts come from GitHub; authority to install comes from the Dispatch server; trust comes from an Ed25519 signature verified against a public key pinned in the client.** The server decides *whether* and *when*; it can never decide *what code runs*.

---

## 5. Wire contract

### 5.1 `client_version` in register and status

Add to the `POST /api/v1/installations/register` and `POST /api/v1/installations/{id}/status` request bodies:

```json
{
  "client_version": "1.4.0"
}
```

Sourced from `homeassistant.loader.async_get_integration(hass, DOMAIN).version`, not from a hardcoded constant — so it always reflects the manifest actually on disk, including after a swap.

Server stores it on `installations.client_version` (nullable string, 32 chars). This is also the **independent confirmation channel** for an update: the server does not have to trust the client's self-reported update result, it can just watch the version on the next check-in.

### 5.2 Release metadata in the status response

The status response gains an optional `client_release` object, present only when an update is applicable to this installation:

```json
{
  "installation_status": "active",
  "config_version": 7,
  "client_release": {
    "version": "1.5.0",
    "url": "https://github.com/straplocked/ha-client/releases/download/v1.5.0/ha_dispatch_client.zip",
    "sha256": "9f2c…",
    "signature": "base64-encoded Ed25519 signature over the raw zip bytes",
    "key_id": "hadc-2026-01",
    "release_url": "https://github.com/straplocked/ha-client/releases/tag/v1.5.0",
    "release_notes": "markdown string",
    "min_ha_version": "2024.1.0",
    "size_bytes": 48213
  }
}
```

Carried on the status response rather than a separate endpoint so the common case — "no update available" — costs zero extra requests on the 60s poll.

`key_id` identifies which pinned public key signed the artifact, so keys can be rotated without a flag day (see [§7.3](#73-key-rotation)).

### 5.3 New `desired_state` keys

Applied in `_apply_configuration` alongside the existing interval and threshold keys:

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `update_channel` | `"stable"` \| `"beta"` | `"stable"` | Which release track the server should offer |
| `auto_update` | bool | `false` | Install without operator action |
| `target_version` | string \| null | `null` | Pin to an exact version; `null` means "latest on channel" |
| `update_window` | string \| null | `null` | Local-time window, `"03:00-05:00"`. Outside it, defer. |
| `restart_after_update` | bool | `true` | Call `homeassistant.restart` after a successful swap |

`auto_update` defaults to **false**. Opt-in, per installation, set from the dashboard.

### 5.4 Update result reporting

```
POST /api/v1/installations/{installation_id}/client-update
```

```json
{
  "status": "started" | "success" | "failed",
  "from_version": "1.4.0",
  "to_version": "1.5.0",
  "error": "signature verification failed",
  "phase": "download" | "verify" | "validate" | "swap" | "restart" | "confirm"
}
```

`started` is sent before the swap so a fleet-wide rollout that bricks instances still leaves a trail — an installation that reports `started` and never checks in again is the signal that halts the rollout ([§10.3](#103-canary-rollout)).

`success` is sent **after** restart, on the next successful setup, once the running version is confirmed to equal `to_version`.

---

## 6. Release artifact

### 6.1 Format

A single zip asset per GitHub release, named `ha_dispatch_client.zip`, whose **root contains the integration files directly**:

```
ha_dispatch_client.zip
├── __init__.py
├── api_client.py
├── config_flow.py
├── const.py
├── coordinator.py
├── health.py
├── manifest.json
├── sensor.py
├── update.py
├── updater.py
├── services.yaml
└── strings.json
```

Not `custom_components/ha_dispatch_client/…`. This is the layout HACS expects when `hacs.json` names a release asset via `filename`, so **one artifact serves both paths** — no second packaging step.

### 6.2 Accompanying assets

| Asset | Purpose |
|-------|---------|
| `ha_dispatch_client.zip` | The integration |
| `ha_dispatch_client.zip.sha256` | Hex digest, for dashboard display and dedupe |
| `ha_dispatch_client.zip.sig` | Base64 Ed25519 signature over the raw zip bytes |

### 6.3 Release process changes

`bump_version.sh` already keeps `VERSION` and `manifest.json` in sync. A new `release.sh` should:

1. Verify `VERSION` == `manifest.json:version` == the git tag (minus `v`)
2. Build the zip from `custom_components/ha_dispatch_client/`, excluding `__pycache__`
3. Compute sha256
4. Sign with the private key (held offline / in a repo secret, **never** on the Dispatch server)
5. `gh release create v<version> --notes-file …` with all three assets

The signing key living anywhere other than the release pipeline defeats the entire trust model. In particular it must not live on the Dispatch server.

---

## 7. Signing and verification

### 7.1 Why this is the load-bearing part

This feature is, by construction, remote code execution on every Home Assistant instance in the fleet. Without signature verification, the security of every managed home collapses to the security of one Laravel box — and to the integrity of DNS and TLS between each client and that box. With a pinned key, an attacker who fully owns the Dispatch server can still only serve releases you actually signed. The worst they can do is pin the fleet to an older signed version, which is detectable (the dashboard shows the versions) and far short of code execution.

### 7.2 Scheme

- **Algorithm:** Ed25519, signature over the raw zip bytes (not over the digest — one fewer link in the chain to reason about).
- **Verification library:** `cryptography`, which ships with Home Assistant core (`Ed25519PublicKey.from_public_bytes(...).verify(sig, data)`). Declare `cryptography>=41.0.0` in `manifest.json` `requirements` anyway — it resolves as already-satisfied on a normal install and fails loudly rather than silently on an unusual one. Verify this against the target HA version before relying on it.
- **Key storage:** public keys pinned as a literal dict in `const.py`:

```python
# Public keys trusted to sign client releases. Pinned in the client so a
# compromised Dispatch server cannot serve arbitrary code -- it can choose
# whether to offer an update, never what that update contains.
RELEASE_SIGNING_KEYS = {
    "hadc-2026-01": "base64-encoded 32-byte Ed25519 public key",
}
```

### 7.3 Key rotation

Because keys are pinned in the client, rotation requires a release signed by the *old* key that adds the *new* key to `RELEASE_SIGNING_KEYS`. Sequence:

1. Release N, signed by key A, ships `{A, B}` in the pinned dict.
2. Wait until the fleet's minimum version ≥ N (the dashboard can show this directly).
3. Release N+1 onwards signed by key B.
4. A later release drops A.

An installation that falls too far behind to have key B must be updated out-of-band via `deploy.sh`. Keep at least two keys valid at all times so this is rare.

---

## 8. Client implementation

### 8.1 New files

| File | Contents |
|------|----------|
| `update.py` | `HADispatchUpdateEntity(CoordinatorEntity, UpdateEntity)` |
| `updater.py` | `ClientUpdater` — download, verify, validate, swap, restart, confirm |

`PLATFORMS` in `__init__.py` becomes `["sensor", "update"]`.

### 8.2 The update entity

```python
class HADispatchUpdateEntity(CoordinatorEntity, UpdateEntity):
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.PROGRESS
        | UpdateEntityFeature.RELEASE_NOTES
    )
```

- `installed_version` — from `async_get_integration(hass, DOMAIN).version`, read at setup
- `latest_version` — `coordinator.data["client_release"]["version"]`, falling back to `installed_version` when absent
- `release_url`, and `async_release_notes()` returning the markdown from the release payload
- `async_install(version, backup, **kwargs)` delegates to `ClientUpdater`

Two features are deliberately **omitted**, both for the same reason — an entity should not advertise a guarantee its code does not make:

- `BACKUP` implies a full Home Assistant backup. The updater keeps a copy of the previous integration directory ([§9.1](#91-what-the-backup-actually-is)), which is a different and smaller promise.
- `SPECIFIC_VERSION` implies any version can be requested. The server offers exactly one applicable release at a time, so the entity cannot honour an arbitrary version. Pinning is done server-side via `target_version`, which is the mechanism that actually works.

> The `in_progress` / `update_percentage` attribute contract changed across HA releases (bool + separate percentage in recent versions, integer percentage in older ones). Confirm the shape against the minimum HA version targeted in `hacs.json` before writing the progress code.

### 8.3 Install sequence

```
1.  Acquire the update lock (asyncio.Lock on the domain). Refuse if held.
2.  Preflight:
      - running HA version >= min_ha_version
      - target version != installed version, and is not a downgrade
        unless explicitly forced
      - inside update_window, if set
      - free disk space >= 5 x size_bytes
3.  Download the zip to a temp dir under config/.ha_dispatch_client/tmp/,
    streaming, aborting past a hard size cap (size_bytes + slack, absolute
    max 25 MB).
4.  Verify:
      - sha256 matches the advertised digest
      - Ed25519 signature verifies against RELEASE_SIGNING_KEYS[key_id]
    Failure here is terminal and reported. Never fall back to "install anyway".
5.  Validate the archive contents, before extraction:
      - no member with an absolute path, a ".." component, or a symlink
      - no member outside the intended root
      - manifest.json present at the zip root
      - manifest domain == "ha_dispatch_client"
      - manifest version == the requested version
      - uncompressed total size within the cap (zip-bomb guard)
6.  Extract to config/.ha_dispatch_client/staging/<version>/.
7.  Swap (all in an executor thread -- this is blocking I/O):
      a. move custom_components/ha_dispatch_client
         -> config/.ha_dispatch_client/backups/<old_version>/
      b. move staging/<version>/ -> custom_components/ha_dispatch_client
      c. on any failure in (b), move the backup straight back and abort
8.  Write the pending-update record via the storage helper (see below).
9.  POST the "started" result.
10. If restart_after_update: hass.services.async_call("homeassistant", "restart")
    Otherwise raise a persistent notification telling the operator to restart.
```

### 8.4 Two placement details that matter

**The pending-update record must not live inside the integration directory.** Step 7 replaces that directory wholesale, which would destroy the marker. Use `homeassistant.helpers.storage.Store` → `config/.storage/ha_dispatch_client.update`, holding `{from_version, to_version, started_at, phase}`.

**Backups must not live inside `custom_components/`.** Home Assistant's loader scans that directory for anything containing a `manifest.json`; a backup copy sitting there is a second integration claiming the same domain. Keep them at `config/.ha_dispatch_client/backups/<version>/` instead, and prune to the two most recent.

### 8.5 Confirmation on next boot

In `async_setup_entry`, before anything else:

```
record = await store.async_load()
if record:
    if running_version == record["to_version"]:
        POST client-update {status: success}
        prune backups older than the previous version
    else:
        POST client-update {status: failed, phase: "confirm"}
        raise a repairs issue: "update to X did not take effect"
    await store.async_remove()
```

---

## 9. Failure handling and rollback

### 9.1 What the backup actually is

A copy of the previous integration directory at `config/.ha_dispatch_client/backups/<version>/`. Restoring it is a single `mv` over SSH. That is the whole promise.

### 9.2 Why there is no automatic rollback

If a bad release fails to import, the integration's own code does not run — which means its `async_setup_entry`, its repairs issue, and any `restore_previous_version` service it might register are all equally unavailable. In-band recovery is structurally impossible at exactly the moment it is needed.

Options considered and rejected:

- **A separate watchdog integration.** Splits the product in two and has the same bootstrapping problem one level up.
- **A deadline in the marker file, checked on next boot.** Requires our code to run, which is the thing that failed.
- **Restoring from a shell script in `hass.config.path()`.** Nothing executes it.

### 9.3 What replaces it

Three controls that operate *before* the failure reaches the fleet:

1. **Validate hard before swapping.** Every check in step 5 runs on the staged copy while the working version is still live. A malformed or wrong-version archive never reaches the swap.
2. **Canary rollout.** Server-side ([§10.3](#103-canary-rollout)). A release that breaks on import breaks 5% of the fleet, not 100%.
3. **Halt on silence.** The server already tracks `last_seen_at`. An installation that POSTs `started` and then stops checking in is the strongest available signal that a release is bad — and it needs no cooperation from the broken client. Wire this to auto-halt the rollout and raise a dashboard alert.

This should be stated plainly in the user-facing docs too. An operator enabling `auto_update` deserves to know the recovery path is SSH.

### 9.4 Restart caveat

`homeassistant.restart` stops the process and relies on the supervisor or container restart policy to bring it back. On HA OS, Supervised, and a normal Docker deployment with `restart: unless-stopped`, this is fine. On a bare `python -m homeassistant` Core install with no process supervisor, **restart means shutdown**. Detect the install type where possible, and default `restart_after_update` to notify-only for Core installs.

---

## 10. Server-side requirements

### 10.1 Release ingestion

The Dispatch server polls the GitHub Releases API on a schedule (authenticated with a PAT to avoid anonymous rate limits) and caches into a `client_releases` table:

| Column | Notes |
|--------|-------|
| `version` | from the tag, unique |
| `channel` | `stable`, or `beta` when the release is marked prerelease |
| `asset_url` | browser download URL of `ha_dispatch_client.zip` |
| `sha256` | fetched from the `.sha256` asset |
| `signature` | fetched from the `.sig` asset |
| `key_id` | from a `key_id` field in the release body, or a fourth small asset |
| `release_notes` | release body markdown |
| `release_url` | release HTML URL |
| `min_ha_version` | from `hacs.json` in the tagged tree |
| `size_bytes` | asset size |
| `published_at` | |

The server never validates the signature — it has no reason to and no key. It transports it.

### 10.2 Per-installation controls

Filament fields on the installation record, projected into `desired_state`: `update_channel`, `auto_update`, `target_version`, `update_window`, `restart_after_update`. Plus a bulk action: "Update selected to version X".

### 10.3 Canary rollout

A `rollouts` record: target version, channel, cohort percentage, halt conditions, state (`pending`/`running`/`halted`/`complete`). The server offers `client_release` in the status response only to installations inside the current cohort.

Auto-halt when, within a rollout:

- any installation reports `status: failed`, or
- an installation that reported `started` has not checked in within 3× its poll interval, or
- the fleet's aggregate health-item count for the cohort rises sharply post-update

All three are computable from data the server already collects.

### 10.4 Dashboard surfaces

- `client_version` column with an "outdated" filter
- A fleet version-distribution widget (also the input for key-rotation decisions, [§7.3](#73-key-rotation))
- Rollout progress and halt state
- Per-installation update history from the `client-update` reports

---

## 11. Threat model

| Threat | Mitigation |
|--------|-----------|
| Dispatch server compromised, serves malicious code | Ed25519 signature, key pinned client-side and absent from the server |
| Attacker MITMs the client↔server channel | HTTPS, plus the same signature check — the release payload is not trusted |
| Attacker MITMs the GitHub download | Signature over the artifact bytes; a tampered zip fails verification |
| Malicious zip with path traversal (`../../`) | Member-path validation before extraction (step 5) |
| Zip bomb | Uncompressed-size cap and download size cap |
| Forced downgrade to a version with a known bug | Downgrades refused unless explicitly forced; version distribution visible on the dashboard |
| Replay of an old signed release | Same as above — signature valid, but the version check refuses it |
| Signing key stolen from the release pipeline | Key rotation ([§7.3](#73-key-rotation)); keep the key out of CI where practical |
| Unauthenticated instance enrols and pulls releases | Existing registration secret gates enrolment |

The two residual risks worth naming: a stolen signing key is game over until rotation completes, and a compromised server can pin the fleet to an older signed version indefinitely. Both are detectable from the dashboard's version distribution.

---

## 12. HACS interaction

With GitHub releases as the artifact source, HACS support is nearly free: add `hacs.json`, and the repo layout is already correct.

```json
{
  "name": "HA Dispatch Client",
  "filename": "ha_dispatch_client.zip",
  "content_in_root": false,
  "homeassistant": "2024.1.0",
  "render_readme": true
}
```

**Caveat:** if HACS installed the integration and the self-updater later rewrites the same files, HACS's record of the installed version goes stale until it rescans, and a subsequent HACS update may overwrite a newer self-installed version. Do not try to reconcile the two automatically. Instead:

- Default `auto_update` to false.
- Detect a HACS-managed install (presence of the repo in HACS's store data) and surface a repairs issue when `auto_update` is enabled alongside it.
- Document "pick one": HACS for individually-managed instances, Dispatch self-update for fleets.

---

## 13. Open questions

1. **Who holds the signing key?** GitHub Actions secret (convenient, key lives in CI) vs. offline signing on a workstation (safer, manual step per release). Recommendation: offline to start, given release frequency is low.
2. **Does `cryptography` resolve cleanly on all target install types?** It is declared in `manifest.json` `requirements` and ships with Home Assistant core, so it should resolve as already-satisfied — but this has only been confirmed on a development machine, not on HA OS, Container, or Core. Verify on a canary before enabling updates. The updater imports it lazily, so a missing copy degrades to "updates do not work" rather than "the integration does not load".
3. **Should `target_version` pinning survive a re-enrolment?** After `_async_reregister` the server issues a fresh installation record; the new record's `desired_state` defaults would silently unpin a deliberately-held-back instance. Probably needs pinning keyed on `client_id` rather than `installation_id`.
4. **Beta channel opt-in** — per installation, or a separate enrolment flag?
5. **Progress reporting granularity** — download percentage only, or phases? Depends on the HA version's `in_progress` contract ([§8.2](#82-the-update-entity)).

---

## 14. Implementation phases

| Phase | Deliverable | Status |
|-------|-------------|--------|
| 1 | `client_version` in register + status | **Done** — including the server column and "outdated" filter |
| 2 | `release.sh`, `generate_signing_key.py`, `hacs.json` | **Done** — no key generated yet |
| 3 | `update.py` entity | **Done** |
| 4 | `updater.py` — download, verify, validate, swap, marker, restart, confirm | **Done** |
| 5 | `desired_state` update keys; `auto_update` honoured by the coordinator | **Done** |
| 6 | Server-side rollouts, canary cohorts, halt-on-silence | **Done** — in the HA Dispatch repository |

### What is left before this can be switched on

1. **Generate a signing key** and pin its public half in `RELEASE_SIGNING_KEYS`. Until then every install fails closed at verification ([§7](#7-signing-and-verification)).
2. **Cut a signed release** with `scripts/release.sh` so there is something to install.
3. **Publish it and start a rollout** from the Dispatch dashboard. Mirroring a release from GitHub deliberately does not publish it, and publishing deliberately does not ship it.
4. **Then** turn on `auto_update` for one canary installation, not the fleet.

### Test coverage

`tests/test_updater.py` covers the phases where a mistake is expensive:

- signature verification: valid, tampered payload, wrong key, unknown key_id, missing signature, malformed encoding, and the fail-closed no-keys-pinned default
- archive validation: path traversal, absolute paths, symlink members, wrong domain, wrong version, missing manifest, nested `custom_components/` layout, zip bomb, non-zip
- swap: success, failure mid-swap with the working version restored, the unrecoverable case naming both paths, missing staged directory, first install with nothing to back up
- version ordering, prerelease ranking, and the refusal to act on unparseable versions
- update windows including the wrap past midnight and the malformed-value case

Not covered by automated tests: the download path, the restart call, and boot-time confirmation, all of which need a running Home Assistant. Exercise those on a canary instance.

