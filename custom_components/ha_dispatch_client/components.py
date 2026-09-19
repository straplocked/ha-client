"""What this installation is actually running.

Metrics say how hard the machine is working; health says what is broken.
Neither says which *versions* are installed, and that is where every diagnosis
starts: what changed? The fleet also derives its update advisories from what
happens to installations after they upgrade, so an installation that never
reports an inventory neither contributes to that signal nor benefits from it.

Three rules shape everything below, and all three come from how the server
reconciles a report:

* **Full snapshot, never a delta.** Anything omitted is treated as removed. An
  agent that sent only what changed would corrupt the inventory permanently the
  first time a report failed to land. There is deliberately no memory of the
  previous report in this module.
* **Stable slugs.** The slug is half a component's identity. Every one here is
  a Home Assistant identifier -- integration domain, Supervisor add-on slug,
  HACS repository -- never a title or a display name, both of which a user can
  rename at will. A slug that moves between polls reads as one component being
  removed and a different one installed, which corrupts both the change
  timeline and the risk evidence.
* **`failing` only where Home Assistant knows.** A config entry in a failed
  setup state is a fact, and so is an add-on Supervisor could not start.
  Everything else would be a guess, and a guess here becomes fleet-wide
  evidence against a version somebody else is about to install.

A whole kind being absent is normal rather than an error: most installations
are Home Assistant Core with no Supervisor, no add-ons and no HACS. Every
source degrades to "nothing found" on its own.

Everything except the integration manifests reads in-memory state, so this is
safe to call from the event loop.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant

from .const import (
    COMPONENT_ADDON_FAILED_STATES,
    COMPONENT_KIND_ADDON,
    COMPONENT_KIND_CORE,
    COMPONENT_KIND_HACS,
    COMPONENT_KIND_INTEGRATION,
    COMPONENT_KIND_OS,
    COMPONENT_KIND_SUPERVISOR,
    COMPONENT_NAME_MAX,
    COMPONENT_REPORT_CAP,
    COMPONENT_SLUG_CORE,
    COMPONENT_SLUG_MAX,
    COMPONENT_SLUG_OS,
    COMPONENT_SLUG_SUPERVISOR,
    COMPONENT_VERSION_MAX,
)
from .health import FAILED_ENTRY_STATES

_LOGGER = logging.getLogger(__name__)

# The kinds describing the platform itself, in reporting order. They survive
# truncation ahead of everything else because a core, OS or Supervisor upgrade
# is the single highest-impact change an installation can make.
_PLATFORM_KINDS = (COMPONENT_KIND_CORE, COMPONENT_KIND_OS, COMPONENT_KIND_SUPERVISOR)

# Errors any of the sources below may raise when Home Assistant's internals are
# not the shape this expects -- a different core version, an integration that
# rearranged its data, a Supervisor helper that moved. None of them are worth
# failing a report over: a partial inventory is far better than none.
_SOFT_ERRORS = (AttributeError, KeyError, TypeError, ValueError)


async def async_collect_components(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Build the full current inventory, ready to post.

    Every call reports everything found, including components that have not
    changed since the last call. That is not redundancy -- it is the contract.
    """
    collected: list[dict[str, Any]] = [_core_component()]
    collected.extend(_platform_components(hass))
    collected.extend(_addon_components(hass))
    collected.extend(_hacs_components(hass))
    collected.extend(await _integration_components(hass))

    components = _prioritise(_deduplicate(collected))

    if len(components) > COMPONENT_REPORT_CAP:
        _LOGGER.debug(
            "Component inventory truncated from %s to %s entries",
            len(components),
            COMPONENT_REPORT_CAP,
        )
        components = components[:COMPONENT_REPORT_CAP]

    return components


# --- Home Assistant itself ---------------------------------------------------


def _core_component() -> dict[str, Any]:
    """The one row every installation has."""
    return _component(
        COMPONENT_KIND_CORE,
        COMPONENT_SLUG_CORE,
        name="Home Assistant Core",
        version=HA_VERSION,
    )


# --- Supervisor: OS, Supervisor itself, add-ons ------------------------------


def _platform_components(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Home Assistant OS and Supervisor, on a supervised installation."""
    hassio = _supervisor(hass)
    if hassio is None:
        return []

    found = []

    os_info = _ask(hassio, "get_os_info", hass)
    os_version = os_info.get("version") if isinstance(os_info, dict) else None
    if os_version:
        found.append(
            _component(
                COMPONENT_KIND_OS,
                COMPONENT_SLUG_OS,
                name="Home Assistant OS",
                version=os_version,
            )
        )

    supervisor_info = _ask(hassio, "get_supervisor_info", hass)
    version = (
        supervisor_info.get("version") if isinstance(supervisor_info, dict) else None
    )
    if not version:
        # Older cores expose it only on the /info payload.
        info = _ask(hassio, "get_info", hass)
        version = info.get("supervisor") if isinstance(info, dict) else None
    if version:
        found.append(
            _component(
                COMPONENT_KIND_SUPERVISOR,
                COMPONENT_SLUG_SUPERVISOR,
                name="Home Assistant Supervisor",
                version=version,
            )
        )

    return [entry for entry in found if entry is not None]


def _addon_components(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Every installed Supervisor add-on.

    Slugged by the Supervisor slug (`core_mosquitto`), which is the add-on's
    real identity. Several add-ons let the user change the displayed name, and
    a slug derived from that would move the moment they did.
    """
    hassio = _supervisor(hass)
    if hassio is None:
        return []

    found = []
    for slug, addon in _addon_records(hassio, hass):
        state = str(addon.get("state") or "").lower()
        entry = _component(
            COMPONENT_KIND_ADDON,
            slug,
            name=addon.get("name"),
            version=addon.get("version"),
            failing=state in COMPONENT_ADDON_FAILED_STATES,
        )
        if entry is not None:
            found.append(entry)

    return found


def _addon_records(hassio, hass: HomeAssistant) -> list[tuple[Any, dict]]:
    """Normalise the two shapes Supervisor add-on data arrives in."""
    addons = _ask(hassio, "get_addons_info", hass)
    if isinstance(addons, dict):
        return [
            (slug, addon) for slug, addon in addons.items() if isinstance(addon, dict)
        ]

    supervisor_info = _ask(hassio, "get_supervisor_info", hass)
    listed = (
        supervisor_info.get("addons") if isinstance(supervisor_info, dict) else None
    )
    return [
        (addon.get("slug"), addon)
        for addon in (listed or [])
        if isinstance(addon, dict)
    ]


def _supervisor(hass: HomeAssistant):
    """Return the Supervisor helper module, or None on a Core-only install."""
    try:
        from homeassistant.components import hassio
    except ImportError:
        return None

    checker = getattr(hassio, "is_hassio", None)
    if checker is not None:
        try:
            return hassio if checker(hass) else None
        except _SOFT_ERRORS:
            pass

    # is_hassio has moved between modules across core releases; whether the
    # hassio integration is loaded answers the same question.
    components = getattr(getattr(hass, "config", None), "components", None)
    try:
        return hassio if components and "hassio" in components else None
    except _SOFT_ERRORS:
        return None


def _ask(module, name: str, hass: HomeAssistant):
    """Call a Supervisor accessor, treating anything unexpected as absent."""
    accessor = getattr(module, name, None)
    if accessor is None:
        return None
    try:
        return accessor(hass)
    except _SOFT_ERRORS as err:
        _LOGGER.debug("Supervisor helper %s was unusable: %s", name, err)
        return None


# --- Integrations ------------------------------------------------------------


async def _integration_components(hass: HomeAssistant) -> list[dict[str, Any]]:
    """One row per integration domain the user actually has installed.

    Keyed on the domain, Home Assistant's own stable identifier. A config
    entry's *title* is used for nothing here: the user can edit it, and an
    integration with two entries has two of them.
    """
    domains: dict[str, bool] = {}

    for entry in _config_entries(hass):
        if getattr(entry, "disabled_by", None) is not None:
            continue
        domain = getattr(entry, "domain", None)
        if not domain:
            continue
        failing = getattr(entry, "state", None) in FAILED_ENTRY_STATES
        # Two entries for one domain, one of them broken, is still a broken
        # integration.
        domains[domain] = domains.get(domain, False) or failing

    # Custom integrations are where versions actually live -- a built-in
    # integration ships no manifest version at all -- so they are included even
    # without a config entry, which covers YAML-configured ones and anything
    # installed but not yet set up.
    custom = await _custom_integrations(hass)
    for domain in custom:
        domains.setdefault(domain, False)

    manifests = await _manifests(hass, sorted(domains))

    found = []
    for domain, failing in sorted(domains.items()):
        manifest = manifests.get(domain) or custom.get(domain)
        entry = _component(
            COMPONENT_KIND_INTEGRATION,
            domain,
            name=getattr(manifest, "name", None) or domain,
            version=getattr(manifest, "version", None),
            failing=failing,
        )
        if entry is not None:
            found.append(entry)

    return found


def _config_entries(hass: HomeAssistant) -> list[Any]:
    """Every config entry, or an empty list if they cannot be enumerated."""
    lister = getattr(getattr(hass, "config_entries", None), "async_entries", None)
    if lister is None:
        return []
    try:
        return list(lister())
    except _SOFT_ERRORS as err:
        _LOGGER.debug("Could not enumerate config entries: %s", err)
        return []


async def _custom_integrations(hass: HomeAssistant) -> dict[str, Any]:
    """Every custom integration on disk, keyed by domain."""
    try:
        from homeassistant.loader import async_get_custom_components
    except ImportError:
        return {}

    try:
        return dict(await async_get_custom_components(hass) or {})
    except _SOFT_ERRORS as err:
        _LOGGER.debug("Could not enumerate custom integrations: %s", err)
        return {}


async def _manifests(hass: HomeAssistant, domains: list[str]) -> dict[str, Any]:
    """Resolve integration manifests for names and versions, in one call.

    Best effort throughout: a domain whose manifest will not load simply gets
    no name and no version, both of which the server accepts as optional.
    """
    if not domains:
        return {}

    try:
        from homeassistant.loader import async_get_integrations
    except ImportError:
        return {}

    try:
        resolved = await async_get_integrations(hass, domains) or {}
    except _SOFT_ERRORS as err:
        _LOGGER.debug("Could not resolve integration manifests: %s", err)
        return {}

    # async_get_integrations reports per-domain failures by putting the
    # exception in the result rather than raising.
    return {
        domain: integration
        for domain, integration in resolved.items()
        if not isinstance(integration, BaseException)
    }


# --- HACS --------------------------------------------------------------------


def _hacs_components(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Everything HACS has downloaded, when HACS is installed.

    Slugged by repository (`owner/repo`), which is what HACS itself keys on and
    the only identifier here that a user cannot rename locally.
    """
    hacs = (getattr(hass, "data", None) or {}).get("hacs")
    if hacs is None:
        return []

    downloaded = getattr(getattr(hacs, "repositories", None), "list_downloaded", None)
    if downloaded is None:
        return []

    try:
        repositories = list(downloaded)
    except TypeError:
        return []

    found = []
    for repository in repositories:
        data = getattr(repository, "data", None)
        if data is None:
            continue
        entry = _component(
            COMPONENT_KIND_HACS,
            getattr(data, "full_name", None),
            name=getattr(data, "name", None),
            version=(
                getattr(data, "installed_version", None)
                or getattr(repository, "display_installed_version", None)
            ),
        )
        if entry is not None:
            found.append(entry)

    return found


# --- Shaping the payload -----------------------------------------------------


def _component(
    kind: str,
    slug: Any,
    *,
    name: Any = None,
    version: Any = None,
    failing: bool = False,
) -> dict[str, Any] | None:
    """Build one wire-format entry, or None when it has no usable slug.

    A component with no slug has no identity, and posting one would either be
    rejected outright or reconciled against the wrong row.
    """
    slug = _text(slug, COMPONENT_SLUG_MAX)
    if not slug:
        return None

    entry: dict[str, Any] = {"kind": kind, "slug": slug, "failing": bool(failing)}

    label = _text(name, COMPONENT_NAME_MAX)
    if label:
        entry["name"] = label

    # Omitted rather than sent empty when genuinely unknown: the server treats
    # a missing version as "not reported" and leaves the recorded one alone.
    number = _text(version, COMPONENT_VERSION_MAX)
    if number:
        entry["version"] = number

    return entry


def _text(value: Any, limit: int) -> str | None:
    """Render a value as trimmed text within the server's column width."""
    if value is None:
        return None
    try:
        text = str(value).strip()
    except _SOFT_ERRORS:
        return None
    return text[:limit] or None


def _deduplicate(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one entry per (kind, slug) -- the server's composite identity."""
    seen: set[tuple[str, str]] = set()
    unique = []
    for entry in components:
        key = (entry["kind"], entry["slug"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return unique


def _prioritise(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order the inventory so truncation drops the least informative rows.

    Platform first, then everything carrying a version, then everything
    without one. A versionless row contributes nothing to the transition ledger
    and nothing to risk scoring, so it is the right thing to lose. Sorted
    within each band so the same installation truncates the same way twice --
    an unstable cut would retire and reinstate the same components forever.
    """

    def rank(entry: dict[str, Any]) -> tuple[int, int, str]:
        kind = entry["kind"]
        if kind in _PLATFORM_KINDS:
            return (0, _PLATFORM_KINDS.index(kind), "")
        return (1 if "version" in entry else 2, 0, f"{kind}:{entry['slug']}")

    return sorted(components, key=rank)
