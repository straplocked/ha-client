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
client self-update: a Core or OS update restarts Home Assistant and kills this
process mid-flight, so the run's target is persisted before the apply call and
confirmed on the next boot -- the confirmation has to survive the very restart
it is confirming.

This module owns the orchestration and reporting. The Supervisor calls that
genuinely change the system live behind SupervisorUpdater, the one seam that
needs a real supervised Home Assistant to exercise.

Agent-side spec: docs/technical/remote-updates.md
"""
from __future__ import annotations

import asyncio
import logging
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
    UPDATE_RUN_KIND_ADDON,
    UPDATE_RUN_KIND_CORE,
    UPDATE_RUN_KIND_OS,
    UPDATE_RUN_KIND_SUPERVISOR,
    UPDATE_RUN_OPERATION_TIMEOUT,
    UPDATE_RUN_PHASE_APPLY,
    UPDATE_RUN_PHASE_BACKUP,
    UPDATE_RUN_PHASE_CONFIRM,
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
        """Install the update. May not return: a Core or OS update restarts us.

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

    def _update_entity(self, kind: str, slug: str) -> str:
        """The update entity that installs this component.

        The platform's three are fixed. An add-on's is found by the icon its
        update entity points at -- `/api/hassio/addons/<slug>/icon` -- which is
        the only place the Supervisor slug appears on the entity, and is stable
        where a friendly name a user can edit is not.
        """
        if kind != UPDATE_RUN_KIND_ADDON:
            entity_id = _PLATFORM_UPDATE_ENTITIES.get(kind)
            if entity_id is None:
                raise UpdateExecutionError(f"Do not know how to update {kind!r}.")
            return entity_id

        needle = f"/addons/{slug}/"
        for state in self._update_states():
            picture = str(state.attributes.get("entity_picture") or "")
            if needle in picture:
                return state.entity_id

        raise UpdateExecutionError(
            f"No update entity found for add-on {slug!r}; it may not be installed."
        )

    def _supports_specific_version(self, entity_id: str) -> bool:
        """Whether this update entity accepts a target version.

        UpdateEntityFeature.SPECIFIC_VERSION is bit 2. Checked rather than
        assumed: Core and the OS advertise it, add-ons do not, and sending the
        parameter to something that does not support it fails the whole run.
        """
        for state in self._update_states():
            if state.entity_id == entity_id:
                try:
                    return bool(int(state.attributes.get("supported_features") or 0) & 2)
                except (TypeError, ValueError):
                    return False
        return False

    def _update_states(self) -> list:
        """Every update entity, or an empty list if they cannot be enumerated."""
        lister = getattr(getattr(self.hass, "states", None), "async_all", None)
        if lister is None:
            return []
        try:
            return list(lister("update"))
        except (AttributeError, KeyError, TypeError, ValueError):
            return []

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
        """Confirm an update left pending by a restart, before the first poll.

        A Core or OS update restarts Home Assistant, killing the process partway
        through the run. The confirmation of whether the new version actually
        took therefore has to happen here, on the next boot, by looking at what
        is now installed.
        """
        try:
            await self.async_confirm_pending()
        except Exception as err:  # noqa: BLE001 - must never block setup
            _LOGGER.error("Could not confirm a pending update run: %s", err)

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

            # Persist before applying: a Core or OS update restarts Home
            # Assistant and this process never returns from the call below, so
            # the record it leaves behind is the only way the run gets confirmed.
            await self._persist_pending(run_id, kind, slug, target)
            await self.executor.async_apply(kind, slug, target)

            # Still here, so the update did not restart us -- an add-on, or a
            # platform update that deferred its restart. Confirm inline.
            await self._async_confirm(run_id, kind, slug, target)
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

    # -- confirmation -------------------------------------------------------

    async def async_confirm_pending(self) -> None:
        """Confirm an update the last run persisted, using what is now installed.

        Cleared first, so a failure while confirming cannot leave the record to
        be confirmed again on every boot.
        """
        record = await self._load_pending()
        if not record:
            return

        run_id = str(record.get("run_id") or "")
        kind = record.get("kind")
        slug = record.get("slug")
        target = record.get("target_version")

        await self._clear_pending()

        if not run_id or kind not in _UPDATABLE_KINDS or not slug or not target:
            return

        await self._async_confirm(run_id, kind, slug, target)

    async def _async_confirm(self, run_id: str, kind: str, slug: str, target: str) -> None:
        """Report success or failure by comparing installed version to target."""
        installed = await self._installed_version(kind, slug)

        if installed and versions_equal(installed, target):
            await self._report(run_id, UPDATE_RUN_STATUS_SUCCESS, phase=UPDATE_RUN_PHASE_CONFIRM)
            _LOGGER.info("Remote update run %s installed %s %s", run_id, kind, target)
        else:
            await self._report(
                run_id,
                UPDATE_RUN_STATUS_FAILED,
                phase=UPDATE_RUN_PHASE_CONFIRM,
                error=(
                    f"After the update, {kind} reports "
                    f"{installed or 'no version'}, not {target}."
                ),
            )
            _LOGGER.warning(
                "Remote update run %s did not land: %s is on %s, wanted %s",
                run_id,
                kind,
                installed,
                target,
            )

    async def _installed_version(self, kind: str, slug: str) -> Optional[str]:
        """The version this installation currently reports for a component."""
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

    async def _persist_pending(self, run_id: str, kind: str, slug: str, target: str) -> None:
        try:
            await self._store.async_save(
                {"run": {"run_id": run_id, "kind": kind, "slug": slug, "target_version": target}}
            )
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
    "versions_equal",
]
