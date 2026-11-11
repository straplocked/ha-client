# HA Dispatch Client

Your installer looks after your Home Assistant from a dashboard. This
integration is the part that lives in your home: it tells HA Dispatch how your
system is doing, so problems are found and fixed before you have to notice
them, and it gives you the final say over anything your installer wants to do.

**Version:** 1.10.0 | **Needs:** Home Assistant 2025.1 or later | **License:** [MIT](LICENSE)

## What it does

- **Keeps watch.** Reports that your system is online, how hard it is working,
  and what is wrong: an unreachable device, a low battery, an automation that
  stopped, an integration that failed to start.
- **Knows what is installed.** Reports the versions of Home Assistant, the
  operating system, add-ons and integrations, so a fault can be traced to the
  change that came before it.
- **Checks your backups.** Reports that each backup finished, when, and how
  large it is, so a backup that quietly stops is noticed.
- **Asks before it acts.** Anything beyond watching starts as a request in your
  own Home Assistant, which you approve or decline.
- **Draws your home.** Builds a room-by-room dashboard from your installer's
  3D plan, with each room's own controls one tap away.
- **Keeps itself current.** Updates arrive as a signed release you can see in
  **Settings > Updates**.

## Install

### From the Marketplace (Home Assistant 2026.11 and later)

1. Open the **Marketplace** in Home Assistant and search for
   **HA Dispatch Client**.
2. Install it, then restart Home Assistant when asked.
3. Continue with [Set up](#set-up).

Installing from the Marketplace's catalog needs no account of your own.

### Before it is in the catalog, or on an earlier Home Assistant

Add this repository as a custom repository:

- **Home Assistant 2026.11 and later:** in the Marketplace choose
  **Add from link** and give it `https://github.com/straplocked/ha-client`.
- **Earlier versions:** in HACS, add `https://github.com/straplocked/ha-client`
  as a custom repository with the category **Integration**.

Install **HA Dispatch Client**, restart Home Assistant, and continue with
[Set up](#set-up).

### By hand

Copy `custom_components/ha_dispatch_client` into your Home Assistant
`config/custom_components/` folder and restart. For many houses, see the
[deployment guide](docs/user/deployment.md).

## Set up

1. Ask your installer for a **pairing code**. They make one in HA Dispatch;
   it looks like `ABCD-1234`, works once, and expires after 30 minutes.
2. In Home Assistant go to **Settings > Devices & Services > Add Integration**
   and choose **HA Dispatch Client**.
3. Type the code. That is all: capitals, spaces and the dash do not matter.

Your house then appears in your installer's dashboard, assigned to them.
Running your own HA Dispatch server? Switch on Advanced Mode in your profile
and the setup form lets you give its address.

## What it sends

Everything goes to the HA Dispatch server you paired with, over HTTPS, with a
credential that belongs to your house alone.

| What | How often | Contents |
|------|-----------|----------|
| Heartbeat and system load | every minute | online or not, CPU load, memory, disk space, uptime |
| Health findings | every minute | the names of devices that are unavailable, batteries that are low, automations that are off or failing, integrations that did not start |
| Installed software | every 30 minutes | the name and version of Home Assistant, the operating system, the Supervisor, each add-on and each integration, and what the Marketplace has installed |
| Backups | as they finish | when it ran, how large it is, whether it is automatic or manual, and a checksum |
| Alerts | when raised | the alert you or an automation raised, and its resolution |

None of this includes camera images, recordings or passwords.

## You decide what your installer can do

Watching is automatic. Doing something in your home is not.

- **A request appears in your Home Assistant.** It says who is asking, why, and
  in plain words what they will be able to do. Nothing happens until you press
  **Approve**. **Decline** is always there, and declining changes nothing.
- **Looking is separate from changing.** A request to *look* gives your
  installer read-only access: Home Assistant itself refuses any change made
  under it. A request to *make changes* says so, and says for how long.
- **You can end it at any moment**, with the `ha_dispatch_client.revoke_access`
  action.
- **Some permissions stay on until you turn them off**, and the request says so
  in those words: *hands-off remote configuration* (your installer setting things
  up without asking each time) and *reaching your home from your phone*.
  If your installer switches hands-off configuration on for you, your Home
  Assistant tells you who did it and when, and gives you the way to turn it
  off again, a link in the notification or the
  `ha_dispatch_client.revoke_enhanced_permissions` action.
- **Reaching your home from your phone** uses your own Home Assistant login.
  The certificate's key is made in your house and never leaves it.

The full design is in [Remote access](docs/technical/remote-access.md).

## Updates, and the Marketplace

When your installer's HA Dispatch offers a newer version of this integration,
it appears at **Settings > Updates** like any other, and installs on a tap, or
when your installer schedules it from the dashboard.

Every release is signed. The client carries the matching public key inside it
and installs a release only if the signature verifies against that key, so a
download that was altered on its way is refused before anything is written. The
signing key is held offline by the people who make this integration. The HA
Dispatch server never has it, so the server can offer an update and choose
when, but it cannot choose what code runs in your home.

This sits comfortably beside the Marketplace. After a self-update the client
corrects the Marketplace's record of which version is installed, so the
Marketplace never offers to replace a newer signed release with an older
one. Installing and updating through the Marketplace works on its own too.

Details: [Updating the client](docs/user/updating.md).

## What you get in Home Assistant

| Entity | Shows |
|--------|-------|
| `sensor.ha_dispatch_status` | Online, offline or warning |
| `sensor.ha_dispatch_cpu_load` | 1-minute CPU load |
| `sensor.ha_dispatch_memory_used` | Memory use, in percent |
| an update entity | The client's own version, and an update when one is offered |
| a binary sensor | Whether a request from your installer is waiting for you |

There are 12 actions (services) for testing, alerts and management; see the
[services guide](docs/user/services-guide.md).

## Documentation

Everything lives in [`docs/`](docs/INDEX.md).

| Audience | Start here |
|----------|------------|
| **Homeowners and installers** | [Quickstart](docs/user/quickstart.md) / [Installation](docs/user/installation.md) / [Updating](docs/user/updating.md) / [Troubleshooting](docs/user/troubleshooting.md) |
| **Developers** | [Architecture](docs/technical/architecture.md) / [API reference](docs/technical/api-reference.md) / [Dev guide](docs/technical/dev-guide/README.md) |
| **Publishing** | [Marketplace submission checklist](docs/technical/marketplace-submission.md) |

## Support

Problems with this integration: [open an issue](https://github.com/straplocked/ha-client/issues).
Problems with your own system: ask your installer first.
