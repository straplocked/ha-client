# Updating the Client

How to update the HA Dispatch client integration, and what happens when you do.

> **Availability.** Self-update is built into version 1.5.0 and later, but it
> stays switched off until your HA Dispatch server is set up to serve signed
> releases. Until then the update entity shows "up to date" and you should keep
> using [deploy.sh](deployment.md). Nothing here breaks in the meantime — the
> client simply never sees an update on offer.

---

## The three ways to update

| Route | Best for | Needs |
|-------|----------|-------|
| **Settings → Updates** in Home Assistant | One instance, when you want to watch it happen | Server offering a release |
| **HA Dispatch dashboard** | A fleet, or scheduled overnight updates | Server-side rollout support |
| **[deploy.sh](deployment.md)** over SSH | First install, recovery, air-gapped boxes | Shell access |

---

## Updating from Home Assistant

When the server offers a newer client, an update appears at
**Settings → Updates**, exactly like a Home Assistant core or add-on update.

1. Open **Settings → Updates**
2. Find **HA Dispatch Client**
3. Review the release notes
4. Click **Install**

Home Assistant restarts automatically once the new version is in place. The
restart is required — Python code that is already loaded cannot be swapped out
in a running process.

You can also trigger this from an automation with the
`ha_dispatch_client.install_update` service. See the
[Services Guide](services-guide.md#7-install-client-update).

---

## Updating from the dashboard

From the HA Dispatch dashboard you can:

- See which installations are running which client version
- Push a specific version to one installation or a group
- Turn on **auto-update** per installation
- Set an **update window** so unattended updates only run overnight
- Pin an installation to a version and hold it there

Auto-update is **off by default** on every installation, and turning it on is a
per-installation decision. Installing code unattended is not something to
inherit by accident.

---

## What actually happens during an update

1. The client downloads the release archive from GitHub
2. It checks the archive against a **signature built into the client itself**
3. It inspects the archive contents before extracting anything
4. It copies the current version aside as a backup
5. It moves the new version into place
6. Home Assistant restarts
7. On startup the client confirms the new version is running and tells the server

Steps 2 and 3 happen while your working copy is still untouched. An archive that
fails either check is discarded and nothing on disk changes.

### Why the signature matters

Installing an update means running new code on your Home Assistant machine. The
client only accepts releases signed with a key that is built into the client
itself — the HA Dispatch server never holds that key.

This means that even if your Dispatch server were completely compromised, an
attacker still could not use it to run their own code on your Home Assistant
instances. They could decline to offer updates, or offer an older genuine
release, but not a forged one.

---

## If an update goes wrong

**Be aware:** there is no automatic rollback. If a release fails to load, the
integration's own code does not run either — which means it cannot repair
itself. This is a deliberate limitation, not an oversight, and it is worth
knowing before you enable auto-update.

What protects you instead:

- Every check that can be made is made *before* your working copy is replaced
- The previous version is kept as a backup
- Staged rollouts mean a bad release reaches a few instances, not all of them
- The server notices when an instance goes quiet after starting an update

### Recovering by hand

The previous version is kept at:

```
<config>/.ha_dispatch_client/backups/<version>/
```

To restore it over SSH:

```bash
ssh user@homeassistant.local
```

```bash
rm -rf /config/custom_components/ha_dispatch_client && cp -r /config/.ha_dispatch_client/backups/1.4.0 /config/custom_components/ha_dispatch_client
```

Then restart Home Assistant. Replace `1.4.0` with whichever version is in the
backups directory, and adjust `/config` if your configuration lives elsewhere.

The two most recent backups are kept; older ones are pruned automatically.

---

## Checking what version you have

**In Home Assistant:** Settings → Devices & Services → HA Dispatch Client, or
look at the `update.ha_dispatch_client_update` entity.

**Over SSH:**

```bash
ssh user@homeassistant.local 'cat /config/custom_components/ha_dispatch_client/manifest.json'
```

**In the logs**, after any update attempt:

```bash
ssh user@homeassistant.local 'ha core logs | grep ha_dispatch_client'
```

---

## Troubleshooting

**"No release signing keys are pinned in this build"**
The client was built without a signing key, so it cannot verify any update. This
is the shipped default. Updates must be installed with
[deploy.sh](deployment.md) until your build pins a key.

**"Signature verification FAILED"**
The archive is not signed by a key this client trusts. Nothing was installed.
Do not work around this — it means either the release was built with the wrong
key, or the archive is not the one that was signed.

**"Refusing a release URL on untrusted host"**
The server offered a download from somewhere other than GitHub. Nothing was
installed.

**The update installed but the version did not change**
The swap succeeded but Home Assistant loaded the old code, usually because the
restart did not happen. Restart Home Assistant. Home Assistant also caches
`manifest.json` aggressively — see the caching notes in
[Deployment](deployment.md).

**Home Assistant did not come back after an update**
See [Recovering by hand](#recovering-by-hand) above.

---

## Related Guides

- [Deployment](deployment.md) — deploy.sh, versioning, caching caveats
- [Services Guide](services-guide.md) — the `install_update` service
- [Troubleshooting](troubleshooting.md) — general issues
- [Self-Update design](../technical/self-update.md) — how it works internally
