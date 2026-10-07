"""Tests for the Home Assistant end of a Remote Screen.

Dispatch renders this house's frontend in a headless browser on its side. The
agent's part is narrow, and these tests hold it to that:

  - a screen session reaches the frontend, but never the login and never the
    raw socket as an HTTP request, and writes only to the REST API;
  - the browser's placeholder token is replaced with ours on this side, so a
    real credential never leaves the house -- and nothing else is rewritten;
  - a screen session the homeowner never saw a prompt for (opened under
    their standing enhanced permissions) is still learned, announced, and
    dropped when the server stops counting it;
  - frames for any session without screen consent here are dropped.

Reuses the fakes in test_remote_access.py rather than inventing new ones.
"""
import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone

from test_remote_access import (  # noqa: E402 -- shares the loader and fakes
    DOMAIN,
    EMPTY_PAYLOAD,
    FakeApiClient,
    _load,
    make_manager,
    notifications,
)

const = _load("const")
screen_mod = _load("screen")
tunnel_mod = _load("tunnel")


def _active(session_id=88, minutes=60):
    return {
        "id": session_id,
        "requested_by": "Sam Rivera",
        "scope": "screen",
        "scope_label": "See and use your screen",
        "scope_description": "Let your installer see and use your Home Assistant screen.",
        "reason": "Tune the wall tablet dashboard",
        "consent_method": "enhanced",
        "duration_minutes": minutes,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(),
        "consent_url": "https://dispatch.example.com/access/tok-screen",
    }


class ScreenRefusalTest(unittest.TestCase):
    def test_a_screen_reaches_the_frontend(self):
        for path in ("/", "/lovelace/0", "/frontend_latest/core.js", "/static/x.png",
                     "/hacsfiles/card.js", "/local/plan.png", "/api/states"):
            self.assertIsNone(tunnel_mod.refusal_reason("GET", path, "screen"), path)
        self.assertIsNone(tunnel_mod.refusal_reason("POST", "/api/services/light/turn_on", "screen"))

    def test_a_screen_never_reaches_the_login_or_the_raw_socket(self):
        for method, path in (("GET", "/auth/authorize"), ("POST", "/auth/token"),
                             ("GET", "/auth"), ("GET", "/api/websocket")):
            self.assertIsNotNone(tunnel_mod.refusal_reason(method, path, "screen"), path)

    def test_a_screen_writes_only_to_the_rest_api(self):
        self.assertIsNotNone(tunnel_mod.refusal_reason("POST", "/lovelace/0", "screen"))
        self.assertIsNotNone(tunnel_mod.refusal_reason("DELETE", "/local/plan.png", "screen"))

    def test_other_scopes_keep_the_rest_only_rule(self):
        self.assertIsNotNone(tunnel_mod.refusal_reason("GET", "/lovelace/0", "full"))
        self.assertIsNotNone(tunnel_mod.refusal_reason("GET", "/frontend_latest/core.js"))

    def test_a_screen_is_served_with_the_admin_credential(self):
        manager, _, _ = make_manager()
        self.assertEqual(manager.tunnel._group_for("screen"), tunnel_mod.GROUP_ADMIN)


class AuthSubstitutionTest(unittest.TestCase):
    def test_the_auth_frame_carries_our_token_not_the_browsers(self):
        out = screen_mod.substitute_auth(
            json.dumps({"type": "auth", "access_token": "dispatch-screen-placeholder"}), "real-local"
        )
        self.assertEqual(json.loads(out), {"type": "auth", "access_token": "real-local"})

    def test_every_other_frame_is_left_alone(self):
        for payload in ('{"type":"call_service","domain":"light","service":"toggle","id":5}',
                        '[{"type":"auth"},{"type":"ping"}]',
                        "not json at all"):
            self.assertEqual(screen_mod.substitute_auth(payload, "real-local"), payload)


class ActiveSessionTest(unittest.TestCase):
    def test_a_screen_opened_without_a_prompt_is_learned_and_announced(self):
        payload = dict(EMPTY_PAYLOAD, active=[_active()])
        manager, _, _ = make_manager(pending=[payload])

        asyncio.run(manager.async_poll_pending())

        self.assertEqual(manager.scope_for("88"), "screen")
        self.assertIn(
            f"{const.ACCESS_ACTIVE_NOTIFICATION_PREFIX}88",
            [n["notification_id"] for n in notifications.created],
            "The homeowner sees that someone is on their screen.",
        )

    def test_it_ends_when_the_server_stops_counting_it(self):
        manager, _, _ = make_manager(pending=[
            dict(EMPTY_PAYLOAD, active=[_active()]),
            dict(EMPTY_PAYLOAD, active=[]),
        ])
        asyncio.run(manager.async_poll_pending())
        asyncio.run(manager.async_poll_pending())

        self.assertIsNone(manager.scope_for("88"))

    def test_a_server_without_the_list_changes_nothing(self):
        manager, _, _ = make_manager(pending=[
            dict(EMPTY_PAYLOAD, active=[_active()]),
            EMPTY_PAYLOAD,
        ])
        asyncio.run(manager.async_poll_pending())
        asyncio.run(manager.async_poll_pending())

        self.assertEqual(manager.scope_for("88"), "screen")


class FrameGateTest(unittest.TestCase):
    def test_frames_for_a_session_without_screen_consent_are_dropped(self):
        manager, _, _ = make_manager()
        opened = []

        async def fake_open(session_id):
            opened.append(session_id)

        manager.tunnel.screen._async_open = fake_open
        asyncio.run(manager.tunnel.screen.async_handle([
            {"session_id": 12, "kind": "open"},
        ]))

        self.assertEqual(opened, [], "No consent on record here means no socket.")

    def test_frames_for_a_live_screen_are_applied_in_order(self):
        manager, _, _ = make_manager(pending=[dict(EMPTY_PAYLOAD, active=[_active()])])
        asyncio.run(manager.async_poll_pending())
        seen = []

        async def fake_open(session_id):
            seen.append(("open", session_id))

        async def fake_send(session_id, payload):
            seen.append(("frame", payload))

        manager.tunnel.screen._async_open = fake_open
        manager.tunnel.screen._async_send = fake_send
        asyncio.run(manager.tunnel.screen.async_handle([
            {"session_id": 88, "kind": "open"},
            {"session_id": 88, "kind": "frame", "payload": "a"},
            {"session_id": 88, "kind": "frame", "payload": "b"},
        ]))

        self.assertEqual(seen, [("open", "88"), ("frame", "a"), ("frame", "b")])


class PollFallbackTest(unittest.TestCase):
    def test_an_api_client_without_the_full_poll_still_works(self):
        api = FakeApiClient()
        manager, _, _ = make_manager(api=api)

        self.assertTrue(asyncio.run(manager.tunnel.async_pump_once()))
        self.assertEqual(api.polled, 1)

    def test_frames_from_the_full_poll_reach_the_socket_bridge(self):
        api = FakeApiClient(pending=[dict(EMPTY_PAYLOAD, active=[_active()])])
        handled = []

        async def poll_access_payload(installation_id):
            return {"requests": [], "frames": [{"session_id": 88, "kind": "open"}]}

        api.poll_access_payload = poll_access_payload
        manager, _, _ = make_manager(api=api)
        asyncio.run(manager.async_poll_pending())

        async def fake_handle(frames):
            handled.extend(frames)

        manager.tunnel.screen.async_handle = fake_handle
        asyncio.run(manager.tunnel.async_pump_once())

        self.assertEqual(handled, [{"session_id": 88, "kind": "open"}])


if __name__ == "__main__":
    unittest.main()
