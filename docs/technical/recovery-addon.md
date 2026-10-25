# Recovery Add-on (the watchdog)

**Status:** Phase 1 (lab), 2026-10-25. It runs on the owner's Pi as a local
add-on, built on the house, with **no Dispatch connection yet**. The design, the
owner's decisions (D1–D16, D16 dropped) and the Phase 0 findings are in the
Dispatch repo: `docs/technical/core-watchdog-addon.md`. This page covers what
is built here.

## Why it is an add-on and not part of the integration

The integration runs inside Home Assistant Core's process. On 2026-10-24 Core
segfaulted on start after the 1.9.1 update and stayed down for about four
hours. The integration, the technician tunnel and Remote UI went down with it.
The Supervisor never retried, because a crash inside its own restart job
leaves it in an error state that its Core watchdog skips. An add-on is a
separate container that the Supervisor keeps running when Core is down, so it
can make the call that fixed it on the day: `POST /core/start`.

## What it does

| When | It does | Wait |
|---|---|---|
| Core gives no 200 on `/auth/providers` for **3 minutes** | `POST /core/start` | 5 min |
| Still down | `POST /core/rebuild` (skipped under 2 GB free) | 10 min |
| Still down, **and** our client update started under 6 h ago and never confirmed, **and** the backup of the previous version is on disk | Copies the backup into place, then `POST /core/start` | 10 min |
| Still down | Stops, logs "Needs a human", writes the incident note | — |
| Core **up** for 10 min but the pending record is still there (the integration never loaded) | Copies the backup into place, then `POST /core/restart` (once) | 10 min |

"Back" means 2 minutes of answering. In the Core-up case it also means the
integration loaded and cleared its pending record.

It **stands down** while the Supervisor runs a Core update, a backup or a
restore. That list is fixed: `home_assistant_core_restart_after_problem` stays
open for ever in the failure state, so it is deliberately not on it. It
**waits** when the Supervisor refuses for now (not `running`, or an offline
migration), and it never sends `force`. A crash within an hour of a recovery,
or a fourth ladder in 24 hours, is not re-laddered. It goes straight to "Needs
a human".

## The contract with the integration

The add-on reads, and never writes:

- `.storage/ha_dispatch_client.update`: the updater's pending record
  (`from_version`, `to_version`, `started_at`). The updater writes it before it
  restarts Core, and `async_confirm_pending()` clears it once the integration
  loads.
- `.ha_dispatch_client/backups/<version>/`: the previous tree, which must hold
  a `manifest.json` with `domain: ha_dispatch_client` and that `version`.
- `.ha_dispatch_client/contract.json`: `{"watchdog_contract": N}`. When absent
  it is read as 1, the 1.9.x layout. An unknown N refuses restore.

It writes only to:

- `custom_components/ha_dispatch_client/`, and only in the restore swap
- `.ha_dispatch_client/recovery/`: `staging/`, `displaced/<version>/` (exactly
  one kept), and `incidents/<id>.json`, for the integration to announce
  (Phase 2)

**Changing any of the read paths or fields is a contract change.** Bump
`watchdog_contract`.

## What it must never do (each has a test)

- Write outside the two areas above. `restore._writable()` checks every write
  after resolving symlinks.
- Restore without a backup that matches `from_version`.
- Call any Supervisor path not in `supervisor.py`. That file has no generic
  call, nothing under `/store`, `/backups` or another add-on's `/addons/<slug>`,
  and nothing that updates or reboots anything.
- Run a command: no `subprocess`, `os.system`, `eval` or `exec` anywhere in the
  package.
- Probe `/api/`, or send `force`.

It runs with `hassio_role: homeassistant`. Phase 1 proved that this covers
every ladder call. The host journal, where a segfault that happens before
Python starts is recorded, needs `manager`, and returns 403 as expected.

## Installing it in the lab

On the house, from an SSH session (this is how Phase 1 was installed; from
Phase 2 on, the integration installs it):

```bash
sudo mkdir -p /addons/ha_dispatch_recovery
# copy recovery/ from this repo into it, then:
ha store reload
ha apps install local_ha_dispatch_recovery
ha apps start local_ha_dispatch_recovery
ha apps logs local_ha_dispatch_recovery
```

The first log lines are a self-check of every call it makes, with its HTTP
status, and the sha256 of its own code.

**If you stop Core by hand, stop this add-on first.** Otherwise Core is started
again after about three minutes.

## What the first stop attempt showed

The first `ha core stop` (14:50:05) didn't leave Core stopped. Core **exited
with code 139 (a segfault) on shutdown**. The Supervisor's own Core watchdog
treated that as a crash and started Core again 16 seconds later, and Recovery
logged "answering again; nothing done". That is the intended order: the
Supervisor acts first when it can, and Recovery steps in only when it doesn't,
as on 2026-10-24.

It also means this Pi's Core has segfaulted twice in two days: on start on
2026-10-24, and on shutdown on 2026-10-25. That is a fault on the box (Core
2026.6.4, a native library, or the hardware), not something the client does.
To get a stopped Core that is left alone, as on 2026-10-24, proof 1 switched
the Supervisor's Core watchdog off (`ha core options --watchdog=false`) and
back on afterwards.

Recovery keeps its budget across restarts in `/data/state.json`. A Core that
dies within an hour of a recovery goes straight to "Needs a human". Any proof
run inside that hour will see that rule, not the ladder.

## Tests

`tests/test_recovery.py`, with no Home Assistant needed:

```bash
python3 -m pytest tests/test_recovery.py -q
```

## Phase 1 proofs on the owner's Pi

See the Dispatch repo's design document for the plan. The results so far:

| # | Proof | Result |
|---|---|---|
| — | Install, self-check, own watchdog on, `homeassistant` role, bridge reaches `homeassistant:8123` | **Passed** 2026-10-25 14:49 UTC: every ladder read 200, host journal 403 as expected, `watchdog: true` |
| 1 | `ha core stop` → Core started again about 3 min later | **Passed** 2026-10-25 (EDT). Stop at 14:53:36; noticed 14:53:47; `POST /core/start` at 14:56:47; Core running 14:57:17; confirmed back 14:59:32. Outage **3 min 41 s**. Incident note written; no failed-login noise in Core's log. The Supervisor's own Core watchdog was switched off for the run and back on afterwards (see below) |
| 2 | `lab_simulate_start_failure` → rebuild brings Core back | not run yet |
| 3 | A broken signed test release → restore and restart (Core up, integration not loaded) | not run yet |
| 4 | The same with the backup removed → restore refused | not run yet |
| 5 | Unit tests for every never-rule, eligibility check and stop condition | **Passed** (38) |
