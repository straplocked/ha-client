"""Remote UI: the homeowner's own way in, through the relay.

What is asserted here is the agent's side of the slice-1 contract:

  - **off until the files are there**, and off again (loudly) when they are
    there but wrong -- a server cannot make a house reachable, and neither can
    a typo make it half-reachable;
  - the dial presents exactly what ``mint_token.py`` wrote, as bytes, with the
    matching protocol version, so the relay and the house agree;
  - a drop is followed by a redial, an expired token is not, and unload hangs
    up without touching the files.

The TLS context is built from a real self-signed certificate so a mismatched
key is caught the way it would be in the house. snitun itself is not driven
here -- the relay's own ``docker/relay/test_relay.py`` in ha-dispatch does
that, end to end, with nothing mocked -- so the client is a stand-in that
records what it was asked to do.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import tempfile
import types
import unittest
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from test_remote_access import _load  # noqa: E402 -- shares the loader

const = _load("const")
remote_ui = _load("remote_ui")

REMOTE_UI_CERT_FILE = const.REMOTE_UI_CERT_FILE
REMOTE_UI_CONFIG_FILE = const.REMOTE_UI_CONFIG_FILE
REMOTE_UI_DIR = const.REMOTE_UI_DIR
REMOTE_UI_KEY_FILE = const.REMOTE_UI_KEY_FILE
REMOTE_UI_RECONNECT_MAX = const.REMOTE_UI_RECONNECT_MAX
REMOTE_UI_RECONNECT_MIN = const.REMOTE_UI_RECONNECT_MIN

HADispatchRemoteUI = remote_ui.HADispatchRemoteUI
RemoteUIConfigError = remote_ui.RemoteUIConfigError
build_server_context = remote_ui.build_server_context
load_remote_ui_files = remote_ui.load_remote_ui_files
parse_remote_ui_config = remote_ui.parse_remote_ui_config
reconnect_delay = remote_ui.reconnect_delay
token_is_valid = remote_ui.token_is_valid

HOSTNAME = "lab1.remote.hadispatch.app"
FAR_FUTURE = 4_000_000_000


def good_config(**overrides):
    config = {
        "hostname": HOSTNAME,
        "server": "relay.hadispatch.app",
        "port": 8443,
        "token": "gAAAAABtoken",
        "aes_key": "ab" * 32,
        "aes_iv": "cd" * 16,
        "valid": FAR_FUTURE,
        "protocol_version": 2,
    }
    config.update(overrides)
    return config


def write_key_and_cert(directory: Path, hostname: str = HOSTNAME, key=None) -> None:
    key = key or ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    (directory / REMOTE_UI_CERT_FILE).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (directory / REMOTE_UI_KEY_FILE).write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


def write_house(config_dir: Path, config=None, with_cert=True) -> Path:
    directory = config_dir.joinpath(*REMOTE_UI_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / REMOTE_UI_CONFIG_FILE).write_text(json.dumps(config or good_config()))
    if with_cert:
        write_key_and_cert(directory)
    return directory


class FakeHass:
    def __init__(self, config_dir: Path, with_http: bool = True):
        self.config = types.SimpleNamespace(path=lambda *parts: str(config_dir.joinpath(*parts)))
        self.http = types.SimpleNamespace(runner=object()) if with_http else None
        self.tasks = []

    async def async_add_executor_job(self, func, *args):
        return func(*args)

    def async_create_background_task(self, coro, name=None):
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self.tasks.append(task)
        return task


class FakeClient:
    """Stands in for SniTunClientAioHttp and records what it was asked."""

    def __init__(self, runner, context, server, port):
        self.runner = runner
        self.context = context
        self.server = server
        self.port = port
        self.started = False
        self.stopped = False
        self.connects = []
        self.is_connected = False
        self.fail_next = None
        self._drop = asyncio.Event()

    async def start(self, access_list=None):
        self.started = True

    async def stop(self, *, wait=False):
        self.stopped = True
        self.is_connected = False
        self._drop.set()

    async def connect(self, fernet_key, aes_key, aes_iv, throttling=None, protocol_version=0, cipher="aes-cbc"):
        if self.fail_next is not None:
            err, self.fail_next = self.fail_next, None
            raise err
        self.connects.append((fernet_key, aes_key, aes_iv, protocol_version))
        self.is_connected = True
        self._drop = asyncio.Event()

    def wait(self):
        return self._drop.wait()

    def drop(self):
        self.is_connected = False
        self._drop.set()


def run(coro):
    return asyncio.run(coro)


async def settle(times: int = 5):
    for _ in range(times):
        await asyncio.sleep(0)


class ConfigParsingTest(unittest.TestCase):
    def test_the_minters_output_is_accepted_and_normalised(self):
        parsed = parse_remote_ui_config(good_config(aes_key="AB" * 32))
        self.assertEqual(parsed["hostname"], HOSTNAME)
        self.assertEqual(parsed["aes_key"], "ab" * 32)
        self.assertEqual(parsed["port"], 8443)
        self.assertEqual(parsed["protocol_version"], 2)

    def test_the_port_and_protocol_default_when_absent(self):
        config = good_config()
        del config["port"]
        del config["protocol_version"]
        parsed = parse_remote_ui_config(config)
        self.assertEqual(parsed["port"], 443)
        self.assertEqual(parsed["protocol_version"], 2)

    def test_every_field_the_dial_needs_is_checked(self):
        for bad in (
            good_config(hostname=""),
            good_config(server=None),
            good_config(token=""),
            good_config(aes_key="zz" * 32),
            good_config(aes_key="ab" * 16),
            good_config(aes_iv="cd" * 8),
            good_config(port=0),
            good_config(port="8443"),
            good_config(valid="soon"),
            good_config(protocol_version="2"),
            ["not", "an", "object"],
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(RemoteUIConfigError):
                    parse_remote_ui_config(bad)

    def test_token_validity_is_the_timestamp(self):
        config = good_config(valid=1000)
        self.assertTrue(token_is_valid(config, now=999))
        self.assertFalse(token_is_valid(config, now=1000))

    def test_the_redial_wait_is_a_spread_not_a_constant(self):
        rng = __import__("random").Random(7)
        delays = {reconnect_delay(rng) for _ in range(50)}
        self.assertGreater(len(delays), 1)
        self.assertTrue(all(REMOTE_UI_RECONNECT_MIN <= d <= REMOTE_UI_RECONNECT_MAX for d in delays))


class FilesTest(unittest.TestCase):
    def test_no_config_file_means_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_remote_ui_files(Path(tmp) / "nowhere"))
            self.assertIsNone(load_remote_ui_files(Path(tmp)))

    def test_a_config_without_its_certificate_is_an_error_not_silence(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = write_house(Path(tmp), with_cert=False)
            with self.assertRaises(RemoteUIConfigError) as caught:
                load_remote_ui_files(directory)
            self.assertIn(REMOTE_UI_CERT_FILE, str(caught.exception))

    def test_the_full_set_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = write_house(Path(tmp))
            config, cert, key = load_remote_ui_files(directory)
            self.assertEqual(config["hostname"], HOSTNAME)
            self.assertTrue(cert.is_file() and key.is_file())
            context = build_server_context(cert, key)
            self.assertEqual(context.minimum_version, __import__("ssl").TLSVersion.TLSv1_2)

    def test_a_key_that_does_not_match_its_certificate_is_caught_at_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = write_house(Path(tmp))
            other = ec.generate_private_key(ec.SECP256R1())
            (directory / REMOTE_UI_KEY_FILE).write_bytes(
                other.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            with self.assertRaises(RemoteUIConfigError):
                build_server_context(directory / REMOTE_UI_CERT_FILE, directory / REMOTE_UI_KEY_FILE)


class LifecycleTest(unittest.TestCase):
    def make(self, tmp: Path, with_http: bool = True, delay: float = 0):
        hass = FakeHass(tmp, with_http=with_http)
        clients = []

        def factory(runner, context, server, port):
            client = FakeClient(runner, context, server, port)
            clients.append(client)
            return client

        manager = HADispatchRemoteUI(hass, coordinator=None, client_factory=factory, delay=lambda: delay)
        return hass, manager, clients

    def test_without_the_files_nothing_is_dialled(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                hass, manager, clients = self.make(Path(tmp))
                await manager.async_load()
                self.assertFalse(manager.enabled)
                self.assertEqual(clients, [])
                await manager.async_unload()

        run(scenario())

    def test_the_dial_presents_exactly_what_was_minted(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                hass, manager, clients = self.make(Path(tmp))
                write_house(Path(tmp))
                await manager.async_load()
                await settle()
                self.assertTrue(manager.enabled)
                self.assertEqual(len(clients), 1)
                client = clients[0]
                self.assertTrue(client.started)
                self.assertIs(client.runner, hass.http.runner)
                self.assertEqual((client.server, client.port), ("relay.hadispatch.app", 8443))
                self.assertEqual(
                    client.connects,
                    [(b"gAAAAABtoken", bytes.fromhex("ab" * 32), bytes.fromhex("cd" * 16), 2)],
                )
                self.assertTrue(manager.connected)
                self.assertEqual(manager.dials, 1)
                await manager.async_unload()

        run(scenario())

    def test_a_drop_is_followed_by_a_redial(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                hass, manager, clients = self.make(Path(tmp))
                write_house(Path(tmp))
                await manager.async_load()
                await settle()
                clients[0].drop()
                await settle(10)
                self.assertEqual(manager.dials, 2)
                self.assertEqual(manager.drops, 1)
                self.assertTrue(manager.connected)
                await manager.async_unload()

        run(scenario())

    def test_a_refused_dial_is_remembered_and_waited_out(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                # A long wait, so the failure can be observed before the redial.
                hass, manager, clients = self.make(Path(tmp), delay=60)
                write_house(Path(tmp))
                # The factory runs inside async_load, so arm the failure through it.
                original = manager._client_factory

                def failing(*args):
                    client = original(*args)
                    client.fail_next = OSError("relay unreachable")
                    return client

                manager._client_factory = failing
                with self.assertLogs(remote_ui._LOGGER, level=logging.WARNING):
                    await manager.async_load()
                    await settle(10)
                self.assertEqual(manager.dials, 0)
                self.assertFalse(manager.connected)
                self.assertIn("relay unreachable", manager.last_error or "")
                # Still enabled: it is waiting to redial, not given up.
                self.assertTrue(manager.enabled)
                await manager.async_unload()

        run(scenario())

    def test_a_dial_that_succeeds_clears_the_last_error(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                hass, manager, clients = self.make(Path(tmp))
                write_house(Path(tmp))
                original = manager._client_factory

                def failing(*args):
                    client = original(*args)
                    client.fail_next = OSError("relay unreachable")
                    return client

                manager._client_factory = failing
                await manager.async_load()
                await settle(15)
                self.assertEqual(manager.dials, 1)
                self.assertIsNone(manager.last_error)
                await manager.async_unload()

        run(scenario())

    def test_an_expired_token_is_not_presented(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                hass, manager, clients = self.make(Path(tmp))
                write_house(Path(tmp), good_config(valid=1))
                with self.assertLogs(remote_ui._LOGGER, level=logging.ERROR) as logs:
                    await manager.async_load()
                self.assertFalse(manager.enabled)
                self.assertEqual(clients, [])
                self.assertTrue(any("expired" in line for line in logs.output))

        run(scenario())

    def test_broken_files_are_said_once_and_nothing_is_dialled(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                hass, manager, clients = self.make(Path(tmp))
                write_house(Path(tmp), with_cert=False)
                with self.assertLogs(remote_ui._LOGGER, level=logging.ERROR):
                    await manager.async_load()
                self.assertFalse(manager.enabled)
                self.assertEqual(clients, [])

        run(scenario())

    def test_without_an_http_server_there_is_nothing_to_serve(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                hass, manager, clients = self.make(Path(tmp), with_http=False)
                write_house(Path(tmp))
                with self.assertLogs(remote_ui._LOGGER, level=logging.ERROR):
                    await manager.async_load()
                self.assertFalse(manager.enabled)
                self.assertEqual(clients, [])

        run(scenario())

    def test_unload_hangs_up_and_leaves_the_files(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                hass, manager, clients = self.make(Path(tmp))
                directory = write_house(Path(tmp))
                await manager.async_load()
                await settle()
                await manager.async_unload()
                self.assertTrue(clients[0].stopped)
                self.assertFalse(manager.connected)
                self.assertTrue(all(task.done() for task in hass.tasks))
                self.assertTrue((directory / REMOTE_UI_CONFIG_FILE).is_file())
                self.assertTrue((directory / REMOTE_UI_KEY_FILE).is_file())

        run(scenario())


if __name__ == "__main__":
    unittest.main()
