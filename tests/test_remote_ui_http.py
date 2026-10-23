"""Remote UI: the http block Home Assistant needs, and the house's status report.

Remote UI arrives through a proxy the homeowner does not run, so Home Assistant
must be told which peers may set ``X-Forwarded-For``. That is an edit to the
homeowner's own ``configuration.yaml``, which is the riskiest thing this
integration does to a house, so what is asserted here is mostly restraint:

  - **THE GUARD.** A ``configuration.yaml`` with comments, ``!include`` and
    ``!secret`` lines and no ``http:`` key gains exactly one comment line and
    one include line, and **the original bytes are a prefix of the new file**.
    A writer that parsed and re-dumped the YAML would lose every comment and
    fail that assertion; the expected text below is written by hand, not
    derived from the module's own constants.
  - an ``http:`` block that is not ours is never touched, in any of the forms
    it takes, and is reported ``present_unmanaged`` for a technician;
  - ``http.yaml`` is rewritten only when its content differs, and a rewrite
    (or a first include) is reported as ``restart_required`` until Home
    Assistant has actually restarted -- the agent never restarts it itself;
  - the status report carries the contract's fields, goes out on change and
    at least every ten minutes, and cannot raise.
"""
from __future__ import annotations

import datetime as dt
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from test_remote_access import _load  # noqa: E402 -- shares the loader
from test_remote_ui import (  # noqa: E402
    FAR_FUTURE,
    HOSTNAME,
    OLD_MTIME,
    FakeClient,
    FakeCoordinator,
    FakeHass,
    FakeRemoteUiApi,
    desired_state,
    run,
    settle,
)

api_client = _load("api_client")
const = _load("const")
http_config = _load("http_config")
remote_ui = _load("remote_ui")

HADispatchRemoteUI = remote_ui.HADispatchRemoteUI

# A realistic configuration.yaml: comments, includes, a secret, mixed
# formatting, a trailing blank line. No http: key.
HOMEOWNER_CONFIG = (
    "# Loads default set of integrations. Do not remove.\n"
    "default_config:\n"
    "\n"
    "# Text to speech\n"
    "tts:\n"
    "  - platform: google_translate\n"
    "\n"
    "automation: !include automations.yaml\n"
    "script: !include scripts.yaml\n"
    "scene: !include scenes.yaml   # my scenes\n"
    "mqtt:\n"
    "  password: !secret mqtt_password\n"
    "#http:\n"
    "#  ip_ban_enabled: true\n"
    "\n"
)

# What the contract says gets appended, spelled out by hand.
EXPECTED_SUFFIX = (
    "\n# Managed by HA Dispatch Client (remote access): trusted proxies for the relay.\n"
    "http: !include ha_dispatch_client/http.yaml\n"
)

PROXIES = ["173.245.48.0/20", "2400:cb00::/32"]

EXPECTED_HTTP_YAML = (
    "# Managed by HA Dispatch Client. Rewritten when the trusted proxy list changes.\n"
    "use_x_forwarded_for: true\n"
    "trusted_proxies:\n"
    "  - 173.245.48.0/20\n"
    "  - 2400:cb00::/32\n"
)


def paths(tmp: Path):
    return tmp / "ha_dispatch_client" / "http.yaml", tmp / "configuration.yaml"


class AppendOnlyTest(unittest.TestCase):
    def apply(self, tmp: Path, original: bytes, proxies=PROXIES):
        http_yaml, configuration = paths(tmp)
        configuration.write_bytes(original)
        return http_config.apply_http_config(http_yaml, configuration, proxies), http_yaml, configuration

    def test_a_homeowners_file_gains_exactly_the_include_and_nothing_else(self):
        """THE GUARD: the original bytes are a prefix of the new file."""
        with tempfile.TemporaryDirectory() as tmp:
            original = HOMEOWNER_CONFIG.encode()
            (block, changed), http_yaml, configuration = self.apply(Path(tmp), original)
            after = configuration.read_bytes()

            self.assertTrue(after.startswith(original), "the homeowner's bytes must survive untouched")
            self.assertEqual(after[len(original):].decode(), EXPECTED_SUFFIX)
            self.assertEqual((block, changed), ("managed", True))
            # The things a parse-and-dump would have lost, named one by one.
            for kept in ("# Loads default set", "!include automations.yaml", "!secret mqtt_password",
                         "# my scenes", "#http:"):
                self.assertIn(kept, after.decode())
            self.assertEqual(http_yaml.read_text(), EXPECTED_HTTP_YAML)

    def test_a_file_with_no_final_newline_does_not_get_our_comment_glued_to_its_last_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = b"default_config:\nlogger:\n  default: warning"
            _, _, configuration = self.apply(Path(tmp), original)
            after = configuration.read_bytes()
            self.assertTrue(after.startswith(original))
            self.assertEqual(
                after.decode(),
                "default_config:\nlogger:\n  default: warning\n" + EXPECTED_SUFFIX,
            )

    def test_windows_line_endings_and_a_byte_order_mark_are_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = b"\xef\xbb\xbfdefault_config:\r\n# note\r\n"
            (block, _), _, configuration = self.apply(Path(tmp), original)
            self.assertEqual(block, "managed")
            self.assertTrue(configuration.read_bytes().startswith(original))

    def test_the_second_application_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, http_yaml, configuration = self.apply(Path(tmp), HOMEOWNER_CONFIG.encode())
            after_first = configuration.read_bytes()
            os.utime(http_yaml, (OLD_MTIME, OLD_MTIME))
            os.utime(configuration, (OLD_MTIME, OLD_MTIME))

            block, changed = http_config.apply_http_config(http_yaml, configuration, PROXIES)

            self.assertEqual((block, changed), ("managed", False))
            self.assertEqual(configuration.read_bytes(), after_first)
            self.assertEqual(int(http_yaml.stat().st_mtime), OLD_MTIME)
            self.assertEqual(int(configuration.stat().st_mtime), OLD_MTIME)
            self.assertEqual(after_first.count(b"http: !include"), 1)

    def test_a_symlinked_configuration_stays_a_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            real = tmp / "elsewhere" / "configuration.yaml"
            real.parent.mkdir()
            real.write_bytes(HOMEOWNER_CONFIG.encode())
            _, configuration = paths(tmp)
            configuration.symlink_to(real)

            http_config.apply_http_config(tmp / "ha_dispatch_client" / "http.yaml", configuration, PROXIES)

            self.assertTrue(configuration.is_symlink())
            self.assertTrue(real.read_text().endswith(EXPECTED_SUFFIX))

    def test_the_file_keeps_its_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, configuration = self.apply(Path(tmp), HOMEOWNER_CONFIG.encode())
            os.chmod(configuration, 0o640)
            http_config.apply_http_config(
                Path(tmp) / "ha_dispatch_client" / "http.yaml", configuration, PROXIES + ["10.0.0.0/8"]
            )
            # Our line was already there; nothing rewrote it, and nothing loosened it.
            self.assertEqual(configuration.stat().st_mode & 0o777, 0o640)

    def test_http_yaml_is_written_before_anything_includes_it(self):
        """An include of a missing file stops Home Assistant loading its configuration."""
        with tempfile.TemporaryDirectory() as tmp:
            http_yaml, configuration = paths(Path(tmp))
            configuration.write_bytes(HOMEOWNER_CONFIG.encode())
            with mock.patch.object(http_config, "write_if_changed", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    http_config.apply_http_config(http_yaml, configuration, PROXIES)
            self.assertEqual(configuration.read_bytes(), HOMEOWNER_CONFIG.encode())
            self.assertFalse(http_yaml.exists())


class ForeignHttpBlockTest(unittest.TestCase):
    FOREIGN = {
        "a block with children": "default_config:\nhttp:\n  ip_ban_enabled: true\n  login_attempts_threshold: 5\n",
        "a block with a comment after the key": "http:   # reverse proxy\n  use_x_forwarded_for: true\n",
        "an include of another file": "http: !include my_http.yaml\n",
        "a flow mapping": "http: {server_port: 8123}\n",
        "a lookalike of our include": "http: !include ha_dispatch_client/http.yaml.bak\n",
        "the key last in the file": "default_config:\nhttp:\n  cors_allowed_origins:\n    - https://example.com",
    }

    def test_an_http_block_that_is_not_ours_is_never_touched(self):
        for name, text in self.FOREIGN.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                http_yaml, configuration = paths(Path(tmp))
                configuration.write_text(text)
                os.utime(configuration, (OLD_MTIME, OLD_MTIME))

                block, changed = http_config.apply_http_config(http_yaml, configuration, PROXIES)

                self.assertEqual(block, "present_unmanaged")
                self.assertFalse(changed, "nothing Home Assistant reads was written")
                self.assertEqual(configuration.read_text(), text)
                self.assertEqual(int(configuration.stat().st_mtime), OLD_MTIME)
                # The list is on disk for the technician who has to merge it.
                self.assertEqual(http_yaml.read_text(), EXPECTED_HTTP_YAML)

    def test_things_that_only_look_like_an_http_key_do_not_count(self):
        decoys = "# http:\n#   ip_ban_enabled: true\nsensor:\n  - platform: template\n    http: not-a-top-level-key\nhttpx:\n  a: 1\n"
        with tempfile.TemporaryDirectory() as tmp:
            http_yaml, configuration = paths(Path(tmp))
            configuration.write_text(decoys)
            block, _ = http_config.apply_http_config(http_yaml, configuration, PROXIES)
            self.assertEqual(block, "managed")
            self.assertTrue(configuration.read_text().startswith(decoys))

    def test_our_own_include_is_recognised_with_trailing_comment_or_spacing(self):
        for line in (
            "http: !include ha_dispatch_client/http.yaml\n",
            "http:   !include   ha_dispatch_client/http.yaml   # ours\n",
        ):
            with self.subTest(line), tempfile.TemporaryDirectory() as tmp:
                http_yaml, configuration = paths(Path(tmp))
                configuration.write_text("default_config:\n" + line)
                block, _ = http_config.apply_http_config(http_yaml, configuration, PROXIES)
                self.assertEqual(block, "managed")
                self.assertEqual(configuration.read_text(), "default_config:\n" + line)

    def test_no_configuration_file_is_reported_missing_and_none_is_invented(self):
        with tempfile.TemporaryDirectory() as tmp:
            http_yaml, configuration = paths(Path(tmp))
            block, changed = http_config.apply_http_config(http_yaml, configuration, PROXIES)
            self.assertEqual((block, changed), ("missing", False))
            self.assertFalse(configuration.exists())


class HttpYamlTest(unittest.TestCase):
    def test_the_file_is_one_cidr_per_line_and_nothing_malformed_is_written(self):
        cleaned = http_config.clean_trusted_proxies(
            ["173.245.48.0/20", "not an address", 7, None, "173.245.48.0/20", "1.2.3.4", "2400:cb00::/32"]
        )
        self.assertEqual(cleaned, ["173.245.48.0/20", "1.2.3.4/32", "2400:cb00::/32"])
        self.assertEqual(
            http_config.render_http_yaml(cleaned),
            "# Managed by HA Dispatch Client. Rewritten when the trusted proxy list changes.\n"
            "use_x_forwarded_for: true\ntrusted_proxies:\n  - 173.245.48.0/20\n  - 1.2.3.4/32\n  - 2400:cb00::/32\n",
        )
        self.assertEqual(http_config.clean_trusted_proxies(None), [])
        self.assertEqual(http_config.clean_trusted_proxies("173.245.48.0/20"), [])

    def test_the_file_parses_as_the_http_settings_it_claims_to_be(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed here")
        parsed = yaml.safe_load(EXPECTED_HTTP_YAML)
        self.assertEqual(parsed, {"use_x_forwarded_for": True, "trusted_proxies": PROXIES})


# --- through the manager --------------------------------------------------------


class ManagerTest(unittest.TestCase):
    def make(self, tmp: Path, api, started_at=None, clock=None, config_text=HOMEOWNER_CONFIG):
        hass = FakeHass(tmp)
        if config_text is not None:
            (tmp / "configuration.yaml").write_text(config_text)
        clients = []

        def factory(runner, context, server, port):
            client = FakeClient(runner, context, server, port)
            clients.append(client)
            return client

        return HADispatchRemoteUI(
            hass, FakeCoordinator(api), client_factory=factory, delay=lambda: 0,
            started_at=started_at or (lambda: time.time() - 3600),
            clock=clock or time.time,
        ), clients

    def test_a_first_poll_writes_http_yaml_adds_the_include_and_asks_for_a_restart(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                api = FakeRemoteUiApi(desired=desired_state())
                manager, _ = self.make(tmp, api)
                await manager.async_poll()
                await settle()

                http_yaml, configuration = paths(tmp)
                self.assertEqual(http_yaml.read_text(), EXPECTED_HTTP_YAML)
                self.assertTrue(configuration.read_text().endswith(EXPECTED_SUFFIX))
                self.assertEqual(manager.http_block, "managed")
                self.assertTrue(manager.restart_required)
                await manager.async_unload()

        run(scenario())

    def test_a_changed_range_set_rewrites_http_yaml_and_asks_for_a_restart_again(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                api = FakeRemoteUiApi(desired=desired_state())
                manager, _ = self.make(tmp, api)
                await manager.async_poll()
                await settle()
                http_yaml, configuration = paths(tmp)

                # Home Assistant restarts (the process is newer than the file)...
                os.utime(http_yaml, (OLD_MTIME, OLD_MTIME))
                restarted, _ = self.make(tmp, api, started_at=lambda: time.time(), config_text=None)
                await restarted.async_poll()
                self.assertFalse(restarted.restart_required, "restarted since the write: nothing is owed")
                self.assertEqual(int(http_yaml.stat().st_mtime), OLD_MTIME, "same list, so no rewrite")

                # ...then Cloudflare adds a range.
                api.desired["trusted_proxies"] = PROXIES + ["198.41.128.0/17"]
                api.desired["ranges_version"] = 8
                api.desired["config_version"] = 13
                await restarted.async_poll()

                self.assertEqual(http_yaml.read_text(), EXPECTED_HTTP_YAML + "  - 198.41.128.0/17\n")
                self.assertGreater(int(http_yaml.stat().st_mtime), OLD_MTIME)
                self.assertTrue(restarted.restart_required)
                self.assertEqual(configuration.read_text().count("http: !include"), 1)
                await restarted.async_unload()
                await manager.async_unload()

        run(scenario())

    def test_an_unchanged_range_set_does_not_rewrite_anything(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                api = FakeRemoteUiApi(desired=desired_state())
                manager, _ = self.make(tmp, api)
                await manager.async_poll()
                await settle()
                http_yaml, configuration = paths(tmp)
                for path in (http_yaml, configuration):
                    os.utime(path, (OLD_MTIME, OLD_MTIME))
                before = configuration.read_bytes()

                api.desired["config_version"] = 13  # a version bump for some other reason
                await manager.async_poll()
                await manager.async_poll()

                self.assertEqual(int(http_yaml.stat().st_mtime), OLD_MTIME)
                self.assertEqual(int(configuration.stat().st_mtime), OLD_MTIME)
                self.assertEqual(configuration.read_bytes(), before)
                await manager.async_unload()

        run(scenario())

    def test_a_reload_of_the_integration_is_not_a_restart(self):
        """A fresh manager in the same Home Assistant process still owes the restart."""
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                api = FakeRemoteUiApi(desired=desired_state())
                started = time.time() - 3600
                manager, _ = self.make(tmp, api, started_at=lambda: started)
                await manager.async_poll()
                await settle()
                await manager.async_unload()

                reloaded, _ = self.make(tmp, api, started_at=lambda: started, config_text=None)
                await reloaded.async_poll()
                self.assertTrue(reloaded.restart_required)
                await reloaded.async_unload()

        run(scenario())

    def test_a_house_with_its_own_http_block_is_left_alone_and_says_so(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                own = "default_config:\nhttp:\n  ip_ban_enabled: true\n"
                api = FakeRemoteUiApi(desired=desired_state())
                manager, _ = self.make(tmp, api, config_text=own)
                with self.assertLogs(remote_ui._LOGGER, level="WARNING") as logs:
                    await manager.async_poll()
                    await settle()

                self.assertEqual(paths(tmp)[1].read_text(), own)
                self.assertEqual(manager.http_block, "present_unmanaged")
                self.assertFalse(manager.restart_required)
                self.assertTrue(any("already has its own http: block" in line for line in logs.output))
                self.assertEqual(api.status_posts[-1]["http_block"], "present_unmanaged")
                self.assertFalse(api.status_posts[-1]["restart_required"])
                await manager.async_unload()

        run(scenario())

    def test_without_trusted_proxies_nothing_is_written(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                api = FakeRemoteUiApi(desired=desired_state(trusted_proxies=[]))
                manager, _ = self.make(tmp, api)
                await manager.async_poll()
                await settle()
                self.assertFalse(paths(tmp)[0].exists())
                self.assertEqual(paths(tmp)[1].read_text(), HOMEOWNER_CONFIG)
                self.assertEqual(api.status_posts[-1]["http_block"], "missing")
                await manager.async_unload()

        run(scenario())

    def test_switching_off_leaves_http_yaml_and_the_include_in_place(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                api = FakeRemoteUiApi(desired=desired_state())
                manager, _ = self.make(tmp, api)
                await manager.async_poll()
                await settle()
                after_on = paths(tmp)[1].read_bytes()

                api.desired = {"enabled": False}
                await manager.async_poll()

                self.assertEqual(paths(tmp)[0].read_text(), EXPECTED_HTTP_YAML)
                self.assertEqual(paths(tmp)[1].read_bytes(), after_on)

        run(scenario())


class StatusReportTest(unittest.TestCase):
    def make(self, tmp, api, clock):
        return ManagerTest().make(tmp, api, clock=clock)

    def test_the_payload_carries_the_contracts_fields(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                api = FakeRemoteUiApi(desired=desired_state())
                manager, _ = self.make(tmp, api, time.time)
                await manager.async_poll()
                await settle()
                await manager.async_poll()  # the dial has settled; the next report says so

                self.assertGreaterEqual(len(api.status_posts), 1)
                sent = api.status_posts[-1]
                self.assertEqual(
                    sorted(sent),
                    ["certificate_expires_at", "connected", "dials", "drops", "http_block",
                     "last_error", "ranges_version", "restart_required", "token_valid"],
                )
                self.assertIs(sent["connected"], True)
                self.assertEqual(sent["dials"], 1)
                self.assertEqual(sent["drops"], 0)
                self.assertIsNone(sent["last_error"])
                self.assertEqual(sent["http_block"], "managed")
                self.assertEqual(sent["ranges_version"], 7)
                self.assertIs(sent["restart_required"], True)
                self.assertEqual(sent["token_valid"], FAR_FUTURE)
                # The fake CA signs for 1825 days; the report says so in ISO 8601 UTC.
                expires = dt.datetime.strptime(sent["certificate_expires_at"], "%Y-%m-%dT%H:%M:%SZ")
                days = (expires - dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)).days
                self.assertTrue(1823 <= days <= 1825, days)
                await manager.async_unload()

        run(scenario())

    def test_it_goes_on_change_and_at_least_every_ten_minutes(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                now = [10_000.0]
                api = FakeRemoteUiApi(desired=desired_state())
                manager, clients = self.make(tmp, api, lambda: now[0])
                await manager.async_poll()
                await settle()
                await manager.async_poll()
                settled = len(api.status_posts)

                # Nothing changed: nothing sent, poll after poll, for nine minutes.
                for _ in range(9):
                    now[0] += 60
                    await manager.async_poll()
                self.assertEqual(len(api.status_posts), settled)

                # The tenth minute is the heartbeat, identical payload and all.
                now[0] += 60
                await manager.async_poll()
                self.assertEqual(len(api.status_posts), settled + 1)
                self.assertEqual(api.status_posts[-1], api.status_posts[-2])

                # A drop is a change and goes at once, not at the next heartbeat.
                clients[0].drop()
                await settle()
                now[0] += 60
                await manager.async_poll()
                self.assertEqual(len(api.status_posts), settled + 2)
                self.assertEqual(api.status_posts[-1]["drops"], 1)
                await manager.async_unload()

        run(scenario())

    def test_a_verbose_dial_error_is_cut_to_what_the_server_accepts(self):
        """Dispatch validates last_error at 500 characters and answers 422 to
        more. A house whose dial error is a long traceback-ish message must
        still get its report through: the message is cut, never the report."""
        with tempfile.TemporaryDirectory() as tmp:
            manager, _ = self.make(Path(tmp), FakeRemoteUiApi(desired=desired_state()), time.time)

            manager.last_error = "ConnectionRefusedError: " + "x" * 700
            sent = manager.status_payload()["last_error"]
            self.assertEqual(500, len(sent))  # the server's limit, written by hand
            self.assertTrue(sent.startswith("ConnectionRefusedError: xxxx"))

            # A short message and no message both pass through as they are.
            manager.last_error = "TimeoutError"
            self.assertEqual("TimeoutError", manager.status_payload()["last_error"])
            manager.last_error = None
            self.assertIsNone(manager.status_payload()["last_error"])

    def test_a_house_that_is_off_or_a_server_that_predates_it_hears_nothing(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                off = FakeRemoteUiApi(desired={"enabled": False})
                manager, _ = self.make(tmp, off, time.time)
                await manager.async_poll()
                self.assertEqual(off.status_posts, [])

                old = FakeRemoteUiApi(unavailable=True)
                manager, _ = self.make(tmp, old, time.time)
                await manager.async_poll()
                self.assertEqual(old.status_posts, [])

        run(scenario())

    def test_a_404_on_the_status_path_is_remembered_and_a_failure_cannot_raise(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                api = FakeRemoteUiApi(desired=desired_state())
                calls = []

                async def gone(installation_id, payload):
                    calls.append(payload)
                    raise api_client.RemoteUiUnavailable("HTTP 404")

                api.report_remote_ui_status = gone
                manager, _ = self.make(tmp, api, time.time)
                await manager.async_poll()
                await settle()
                await manager.async_poll()
                await manager.async_poll()
                self.assertEqual(len(calls), 1, "asked once, then never again this run")
                await manager.async_unload()

                api = FakeRemoteUiApi(desired=desired_state())

                async def boom(installation_id, payload):
                    raise RuntimeError("the wire caught fire")

                api.report_remote_ui_status = boom
                manager, _ = self.make(tmp, api, time.time)
                await manager.async_poll()  # must not raise
                await manager.async_report_status()
                await manager.async_unload()

        run(scenario())


class CertificateExpiryTest(unittest.TestCase):
    def test_the_expiry_is_read_from_the_certificate_in_iso_8601_utc(self):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOSTNAME)])
        cert = (
            x509.CertificateBuilder()
            .subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1)
            .not_valid_before(dt.datetime(2026, 10, 22, tzinfo=dt.timezone.utc))
            .not_valid_after(dt.datetime(2031, 10, 21, 13, 45, 7, tzinfo=dt.timezone.utc))
            .sign(key, hashes.SHA256())
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fullchain.pem"
            path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            self.assertEqual(remote_ui.certificate_expires_at(path), "2031-10-21T13:45:07Z")
            path.write_text("not a certificate")
            self.assertIsNone(remote_ui.certificate_expires_at(path))
            self.assertIsNone(remote_ui.certificate_expires_at(Path(tmp) / "absent.pem"))


if __name__ == "__main__":
    unittest.main()
