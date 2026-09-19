# Component Inventory

How the client reports what the installation is running, and why the rules
below are not negotiable.

Implementation: `custom_components/ha_dispatch_client/components.py`, posted by
`HADispatchCoordinator.async_report_components()`.

Server contract: the HA Dispatch repository's
`docs/client-integration/component-reporting.md`.

---

## Why this exists

Metrics say how hard the machine is working. Health says what is broken.
Neither says which **versions** are installed, and that is where every
diagnosis starts: *what changed?*

Two features read this:

1. **Update risk intelligence.** The fleet derives its own advisories from what
   happens to installations after they upgrade. An installation that reports an
   inventory contributes to that signal and benefits from it.
2. **The change timeline.** Correlating a fault with the transitions that
   preceded it.

Before 1.7.0 the client never posted one, so the dashboard's Components tab was
empty for every installation and the risk pipeline had nothing to score.

---

## The endpoint

```
POST /api/v1/installations/{installation_id}/components
Authorization: Bearer <access_token>
```

```json
{
  "components": [
    { "kind": "core", "slug": "core", "name": "Home Assistant Core",
      "version": "2026.9.1", "failing": false },
    { "kind": "addon", "slug": "core_mosquitto", "name": "Mosquitto broker",
      "version": "6.5.1", "failing": false }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `kind` | yes | `core`, `os`, `supervisor`, `addon`, `integration`, `hacs` |
| `slug` | yes | stable identifier within its kind, max 191 chars |
| `name` | no | human label, max 255 chars |
| `version` | no | max 64 chars; omitted when genuinely unknown |
| `failing` | no | defaults `false` |

`kind` is a wire contract with the server — `InstallationComponent::KINDS`. An
unknown kind is rejected with a 422.

---

## The three rules

### 1. Full snapshot, never a delta

Every report carries the complete current inventory, including components that
have not changed since the last one.

The server reconciles what it is sent against what it holds and **retires
anything absent**. Removal is expressed by omission — there is no "deleted"
flag. So an agent that sent only what changed would retire almost the entire
inventory on its second report, and an agent that skipped a poll would leave it
permanently wrong.

`components.py` therefore keeps no memory of the previous report. There is
nothing to diff against, deliberately.

### 2. Slugs are stable

The slug is half a component's identity — the server keys on `(kind, slug)`. A
slug that moves between polls reads as one component being removed and a
different one installed, which corrupts both the change timeline and the risk
evidence attached to a version.

Every slug is a Home Assistant identifier the user cannot rename:

| Kind | Slug source | Not used |
|---|---|---|
| `core` | the literal `core` | — |
| `os` / `supervisor` | the literal `os` / `supervisor` | — |
| `addon` | the Supervisor add-on slug (`core_mosquitto`) | the add-on's display name |
| `integration` | the integration **domain** (`zha`) | the config entry title |
| `hacs` | the repository (`owner/repo`) | the repository's display name |

The config entry title is used for nothing at all. It is user-editable, and an
integration with two config entries has two of them — slugging on it would turn
one Hue integration into two components and retire one of them next poll.

### 3. `failing` only where Home Assistant knows

| Set `failing` | Why |
|---|---|
| A config entry in `SETUP_ERROR`, `SETUP_RETRY`, `MIGRATION_ERROR` or `FAILED_UNLOAD` | Home Assistant could not set it up. A fact. |
| A Supervisor add-on in state `error` | Supervisor could not run it. A fact. |

Everything else is left `false`. A **stopped** add-on is explicitly not a
failure: people stop add-ons on purpose, and reporting that as a fault would
blame the last upgrade for a deliberate act — fleet-wide, for everybody about
to install that version.

The failed-config-entry set is shared with health reporting
(`health.FAILED_ENTRY_STATES`), so "Home Assistant could not set this up" has
one definition rather than two that drift.

---

## Where each kind comes from

| Kind | Source | Absent when |
|---|---|---|
| `core` | `homeassistant.const.__version__` | never |
| `os` | `hassio.get_os_info()` | Core or Supervised-on-generic-Linux |
| `supervisor` | `hassio.get_supervisor_info()`, falling back to `get_info()["supervisor"]` | Core-only |
| `addon` | `hassio.get_addons_info()`, falling back to `get_supervisor_info()["addons"]` | Core-only, or no add-ons |
| `integration` | config entry domains + `loader.async_get_custom_components()`, names and versions from `loader.async_get_integrations()` | never (there is always something) |
| `hacs` | `hass.data["hacs"].repositories.list_downloaded` | HACS not installed |

**A missing kind is normal, not an error.** Most installations are Home
Assistant Core with no Supervisor, no add-ons and no HACS. Every source above
degrades independently to "nothing found" — an unavailable Supervisor helper, a
HACS that rearranged its internals, or a manifest that will not load costs that
one source and nothing else.

Built-in integrations ship no manifest `version`, so they are reported with a
name and no version. Inventing one — the core version, say — would put a
fabricated transition in the fleet's evidence. Custom integrations are included
even without a config entry, because that is where versions actually live.

---

## Cadence

| When | Report |
|---|---|
| First coordinator tick after a restart | yes — nothing is known yet |
| Every 30 minutes thereafter | yes |
| Every other 60 s metrics poll | **no** |
| Immediately after a client self-update | yes, forced |

`COMPONENT_REPORT_INTERVAL` is 1800 s, inside the contract's 15–60 minute
window. Component versions change rarely and the server's attribution window is
two hours wide, so riding the metrics poll would cost bandwidth without
improving the signal.

The forced post-update report is the exception that matters. The transition is
what opens the observation window; a version discovered half an hour late gets
credited with half an hour of unrelated faults. The client is itself a
component (`integration:ha_dispatch_client`), so a self-update changes the
inventory. Both update paths report:

- **restart path** — `ClientUpdater.async_confirm_pending()` reports after
  confirming the swap took. This runs during setup, *before* the coordinator's
  first refresh, so it replaces that tick's report rather than adding to it.
- **no-restart path** — `restart_after_update: false` swaps the files and
  leaves Home Assistant running, so nothing else would notice the new version
  for up to half an hour. Reported immediately.

A report that fails to send does not stamp the clock, so the next tick retries
rather than waiting out the full interval on a transport blip.

---

## Limits and truncation

The server validates at **2000** components per request and retains **800** per
report, dropping the tail. The client truncates at **750**, under both.

Truncating here rather than at the far end means a very large installation
loses rows somebody chose for it to lose. The order is:

1. `core`, `os`, `supervisor` — the highest-impact upgrades an installation can
   make, and there is only ever one of each
2. everything carrying a **version**, sorted by `kind:slug`
3. everything **without** a version, sorted by `kind:slug`

A versionless row contributes nothing to the transition ledger and nothing to
risk scoring, so it is the right thing to lose. The sort matters as much as the
bands: an unstable cut would retire and reinstate the same components forever.

Names over 255 characters and versions over 64 are trimmed rather than sent
whole, so one over-long field cannot cost the entire report a 422.

---

## First report

An installation's first-ever inventory is treated by the server as
**enrolment**, not a burst of installs. No transition events are recorded for
it and the versions present on day one are not used as evidence for or against
anything.

This is why the first tick after every Home Assistant restart reporting an
inventory is harmless rather than noisy — it is only enrolment once, on the
server's very first sight of the installation.

---

## Failure behaviour

Inventory reporting is intelligence, not operation. It never takes anything
else down with it:

- A collection failure is logged and the report skipped. Metrics and health
  still go out.
- A transport failure is logged and retried on the next tick.
- An empty collection is never posted — the server validates `components` as
  required, so an empty list is a 422 rather than "this installation runs
  nothing". The `core` row means this cannot happen in practice.

---

## See also

- [API Reference](api-reference.md) — Endpoint 12
- [Self-Update](self-update.md) — the update path that triggers a forced report
- [Architecture](architecture.md)
