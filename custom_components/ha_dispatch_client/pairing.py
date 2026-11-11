"""Pairing codes: how a new Home Assistant joins the installer's fleet.

The installer makes a one-time code in Dispatch and reads it out (or texts
it); the homeowner types it into Home Assistant's setup dialog, and the new
installation lands assigned to that installer. No server URL to copy, no
enrolment secret to keep.

Kept apart from config_flow.py on purpose: the flow imports voluptuous, which
is not installed in the dev environment, so anything the flow decides that is
worth testing lives here instead.

The normalisation is a contract with the server, which applies exactly the
same rules before hashing and comparing:

- uppercase;
- spaces and hyphens removed (the code is shown as ``XXXX-XXXX``);
- ``O`` read as ``0``, ``I`` and ``L`` read as ``1`` -- Crockford base32 has
  no O, I or L precisely because people mistake them for the digits.

The code itself is never stored here. It is single use: once it has been
claimed it is worth nothing, so there is nothing to keep it for.
"""
from __future__ import annotations

import aiohttp

from .const import PAIRING_CODE_ALPHABET, PAIRING_CODE_LENGTH

# Characters people type that mean something else in this alphabet.
_LOOKALIKES = str.maketrans({"O": "0", "I": "1", "L": "1"})

# Form error keys, as named in strings.json's config.error block.
ERROR_INVALID_PAIRING_CODE = "invalid_pairing_code"
ERROR_INVALID_REGISTRATION_SECRET = "invalid_registration_secret"
ERROR_TOO_MANY_ATTEMPTS = "too_many_attempts"
ERROR_CANNOT_CONNECT = "cannot_connect"
ERROR_UNKNOWN = "unknown"


def normalize_pairing_code(raw: object) -> str:
    """The code as the server compares it. Never raises; may return ''."""
    if raw is None:
        return ""
    text = "".join(str(raw).split())  # every kind of whitespace
    return text.replace("-", "").upper().translate(_LOOKALIKES)


def is_well_formed_pairing_code(code: str) -> bool:
    """Whether a NORMALISED code could be a real one.

    Lets the form catch a short or mistyped code without a round trip -- and
    without spending one of the server's throttled attempts on it.
    """
    return len(code) == PAIRING_CODE_LENGTH and all(
        char in PAIRING_CODE_ALPHABET for char in code
    )


def format_pairing_code(code: str) -> str:
    """``ABCD1234`` -> ``ABCD-1234``, the way Dispatch shows it."""
    code = normalize_pairing_code(code)
    if len(code) != PAIRING_CODE_LENGTH:
        return code
    half = PAIRING_CODE_LENGTH // 2
    return f"{code[:half]}-{code[half:]}"


def registration_error_key(err: BaseException) -> str:
    """Which form error a failed registration shows. Pure; tested directly.

    The distinction matters to the person at the screen: a bad code means
    "ask your installer for a new one", a connection failure means "check the
    internet", and showing one for the other sends them the wrong way.
    """
    # Imported here, not at module scope: api_client imports this module for
    # normalize_pairing_code, and the errors live with the requests that
    # raise them.
    from .api_client import (
        PairingCodeError,
        RegistrationSecretError,
        RegistrationThrottledError,
    )

    if isinstance(err, PairingCodeError):
        return ERROR_INVALID_PAIRING_CODE
    if isinstance(err, RegistrationThrottledError):
        return ERROR_TOO_MANY_ATTEMPTS
    if isinstance(err, RegistrationSecretError):
        return ERROR_INVALID_REGISTRATION_SECRET
    if isinstance(err, (aiohttp.ClientError, TimeoutError)):
        return ERROR_CANNOT_CONNECT
    return ERROR_UNKNOWN
