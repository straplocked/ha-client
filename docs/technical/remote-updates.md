# Remote Updates — Design Specification

**Status:** Implemented. Client in 1.7.0 (OS reboot-to-activate in 1.11.1); server side built in the HA Dispatch repository.
**Author:** Design spec, 2026-09-21
**Related:** [Architecture](architecture.md) · [Remote Access](remote-access.md) · [Self-Update](self-update.md)

> The server's half — the consent-gated update run, the fleet-risk gate, and the
> technician console — lives in the HA Dispatch repository at
> `docs/technical/remote-updates.md` and `docs/client-integration/remote-updates.md`.
> Where that documentation and this one differ about the server, that one is
> authoritative.

---

## 1. Problem

A competitor could reach into a customer's Home Assistant and update its Core,
its OS and its add-ons. This client could report what was installed and score
the risk of a version, but it could not *apply* an update. Remote access lets a
technician drive the REST API by hand through the consent tunnel, but a Core
version bump with a pre-update backup is a multi-step operation that should be
one deliberate, audited act — not a sequence of raw proxied requests.

## 2. Shape

An update is a **run**: the technician plans it on the server, the homeowner
consents to that specific version change on the consent page, and only then does
the server offer the run to this agent. The agent's job is narrow — back up,
apply, report each phase — and it never decides whether an update is allowed.
That is the server's, enforced by consent.

```
server: plan ──▶ homeowner consents ──▶ run becomes "queued"
                                              │
agent:  poll updates/pending ◀────────────────┘
        report started  →  backup  →  apply  →  confirm
                                             ╰─ OS: restart (host reboot) → confirm on boot
```

Consent is **not** re-checked here. A run only appears on `updates/pending` once
consent is granted, and it leaves the moment consent is revoked, denied or
expires. The agent executes what it is handed and reports honestly.

## 3. The two endpoints

Both use the same bearer token as the rest of the V1 API, and both answer 404 on
a server that predates remote updates — which disables the feature quietly rather
than triggering re-enrolment (`RemoteUpdatesUnavailable`, mirroring remote
access).

```
GET  /api/v1/installations/{id}/updates/pending
POST /api/v1/installations/{id}/updates/{run}/report
```

`pending` returns `{ "updates": [ {run_id, kind, slug, from_version,
target_version, backup} ] }`. `kind` is `core`, `os`, `supervisor` or `addon`;
`slug` is `core`/`os`/`supervisor` for the platform or the add-on slug.

`report` takes `{status, phase, backup_reference, error, detail, timestamp}`.
`status` is `started`, `progress`, `success` or `failed`; `phase` is `backup`,
`download`, `apply`, `restart` or `confirm`.

## 4. Running a update

`HADispatchUpdates`, polled on the coordinator's cadence alongside remote access
and just as unable to raise, schedules one run at a time (the server guarantees
one open run per installation; the guard is insurance). Each run, in
`_async_run`:

1. **Report `started`** before touching anything. As with a self-update, an
   installation that reports it began and then goes silent has told us the
   update broke it — a signal no failure report from a dead box could send.
2. **Back up**, unless the run said not to. A partial backup of the add-on when
   updating one, a full backup otherwise. The backup's name is reported as the
   `backup_reference` the receipt records.
3. **Persist the run's target**, then **apply**. The persist happens *before*
   the apply because a Core update restarts Home Assistant and kills this
   process mid-call — the persisted record is the only way the run gets
   confirmed afterwards.
4. **OS only: reboot the host** (client 1.11.1). See below.
5. **Confirm.** If the process is still alive after the apply — an add-on, or a
   platform update that deferred its restart — the run confirms inline by
   comparing the now-installed version to the target. If the process was
   restarted, `async_load()` on the next boot reads the persisted record and
   confirms from what is installed then, reporting `success` or `failed` at the
   `confirm` phase.

A failed backup or apply reports `failed` with the phase it happened in and
stops. The inventory is left to reconcile through the normal component report
rather than being patched by the run.

### 4.1 Home Assistant OS: staged, then a host reboot

Every OS run before 1.11.1 failed. `update.install` for the OS **returns
normally** having only written the new OS to the spare boot slot; it runs after
the next *host* reboot. The agent confirmed inline, read the old version and
reported "After the update, os reports 18.0, not 18.4." — and cleared the
record, so nothing re-checked. Asked again, the Supervisor refused with
"Version 18.5 is already installed, reboot the system to activate it", which
was reported as a second failure.

Since 1.11.1 an OS run, once the apply returns:

- **marks the record `reboot_requested`** and writes it *before* anything
  else, so the boot that follows confirms from it;
- **reports `progress` at phase `restart`** with
  `detail: {"action": "reboot", "reason": "os_staged", "target_version": …}`
  — what Dispatch shows as "Rebooting";
- **asks the Supervisor to reboot the host** (`hassio.host_reboot`). Never a
  Core restart: that leaves the old OS running. The homeowner consented to the
  update, and the reboot is how it lands.

It is **never confirmed inline**: before that reboot nothing can tell a staged
OS from an active one — the OS update entity already names the staged version
as installed.

The Supervisor's "already installed … reboot … to activate" refusal takes the
same path, **if the staged version is the run's target**. If it names another
version, the run fails with that version in the message and no reboot: the
homeowner consented to a version, not to whatever happens to be staged.

**One reboot per run.** The `reboot_requested` mark is carried over if the
same run is handed out again, and a run that has it is failed, not rebooted.
On boot, a host that came back on the old version is reported `failed` at
`confirm` — "Rebooted to activate Home Assistant OS 18.5, but the host came
back on 18.0." — and the record is cleared. The boot-time confirmation itself
never reboots.

If the reboot request is refused, the run is reported `failed` at `restart`
("… is installed and waiting for a reboot … Reboot the host to finish the
update.") and the record is kept marked `reboot_failed`: a host rebooted by
hand later still reports `success` on that boot, and anything else is not
said twice.

### 4.2 What "installed" means at confirm time

The confirmation reads the component's **update entity's `installed_version`
first**, then the component inventory. The inventory reads the Supervisor's
cached info, which can lag — it said 18.0 while the entity already said 18.5 —
so either source naming the target settles it, and a loaded entity naming
another version is a definite "no".

At boot (`async_load`) the confirmation runs **in the background** and waits
for Home Assistant to know: an entity that is missing, `unavailable` or
`unknown` is "no answer yet", not a version. It looks up to 20 times, 15 s
apart (`UPDATE_RUN_CONFIRM_ATTEMPTS`, `UPDATE_RUN_CONFIRM_INTERVAL`: five
minutes), then decides from the inventory, or reports that no version could be
read. The record is cleared once the outcome is decided, so a restart during
the wait confirms on the next boot instead of being lost. The inline
confirmation (add-ons) looks once.

## 5. The Supervisor seam

Everything that changes the system lives behind `SupervisorUpdater`, the one part
that needs a real supervised Home Assistant to exercise. It makes Home Assistant
service calls only, so the orchestration around it is tested with a fake in its
place:

| Operation | Call |
|---|---|
| Backup (add-on) | `hassio.backup_partial` `{name, addons: [slug]}` |
| Backup (platform) | `hassio.backup_full` `{name}` |
| Update (everything) | `update.install` on the component's update entity, `backup: false` |
| Activate a staged OS | `hassio.host_reboot` |

**Everything installs through `update.install`, including add-ons.** The
Supervisor's own `hassio.addon_update` was deprecated in Home Assistant 2024.11
(core PR #127927) in favour of the update entity, so calling it on a recent core
fails outright.

Two things are resolved at run time rather than assumed, both verified against a
real supervised 2026.6.4:

- **Which entity.** The platform's three are fixed
  (`update.home_assistant_core_update`, `..._operating_system_update`,
  `..._supervisor_update`). An add-on's id derives from a name the user can edit,
  so it is found by the Supervisor slug in its icon URL
  (`/api/hassio/addons/<slug>/icon`) — the only place the slug appears on the
  entity.
- **Whether a version may be sent.** `UpdateEntityFeature.SPECIFIC_VERSION` is
  bit 2 of `supported_features`. Core reports 15 and the OS 11, both of which
  include it; an add-on reports 29, which does not. Sending `version` to an
  entity that does not support it fails the whole run, so the bit is checked and
  an add-on run installs whatever the Supervisor has.

The backup is taken as its own named call rather than by passing `backup: true`
to `update.install`, so the run can report the restore point's slug on the
receipt. `hassio.backup_full` returns that slug as response data; a core that
refuses `return_response` falls back to the backup's name.

> These names remain the integration seam. Home Assistant has moved them before —
> that is exactly how `hassio.addon_update` became a trap — so re-validate against
> the target version, and adjust `SupervisorUpdater` alone if they have shifted.
> Nothing else in the run pipeline depends on them.

## 6. What is deliberately not here

- **No consent enforcement.** The server owns it. The agent cannot discover, let
  alone run, an update nobody agreed to.
- **No inventory patching.** A successful update and the version report that
  follows it are two separate, honest signals; the run does not fake the second.
- **No unattended trigger.** Unlike the client self-update's `auto_update`, a
  remote HA update always originates from a technician plan and a homeowner
  consent. There is no path here that installs a Home Assistant update without a
  deliberate human decision on both ends.
