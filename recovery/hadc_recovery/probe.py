"""Is Home Assistant Core serving?

`GET /auth/providers` answers 200 with no credential and writes nothing to
Core's log. Never probe `/api/`: without a token it is a 401, and every 401
goes through Core's ban middleware -- a failed-login warning, a notification in
front of the homeowner, and, with `login_attempts_threshold` set, a ban of this
add-on's address. `/api/onboarding` is no use either: 404 once onboarded.

Only an HTTP answer counts. During Supervisor boot a systemd socket holds :8123,
so a bare TCP connect can succeed while Core is not running.
"""
from __future__ import annotations

import urllib.error
import urllib.request

CORE_PROBE_URL = "http://homeassistant:8123/auth/providers"


def core_serving(url: str = CORE_PROBE_URL, timeout: float = 5, opener=None) -> bool:
    open_ = opener or urllib.request.urlopen
    request = urllib.request.Request(url, method="GET")
    request.add_header("User-Agent", "ha-dispatch-recovery")
    try:
        with open_(request, timeout=timeout) as response:
            return response.status == 200
    except urllib.error.HTTPError:
        return False
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
