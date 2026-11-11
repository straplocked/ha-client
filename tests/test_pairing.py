"""Tests for pairing-code enrolment.

Onboarding starts with the installer making a one-time code in Dispatch; the
homeowner types it into Home Assistant. What matters:

- the code is normalised exactly as the server normalises it, so the way a
  person types it (lower case, a dash, an O for a zero) does not matter;
- the code reaches the server in the registration body, and nothing else
  about registration changes;
- a refused code is a refused code -- "ask your installer for a new one" --
  and never mistaken for a connection problem or a client_id clash.

config_flow.py itself imports voluptuous, which is not installed here, so it
is verified by inspection only. Every decision it makes that is worth testing
is made by the functions under test here.
"""
import asyncio
import importlib
import json
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
pairing = _load("pairing")


# --- fake aiohttp ------------------------------------------------------------


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status = status
        self.url = "https://app.hadispatch.app/api/v1/installations/register"
        self._payload = payload if payload is not None else {}
        self._text = text

    async def json(self):
        return self._payload

    async def text(self):
        return self._text

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

    def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return FakeRequestContext(self.response)


def register(response, **kwargs):
    session = FakeSession(response)
    client = api_client.HADispatchApiClient(
        session=session, server_url="https://app.hadispatch.app/"
    )
    defaults = {
        "client_id": "11111111-1111-4111-8111-111111111111",
        "hostname": "homeassistant.local:8123",
        "name": "Test Home",
    }
    result = asyncio.run(client.register_installation(**{**defaults, **kwargs}))
    return result, session


REGISTERED = {"installation_id": 42, "access_token": "tok-42"}


# --- normalisation -------------------------------------------------------------


class NormalisationTest(unittest.TestCase):
    """Hand-written cases, because the server applies the same rules."""

    def test_lower_case_and_the_dash_are_ignored(self):
        self.assertEqual("ABCDEFGH", pairing.normalize_pairing_code("abcd-efgh"))

    def test_an_o_is_read_as_a_zero(self):
        self.assertEqual("0", pairing.normalize_pairing_code("O"))
        self.assertEqual("0", pairing.normalize_pairing_code("o"))

    def test_an_i_or_an_l_is_read_as_a_one(self):
        self.assertEqual("1", pairing.normalize_pairing_code("I"))
        self.assertEqual("1", pairing.normalize_pairing_code("L"))
        self.assertEqual("1", pairing.normalize_pairing_code("i"))
        self.assertEqual("1", pairing.normalize_pairing_code("l"))

    def test_spaces_anywhere_are_ignored(self):
        self.assertEqual("7K3M9P2Q", pairing.normalize_pairing_code("  7k3m 9p2q "))

    def test_a_code_read_out_with_every_lookalike(self):
        self.assertEqual("10110A0B", pairing.normalize_pairing_code("lO-iL oa0b"))

    def test_nothing_typed_is_an_empty_code_not_a_crash(self):
        self.assertEqual("", pairing.normalize_pairing_code(None))
        self.assertEqual("", pairing.normalize_pairing_code("  - "))

    def test_well_formed_means_eight_characters_of_the_alphabet(self):
        self.assertTrue(pairing.is_well_formed_pairing_code("7K3M9P2Q"))
        self.assertTrue(pairing.is_well_formed_pairing_code("00000000"))
        # Seven, nine.
        self.assertFalse(pairing.is_well_formed_pairing_code("7K3M9P2"))
        self.assertFalse(pairing.is_well_formed_pairing_code("7K3M9P2QR"))
        # U is not in Crockford base32; neither are O, I and L, which
        # normalisation has already mapped away by the time this is asked.
        self.assertFalse(pairing.is_well_formed_pairing_code("7K3M9P2U"))
        self.assertFalse(pairing.is_well_formed_pairing_code("7K3M9P2O"))
        self.assertFalse(pairing.is_well_formed_pairing_code(""))

    def test_the_alphabet_is_crockford_base32(self):
        self.assertEqual("0123456789ABCDEFGHJKMNPQRSTVWXYZ", const.PAIRING_CODE_ALPHABET)
        self.assertEqual(8, const.PAIRING_CODE_LENGTH)

    def test_it_is_shown_the_way_dispatch_shows_it(self):
        self.assertEqual("7K3M-9P2Q", pairing.format_pairing_code("7k3m9p2q"))

    def test_the_default_server_is_the_hosted_one(self):
        self.assertEqual("https://app.hadispatch.app", const.DEFAULT_SERVER_URL)


# --- the request -----------------------------------------------------------------


class RegistrationWireTest(unittest.TestCase):
    def test_the_normalised_code_rides_the_registration_body(self):
        result, session = register(
            FakeResponse(201, REGISTERED), pairing_code="7k3m-9p2q"
        )

        self.assertEqual(REGISTERED, result)
        call = session.calls[0]
        self.assertEqual(
            "https://app.hadispatch.app/api/v1/installations/register", call["url"]
        )
        self.assertEqual("7K3M9P2Q", call["json"]["pairing_code"])
        # Everything registration sent before is still sent.
        self.assertEqual("11111111-1111-4111-8111-111111111111", call["json"]["client_id"])
        self.assertEqual("homeassistant.local:8123", call["json"]["hostname"])
        self.assertEqual("Test Home", call["json"]["name"])
        # No secret was given, so none is sent: the code stands in for it.
        self.assertNotIn("X-Registration-Secret", call["headers"])

    def test_without_a_code_the_body_has_no_pairing_field_at_all(self):
        # The legacy path is unchanged: a server predating pairing must not be
        # handed a field it might validate.
        _, session = register(FakeResponse(201, REGISTERED), registration_secret="s3cret")
        call = session.calls[0]
        self.assertNotIn("pairing_code", call["json"])
        self.assertEqual("s3cret", call["headers"]["X-Registration-Secret"])

    def test_a_refused_code_is_a_pairing_code_error(self):
        with self.assertRaises(api_client.PairingCodeError):
            register(
                FakeResponse(422, text='{"error": "invalid_pairing_code"}'),
                pairing_code="7K3M9P2Q",
            )

    def test_a_refused_code_is_never_read_as_a_client_id_clash(self):
        # The old 422 handling matched "client_id" as a substring.
        body = '{"error": "invalid_pairing_code", "client_id": "1111"}'
        with self.assertRaises(api_client.PairingCodeError):
            register(FakeResponse(422, text=body), pairing_code="7K3M9P2Q")

    def test_a_client_id_clash_is_still_a_client_id_clash(self):
        body = '{"message": "The client id has already been taken.", "errors": {"client_id": ["taken"]}}'
        with self.assertRaises(api_client.ClientIdTakenError):
            register(FakeResponse(422, text=body), pairing_code="7K3M9P2Q")

    def test_throttling_is_its_own_error(self):
        with self.assertRaises(api_client.RegistrationThrottledError):
            register(FakeResponse(429, text="Too Many Attempts."), pairing_code="7K3M9P2Q")

    def test_the_422_body_is_matched_on_the_field_not_a_substring(self):
        self.assertTrue(api_client.is_invalid_pairing_code_body('{"error":"invalid_pairing_code"}'))
        self.assertTrue(api_client.is_invalid_pairing_code_body({"error": "invalid_pairing_code"}))
        self.assertFalse(api_client.is_invalid_pairing_code_body(
            '{"message": "The pairing code field must be 8 characters."}'
        ))
        self.assertFalse(api_client.is_invalid_pairing_code_body("invalid_pairing_code"))
        self.assertFalse(api_client.is_invalid_pairing_code_body("<html>"))


# --- which error the form shows ----------------------------------------------------


class FormErrorTest(unittest.TestCase):
    def test_each_failure_shows_its_own_error(self):
        cases = [
            (api_client.PairingCodeError("x"), "invalid_pairing_code"),
            (api_client.RegistrationThrottledError("x"), "too_many_attempts"),
            (api_client.RegistrationSecretError("x"), "invalid_registration_secret"),
            (aiohttp.ClientConnectionError("x"), "cannot_connect"),
            (TimeoutError(), "cannot_connect"),
            (KeyError("installation_id"), "unknown"),
        ]
        for err, expected in cases:
            with self.subTest(err=type(err).__name__):
                self.assertEqual(expected, pairing.registration_error_key(err))

    def test_every_error_the_form_can_show_has_words(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "custom_components", "ha_dispatch_client", "strings.json",
        )
        with open(path) as handle:
            errors = json.load(handle)["config"]["error"]
        for key in (
            pairing.ERROR_INVALID_PAIRING_CODE,
            pairing.ERROR_TOO_MANY_ATTEMPTS,
            pairing.ERROR_INVALID_REGISTRATION_SECRET,
            pairing.ERROR_CANNOT_CONNECT,
            pairing.ERROR_UNKNOWN,
        ):
            with self.subTest(key=key):
                self.assertIn(key, errors)
        self.assertEqual(
            "That code didn't work. It may have expired or already been used. "
            "Ask your installer for a new one.",
            errors["invalid_pairing_code"],
        )

    def test_the_first_step_asks_for_the_code_and_nothing_else(self):
        # config_flow.py cannot be imported here, so this reads strings.json:
        # the pair step's only field is the code.
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "custom_components", "ha_dispatch_client", "strings.json",
        )
        with open(path) as handle:
            steps = json.load(handle)["config"]["step"]
        self.assertEqual(["pairing_code"], list(steps["pair"]["data"]))
        self.assertEqual(
            ["pair", "custom_server"], list(steps["user"]["menu_options"])
        )


if __name__ == "__main__":
    unittest.main()
