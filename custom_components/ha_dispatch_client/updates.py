"""Consent-gated remote updates, the Home Assistant end.

A technician plans an update on the Dispatch server; the homeowner consents to
that specific version change on a screen they already trust. Only then does the
server offer the run to this agent, and only then does anything here touch Home
Assistant.

The agent's job is deliberately narrow:

1. Ask what it has been consented to install.
2. Take a backup, apply the update against the local Supervisor, and report each
   phase -- most importantly *before* it changes anything.

Consent is the server's to enforce, not ours: a run only appears on the pending
list once consent is granted, and it leaves the moment consent is revoked,
denied or expires. This module never re-checks it. It executes what the server
hands it and reports honestly.

The report that lands *before* the swap is the valuable one, exactly as with a
client self-update: a Core update restarts Home Assistant and kills this
process mid-flight, so the run's target is persisted before the apply call and
confirmed on the next boot -- the confirmation has to survive the very restart
it is confirming.

An OS update is different, and treating it like Core is what failed every OS
run in the field: `update.install` returns normally having only written the new
OS to the spare boot slot, and it takes effect on the next *host* reboot. So an
OS run asks the Supervisor to reboot the host -- the homeowner consented to the
update, and the reboot is how it lands -- reports the `restart` phase first,
and leaves the record for the boot-time confirmation. Nothing running before
that reboot can tell a staged OS from an active one (the update entity already
names the staged version as installed), so an OS run is never confirmed
inline. One reboot per run, never two: a host that comes back on the old
version is a failed run, not a reason to reboot again.

This module owns the orchestration and reporting. The Supervisor calls that
genuinely change the system live behind SupervisorUpdater, the one seam that
needs a real supervised Home Assistant to exercise.

Agent-side spec: docs/technical/remote-updates.md
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .api_client import RemoteUpdatesUnavailable
from .components import async_collect_components
from .const import (
    DOMAIN,
    UPDATE_ENTITY_CORE,
    UPDATE_ENTITY_OS,
    UPDATE_ENTITY_SUPERVISOR,
    UPDATE_RUN_BACKUP_PREFIX,
    UPDATE_RUN_CONFIRM_ATTEMPTS,
    UPDATE_RUN_CONFIRM_INTERVAL,
    UPDATE_RUN_KIND_ADDON,
    UPDATE_RUN_KIND_CORE,
    UPDATE_RUN_KIND_OS,
    UPDATE_RUN_KIND_SUPERVISOR,
    UPDATE_RUN_OPERATION_TIMEOUT,
    UPDATE_RUN_PHASE_APPLY,
    UPDATE_RUN_PHASE_BACKUP,
    UPDATE_RUN_PHASE_CONFIRM,
    UPDATE_RUN_PHASE_RESTART,
    UPDATE_RUN_STATUS_FAILED,
    UPDATE_RUN_STATUS_PROGRESS,
    UPDATE_RUN_STATUS_STARTED,
    UPDATE_RUN_STATUS_SUCCESS,
    UPDATE_RUN_STORAGE_KEY,
    UPDATE_RUN_STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

# The kinds this agent knows how to update, and the platform update entity for
# each of the three there is only one of.
_PLATFORM_UPDATE_ENTITIES = {
    UPDATE_RUN_KIND_CORE: UPDATE_ENTITY_CORE,
    UPDATE_RUN_KIND_OS: UPDATE_ENTITY_OS,
    UPDATE_RUN_KIND_SUPERVISOR: UPDATE_ENTITY_SUPERVISOR,
}

_UPDATABLE_KINDS = frozenset(
    {
        UPDATE_RUN_KIND_CORE,
        UPDATE_RUN_KIND_OS,
        UPDATE_RUN_KIND_SUPERVISOR,
        UPDATE_RUN_KIND_ADDON,
    }
)


# What the Supervisor answers when the OS a run asks for has already been written
# to the spare slot and only a reboot stands between it and running -- "Version
# 18.5 is already installed, reboot the system to activate it". That is a staged
# update waiting for its reboot, not a failure.
_AWAITS_REBOOT = re.compile(r"already installed.*reboot", re.IGNORECASE | re.DOTALL)
_STAGED_VERSION = re.compile(r"version\s+(\S+)\s+is already installed", re.IGNORECASE)

# Entity states that mean "not loaded yet", not "this version".
_UNLOADED_STATES = frozenset({"", "unavailable", "unknown"})


class UpdateExecutionError(Exception):
    """A Supervisor operation could not be carried out.

    Distinct from a transport error talking to the Dispatch server: this means
    the local update itself failed, and it is reported to the server as a failed
    run with the phase it happened in.
    """


def versions_equal(left: Any, right: Any) -> bool:
    """Whether two version strings name the same version.

    Tolerant of a leading `v` and surrounding whitespace, matching how the
    server normalises versions, so `2026.9.1` and `v2026.9.1` confirm as equal.
    """
    return _version_key(left) == _version_key(right)


def _version_key(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value).strip().lstrip("vV")
    except (TypeError, ValueError):
        return ""


def awaits_reboot(message: Any) -> bool:
    """Whether a Supervisor refusal means "staged, reboot to activate"."""
    return bool(_AWAITS_REBOOT.search(str(message or "")))


def staged_version(message: Any) -> Optional[str]:
    """The version a "reboot to activate" refusal names, if it names one."""
    found = _STAGED_VERSION.search(str(message or ""))
    return found.group(1).rstrip(",.") if found else None


def _update_states(hass: HomeAssistant) -> list:
    """Every update entity, or an empty list if they cannot be enumerated."""
    lister = getattr(getattr(hass, "states", None), "async_all", None)
    if lister is None:
        return []
    try:
        return list(lister("update"))
    except (AttributeError, KeyError, TypeError, ValueError):
        return []


def update_entity_id(hass: HomeAssistant, kind: str, slug: str) -> Optional[str]:
    """The update entity that installs this component, or None if there is none.

    The platform's three are fixed. An add-on's is found by the icon its update
    entity points at -- `/api/hassio/addons/<slug>/icon` -- which is the only
    place the Supervisor slug appears on the entity, and is stable where a
    friendly name a user can edit is not.
    """
    if kind != UPDATE_RUN_KIND_ADDON:
        return _PLATFORM_UPDATE_ENTITIES.get(kind)

    needle = f"/addons/{slug}/"
    for state in _update_states(hass):
        picture = str(state.attributes.get("entity_picture") or "")
        if needle in picture:
            return state.entity_id
    return None


def entity_installed_version(hass: HomeAssistant, kind: str, slug: str) -> Optional[str]:
    """What the component's update entity says is installed, once it has loaded.

    None means "no answer yet" -- no entity, or one still unavailable/unknown,
    as it is for a while after a reboot -- never "nothing installed".
    """
    entity_id = update_entity_id(hass, kind, slug)
    getter = getattr(getattr(hass, "states", None), "get", None)
    if not entity_id or getter is None:
        return None
    try:
        state = getter(entity_id)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    if state is None or str(getattr(state, "state", "")).lower() in _UNLOADED_STATES:
        return None
    attributes = getattr(state, "attributes", None) or {}
    installed = attributes.get("installed_version")
    return str(installed) if installed else None


class SupervisorUpdater:
    """The live Supervisor calls: take a backup, apply an update.

    Isolated here because this is the only part that cannot be exercised without
    a real supervised Home Assistant. Everything it does is a Home Assistant
    service call, so the orchestration around it can be tested with a fake in
    its place.

    Backups go through the Supervisor's own backup services, which are stable
    and present on every supervised system. Updates go through the platform's
    `update.install` entity service for Core, OS and Supervisor, and through the
    add-on update service for add-ons.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def async_backup(self, kind: str, slug: str, name: str) -> Optional[str]:
        """Take a pre-update backup, returning a handle to it.

        A partial backup of just the add-on when updating one, a full backup
        otherwise. The Supervisor returns the backup's slug, which is what the
        receipt should name -- a slug identifies the restore point even if two
        runs picked the same name. The name is the fallback for a Supervisor
        that answers without response data.
        """
        if kind == UPDATE_RUN_KIND_ADDON:
            service, data = "backup_partial", {"name": name, "addons": [slug]}
        else:
            service, data = "backup_full", {"name": name}

        response = await self._call("hassio", service, data, want_response=True)

        if isinstance(response, dict):
            reference = response.get("slug") or response.get("backup")
            if reference:
                return str(reference)

        return name

    async def async_apply(self, kind: str, slug: str, target_version: Optional[str]) -> None:
        """Install the update. May not return: a Core update restarts us. An OS
        update returns once the new OS is in the spare slot; see async_reboot.

        Everything goes through `update.install`, including add-ons. The
        Supervisor's own `hassio.addon_update` was deprecated in Home Assistant
        2024.11 and the update entity is its documented replacement, so calling
        it would fail outright on any recent core.
        """
        entity_id = self._update_entity(kind, slug)

        # backup=False because the run has already taken its own named backup by
        # this point, and the receipt names that one. Asking the update entity
        # for a second would double the work and leave a restore point nobody
        # recorded.
        data: Dict[str, Any] = {"entity_id": entity_id, "backup": False}

        # Only Core and the OS accept a specific version; an add-on's update
        # entity does not advertise SPECIFIC_VERSION and rejects the parameter,
        # so an add-on run always installs whatever the Supervisor has.
        if target_version and self._supports_specific_version(entity_id):
            data["version"] = target_version

        await self._call("update", "install", data)

    async def async_reboot(self) -> None:
        """Ask the Supervisor to reboot the host. May not return.

        This is how a staged OS update takes effect. It is a host reboot, not
        a Home Assistant restart: restarting Core alone leaves the old OS
        running and the new one still waiting in the spare slot.
        """
        await self._call("hassio", "host_reboot", {})

    def _update_entity(self, kind: str, slug: str) -> str:
        """The update entity that installs this component (see update_entity_id)."""
        entity_id = update_entity_id(self.hass, kind, slug)
        if entity_id is not None:
            return entity_id
        if kind != UPDATE_RUN_KIND_ADDON:
            raise UpdateExecutionError(f"Do not know how to update {kind!r}.")
        raise UpdateExecutionError(
            f"No update entity found for add-on {slug!r}; it may not be installed."
        )

    def _supports_specific_version(self, entity_id: str) -> bool:
        """Whether this update entity accepts a target version.

        UpdateEntityFeature.SPECIFIC_VERSION is bit 2. Checked rather than
        assumed: Core and the OS advertise it, add-ons do not, and sending the
        parameter to something that does not support it fails the whole run.
        """
        for state in _update_states(self.hass):
            if state.entity_id == entity_id:
                try:
                    return bool(int(state.attributes.get("supported_features") or 0) & 2)
                except (TypeError, ValueError):
                    return False
        return False

    async def _call(
        self,
        domain: str,
        service: str,
        data: Dict[str, Any],
        want_response: bool = False,
    ) -> Any:
        """Call a blocking service, mapping any failure to UpdateExecutionError."""
        try:
            return await asyncio.wait_for(
                self._invoke(domain, service, data, want_response),
                timeout=UPDATE_RUN_OPERATION_TIMEOUT,
            )
        except UpdateExecutionError:
            raise
        except (HomeAssistantError, asyncio.TimeoutError) as err:
            raise UpdateExecutionError(
                f"{domain}.{service} failed: {err}"
            ) from err
        except Exception as err:  # noqa: BLE001 - the Supervisor is external
            raise UpdateExecutionError(
                f"{domain}.{service} failed: {err}"
            ) from err

    async def _invoke(
        self,
        domain: str,
        service: str,
        data: Dict[str, Any],
        want_response: bool,
    ) -> Any:
        """Make the call, tolerating a core that will not return response data.

        return_response is rejected outright by some versions and by services
        that do not supply it, and a backup that ran is not worth failing over
        the shape of its reply -- so the response is a bonus, never a
        requirement.
        """
        if want_response:
            try:
                return await self.hass.services.async_call(
                    domain, service, data, blocking=True, return_response=True
                )
            except (TypeError, ValueError, HomeAssistantError):
                pass

        return await self.hass.services.async_call(domain, service, data, blocking=True)


class HADispatchUpdates:
    """Polls for consented updates, runs them, and reports each phase."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator,
        executor: Optional[SupervisorUpdater] = None,
    ) -> None:
        """Initialise. Nothing touches the network until the first poll."""
        self.hass = hass
        self.coordinator = coordinator
        self.available = True
        self.executor = executor or SupervisorUpdater(hass)

        # Run ids currently being executed, so repeated polls never start the
        # same run twice. The server allows only one open run per installation,
        # so this holds at most one, but the guard is cheap insurance.
        self._running: set[str] = set()

        self._store = Store(hass, UPDATE_RUN_STORAGE_VERSION, UPDATE_RUN_STORAGE_KEY)

        # How long the boot-time confirmation waits for Home Assistant to know
        # what is installed. Attributes so tests can shorten the wait.
        self._confirm_attempts = UPDATE_RUN_CONFIRM_ATTEMPTS
        self._confirm_interval = UPDATE_RUN_CONFIRM_INTERVAL

    @property
    def api(self):
        """The API client, which the coordinator may have re-tokened."""
        return self.coordinator.api_client

    @property
    def installation_id(self):
        """The installation id, which re-enrolment may have changed."""
        return self.coordinator.installation_id

    def async_disable(self) -> None:
        """Stop trying: this server does not offer remote updates."""
        self.available = False

    # -- lifecycle ----------------------------------------------------------

    async def async_load(self) -> None:
        """Start confirming an update left pending by a restart or a reboot.

        A Core update restarts Home Assistant, and an OS update reboots the
        host, killing the process partway through the run. The confirmation of
        whether the new version actually took therefore has to happen on the
        next boot, by looking at what is now installed.

        Run in the background: right after a boot the Supervisor's data and
        update entities may take minutes to load, and the confirmation waits
        for them rather than holding up setup. Nothing else can start meanwhile
        -- the server offers no new run while this one is open.
        """
        self._create_task(self._async_confirm_pending_guarded())

    async def _async_confirm_pending_guarded(self) -> None:
        try:
            await self.async_confirm_pending()
        except Exception as err:  # noqa: BLE001 - must never break the integration
            _LOGGER.error("Could not confirm a pending update run: %s", err)

    def _create_task(self, coro):
        creator = getattr(self.hass, "async_create_background_task", None)
        if creator is not None:
            return creator(coro, "ha_dispatch_client confirm update run")
        return self.hass.async_create_task(coro)

    # -- the poll loop ------------------------------------------------------

    async def async_poll_pending(self) -> None:
        """Fetch consented updates and start any that are not already running.

        Called on the coordinator's cadence, and deliberately incapable of
        raising: a customer's Home Assistant must keep reporting metrics even
        when remote updates are broken.
        """
        try:
            await self._async_poll_pending()
        except Exception as err:  # noqa: BLE001 - see the docstring
            _LOGGER.error("Unexpected error handling remote updates: %s", err)

    async def _async_poll_pending(self) -> None:
        if not self.available:
            return

        try:
            updates = await self.api.fetch_pending_updates(self.installation_id)
        except RemoteUpdatesUnavailable:
            _LOGGER.info(
                "This Dispatch server has no remote update endpoints; not asking again"
            )
            self.async_disable()
            return
        except (aiohttp.ClientError, TimeoutError, KeyError, ValueError) as err:
            _LOGGER.debug("Could not fetch pending updates: %s", err)
            return

        for run in updates:
            run_id = str(run.get("run_id") or "")
            if not run_id or run_id in self._running:
                continue
            # One run at a time. The server guarantees at most one open run per
            # installation, so this only ever matters if that guarantee slips.
            if self._running:
                break
            self._running.add(run_id)
            self.hass.async_create_task(self._async_run(run))

    async def _async_run(self, run: Dict[str, Any]) -> None:
        """Execute one run: back up, apply, and report every step."""
        run_id = str(run.get("run_id") or "")
        kind = run.get("kind")
        slug = run.get("slug")
        target = run.get("target_version")
        backup = run.get("backup", True)

        if not run_id or kind not in _UPDATABLE_KINDS or not slug or not target:
            _LOGGER.warning("Ignoring a malformed update run: %s", run)
            self._running.discard(run_id)
            return

        # The first thing we will actually do: a backup, or the apply itself when
        # no backup was asked for.
        phase = UPDATE_RUN_PHASE_BACKUP if backup else UPDATE_RUN_PHASE_APPLY
        try:
            # Before touching anything: an installation that reports it began and
            # then goes silent has told us the update broke it.
            await self._report(run_id, UPDATE_RUN_STATUS_STARTED, phase=phase)

            if backup:
                name = f"{UPDATE_RUN_BACKUP_PREFIX} {slug} {target}"
                reference = await self.executor.async_backup(kind, slug, name)
                await self._report(
                    run_id,
                    UPDATE_RUN_STATUS_PROGRESS,
                    phase=UPDATE_RUN_PHASE_BACKUP,
                    backup_reference=reference or name,
                )

            phase = UPDATE_RUN_PHASE_APPLY
            await self._report(run_id, UPDATE_RUN_STATUS_PROGRESS, phase=UPDATE_RUN_PHASE_APPLY)

            # Persist before applying: a Core update restarts Home Assistant
            # and this process never returns from the call below, so the record
            # it leaves behind is the only way the run gets confirmed. A mark
            # that this very run already rebooted the host is carried over, so
            # the one-reboot guard survives the run being handed out again.
            prior = await self._load_pending() or {}
            await self._persist_pending(
                run_id,
                kind,
                slug,
                target,
                reboot_requested=(
                    str(prior.get("run_id") or "") == run_id
                    and bool(prior.get("reboot_requested"))
                ),
            )
            try:
                await self.executor.async_apply(kind, slug, target)
            except UpdateExecutionError as err:
                if kind != UPDATE_RUN_KIND_OS or not awaits_reboot(err):
                    raise
                # The OS is already in the spare slot, waiting for a reboot --
                # from this run's own earlier attempt, or an earlier run's.
                # Reboot into it only if it is the version consented to.
                staged = staged_version(err)
                if staged and not versions_equal(staged, target):
                    raise UpdateExecutionError(
                        f"Home Assistant OS {staged} is already installed and "
                        f"waiting for a reboot; this run asked for {target}. "
                        "Reboot the host, then plan the update again."
                    ) from err
                _LOGGER.info(
                    "Home Assistant OS %s is already staged for run %s; rebooting to activate it",
                    target,
                    run_id,
                )

            if kind == UPDATE_RUN_KIND_OS:
                # Written to the spare slot; it runs only after a host reboot.
                # Confirmed on the next boot, never here (see the module
                # docstring).
                phase = UPDATE_RUN_PHASE_RESTART
                await self._async_reboot_to_activate(run_id, kind, slug, target)
                return

            # Still here, so the update did not restart us -- an add-on, or a
            # platform update that deferred its restart. Confirm inline.
            await self._async_confirm(run_id, kind, slug, target, wait=False)
            await self._clear_pending()

        except UpdateExecutionError as err:
            await self._clear_pending()
            await self._report(run_id, UPDATE_RUN_STATUS_FAILED, phase=phase, error=str(err))
            _LOGGER.error("Remote update run %s failed at %s: %s", run_id, phase, err)
        except Exception as err:  # noqa: BLE001 - a failed update must report, not crash
            await self._clear_pending()
            await self._report(run_id, UPDATE_RUN_STATUS_FAILED, phase=phase, error=str(err))
            _LOGGER.exception("Unexpected error in remote update run %s", run_id)
        finally:
            self._running.discard(run_id)

    # -- the OS reboot ------------------------------------------------------

    async def _async_reboot_to_activate(
        self, run_id: str, kind: str, slug: str, target: str
    ) -> None:
        """Reboot the host so a staged OS update takes effect.

        The record is kept, marked as rebooted, and written *before* the
        reboot is asked for: the boot that follows confirms from it, and the
        mark is what stops a second reboot. A run that has already rebooted
        once never reboots again -- that is the whole loop guard.
        """
        record = await self._load_pending() or {}
        if str(record.get("run_id") or "") == run_id and record.get("reboot_requested"):
            raise UpdateExecutionError(
                f"Already rebooted once to activate Home Assistant OS {target} "
                "for this run; not rebooting again."
            )

        await self._persist_pending(run_id, kind, slug, target, reboot_requested=True)
        await self._report(
            run_id,
            UPDATE_RUN_STATUS_PROGRESS,
            phase=UPDATE_RUN_PHASE_RESTART,
            detail={"action": "reboot", "reason": "os_staged", "target_version": target},
        )

        try:
            await self.executor.async_reboot()
        except UpdateExecutionError as err:
            # The OS is staged; only the reboot is missing. Said as a failure,
            # but the record stays: if the host is rebooted by hand later, the
            # boot that follows still reports the run as installed.
            await self._persist_pending(
                run_id, kind, slug, target, reboot_requested=True, reboot_failed=True
            )
            await self._report(
                run_id,
                UPDATE_RUN_STATUS_FAILED,
                phase=UPDATE_RUN_PHASE_RESTART,
                error=(
                    f"Home Assistant OS {target} is installed and waiting for a "
                    f"reboot, but the reboot request failed: {err}. Reboot the "
                    "host to finish the update."
                ),
            )
            _LOGGER.error("Could not reboot to activate OS %s for run %s: %s", target, run_id, err)
            return

        _LOGGER.info("Rebooting the host to activate Home Assistant OS %s (run %s)", target, run_id)

    # -- confirmation -------------------------------------------------------

    async def async_confirm_pending(self) -> None:
        """Confirm an update the last run persisted, using what is now installed.

        Waits for Home Assistant to know what is installed before deciding --
        a version read before the Supervisor's data has loaded is no answer.
        The record is cleared once the outcome is decided and before it is
        reported, so a failure while reporting cannot confirm it again on every
        boot; a restart during the wait just confirms on the next one.
        """
        record = await self._load_pending()
        if not record:
            return

        run_id = str(record.get("run_id") or "")
        kind = record.get("kind")
        slug = record.get("slug")
        target = record.get("target_version")

        if not run_id or kind not in _UPDATABLE_KINDS or not slug or not target:
            await self._clear_pending()
            return

        if record.get("reboot_failed"):
            # Already reported failed when the reboot request was refused. A
            # host rebooted by hand since then turns it into a success; anything
            # else has been said already.
            installed = await self._async_read_installed(kind, slug, target, wait=True)
            await self._clear_pending()
            if installed and versions_equal(installed, target):
                await self._report(
                    run_id, UPDATE_RUN_STATUS_SUCCESS, phase=UPDATE_RUN_PHASE_CONFIRM
                )
            return

        await self._async_confirm(
            run_id,
            kind,
            slug,
            target,
            wait=True,
            rebooted=bool(record.get("reboot_requested")),
            clear_first=True,
        )

    async def _async_confirm(
        self,
        run_id: str,
        kind: str,
        slug: str,
        target: str,
        wait: bool,
        rebooted: bool = False,
        clear_first: bool = False,
    ) -> None:
        """Report success or failure by comparing installed version to target."""
        installed = await self._async_read_installed(kind, slug, target, wait=wait)
        if clear_first:
            await self._clear_pending()

        if installed and versions_equal(installed, target):
            await self._report(run_id, UPDATE_RUN_STATUS_SUCCESS, phase=UPDATE_RUN_PHASE_CONFIRM)
            _LOGGER.info("Remote update run %s installed %s %s", run_id, kind, target)
        else:
            if rebooted:
                error = (
                    f"Rebooted to activate Home Assistant OS {target}, but the "
                    f"host came back on {installed or 'no readable version'}."
                )
            else:
                error = (
                    f"After the update, {kind} reports "
                    f"{installed or 'no version'}, not {target}."
                )
            await self._report(
                run_id,
                UPDATE_RUN_STATUS_FAILED,
                phase=UPDATE_RUN_PHASE_CONFIRM,
                error=error,
            )
            _LOGGER.warning(
                "Remote update run %s did not land: %s is on %s, wanted %s",
                run_id,
                kind,
                installed,
                target,
            )

    async def _async_read_installed(
        self, kind: str, slug: str, target: str, wait: bool
    ) -> Optional[str]:
        """What is installed, asking the update entity first, then the inventory.

        Either source naming the target settles it: the inventory reads the
        Supervisor's cached info, which can lag the update entity (it said
        18.0 while the entity already said 18.5). A loaded entity that names
        another version is a definite answer. With `wait`, an entity that has
        not loaded yet is waited for -- up to the confirm window, after which
        the inventory's answer, or None, is all there is.
        """
        attempts = max(1, self._confirm_attempts) if wait else 1
        installed: Optional[str] = None
        for attempt in range(attempts):
            from_entity = entity_installed_version(self.hass, kind, slug)
            from_inventory = await self._installed_version(kind, slug)
            for candidate in (from_entity, from_inventory):
                if candidate and versions_equal(candidate, target):
                    return candidate
            if from_entity:
                return from_entity
            installed = from_inventory
            if attempt + 1 < attempts:
                await asyncio.sleep(self._confirm_interval)
        return installed

    async def _installed_version(self, kind: str, slug: str) -> Optional[str]:
        """The version the component inventory currently reports for a component."""
        try:
            components = await async_collect_components(self.hass)
        except (AttributeError, KeyError, TypeError, ValueError) as err:
            _LOGGER.debug("Could not read installed version for %s/%s: %s", kind, slug, err)
            return None

        for component in components:
            if component.get("kind") == kind and component.get("slug") == slug:
                return component.get("version")
        return None

    # -- reporting ----------------------------------------------------------

    async def _report(
        self,
        run_id: str,
        status: str,
        phase: Optional[str] = None,
        backup_reference: Optional[str] = None,
        error: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Report one phase, tolerating a server that cannot take it.

        A dropped progress report is survivable -- the terminal success or
        failure is what matters -- so a transport failure here is logged, not
        raised. A 404 means the server has no remote updates after all, so we
        stop trying.
        """
        try:
            await self.api.report_update(
                self.installation_id,
                run_id,
                status,
                phase=phase,
                backup_reference=backup_reference,
                error=error,
                detail=detail,
            )
        except RemoteUpdatesUnavailable:
            _LOGGER.info("Server no longer offers remote updates; giving up on run %s", run_id)
            self.async_disable()
        except (aiohttp.ClientError, TimeoutError, KeyError, ValueError) as err:
            _LOGGER.warning("Could not report update run %s (%s): %s", run_id, status, err)

    # -- pending record -----------------------------------------------------

    async def _load_pending(self) -> Optional[Dict[str, Any]]:
        try:
            stored = await self._store.async_load()
        except (OSError, ValueError) as err:
            _LOGGER.warning("Could not read the pending update record: %s", err)
            return None
        if not stored:
            return None
        return stored.get("run")

    async def _persist_pending(
        self,
        run_id: str,
        kind: str,
        slug: str,
        target: str,
        reboot_requested: bool = False,
        reboot_failed: bool = False,
    ) -> None:
        record: Dict[str, Any] = {
            "run_id": run_id,
            "kind": kind,
            "slug": slug,
            "target_version": target,
        }
        if reboot_requested:
            record["reboot_requested"] = True
        if reboot_failed:
            record["reboot_failed"] = True
        try:
            await self._store.async_save({"run": record})
        except (OSError, ValueError) as err:
            _LOGGER.warning("Could not persist the pending update record: %s", err)

    async def _clear_pending(self) -> None:
        try:
            await self._store.async_save({})
        except (OSError, ValueError) as err:
            _LOGGER.warning("Could not clear the pending update record: %s", err)


def async_all_update_managers(hass: HomeAssistant) -> List[HADispatchUpdates]:
    """Every configured installation's update manager."""
    return [
        coordinator.updates
        for coordinator in (hass.data.get(DOMAIN) or {}).values()
        if getattr(coordinator, "updates", None) is not None
    ]


__all__ = [
    "HADispatchUpdates",
    "SupervisorUpdater",
    "UpdateExecutionError",
    "async_all_update_managers",
    "awaits_reboot",
    "entity_installed_version",
    "staged_version",
    "update_entity_id",
    "versions_equal",
]
