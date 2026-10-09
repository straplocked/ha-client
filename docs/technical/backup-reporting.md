# Backup Reporting

The agent tells the Dispatch server about this installation's own backups, so
the server can notice a backup schedule that has stopped or an archive that
has suddenly shrunk. The server-side checks and alert thresholds are described
in ha-dispatch's `docs/technical/backup-verification.md`; the wire contract is
in its `docs/client-integration/backup-reporting.md`.

Code: `custom_components/ha_dispatch_client/backups.py`. Tests:
`tests/test_backups.py`.

## Source

Home Assistant's backup manager, `hass.data["backup"]`, from **2025.1**. It
lists every backup on every agent (the local disk on Core, the Supervisor's
`/backup` on HAOS, Nabu Casa cloud, any other target) and lists each backup
once however many agents hold a copy. Before 2025.1 the manager had a
different shape; the agent detects that and reports nothing, which the server
reads as "this agent does not report backups", not as "overdue".

## What is reported

| Backup | Slug | `type` |
|---|---|---|
| Made by Home Assistant's automatic backup schedule | `automatic` | `full` or `partial` |
| Any other full backup (often a user's own automation) | `manual` | `full` |
| A manual partial backup | not reported | |
| A remote update run's own pre-update backup (`HA Dispatch pre-update …`) | not reported | |

The server scores freshness and size **per slug**, so a slug has to name one
schedule whose backups are comparable night to night. Manual partial backups
are left out for that reason: a one-off snapshot of one add-on next to a 2 GB
full backup reads as a 99.9% size collapse. "Full" means Home Assistant and its
database are included and, on a supervised install, add-ons or folders as well.
Without that last condition, a config-only backup (what Core's own pre-update
backup looks like on HAOS) would count as full.

Each entry carries:

- `completed_at`: the backup's own date.
- `size_bytes`: the size of the copy on this machine, or else the largest copy.
- `name`: the backup's name.
- `checksum`: `sha256:<hex>`, when one could be computed (see below).

## Once each, oldest first

The server does not de-duplicate. The agent keeps the ids it has reported in
Home Assistant's storage (`ha_dispatch_client.backups_reported`), keyed to the
installation id. So a restart sends nothing twice, and a **re-enrolled**
installation, which is a new record on the server, gets its history again.
Ids that Home Assistant no longer lists (removed by retention) are pruned.

The first scan sends at most the newest 10 backups per slug, which is enough
history for the server's size check (median of the last 5). Older ones are
marked as seen and never sent. Everything goes to `POST .../backups/batch`,
oldest first, at most 100 backups per request.

## Checksums

The agent hashes a copy that sits on the house's own network:

- `backup.local` (a Core install): read from disk in the executor.
- `hassio.local` (this machine's `/backup` on HAOS): streamed from the
  Supervisor.
- `hassio.<mount>` (a network share the Supervisor mounts for backups, such
  as a NAS): also streamed through the Supervisor. This arrived in 1.7.8
  without it; a house that backs up only to a share got no checksum at all.

When there is a choice, this machine's copy is read first. A backup held only
in Nabu Casa cloud or another off-site target is never downloaded to hash it.
Within one scan, only the newest new backup of each slug is hashed. In normal
running each backup is the newest one when it is first seen, so every backup
gets a checksum; on the first scan, only the latest backup of each slug does.
If hashing fails, the backup is still reported, without a checksum, and a
warning naming the backup and its source goes to the Home Assistant log.

## Cadence

- A scan runs every 30 minutes, and on the next 60-second tick after Home
  Assistant reports a backup as `completed`. The agent subscribes to the
  backup manager's events and keeps retrying the subscription until the
  backup integration has loaded.
- The scan runs as a background task, so hashing a large archive never holds
  up the coordinator's poll, and only one scan runs at a time.
- If a report fails in transit, the scan is not recorded as done, so the next
  tick retries. Backups that did reach the server are remembered.
- A 404 from the batch endpoint means the server predates backup reporting.
  Reporting then stays off until Home Assistant restarts.
