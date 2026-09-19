"""Tests for consent-gated remote access.

The bug these cover is the whole feature: the client never received consent
requests at all, so a technician's request sat on the server waiting for an
answer that Home Assistant was never going to ask for.

What matters here is not that a request is fetched -- it is that the person
being asked sees the truth, that their answer arrives at the server as the
decision they actually made, and that a request which is no longer pending
stops being shown. A prompt for a dead request teaches people to ignore
prompts.

Home Assistant is not installed here; tests/conftest.py stubs the symbols the
integration imports, including recording versions of persistent_notification
and issue_registry.
"""
import asyncio
import importlib
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone


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
remote_access_mod = _load("remote_access")
repairs_mod = _load("repairs")
tunnel_mod = _load("tunnel")

notifications = sys.modules["homeassistant.components.persistent_notification"]
issue_registry = sys.modules["homeassistant.helpers.issue_registry"]

DOMAIN = const.DOMAIN


# --- the payload under test --------------------------------------------------
#
# Lifted from the server's own documented example, so the wording below is a
# real homeowner-facing scope description rather than something invented to
# match the code.

PENDING_PAYLOAD = {
    "policy": "always_ask",
    "standing_consent_until": None,
    "requests": [
        {
            "id": 41,
            "requested_by": "Sam Rivera",
            "scope": "maintenance",
            "scope_label": "Diagnostics and repairs",
            "scope_description": (
                "Everything in read-only access, plus the ability to restart "
                "things, reload a broken device and run repairs."
            ),
            "reason": "Zigbee stopped after the 0.0.48 update",
            "duration_minutes": 60,
            "requested_at": "2026-09-18T04:28:00+00:00",
            "expires_at": "2026-09-18T05:28:00+00:00",
            "consent_url": "https://dispatch.example.com/access/tok-abc",
        }
    ],
}

EMPTY_PAYLOAD = {"policy": "always_ask", "standing_consent_until": None, "requests": []}

# Hand-written on purpose. Deriving these from PENDING_PAYLOAD -- or worse from
# the module's own formatting helper -- would assert that the code equals
# itself.
EXPECTED_TITLE = "Sam Rivera is asking to access your Home Assistant"

EXPECTED_MESSAGE = (
    "Reason: Zigbee stopped after the 0.0.48 update\n"
    "\n"
    "What they will be able to do: Everything in read-only access, plus the "
    "ability to restart things, reload a broken device and run repairs.\n"
    "\n"
    "For how long: 60 minutes.\n"
    "\n"
    "Approve or decline under Settings > System > Repairs.\n"
    "You can also answer here: https://dispatch.example.com/access/tok-abc"
)

EXPECTED_PLACEHOLDERS = {
    "requested_by": "Sam Rivera",
    "reason": "Zigbee stopped after the 0.0.48 update",
    "scope_description": (
        "Everything in read-only access, plus the ability to restart things, "
        "reload a broken device and run repairs."
    ),
    "scope_label": "Diagnostics and repairs",
    "duration_minutes": "60",
}


# --- fakes -------------------------------------------------------------------


class FakeTask:
    def __init__(self):
        self.cancelled = False

    def done(self):
        return self.cancelled

    def cancel(self):
        self.cancelled = True


class FakeAuth:
    def __init__(self):
        self.users = []

    async def async_get_user(self, user_id):
        return types.SimpleNamespace(name="Alex (owner)") if user_id else None

    async def async_get_users(self):
        return list(self.users)


class FakeHass:
    def __init__(self):
        self.data = {}
        self.auth = FakeAuth()
        self.config = types.SimpleNamespace(location_name="Test Home")
        self.background_tasks = []

    def async_create_background_task(self, coro, name=None):
        # The loop itself is exercised through async_pump_once; closing the
        # coroutine here keeps the scheduling decision testable without
        # actually spawning it.
        coro.close()
        task = FakeTask()
        self.background_tasks.append((name, task))
        return task


class FakeApiClient:
    """Records what the manager asked the server to do."""

    def __init__(self, pending=None, respond_result=None, respond_error=None):
        self.pending_payloads = list(pending or [])
        self.last_pending = EMPTY_PAYLOAD
        self.respond_result = respond_result or {
            "status": "granted",
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=60)
            ).isoformat(),
        }
        self.respond_error = respond_error
        self.revoke_error = None
        self.responded = []
        self.revoked = []
        self.polled = 0
        self.poll_batches = []
        self.exchange_responses = []

    async def fetch_pending_access(self, installation_id):
        if self.pending_payloads:
            self.last_pending = self.pending_payloads.pop(0)
        return self.last_pending

    async def respond_to_access(
        self, installation_id, session_id, decision, note=None, responder=None
    ):
        self.responded.append(
            {
                "installation_id": installation_id,
                "session_id": session_id,
                "decision": decision,
                "note": note,
                "responder": responder,
            }
        )
        if self.respond_error:
            raise self.respond_error
        return self.respond_result

    async def revoke_access(self, installation_id, session_id, note=None):
        self.revoked.append({"session_id": session_id, "note": note})
        if self.revoke_error:
            raise self.revoke_error
        return {"status": "revoked"}

    async def poll_access(self, installation_id):
        self.polled += 1
        return self.poll_batches.pop(0) if self.poll_batches else []

    async def respond_to_exchange(
        self, installation_id, request_id, status=None, headers=None, body=None,
        error=None,
    ):
        self.exchange_responses.append(
            {
                "request_id": request_id,
                "status": status,
                "headers": headers,
                "body": body,
                "error": error,
            }
        )
        return {"status": "recorded"}


class FakeCoordinator:
    def __init__(self, api, hass):
        self.api_client = api
        self.installation_id = 7
        self.hass = hass
        self.remote_access = None
        self.listener_pushes = 0

    def async_update_listeners(self):
        self.listener_pushes += 1


def make_manager(api=None, pending=None):
    """Wire a manager the way async_setup_entry does."""
    api = api or FakeApiClient(pending=pending)
    hass = FakeHass()
    coordinator = FakeCoordinator(api, hass)
    manager = remote_access_mod.HADispatchRemoteAccess(hass, coordinator)
    coordinator.remote_access = manager
    hass.data[DOMAIN] = {"entry-1": coordinator}
    return manager, api, hass


# --- fake aiohttp, for asserting the wire format -----------------------------


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status = status
        self.url = "https://dispatch.example.com/fake"
        self._payload = payload if payload is not None else {}
        self._text = text

    async def json(self):
        return self._payload

    async def text(self):
        return self._text

    async def read(self):
        return self._text.encode("utf-8")

    @property
    def headers(self):
        return {"Content-Type": "application/json"}

    def raise_for_status(self):
        if self.status >= 400:
            raise AssertionError(f"unexpected status {self.status}")


class FakeRequestContext:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Records method, URL and JSON body for every call."""

    def __init__(self, response=None):
        self.response = response or FakeResponse()
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(("GET", url, None, headers))
        return FakeRequestContext(self.response)

    def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url, json, headers))
        return FakeRequestContext(self.response)


# --- tests -------------------------------------------------------------------


class ConsentPromptTest(unittest.TestCase):
    """What the customer is shown, and whether it goes away."""

    def setUp(self):
        notifications.reset()
        issue_registry.reset()

    def test_the_notification_says_who_why_what_and_how_long(self):
        manager, _, _ = make_manager(pending=[PENDING_PAYLOAD])

        asyncio.run(manager.async_poll_pending())

        self.assertEqual(1, len(notifications.created))
        created = notifications.created[0]
        self.assertEqual(EXPECTED_TITLE, created["title"])
        self.assertEqual(EXPECTED_MESSAGE, created["message"])
        self.assertEqual("dispatch_access_41", created["notification_id"])

    def test_the_scope_description_is_passed_through_not_technicalised(self):
        # The server writes this for a homeowner. Rewording it -- or falling
        # back to the scope slug -- defeats the point of asking.
        manager, _, _ = make_manager(pending=[PENDING_PAYLOAD])

        asyncio.run(manager.async_poll_pending())
        message = notifications.created[0]["message"]

        self.assertIn(
            "Everything in read-only access, plus the ability to restart "
            "things, reload a broken device and run repairs.",
            message,
        )
        self.assertNotIn("maintenance", message)

    def test_the_repairs_issue_is_fixable_and_carries_the_same_four_facts(self):
        manager, _, _ = make_manager(pending=[PENDING_PAYLOAD])

        asyncio.run(manager.async_poll_pending())

        self.assertEqual(1, len(issue_registry.created_issues))
        issue = issue_registry.created_issues[0]
        self.assertEqual(DOMAIN, issue["domain"])
        self.assertEqual("access_request_41", issue["issue_id"])
        self.assertTrue(issue["is_fixable"])
        self.assertEqual("access_request", issue["translation_key"])
        self.assertEqual(EXPECTED_PLACEHOLDERS, issue["translation_placeholders"])
        self.assertEqual({"session_id": "41"}, issue["data"])

    def test_a_request_seen_twice_is_only_announced_once(self):
        manager, _, _ = make_manager(pending=[PENDING_PAYLOAD, PENDING_PAYLOAD])

        asyncio.run(manager.async_poll_pending())
        asyncio.run(manager.async_poll_pending())

        self.assertEqual(1, len(notifications.created))
        self.assertEqual(1, len(issue_registry.created_issues))

    def test_a_request_that_leaves_the_pending_list_clears_both_prompts(self):
        # However it left -- answered on the web page, or simply lapsed.
        manager, _, _ = make_manager(pending=[PENDING_PAYLOAD, EMPTY_PAYLOAD])

        asyncio.run(manager.async_poll_pending())
        self.assertIn("41", manager.pending)
        self.assertNotIn("dispatch_access_41", notifications.dismissed)

        asyncio.run(manager.async_poll_pending())

        self.assertEqual({}, manager.pending)
        self.assertIn("dispatch_access_41", notifications.dismissed)
        self.assertIn((DOMAIN, "access_request_41"), issue_registry.deleted_issues)
        self.assertNotIn((DOMAIN, "access_request_41"), issue_registry.issues)

    def test_answering_here_clears_both_prompts(self):
        manager, _, _ = make_manager(pending=[PENDING_PAYLOAD])
        asyncio.run(manager.async_poll_pending())

        asyncio.run(manager.async_respond(41, "deny"))

        self.assertEqual({}, manager.pending)
        self.assertIn("dispatch_access_41", notifications.dismissed)
        self.assertIn((DOMAIN, "access_request_41"), issue_registry.deleted_issues)

    def test_an_already_answered_request_clears_the_prompt_without_retrying(self):
        # A 422 is a normal outcome: somebody answered on the web page while
        # the notification was still on screen.
        api = FakeApiClient(
            pending=[PENDING_PAYLOAD],
            respond_error=api_client.RemoteAccessConflict("already answered"),
        )
        manager, _, _ = make_manager(api=api)
        asyncio.run(manager.async_poll_pending())

        accepted = asyncio.run(manager.async_respond(41, "grant"))

        self.assertFalse(accepted)
        self.assertEqual(1, len(api.responded))
        self.assertEqual({}, manager.pending)
        self.assertIn("dispatch_access_41", notifications.dismissed)
        self.assertIn((DOMAIN, "access_request_41"), issue_registry.deleted_issues)
        # Nothing became live off the back of a refused decision.
        self.assertFalse(manager.has_live_sessions())

    def test_a_transient_failure_leaves_a_valid_prompt_alone(self):
        class Flaky(FakeApiClient):
            async def fetch_pending_access(self, installation_id):
                if self.polled == 0:
                    self.polled += 1
                    return PENDING_PAYLOAD
                raise TimeoutError("server unreachable")

        manager, _, _ = make_manager(api=Flaky())
        asyncio.run(manager.async_poll_pending())
        asyncio.run(manager.async_poll_pending())

        self.assertIn("41", manager.pending)
        self.assertEqual([], notifications.dismissed)


class DecisionWireFormatTest(unittest.TestCase):
    """The decision that reaches the server is the one the customer made."""

    def setUp(self):
        notifications.reset()
        issue_registry.reset()

    def _respond(self, decision):
        session = FakeSession(FakeResponse(200, {"status": "granted"}))
        client = api_client.HADispatchApiClient(
            session, "https://dispatch.example.com", token="tok"
        )
        asyncio.run(
            client.respond_to_access(
                7, 41, decision, note="Go ahead", responder="Alex (owner)"
            )
        )
        return session.calls[0]

    def test_approving_posts_grant_to_the_respond_endpoint(self):
        method, url, body, headers = self._respond("grant")

        self.assertEqual("POST", method)
        self.assertEqual(
            "https://dispatch.example.com/api/v1/installations/7/access/41/respond",
            url,
        )
        # Wrong answer first: a stub returning a fixed decision cannot pass.
        self.assertNotEqual("deny", body["decision"])
        self.assertEqual("grant", body["decision"])
        self.assertEqual("Go ahead", body["note"])
        self.assertEqual("Alex (owner)", body["responder"])
        self.assertEqual("Bearer tok", headers["Authorization"])

    def test_declining_posts_deny_to_the_respond_endpoint(self):
        _, url, body, _ = self._respond("deny")

        self.assertEqual(
            "https://dispatch.example.com/api/v1/installations/7/access/41/respond",
            url,
        )
        self.assertNotEqual("grant", body["decision"])
        self.assertEqual("deny", body["decision"])

    def test_the_responder_is_omitted_rather_than_invented(self):
        session = FakeSession(FakeResponse(200, {"status": "denied"}))
        client = api_client.HADispatchApiClient(
            session, "https://dispatch.example.com", token="tok"
        )
        asyncio.run(client.respond_to_access(7, 41, "deny"))

        self.assertNotIn("responder", session.calls[0][2])
        self.assertNotIn("note", session.calls[0][2])

    def test_the_approve_button_grants_and_the_decline_button_denies(self):
        # Through the Repairs dialog the customer actually taps.
        manager, api, hass = make_manager(pending=[PENDING_PAYLOAD, PENDING_PAYLOAD])
        asyncio.run(manager.async_poll_pending())

        approve = repairs_mod.HADispatchConsentRepairFlow("41")
        approve.hass = hass
        approve.context = {"user_id": "user-1"}
        asyncio.run(approve.async_step_grant())

        self.assertEqual(1, len(api.responded))
        self.assertNotEqual("deny", api.responded[0]["decision"])
        self.assertEqual("grant", api.responded[0]["decision"])
        self.assertEqual("Alex (owner)", api.responded[0]["responder"])

        asyncio.run(manager.async_poll_pending())
        decline = repairs_mod.HADispatchConsentRepairFlow("41")
        decline.hass = hass
        decline.context = {}
        asyncio.run(decline.async_step_deny())

        self.assertEqual(2, len(api.responded))
        self.assertNotEqual("grant", api.responded[1]["decision"])
        self.assertEqual("deny", api.responded[1]["decision"])
        # No user id on the flow context, so no name is invented.
        self.assertIsNone(api.responded[1]["responder"])

    def test_the_dialog_offers_exactly_approve_and_decline(self):
        flow = repairs_mod.HADispatchConsentRepairFlow("41")
        result = asyncio.run(flow.async_step_init())

        self.assertEqual("menu", result["type"])
        self.assertEqual(["grant", "deny"], result["menu_options"])

    def test_the_fix_flow_routes_active_sessions_to_the_off_switch(self):
        hass = FakeHass()
        request_flow = asyncio.run(
            repairs_mod.async_create_fix_flow(hass, "access_request_41", None)
        )
        active_flow = asyncio.run(
            repairs_mod.async_create_fix_flow(hass, "access_active_41", None)
        )

        self.assertIsInstance(request_flow, repairs_mod.HADispatchConsentRepairFlow)
        self.assertIsInstance(active_flow, repairs_mod.HADispatchRevokeRepairFlow)
        self.assertEqual("41", active_flow._session_id)


class LiveSessionTest(unittest.TestCase):
    """What happens once consent has been given."""

    def setUp(self):
        notifications.reset()
        issue_registry.reset()

    def test_granting_makes_the_session_live_and_starts_the_tunnel(self):
        manager, api, _ = make_manager(pending=[PENDING_PAYLOAD])
        asyncio.run(manager.async_poll_pending())
        self.assertFalse(manager.tunnel.running)

        asyncio.run(manager.async_respond(41, "grant"))

        self.assertTrue(manager.has_live_sessions())
        self.assertEqual("maintenance", manager.scope_for(41))
        self.assertTrue(manager.tunnel.running)

    def test_declining_leaves_nothing_live_and_no_tunnel(self):
        manager, _, _ = make_manager(pending=[PENDING_PAYLOAD])
        asyncio.run(manager.async_poll_pending())

        asyncio.run(manager.async_respond(41, "deny"))

        self.assertFalse(manager.has_live_sessions())
        self.assertIsNone(manager.scope_for(41))
        self.assertFalse(manager.tunnel.running)

    def test_a_live_session_gets_an_off_switch_on_screen(self):
        manager, _, _ = make_manager(pending=[PENDING_PAYLOAD])
        asyncio.run(manager.async_poll_pending())
        asyncio.run(manager.async_respond(41, "grant"))

        active = [
            issue for issue in issue_registry.created_issues
            if issue["issue_id"] == "access_active_41"
        ]
        self.assertEqual(1, len(active))
        self.assertTrue(active[0]["is_fixable"])
        self.assertEqual("access_active", active[0]["translation_key"])

    def test_revoking_closes_the_session_and_clears_the_off_switch(self):
        manager, api, _ = make_manager(pending=[PENDING_PAYLOAD])
        asyncio.run(manager.async_poll_pending())
        asyncio.run(manager.async_respond(41, "grant"))

        closed = asyncio.run(manager.async_revoke(41, note="No thanks"))

        self.assertEqual(1, closed)
        self.assertEqual([{"session_id": "41", "note": "No thanks"}], api.revoked)
        self.assertFalse(manager.has_live_sessions())
        self.assertFalse(manager.tunnel.running)
        self.assertIn((DOMAIN, "access_active_41"), issue_registry.deleted_issues)
        self.assertIn("dispatch_access_active_41", notifications.dismissed)

    def test_revoking_with_no_session_named_ends_everything(self):
        manager, api, _ = make_manager(pending=[PENDING_PAYLOAD])
        asyncio.run(manager.async_poll_pending())
        asyncio.run(manager.async_respond(41, "grant"))

        asyncio.run(manager.async_revoke())

        self.assertEqual(["41"], [call["session_id"] for call in api.revoked])
        self.assertFalse(manager.has_live_sessions())

    def test_a_session_answered_elsewhere_is_served_but_never_announced(self):
        # It left the pending list without us answering, so it may have been
        # granted on the web page -- serve it -- or declined there. Claiming on
        # screen that access is active would be a guess, and a lie half the
        # time.
        manager, _, _ = make_manager(pending=[PENDING_PAYLOAD, EMPTY_PAYLOAD])
        asyncio.run(manager.async_poll_pending())
        asyncio.run(manager.async_poll_pending())

        self.assertTrue(manager.has_live_sessions())
        self.assertEqual("maintenance", manager.scope_for(41))
        self.assertEqual(
            [],
            [
                issue for issue in issue_registry.created_issues
                if issue["issue_id"] == "access_active_41"
            ],
        )
        self.assertFalse(manager.live_sessions()[0]["confirmed_here"])

    def test_an_expired_session_stops_the_tunnel(self):
        manager, api, _ = make_manager(pending=[PENDING_PAYLOAD, EMPTY_PAYLOAD])
        api.respond_result = {
            "status": "granted",
            "expires_at": (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).isoformat(),
        }
        asyncio.run(manager.async_poll_pending())
        asyncio.run(manager.async_respond(41, "grant"))
        self.assertTrue(manager.has_live_sessions())

        asyncio.run(manager.async_poll_pending())

        self.assertFalse(manager.has_live_sessions())
        self.assertFalse(manager.tunnel.running)

    def test_live_sessions_survive_a_restart(self):
        manager, _, _ = make_manager(pending=[PENDING_PAYLOAD])
        asyncio.run(manager.async_poll_pending())
        asyncio.run(manager.async_respond(41, "grant"))
        stored = manager._store.data

        revived, _, _ = make_manager()
        revived._store.data = stored
        asyncio.run(revived.async_load())

        self.assertTrue(revived.has_live_sessions())
        self.assertEqual("maintenance", revived.scope_for("41"))
        self.assertTrue(revived.tunnel.running)


class RemoteAccessTransportTest(unittest.TestCase):
    """The agent's side of the relay."""

    def setUp(self):
        notifications.reset()
        issue_registry.reset()

    def test_a_404_means_no_remote_access_not_a_vanished_installation(self):
        # The trap: /access/ 404s on an older server, and mapping that onto
        # InstallationGoneError would re-enrol a perfectly healthy
        # installation once a minute forever.
        session = FakeSession(FakeResponse(404, {}))
        client = api_client.HADispatchApiClient(
            session, "https://dispatch.example.com", token="tok"
        )

        with self.assertRaises(api_client.RemoteAccessUnavailable):
            asyncio.run(client.fetch_pending_access(7))

        self.assertFalse(
            issubclass(
                api_client.RemoteAccessUnavailable, api_client.InstallationAuthError
            )
        )

    def test_a_server_without_remote_access_is_asked_only_once(self):
        class Missing(FakeApiClient):
            async def fetch_pending_access(self, installation_id):
                self.polled += 1
                raise api_client.RemoteAccessUnavailable("404")

        manager, api, _ = make_manager(api=Missing())
        asyncio.run(manager.async_poll_pending())
        asyncio.run(manager.async_poll_pending())

        self.assertEqual(1, api.polled)
        self.assertFalse(manager.available)

    def test_poll_returns_the_request_list(self):
        session = FakeSession(
            FakeResponse(200, {"requests": [{"request_id": "abc", "path": "/api/states"}]})
        )
        client = api_client.HADispatchApiClient(
            session, "https://dispatch.example.com", token="tok"
        )

        requests = asyncio.run(client.poll_access(7))

        self.assertEqual(
            "https://dispatch.example.com/api/v1/installations/7/access/poll",
            session.calls[0][1],
        )
        self.assertEqual("abc", requests[0]["request_id"])

    def test_an_exchange_failure_posts_an_error_and_nothing_else(self):
        session = FakeSession(FakeResponse(200, {"status": "recorded"}))
        client = api_client.HADispatchApiClient(
            session, "https://dispatch.example.com", token="tok"
        )

        asyncio.run(
            client.respond_to_exchange(7, "req-1", error="Could not reach the local API")
        )

        method, url, body, _ = session.calls[0]
        self.assertEqual(
            "https://dispatch.example.com/api/v1/installations/7/access/"
            "exchanges/req-1/respond",
            url,
        )
        self.assertEqual({"error": "Could not reach the local API"}, body)

    def test_the_whole_batch_is_answered(self):
        manager, api, _ = make_manager()
        api.poll_batches = [
            [
                {"request_id": "r1", "session_id": 41, "method": "GET", "path": "/config"},
                {"request_id": "r2", "session_id": 41, "method": "GET", "path": "/api/stream"},
            ]
        ]

        asyncio.run(manager.tunnel.async_pump_once())

        self.assertEqual(
            ["r1", "r2"], [r["request_id"] for r in api.exchange_responses]
        )

    def test_a_request_for_a_session_we_have_no_consent_for_is_refused(self):
        manager, api, _ = make_manager()
        api.poll_batches = [
            [{"request_id": "r1", "session_id": 99, "method": "GET", "path": "/api/states"}]
        ]

        asyncio.run(manager.tunnel.async_pump_once())

        self.assertEqual(
            "No consent on record for this session.",
            api.exchange_responses[0]["error"],
        )

    def test_local_policy_refuses_what_is_not_the_rest_api(self):
        self.assertIsNone(tunnel_mod.refusal_reason("GET", "/api/states"))
        self.assertIsNotNone(tunnel_mod.refusal_reason("GET", "/lovelace"))
        self.assertIsNotNone(tunnel_mod.refusal_reason("POST", "/auth/token"))
        self.assertIsNotNone(tunnel_mod.refusal_reason("GET", "/api/stream"))
        self.assertIsNotNone(tunnel_mod.refusal_reason("GET", "/api/websocket"))

    def test_read_only_scopes_never_reach_for_an_admin_credential(self):
        manager, _, _ = make_manager()
        tunnel = manager.tunnel

        self.assertEqual(tunnel_mod.GROUP_READ_ONLY, tunnel._group_for("diagnostic"))
        self.assertEqual(tunnel_mod.GROUP_ADMIN, tunnel._group_for("maintenance"))
        self.assertEqual(tunnel_mod.GROUP_ADMIN, tunnel._group_for("full"))

    def test_a_binary_response_crosses_as_base64(self):
        encoded = tunnel_mod.HADispatchTunnel._encode_response(
            200, {"Content-Type": "image/jpeg"}, b"\xff\xd8\xff\xe0binary"
        )
        self.assertEqual("base64", encoded["headers"]["Content-Transfer-Encoding"])
        self.assertEqual("/9j/4GJpbmFyeQ==", encoded["body"])

        plain = tunnel_mod.HADispatchTunnel._encode_response(
            200, {"Content-Type": "text/plain; charset=utf-8"}, b"all good"
        )
        self.assertNotIn("Content-Transfer-Encoding", plain["headers"])
        self.assertEqual("all good", plain["body"])


class PendingConsentEntityTest(unittest.TestCase):
    """The binary sensor is how an automation learns somebody is waiting."""

    def setUp(self):
        notifications.reset()
        issue_registry.reset()

    def _entity(self, manager):
        binary_sensor = _load("binary_sensor")
        entry = types.SimpleNamespace(entry_id="entry-1")
        return binary_sensor.HADispatchPendingConsentSensor(
            manager.coordinator, entry
        )

    def test_it_is_off_until_somebody_asks(self):
        manager, _, _ = make_manager(pending=[EMPTY_PAYLOAD])
        entity = self._entity(manager)

        asyncio.run(manager.async_poll_pending())

        self.assertFalse(entity.is_on)
        self.assertEqual(0, entity.extra_state_attributes["pending_count"])

    def test_it_carries_the_request_a_dashboard_needs(self):
        manager, _, _ = make_manager(pending=[PENDING_PAYLOAD])
        entity = self._entity(manager)

        asyncio.run(manager.async_poll_pending())

        self.assertTrue(entity.is_on)
        attributes = entity.extra_state_attributes
        self.assertEqual(1, attributes["pending_count"])
        self.assertEqual("always_ask", attributes["policy"])
        request = attributes["requests"][0]
        self.assertEqual("41", request["session_id"])
        self.assertEqual("Sam Rivera", request["requested_by"])
        self.assertEqual("https://dispatch.example.com/access/tok-abc",
                         request["consent_url"])

    def test_it_goes_unavailable_when_the_server_has_no_remote_access(self):
        manager, _, _ = make_manager()
        entity = self._entity(manager)
        self.assertTrue(entity.available)

        manager.async_disable()

        self.assertFalse(entity.available)


if __name__ == "__main__":
    unittest.main()
