"""Remote UI: the homeowner's own way in, through the relay.

What is asserted here is the agent's side of the slice-1 contract:

  - **off until the files are there**, and off again (loudly) when they are
    there but wrong -- a server cannot make a house reachable, and neither can
    a typo make it half-reachable;
  - the dial presents exactly what ``mint_token.py`` wrote, as bytes, with the
    matching protocol version, so the relay and the house agree;
  - a drop is followed by a redial, an expired token is not, and unload hangs
    up without touching the files.

Slice 2's provisioner is asserted the same way, against a fake Dispatch that
signs CSRs with its own throwaway CA: a server without the feature (404)
leaves hand-placed files alone; off removes the three files and nothing else;
a first poll generates a key that **never leaves the house** (the guard: the
CSR payload holds no private key), has a CSR with the hostname as CN signed,
writes the minted token, and dials; an unchanged desired state writes
nothing; a moved config_version re-mints only the token.

The TLS context is built from a real self-signed certificate so a mismatched
key is caught the way it would be in the house. snitun itself is not driven
here -- the relay's own ``docker/relay/test_relay.py`` in ha-dispatch does
that, end to end, with nothing mocked -- so the client is a stand-in that
records what it was asked to do.
"""
from __future__ import annotations

import asyncio
import copy
import datetime as dt
import json
import logging
import os
import stat
import tempfile
import time
import types
import unittest
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from test_remote_access import FakeResponse, FakeSession, _load  # noqa: E402 -- shares the loader

api_client = _load("api_client")
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
        "server": "remote.hadispatch.app",
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
                self.assertEqual((client.server, client.port), ("remote.hadispatch.app", 8443))
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


# --- slice 2: the provisioner -----------------------------------------------


class FakeRemoteUiApi:
    """Stands in for Dispatch's /remote-ui endpoints, with its own throwaway CA.

    It records every request body, because what the house *sends* is the
    thing under test. It does not bump config_version on its own: the tests
    move it by hand, so each one says exactly what the server changed.
    """

    def __init__(self, desired=None, unavailable=False):
        self.protocol_versions_asked = []
        self.desired = desired
        self.unavailable = unavailable
        self.fetches = 0
        self.csr_posts = []
        self.token_posts = []
        self.status_posts = []
        self.minted = 0
        self.ca_key = ec.generate_private_key(ec.SECP256R1())
        self.ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fake Origin CA")])

    async def fetch_remote_ui(self, installation_id):
        self.fetches += 1
        if self.unavailable:
            raise api_client.RemoteUiUnavailable("HTTP 404")
        return copy.deepcopy(self.desired)

    async def sign_remote_ui_csr(self, installation_id, csr_pem):
        self.csr_posts.append({"csr_pem": csr_pem})
        csr = x509.load_pem_x509_csr(csr_pem.encode("ascii"))
        now = dt.datetime.now(dt.timezone.utc)
        builder = (
            x509.CertificateBuilder()
            .subject_name(csr.subject)
            .issuer_name(self.ca_name)
            .public_key(csr.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=1))
            .not_valid_after(now + dt.timedelta(days=1825))
        )
        for extension in csr.extensions:
            builder = builder.add_extension(extension.value, critical=extension.critical)
        cert = builder.sign(self.ca_key, hashes.SHA256())
        pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
        expires = (now + dt.timedelta(days=1825)).isoformat()
        self.desired["certificate"] = {"fullchain_pem": pem, "expires_at": expires}
        return {"fullchain_pem": pem, "expires_at": expires}

    async def mint_remote_ui_token(self, installation_id, aes_key_hex, aes_iv_hex, protocol_version=None):
        self.protocol_versions_asked.append(protocol_version)
        self.token_posts.append({"aes_key": aes_key_hex, "aes_iv": aes_iv_hex})
        self.minted += 1
        return {"token": f"gAAAAABminted{self.minted}", "valid": FAR_FUTURE, "protocol_version": 2}

    async def report_remote_ui_status(self, installation_id, payload):
        self.status_posts.append(payload)


class FakeCoordinator:
    def __init__(self, api):
        self.api_client = api
        self.installation_id = 7


def desired_state(**overrides):
    state = {
        "enabled": True,
        "hostname": HOSTNAME,
        "relay": {"server": "remote.hadispatch.app", "port": 443},
        "protocol_version": 2,
        "trusted_proxies": ["173.245.48.0/20", "2400:cb00::/32"],
        "ranges_version": 7,
        "certificate": None,
        "config_version": 12,
    }
    state.update(overrides)
    return state


OLD_MTIME = 1_000_000_000  # 2001: a timestamp no write today could produce


def pin_mtimes(directory: Path):
    """Set every file's mtime to a known old value, so a rewrite is unmistakable."""
    for path in directory.iterdir():
        os.utime(path, (OLD_MTIME, OLD_MTIME))


def untouched(directory: Path, *names) -> bool:
    return all(int(os.stat(directory / name).st_mtime) == OLD_MTIME for name in names)


class ProvisionerTest(unittest.TestCase):
    def make(self, tmp: Path, api):
        hass = FakeHass(tmp)
        clients = []

        def factory(runner, context, server, port):
            client = FakeClient(runner, context, server, port)
            clients.append(client)
            return client

        manager = HADispatchRemoteUI(hass, FakeCoordinator(api), client_factory=factory, delay=lambda: 0)
        return hass, manager, clients

    def house_dir(self, tmp: Path) -> Path:
        return tmp.joinpath(*REMOTE_UI_DIR)

    def test_a_server_without_remote_ui_leaves_hand_placed_files_alone_and_dials_with_them(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                api = FakeRemoteUiApi(unavailable=True)
                hass, manager, clients = self.make(Path(tmp), api)
                directory = write_house(Path(tmp))
                pin_mtimes(directory)
                await manager.async_load()
                await settle()
                with self.assertLogs(remote_ui._LOGGER, level=logging.INFO) as logs:
                    await manager.async_poll()
                    await manager.async_poll()
                    await manager.async_poll()
                # Asked once, said once, and the hand-placed token is what is dialled.
                self.assertEqual(api.fetches, 1)
                self.assertEqual(sum("no Remote UI endpoint" in line for line in logs.output), 1)
                self.assertTrue(untouched(directory, REMOTE_UI_CONFIG_FILE, REMOTE_UI_CERT_FILE, REMOTE_UI_KEY_FILE))
                self.assertEqual(clients[0].connects[0][0], b"gAAAAABtoken")
                self.assertTrue(manager.connected)
                self.assertEqual(api.csr_posts, [])
                self.assertEqual(api.token_posts, [])
                await manager.async_unload()

        run(scenario())

    def test_enabled_false_hangs_up_and_removes_the_three_files_it_wrote_but_not_http_yaml(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                api = FakeRemoteUiApi(desired={"enabled": False})
                hass, manager, clients = self.make(Path(tmp), api)
                # Files the provisioner wrote carry its stamp.
                directory = write_house(Path(tmp), {**good_config(), "managed_by": "dispatch"})
                http_yaml = Path(tmp, "ha_dispatch_client", "http.yaml")
                http_yaml.write_text("use_x_forwarded_for: true\ntrusted_proxies:\n  - 173.245.48.0/20\n")
                await manager.async_load()
                await settle()
                self.assertTrue(manager.connected)

                await manager.async_poll()
                self.assertTrue(clients[0].stopped)
                self.assertFalse(manager.connected)
                self.assertFalse(manager.enabled)
                for name in (REMOTE_UI_CONFIG_FILE, REMOTE_UI_CERT_FILE, REMOTE_UI_KEY_FILE):
                    self.assertFalse((directory / name).exists(), name)
                self.assertTrue(http_yaml.is_file())
                self.assertIn("173.245.48.0/20", http_yaml.read_text())

                # Off again is a no-op: nothing to remove, nothing to say.
                with self.assertNoLogs(remote_ui._LOGGER, level=logging.INFO):
                    await manager.async_poll()
                self.assertEqual(len(clients), 1)

        run(scenario())

    def test_enabled_false_leaves_hand_placed_files_alone_and_says_so_once(self):
        # Slice 1's lab files, or an operator's own set, carry no stamp. The
        # server saying off ends the dial -- that part is ours -- but deleting
        # a private key this client did not create is not.
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                api = FakeRemoteUiApi(desired={"enabled": False})
                hass, manager, clients = self.make(Path(tmp), api)
                directory = write_house(Path(tmp))  # no managed_by stamp
                await manager.async_load()
                await settle()
                self.assertTrue(manager.connected)

                with self.assertLogs(remote_ui._LOGGER, level=logging.INFO) as logs:
                    await manager.async_poll()
                self.assertTrue(clients[0].stopped)
                self.assertFalse(manager.connected)
                for name in (REMOTE_UI_CONFIG_FILE, REMOTE_UI_CERT_FILE, REMOTE_UI_KEY_FILE):
                    self.assertTrue((directory / name).is_file(), name)
                self.assertTrue(any("placed by hand" in line for line in logs.output))

                # Said once; the next poll with the same answer is silent.
                with self.assertNoLogs(remote_ui._LOGGER, level=logging.INFO):
                    await manager.async_poll()
                for name in (REMOTE_UI_CONFIG_FILE, REMOTE_UI_CERT_FILE, REMOTE_UI_KEY_FILE):
                    self.assertTrue((directory / name).is_file(), name)

        run(scenario())

    def test_a_first_poll_provisions_a_key_that_never_leaves_the_house(self):
        """THE GUARD. The CSR is the only thing sent; the key is only ever on disk."""

        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                api = FakeRemoteUiApi(desired=desired_state())
                hass, manager, clients = self.make(Path(tmp), api)
                directory = self.house_dir(Path(tmp))
                self.assertFalse(directory.exists())

                await manager.async_poll()
                await settle()

                # The key: on disk, private to the HA user, and nowhere in what was posted.
                key_path = directory / REMOTE_UI_KEY_FILE
                self.assertTrue(key_path.is_file())
                self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)
                self.assertIn("PRIVATE KEY", key_path.read_text())
                self.assertEqual(len(api.csr_posts), 1)
                self.assertNotIn("PRIVATE KEY", json.dumps(api.csr_posts))
                self.assertNotIn("PRIVATE KEY", json.dumps(api.token_posts))

                # The CSR: CN and SAN are the hostname, and it is this key's.
                csr = x509.load_pem_x509_csr(api.csr_posts[0]["csr_pem"].encode())
                self.assertEqual(csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value, HOSTNAME)
                san = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                self.assertEqual(san.get_values_for_type(x509.DNSName), [HOSTNAME])
                key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
                self.assertEqual(
                    csr.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo),
                    key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo),
                )

                # The certificate is what the fake signed, verbatim.
                self.assertEqual((directory / REMOTE_UI_CERT_FILE).read_text(), api.desired["certificate"]["fullchain_pem"])

                # remote_ui.json: the slice-1 shape plus our stamp, holding what was minted
                # and the keyset that was posted to mint it.
                written = json.loads((directory / REMOTE_UI_CONFIG_FILE).read_text())
                self.assertEqual(len(api.token_posts), 1)
                self.assertEqual(written, {
                    "managed_by": "dispatch",
                    "hostname": HOSTNAME,
                    "server": "remote.hadispatch.app",
                    "port": 443,
                    "token": "gAAAAABminted1",
                    "aes_key": api.token_posts[0]["aes_key"],
                    "aes_iv": api.token_posts[0]["aes_iv"],
                    "valid": FAR_FUTURE,
                    "protocol_version": 2,
                })
                self.assertEqual(len(bytes.fromhex(written["aes_key"])), 32)
                self.assertEqual(len(bytes.fromhex(written["aes_iv"])), 16)
                self.assertEqual(stat.S_IMODE((directory / REMOTE_UI_CONFIG_FILE).stat().st_mode), 0o600)

                # And it dialled with them.
                self.assertEqual(len(clients), 1)
                self.assertEqual(clients[0].connects, [(
                    b"gAAAAABminted1", bytes.fromhex(written["aes_key"]), bytes.fromhex(written["aes_iv"]), 2,
                )])
                self.assertTrue(manager.connected)
                self.assertEqual(manager.dials, 1)
                await manager.async_unload()

        run(scenario())

    def test_an_unchanged_desired_state_writes_nothing(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                api = FakeRemoteUiApi(desired=desired_state())
                hass, manager, clients = self.make(Path(tmp), api)
                directory = self.house_dir(Path(tmp))
                await manager.async_poll()
                await settle()
                pin_mtimes(directory)

                await manager.async_poll()
                await manager.async_poll()
                await settle()
                self.assertEqual(api.fetches, 3)
                self.assertTrue(untouched(directory, REMOTE_UI_CONFIG_FILE, REMOTE_UI_CERT_FILE, REMOTE_UI_KEY_FILE))
                self.assertEqual(len(api.csr_posts), 1)
                self.assertEqual(len(api.token_posts), 1)
                # No redial either: the connection it had is the one it keeps.
                self.assertEqual(len(clients), 1)
                self.assertEqual(manager.dials, 1)
                await manager.async_unload()

        run(scenario())

    def test_a_moved_config_version_re_mints_the_token_only(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                api = FakeRemoteUiApi(desired=desired_state())
                hass, manager, clients = self.make(Path(tmp), api)
                directory = self.house_dir(Path(tmp))
                await manager.async_poll()
                await settle()
                pin_mtimes(directory)

                api.desired["config_version"] = 13
                await manager.async_poll()
                await settle()

                self.assertEqual(len(api.csr_posts), 1, "the certificate was still good; no new signing")
                self.assertEqual(len(api.token_posts), 2)
                self.assertTrue(untouched(directory, REMOTE_UI_CERT_FILE, REMOTE_UI_KEY_FILE))
                self.assertFalse(untouched(directory, REMOTE_UI_CONFIG_FILE))
                written = json.loads((directory / REMOTE_UI_CONFIG_FILE).read_text())
                self.assertEqual(written["token"], "gAAAAABminted2")
                self.assertEqual(written["aes_key"], api.token_posts[1]["aes_key"])
                # Hung up and redialled with the new token.
                self.assertTrue(clients[0].stopped)
                self.assertEqual(len(clients), 2)
                self.assertEqual(clients[1].connects[0][0], b"gAAAAABminted2")
                self.assertTrue(manager.connected)

                # And a third poll at the same version is quiet again.
                pin_mtimes(directory)
                await manager.async_poll()
                self.assertTrue(untouched(directory, REMOTE_UI_CONFIG_FILE, REMOTE_UI_CERT_FILE, REMOTE_UI_KEY_FILE))
                self.assertEqual(len(api.token_posts), 2)
                await manager.async_unload()

        run(scenario())

    def test_a_refusal_is_backed_off_not_retried_every_minute(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                api = FakeRemoteUiApi(desired=desired_state())

                async def refuse(installation_id, csr_pem):
                    api.csr_posts.append({"csr_pem": csr_pem})
                    raise api_client.RemoteUiRefused(429, "signed in the last 24 hours")

                api.sign_remote_ui_csr = refuse
                hass, manager, clients = self.make(Path(tmp), api)
                with self.assertLogs(remote_ui._LOGGER, level=logging.WARNING):
                    await manager.async_poll()
                await manager.async_poll()
                await manager.async_poll()
                self.assertEqual(len(api.csr_posts), 1)
                self.assertEqual(api.token_posts, [])
                self.assertEqual(clients, [])
                # The key it generated is kept for the retry; nothing else exists yet.
                directory = self.house_dir(Path(tmp))
                self.assertTrue((directory / REMOTE_UI_KEY_FILE).is_file())
                self.assertFalse((directory / REMOTE_UI_CONFIG_FILE).exists())

        run(scenario())


class PlanningTest(unittest.TestCase):
    """The pure decision, with hand-written expectations."""

    def files(self, tmp: Path, long_cert: bool = False, **config_overrides):
        """Hand-placed files, inspected. The slice-1 fixture's cert lasts a day."""
        config_overrides.setdefault("server", "remote.hadispatch.app")
        config_overrides.setdefault("port", 443)
        directory = write_house(tmp, good_config(**config_overrides))
        if long_cert:
            key = remote_ui.inspect_remote_ui_files(directory).key
            self._write_long_cert(directory, key, config_overrides.get("hostname", HOSTNAME))
        return remote_ui.inspect_remote_ui_files(directory)

    def test_first_poll_with_good_files_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = self.files(Path(tmp), long_cert=True)
            plan = remote_ui.plan_provisioning(desired_state(), files, None, now=time.time())
            self.assertEqual((plan["key"], plan["certificate"], plan["token"]), (False, False, False))

    def test_a_cert_within_sixty_days_is_renewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = self.files(Path(tmp))  # the one-day certificate
            plan = remote_ui.plan_provisioning(desired_state(), files, None, now=time.time())
            self.assertEqual((plan["key"], plan["certificate"], plan["token"]), (False, True, False))

    def test_a_token_within_48h_is_re_minted(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = self.files(Path(tmp), long_cert=True, valid=1_000_000 + 47 * 3600)
            self.assertTrue(remote_ui.plan_provisioning(desired_state(), files, 12, now=1_000_000)["token"])
        with tempfile.TemporaryDirectory() as tmp:
            files = self.files(Path(tmp), long_cert=True, valid=1_000_000 + 49 * 3600)
            self.assertFalse(remote_ui.plan_provisioning(desired_state(), files, 12, now=1_000_000)["token"])

    def test_a_moved_version_re_mints_the_token_and_a_renew_request_re_signs(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = self.files(Path(tmp), long_cert=True)
            now = time.time()
            same = remote_ui.plan_provisioning(desired_state(config_version=12), files, 12, now=now)
            moved = remote_ui.plan_provisioning(desired_state(config_version=13), files, 12, now=now)
            renew = remote_ui.plan_provisioning(desired_state(config_version=13, renew_requested=True), files, 12, now=now)
            self.assertEqual((same["certificate"], same["token"]), (False, False))
            self.assertEqual((moved["certificate"], moved["token"]), (False, True))
            self.assertEqual((renew["certificate"], renew["token"]), (True, True))

    def test_a_different_hostname_or_relay_re_mints(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = self.files(Path(tmp), long_cert=True, server="old-relay.example")
            self.assertTrue(remote_ui.plan_provisioning(desired_state(), files, 12, now=time.time())["token"])
        with tempfile.TemporaryDirectory() as tmp:
            files = self.files(Path(tmp), long_cert=True, hostname="other.remote.hadispatch.app")
            plan = remote_ui.plan_provisioning(desired_state(), files, 12, now=time.time())
            # A renamed house needs both: the certificate names the old hostname too.
            self.assertEqual((plan["certificate"], plan["token"]), (True, True))

    def test_a_cert_for_another_key_is_not_usable(self):
        other = ec.generate_private_key(ec.SECP256R1())
        with tempfile.TemporaryDirectory() as tmp:
            files = self.files(Path(tmp), long_cert=True)
            usable, why = remote_ui.certificate_is_usable(files.cert_pem, other, HOSTNAME, time.time())
            self.assertFalse(usable)
            self.assertIn("does not match", why)

    def _write_long_cert(self, directory: Path, key, hostname: str = HOSTNAME):
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=1))
            .not_valid_after(now + dt.timedelta(days=1825))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
            .sign(key, hashes.SHA256())
        )
        (directory / REMOTE_UI_CERT_FILE).write_bytes(cert.public_bytes(serialization.Encoding.PEM))


class RemoteUiApiTest(unittest.TestCase):
    """The wire: a 404 is "no such feature", never "no such installation"."""

    def make(self, status=200, payload=None, text=""):
        session = FakeSession(FakeResponse(status=status, payload=payload, text=text))
        return session, api_client.HADispatchApiClient(session, "https://dispatch.example.com", token="t")

    def test_a_404_is_unavailable_not_gone(self):
        self.assertFalse(issubclass(api_client.RemoteUiUnavailable, api_client.InstallationAuthError))
        session, client = self.make(status=404)
        for call in (
            client.fetch_remote_ui(7),
            client.sign_remote_ui_csr(7, "-----BEGIN CERTIFICATE REQUEST-----"),
            client.mint_remote_ui_token(7, "ab" * 32, "cd" * 16),
            client.report_remote_ui_status(7, {"connected": True}),
        ):
            with self.subTest(call=call.__name__):
                with self.assertRaises(api_client.RemoteUiUnavailable):
                    run(call)

    def test_enabled_false_is_an_ordinary_answer(self):
        session, client = self.make(payload={"enabled": False, "hostname": None})
        self.assertEqual(run(client.fetch_remote_ui(7)), {"enabled": False, "hostname": None})
        self.assertEqual(session.calls[0][:2], ("GET", "https://dispatch.example.com/api/v1/installations/7/remote-ui"))

    def test_the_bodies_and_paths_are_the_contracts(self):
        session, client = self.make(payload={"token": "x", "valid": 1, "protocol_version": 2})
        run(client.mint_remote_ui_token(7, "ab" * 32, "cd" * 16))
        run(client.sign_remote_ui_csr(7, "CSR"))
        run(client.report_remote_ui_status(7, {"connected": True}))
        self.assertEqual(
            [(m, u.split("/installations/7", 1)[1], body) for m, u, body, _ in session.calls],
            [
                ("POST", "/remote-ui/token", {"aes_key": "ab" * 32, "aes_iv": "cd" * 16}),
                ("POST", "/remote-ui/csr", {"csr_pem": "CSR"}),
                ("POST", "/remote-ui/status", {"connected": True}),
            ],
        )

    def test_a_refusal_carries_its_status(self):
        for status in (409, 422, 429):
            with self.subTest(status=status):
                session, client = self.make(status=status, text="no")
                with self.assertRaises(api_client.RemoteUiRefused) as caught:
                    run(client.sign_remote_ui_csr(7, "CSR"))
                self.assertEqual(caught.exception.status, status)


class WiringTest(unittest.TestCase):
    def test_the_coordinator_polls_the_provisioner_each_cycle(self):
        source = Path(__file__).resolve().parent.parent.joinpath(
            "custom_components", "ha_dispatch_client", "coordinator.py"
        ).read_text()
        self.assertIn("await self.remote_ui.async_poll()", source)


class RelayProtocolTest(unittest.TestCase):
    """Home Assistant bundles an older snitun than the relay; the agent must say so, not hang."""

    def _with_installed(self, version, protocol, body):
        original = remote_ui.installed_relay_protocol
        remote_ui.installed_relay_protocol = lambda: (version, protocol)
        try:
            return run(body())
        finally:
            remote_ui.installed_relay_protocol = original

    def test_a_token_minted_for_a_newer_protocol_than_the_house_speaks_is_refused_not_dialled(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                hass = FakeHass(Path(tmp))
                clients = []

                def factory(runner, context, server, port):
                    client = FakeClient(runner, context, server, port)
                    clients.append(client)
                    return client

                manager = HADispatchRemoteUI(hass, coordinator=None, client_factory=factory, delay=lambda: 0)
                write_house(Path(tmp), good_config(protocol_version=2))
                with self.assertLogs(remote_ui._LOGGER, level=logging.ERROR) as logs:
                    await manager.async_load()
                    await settle(5)
                self.assertEqual(clients[0].connects, [])
                self.assertEqual(manager.dials, 0)
                self.assertIn("speaks up to 1", manager.last_error or "")
                self.assertTrue(any("Not dialling" in line for line in logs.output))
                await manager.async_unload()

        self._with_installed("0.45.1", 1, scenario)

    def test_a_token_at_or_below_the_houses_protocol_dials(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                hass = FakeHass(Path(tmp))
                clients = []

                def factory(runner, context, server, port):
                    client = FakeClient(runner, context, server, port)
                    clients.append(client)
                    return client

                manager = HADispatchRemoteUI(hass, coordinator=None, client_factory=factory, delay=lambda: 0)
                write_house(Path(tmp), good_config(protocol_version=1))
                await manager.async_load()
                await settle(5)
                self.assertEqual(manager.dials, 1)
                self.assertEqual(clients[0].connects[0][3], 1)
                await manager.async_unload()

        self._with_installed("0.45.1", 1, scenario)

    def test_the_mint_request_carries_what_the_house_speaks_and_a_higher_answer_is_refused(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                # The fake always mints protocol 2: a server that ignored the cap.
                api = FakeRemoteUiApi(desired=desired_state())
                hass = FakeHass(Path(tmp))
                coordinator = FakeCoordinator(api)
                manager = HADispatchRemoteUI(hass, coordinator, client_factory=lambda *a: FakeClient(*a), delay=lambda: 0)
                with self.assertLogs(remote_ui._LOGGER, level=logging.WARNING) as logs:
                    await manager.async_poll()
                    await settle(5)
                self.assertEqual(api.protocol_versions_asked, [1])
                self.assertTrue(any("ignored the cap" in line for line in logs.output))
                self.assertFalse((Path(tmp) / "ha_dispatch_client" / "remote_ui" / REMOTE_UI_CONFIG_FILE).exists())
                await manager.async_unload()

        self._with_installed("0.45.1", 1, scenario)


class StartupOrderTest(unittest.TestCase):
    """At boot the HTTP server does not exist yet; the dial waits for Home Assistant to start."""

    def test_without_a_runner_the_dial_waits_for_the_started_event_then_proceeds(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                hass = FakeHass(Path(tmp), with_http=False)
                listeners = []
                hass.bus = types.SimpleNamespace(async_listen_once=lambda event, cb: listeners.append((event, cb)))
                clients = []

                def factory(runner, context, server, port):
                    client = FakeClient(runner, context, server, port)
                    clients.append(client)
                    return client

                manager = HADispatchRemoteUI(hass, coordinator=None, client_factory=factory, delay=lambda: 0)
                write_house(Path(tmp))
                await manager.async_load()
                self.assertEqual(clients, [])
                self.assertEqual([e for e, _ in listeners], ["homeassistant_started"])

                # Home Assistant starts, the HTTP server appears, the event fires.
                hass.http = types.SimpleNamespace(runner=object())
                await listeners[0][1](None)
                await settle(5)
                self.assertEqual(len(clients), 1)
                self.assertTrue(manager.connected)
                await manager.async_unload()

        run(scenario())

    def test_unload_closes_the_local_tls_site_before_the_client(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                hass = FakeHass(Path(tmp))
                order = []

                def factory(runner, context, server, port):
                    client = FakeClient(runner, context, server, port)

                    async def site_stop():
                        order.append("site")

                    client._site = types.SimpleNamespace(stop=site_stop)
                    original_stop = client.stop

                    async def stop(*, wait=False):
                        order.append("client")
                        await original_stop(wait=wait)

                    client.stop = stop
                    return client

                manager = HADispatchRemoteUI(hass, coordinator=None, client_factory=factory, delay=lambda: 0)
                write_house(Path(tmp))
                await manager.async_load()
                await settle()
                await manager.async_unload()
                self.assertEqual(order, ["site", "client"])

        run(scenario())


CONSENT_URL = "https://dispatch.example.test/consent/abc123"


def announce(**overrides):
    block = {"hostname": HOSTNAME, "consent_url": CONSENT_URL, "consent_method": "enhanced"}
    block.update(overrides)
    return block


class AnnouncementTest(unittest.TestCase):
    """The house tells its owner, in the owner's own Home Assistant.

    What is asserted: exactly one notification, carrying the address to type
    into the app and the off-switch link; updated in place (same id) when the
    address changes; taken down when the server says off (THE GUARD: skip the
    dismiss and the homeowner is told their home is reachable after it is
    not); and nothing at all for hand-placed files or a server that announces
    nothing. The expected title and id are written out by hand here, so a
    change to either in const.py has to be made deliberately twice.
    """

    def setUp(self):
        self.notifications = remote_ui.persistent_notification
        self.notifications.reset()

    def make(self, tmp: Path, api):
        return ProvisionerTest().make(tmp, api)

    def test_the_homeowner_is_told_once_with_the_address_and_the_off_switch(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                api = FakeRemoteUiApi(desired=desired_state(announce=announce()))
                hass, manager, clients = self.make(Path(tmp), api)
                await manager.async_poll()
                await settle()
                await manager.async_poll()
                await manager.async_poll()

                self.assertTrue(manager.connected)
                self.assertEqual(len(self.notifications.created), 1)
                note = self.notifications.created[0]
                self.assertEqual(note["notification_id"], "remote_ui_on")
                self.assertEqual(note["title"], "Remote access to your home is on")
                self.assertIn("https://lab1.remote.hadispatch.app", note["message"])
                self.assertNotIn(":443", note["message"])
                self.assertIn(f"[Turn it off any time]({CONSENT_URL})", note["message"])
                self.assertIn("Your installer turned this on, under the permission you gave them.", note["message"])
                self.assertIn("Your login stays yours: your Home Assistant password and two-factor, applied here.",
                              note["message"])
                # Not a word of ours that is not literally true: no vendor name, no credential.
                for forbidden in ("Dispatch", "Cloudflare", "snitun", "token", "gAAAA"):
                    self.assertNotIn(forbidden, note["message"])
                self.assertEqual(self.notifications.dismissed, [])
                await manager.async_unload()

        run(scenario())

    def test_a_tap_consent_is_worded_as_the_homeowners_own_choice_and_a_lab_port_is_shown(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                api = FakeRemoteUiApi(desired=desired_state(announce=announce(consent_method="tap", port=8443)))
                hass, manager, clients = self.make(Path(tmp), api)
                await manager.async_poll()
                await settle()
                note = self.notifications.created[0]
                self.assertIn("**https://lab1.remote.hadispatch.app:8443**", note["message"])
                self.assertIn("You turned this on yourself.", note["message"])
                self.assertNotIn("installer", note["message"])
                await manager.async_unload()

        run(scenario())

    def test_a_changed_hostname_updates_the_one_notification_in_place(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                api = FakeRemoteUiApi(desired=desired_state(announce=announce()))
                hass, manager, clients = self.make(Path(tmp), api)
                await manager.async_poll()
                await settle()
                self.assertEqual(len(self.notifications.created), 1)

                new_hostname = "calm-birch-17.remote.hadispatch.app"
                api.desired = desired_state(
                    hostname=new_hostname, config_version=13, announce=announce(hostname=new_hostname),
                )
                await manager.async_poll()
                await settle()
                await manager.async_poll()

                self.assertEqual(len(self.notifications.created), 2)
                self.assertEqual({n["notification_id"] for n in self.notifications.created}, {"remote_ui_on"})
                self.assertIn("https://calm-birch-17.remote.hadispatch.app", self.notifications.created[1]["message"])
                self.assertNotIn(HOSTNAME, self.notifications.created[1]["message"])
                await manager.async_unload()

        run(scenario())

    def test_off_takes_the_notification_down(self):
        """THE GUARD."""

        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                api = FakeRemoteUiApi(desired=desired_state(announce=announce()))
                hass, manager, clients = self.make(Path(tmp), api)
                await manager.async_poll()
                await settle()
                self.assertEqual(len(self.notifications.created), 1)
                self.assertEqual(self.notifications.dismissed, [])

                api.desired = {"enabled": False}
                await manager.async_poll()
                self.assertFalse(manager.connected)
                self.assertEqual(self.notifications.dismissed, ["remote_ui_on"])

                # Off again dismisses nothing again; on again says it again.
                await manager.async_poll()
                self.assertEqual(self.notifications.dismissed, ["remote_ui_on"])
                api.desired = desired_state(config_version=14, announce=announce())
                await manager.async_poll()
                await settle()
                self.assertEqual(len(self.notifications.created), 2)
                await manager.async_unload()

        run(scenario())

    def test_nothing_is_said_without_an_announce_block(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                # Dispatch provisions the house but has nothing to announce.
                api = FakeRemoteUiApi(desired=desired_state())
                hass, manager, clients = self.make(Path(tmp), api)
                await manager.async_poll()
                await settle()
                self.assertTrue(manager.connected)
                self.assertEqual(self.notifications.created, [])

                # Off after that dismisses once (harmless if nothing was up) and then stays quiet.
                api.desired = {"enabled": False}
                await manager.async_poll()
                await manager.async_poll()
                self.assertEqual(self.notifications.dismissed, ["remote_ui_on"])

        run(scenario())

    def test_hand_placed_files_and_a_server_without_the_feature_say_nothing(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                api = FakeRemoteUiApi(unavailable=True)
                hass, manager, clients = self.make(Path(tmp), api)
                write_house(Path(tmp))
                await manager.async_load()
                await settle()
                await manager.async_poll()
                self.assertTrue(manager.connected)
                self.assertEqual(self.notifications.created, [])
                self.assertEqual(self.notifications.dismissed, [])
                await manager.async_unload()

        run(scenario())

    def test_an_announce_block_without_an_off_switch_is_not_shown(self):
        # Pure: a notification that says "on" with no way off is not one to show.
        self.assertIsNone(remote_ui.build_announcement(None))
        self.assertIsNone(remote_ui.build_announcement({"hostname": HOSTNAME}))
        self.assertIsNone(remote_ui.build_announcement({"hostname": HOSTNAME, "consent_url": ""}))
        self.assertIsNone(remote_ui.build_announcement({"consent_url": CONSENT_URL}))
        self.assertEqual(remote_ui.remote_ui_address(HOSTNAME, 443), f"https://{HOSTNAME}")
        self.assertEqual(remote_ui.remote_ui_address(HOSTNAME, None), f"https://{HOSTNAME}")
        self.assertEqual(remote_ui.remote_ui_address(HOSTNAME, True), f"https://{HOSTNAME}")
        self.assertEqual(remote_ui.remote_ui_address(HOSTNAME, 8443), f"https://{HOSTNAME}:8443")


if __name__ == "__main__":
    unittest.main()
