"""Where Home Assistant keeps the record of community-installed repositories.

Up to 2026.10 that is HACS, a custom integration with its live state at
hass.data["hacs"]. From 2026.11 HACS is gone: the Marketplace, built into Core,
adopts everything HACS had installed and keeps its manager on its own config
entry. The two are the same code with little renamed, so the updater and the
component inventory can treat either as one store once they have found it.

Finding it is best-effort. No store at all is the normal case on a Core-only
install, and a store shaped differently from the one this was written against
reads as no store rather than as an error.
"""
from __future__ import annotations

from typing import Any, NamedTuple, Optional

from homeassistant.core import HomeAssistant

from .const import (
    HACS_DOMAIN,
    HACS_REPOSITORY_SIGNAL,
    MARKETPLACE_DOMAIN,
    MARKETPLACE_REPOSITORY_SIGNAL,
)


class CommunityStore(NamedTuple):
    """The live manager, and the signal that tells its panel a record moved."""

    manager: Any
    repository_signal: str


def community_store(hass: HomeAssistant) -> Optional[CommunityStore]:
    """Return the Marketplace if it is loaded, otherwise HACS, otherwise None."""
    marketplace = _loaded_marketplace(hass)
    if marketplace is not None:
        return CommunityStore(marketplace, MARKETPLACE_REPOSITORY_SIGNAL)

    hacs = (getattr(hass, "data", None) or {}).get(HACS_DOMAIN)
    if hacs is not None:
        return CommunityStore(hacs, HACS_REPOSITORY_SIGNAL)

    return None


def installed_repositories(manager: Any) -> list[Any]:
    """Every repository the store installed, under either store's name for it."""
    repositories = getattr(manager, "repositories", None)
    installed = getattr(repositories, "list_installed", None)
    if installed is None:
        installed = getattr(repositories, "list_downloaded", None)

    try:
        return list(installed or [])
    except TypeError:
        return []


def _loaded_marketplace(hass: HomeAssistant) -> Any:
    """The Marketplace manager, or None before 2026.11 or until it has loaded."""
    # async_loaded_entries is newer than the oldest Home Assistant we support.
    lister = getattr(
        getattr(hass, "config_entries", None), "async_loaded_entries", None
    )
    if lister is None:
        return None

    try:
        entries = lister(MARKETPLACE_DOMAIN)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None

    return getattr(entries[0], "runtime_data", None) if entries else None
