# HA Dispatch Client — Recovery

Brings Home Assistant back if it stops, and lets your installer know.

Recovery runs beside Home Assistant and keeps running when Home Assistant
doesn't. If Home Assistant stops answering for three minutes, Recovery starts
it again. If that doesn't work, it rebuilds it. If Home Assistant stopped
straight after an update of HA Dispatch Client, it can put the previous
version of HA Dispatch Client back. It never changes your settings, devices or
automations, and it never touches any other add-on.

## If you stop Home Assistant on purpose

Recovery can't tell a deliberate stop from a crash. **Stop this add-on first**,
or Home Assistant will be started again after about three minutes. Start the
add-on again when you're done.

## What it can and can't do

- It can start, rebuild and restart Home Assistant, and put back the previous
  version of HA Dispatch Client from the backup the client keeps.
- It can't read your passwords or log in to Home Assistant, open a port, or run
  commands sent to it.
- It writes only inside HA Dispatch Client's own folders.

## Options

- `lab_simulate_start_failure`: for testing only. The first "start" in an
  incident is skipped on purpose, so the rebuild step can be seen working.
  Leave it off.
