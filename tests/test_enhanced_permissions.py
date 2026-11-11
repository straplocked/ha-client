"""Tests for enhanced permissions ("Hands-off remote configuration").

When an admin at the installer ticks the box in Dispatch, the homeowner did
not do it themselves -- so their own Home Assistant tells them: it is on, who
turned it on and when, and how to turn it off. What matters:

- told once per grant, and not again on the next poll or after a restart;
- told again for a new grant;
- the notice comes down when the grant goes;
- a server without the feature (404) is asked once per run and is NEVER
  taken for an installation that has gone -- that would re-enrol a healthy
  house once a minute, forever;
- the off-switch reaches the server, and only then does the notice come down.
"""
import asyncio
import importlib
import os
import sys
import types
import unittest

import aiohttp


def _load(module: str):
    base = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "custom_components", "ha_dispatch_client",
    )
    if "ha_dispatch_client" not in sys.modules:
        pkg = types.ModuleType("ha_dispatch_client")
        pkg.__path__ = [base]
        sys.modules["ha_dispatch_client"] = pkg
    return importlib.import_module(f"ha_dispatch_client.{module}")


api_client = _load("api_client")
const = _load("const")
enhanced_mod = _load("enhanced_permissions")

notifications = sys.modules["homeassistant.components.persistent_notification"]
DOMAIN = const.DOMAIN

GRANT_7 = {
    "granted": True,
    "grant_id": 7,
    "method": "admin_attested",
    "granted_at": "2026-11-11T14:05:00+00:00",
    "granted_by": "Morgan Lee",
    "off_switch_url": "https://app.hadispatch.app/enhanced/off/tok-7",
}
GRANT_8 = {**GRANT_7, "grant_id": 8, "off_switch_url": "https://app.hadispatch.app/enhanced/off/tok-8"}
OFF = {
    "granted": False,
    "grant_id": None,
    "method": None,
    "granted_at": None,
    "granted_by": None,
    "off_switch_url": None,
}

# Hand-written. Deriving it from GRANT_7 through the module's own helper would
# assert that the code equals itself.
EXPECTED_TITLE = "Your installer turned on hands-off configuration"
EXPECTED_MESSAGE_7 = (
    "Morgan Lee turned on hands-off configuration for your Home Assistant "
    "on 11 November 2026 at 14:05.\n"
    "\n"
    "It lets your installer set up and update your Home Assistant remotely -- "
    "dashboards, floor plans, configuration -- without asking you each time. "
    "Everything they do is recorded under their name.\n"
    "\n"
    "It stays on until you turn it off.\n"
    "\n"
    "[Turn it off](https://app.hadispatch.app/enhanced/off/tok-7)\n"
    "\n"
    "Or, from here: open Developer tools > Actions and run "
    "`ha_dispatch_client.revoke_enhanced_permissions`."
)


# --- fakes -------------------------------------------------------------------


class FakeHass:
    def __init__(self):
        self.data = {}


class FakeApi:
    def __init__(self, states=None, fetch_error=None, revoke_error=None):
        self.states = list(states or [])
        self.fetch_error = fetch_error
        self.revoke_error = revoke_error
        self.fetches = 0
        self.revokes = []

    async def fetch_enhanced_permissions(self, installation_id):
        self.fetches += 1
        if self.fetch_error:
            raise self.fetch_error
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    async def revoke_enhanced_permissions(self, installation_id):
        self.revokes.append(installation_id)
        if self.revoke_error:
            raise self.revoke_error


class FakeCoordinator:
    def __init__(self, api):
        self.api_client = api
        self.installation_id = 7


def make_manager(api, hass=None, store_data=None):
    hass = hass or FakeHass()
    coordinator = FakeCoordinator(api)
    manager = enhanced_mod.HADispatchEnhancedPermissions(hass, coordinator)
    if store_data is not None:
        manager._store.data = store_data
    asyncio.run(manager.async_load())
    coordinator.enhanced_permissions = manager
    hass.data.setdefault(DOMAIN, {})["entry-1"] = coordinator
    return manager


def created_ids():
    return [n["notification_id"] for n in notifications.created]


# --- wire format -----------------------------------------------------------------


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.url = "https://app.hadispatch.app/api/v1/installations/7/enhanced-permissions"
        self._payload = payload if payload is not None else {}

    async def json(self):
        return self._payload

    async def text(self):
        return ""

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=None, history=(), status=self.status
            )


class FakeRequestContext:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, headers=None):
        self.calls.append(("GET", url, None, headers))
        return FakeRequestContext(self.response)

    def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url, json, headers))
        return FakeRequestContext(self.response)


def client_for(response):
    session = FakeSession(response)
    client = api_client.HADispatchApiClient(
        session=session, server_url="https://app.hadispatch.app", token="tok"
    )
    return client, session


class WireTest(unittest.TestCase):
    def test_a_404_on_the_state_is_unavailable_and_never_gone(self):
        client, _ = client_for(FakeResponse(404))
        with self.assertRaises(api_client.EnhancedPermissionsUnavailable) as caught:
            asyncio.run(client.fetch_enhanced_permissions(7))
        self.assertNotIsInstance(caught.exception, api_client.InstallationGoneError)
        self.assertNotIsInstance(caught.exception, api_client.InstallationAuthError)

    def test_a_404_on_the_revoke_is_unavailable_and_never_gone(self):
        client, _ = client_for(FakeResponse(404))
        with self.assertRaises(api_client.EnhancedPermissionsUnavailable) as caught:
            asyncio.run(client.revoke_enhanced_permissions(7))
        self.assertNotIsInstance(caught.exception, api_client.InstallationGoneError)

    def test_the_error_is_not_an_auth_error_by_inheritance_either(self):
        # The coordinator re-enrols on InstallationAuthError and its subclasses.
        self.assertFalse(
            issubclass(api_client.EnhancedPermissionsUnavailable, api_client.InstallationAuthError)
        )

    def test_a_401_is_still_an_auth_error(self):
        # A rejected token is a rejected token, whichever path it came on.
        client, _ = client_for(FakeResponse(401))
        with self.assertRaises(api_client.InstallationAuthError):
            asyncio.run(client.fetch_enhanced_permissions(7))

    def test_the_paths_are_the_contract_paths(self):
        client, session = client_for(FakeResponse(200, GRANT_7))
        self.assertEqual(GRANT_7, asyncio.run(client.fetch_enhanced_permissions(7)))
        client.session.response = FakeResponse(204)
        asyncio.run(client.revoke_enhanced_permissions(7))
        self.assertEqual(
            [
                ("GET", "https://app.hadispatch.app/api/v1/installations/7/enhanced-permissions"),
                ("POST", "https://app.hadispatch.app/api/v1/installations/7/enhanced-permissions/revoke"),
            ],
            [(method, url) for method, url, _, _ in session.calls],
        )
        self.assertEqual("Bearer tok", session.calls[0][3]["Authorization"])


# --- the notice ---------------------------------------------------------------------


class NoticeTest(unittest.TestCase):
    def setUp(self):
        notifications.reset()

    def test_it_says_who_when_and_how_to_turn_it_off(self):
        self.assertEqual((EXPECTED_TITLE, EXPECTED_MESSAGE_7), enhanced_mod.build_notice(GRANT_7))

    def test_without_a_name_or_a_link_it_still_says_how_to_turn_it_off(self):
        title, message = enhanced_mod.build_notice(
            {**GRANT_7, "granted_by": None, "granted_at": None, "off_switch_url": None}
        )
        self.assertEqual(EXPECTED_TITLE, title)
        self.assertEqual(
            "Your installer turned on hands-off configuration for your Home "
            "Assistant.\n"
            "\n"
            "It lets your installer set up and update your Home Assistant "
            "remotely -- dashboards, floor plans, configuration -- without "
            "asking you each time. Everything they do is recorded under their "
            "name.\n"
            "\n"
            "It stays on until you turn it off.\n"
            "\n"
            "To turn it off: open Developer tools > Actions and run "
            "`ha_dispatch_client.revoke_enhanced_permissions`.",
            message,
        )

    def test_a_grant_the_homeowner_gave_themselves_is_not_announced(self):
        for method in ("in_app", "link", "consent_link", None):
            with self.subTest(method=method):
                self.assertIsNone(enhanced_mod.build_notice({**GRANT_7, "method": method}))

    def test_off_is_not_announced(self):
        self.assertIsNone(enhanced_mod.build_notice(OFF))
        self.assertIsNone(enhanced_mod.build_notice({**GRANT_7, "granted": False}))
        self.assertIsNone(enhanced_mod.build_notice(None))


class AnnounceOnceTest(unittest.TestCase):
    def setUp(self):
        notifications.reset()

    def test_grant_7_is_announced_once_not_again_on_the_next_poll(self):
        manager = make_manager(FakeApi([GRANT_7]))

        asyncio.run(manager.async_poll())
        asyncio.run(manager.async_poll())
        asyncio.run(manager.async_poll())

        self.assertEqual(["enhanced_permissions_on"], created_ids())
        self.assertEqual(EXPECTED_TITLE, notifications.created[0]["title"])
        self.assertEqual(EXPECTED_MESSAGE_7, notifications.created[0]["message"])

    def test_grant_7_is_not_announced_again_after_a_restart(self):
        hass = FakeHass()
        first = make_manager(FakeApi([GRANT_7]), hass=hass)
        asyncio.run(first.async_poll())
        persisted = first._store.data
        self.assertEqual({"announced_grant_id": 7}, persisted)

        # A restart: a new manager, new memory, the same file on disk.
        notifications.reset()
        second = make_manager(FakeApi([GRANT_7]), store_data=dict(persisted))
        asyncio.run(second.async_poll())

        self.assertEqual([], notifications.created)

    def test_a_grant_id_that_came_back_from_storage_as_text_is_the_same_grant(self):
        manager = make_manager(FakeApi([GRANT_7]), store_data={"announced_grant_id": "7"})
        asyncio.run(manager.async_poll())
        self.assertEqual([], notifications.created)

    def test_grant_8_is_announced_again(self):
        manager = make_manager(FakeApi([GRANT_7, GRANT_8]))

        asyncio.run(manager.async_poll())
        asyncio.run(manager.async_poll())

        self.assertEqual(["enhanced_permissions_on", "enhanced_permissions_on"], created_ids())
        self.assertIn("tok-8", notifications.created[1]["message"])
        self.assertEqual({"announced_grant_id": 8}, manager._store.data)

    def test_it_is_dismissed_when_granted_becomes_false(self):
        manager = make_manager(FakeApi([GRANT_7, OFF, OFF]))

        asyncio.run(manager.async_poll())
        asyncio.run(manager.async_poll())
        asyncio.run(manager.async_poll())

        # Once, not every poll it stays off.
        self.assertEqual(["enhanced_permissions_on"], notifications.dismissed)
        self.assertEqual({"announced_grant_id": None}, manager._store.data)

    def test_it_is_dismissed_after_a_restart_too(self):
        manager = make_manager(FakeApi([OFF]), store_data={"announced_grant_id": 7})
        asyncio.run(manager.async_poll())
        self.assertEqual(["enhanced_permissions_on"], notifications.dismissed)

    def test_off_with_nothing_announced_says_nothing(self):
        manager = make_manager(FakeApi([OFF]))
        asyncio.run(manager.async_poll())
        self.assertEqual([], notifications.created)
        self.assertEqual([], notifications.dismissed)

    def test_a_grant_the_homeowner_gave_replaces_the_installers_notice(self):
        own = {**GRANT_8, "method": "in_app", "granted_by": None}
        manager = make_manager(FakeApi([GRANT_7, own]))
        asyncio.run(manager.async_poll())
        asyncio.run(manager.async_poll())
        self.assertEqual(1, len(notifications.created))
        self.assertEqual(["enhanced_permissions_on"], notifications.dismissed)

    def test_a_transient_failure_leaves_the_notice_as_it_is(self):
        manager = make_manager(FakeApi([GRANT_7]))
        asyncio.run(manager.async_poll())
        manager.coordinator.api_client.fetch_error = aiohttp.ClientConnectionError("down")
        asyncio.run(manager.async_poll())
        self.assertEqual([], notifications.dismissed)
        self.assertEqual({"announced_grant_id": 7}, manager._store.data)


class UnavailableTest(unittest.TestCase):
    def setUp(self):
        notifications.reset()

    def test_a_404_is_asked_once_per_run(self):
        api = FakeApi(fetch_error=api_client.EnhancedPermissionsUnavailable("404"))
        manager = make_manager(api)

        asyncio.run(manager.async_poll())
        asyncio.run(manager.async_poll())
        asyncio.run(manager.async_poll())

        self.assertEqual(1, api.fetches)
        self.assertFalse(manager.available)
        self.assertEqual([], notifications.created)

    def test_a_404_never_reaches_the_coordinator_as_gone(self):
        # The poll swallows it; a coordinator would only re-enrol on an
        # InstallationAuthError escaping.
        api = FakeApi(fetch_error=api_client.EnhancedPermissionsUnavailable("404"))
        manager = make_manager(api)
        try:
            asyncio.run(manager.async_poll())
        except api_client.InstallationAuthError:  # pragma: no cover - the guard
            self.fail("a 404 here must never look like the installation has gone")


class RevokeTest(unittest.TestCase):
    def setUp(self):
        notifications.reset()

    def test_the_off_switch_reaches_the_server_and_takes_the_notice_down(self):
        api = FakeApi([GRANT_7])
        manager = make_manager(api)
        asyncio.run(manager.async_poll())

        self.assertTrue(asyncio.run(manager.async_revoke()))

        self.assertEqual([7], api.revokes)
        self.assertEqual(["enhanced_permissions_on"], notifications.dismissed)
        self.assertFalse(manager.granted)
        self.assertEqual({"announced_grant_id": None}, manager._store.data)

    def test_a_failed_revoke_leaves_the_notice_up(self):
        api = FakeApi([GRANT_7], revoke_error=aiohttp.ClientConnectionError("down"))
        manager = make_manager(api)
        asyncio.run(manager.async_poll())

        self.assertFalse(asyncio.run(manager.async_revoke()))

        self.assertEqual([], notifications.dismissed)
        self.assertTrue(manager.granted)

    def test_the_action_finds_every_installation(self):
        hass = FakeHass()
        first = make_manager(FakeApi([GRANT_7]), hass=hass)
        self.assertEqual([first], enhanced_mod.async_all_managers(hass))
        self.assertEqual([], enhanced_mod.async_all_managers(FakeHass()))


if __name__ == "__main__":
    unittest.main()
