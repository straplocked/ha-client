"""Tests for the signed client self-update path.

These cover the steps where a mistake is expensive: a signature that should not
verify, an archive that should never be extracted, and a swap that has to leave
a working integration behind even when it fails halfway.

Home Assistant is not installed here, so the handful of symbols updater.py
imports are stubbed, in the same additive style as the other test modules.
cryptography is a real dependency and is used for real -- signatures here are
genuinely generated and genuinely verified.
"""
import base64
import importlib
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
import zipfile
from datetime import datetime
from pathlib import Path


def _module(name: str):
    """Fetch or create a stub module, registering it in sys.modules."""
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)
    return sys.modules[name]


def _install_stubs() -> None:
    """Add any stub symbols that are missing.

    Additive rather than all-or-nothing: the other test modules install their
    own narrower sets, and whichever the runner imports first would otherwise
    leave the rest absent.
    """
    _module("homeassistant")

    const = _module("homeassistant.const")
    if not hasattr(const, "__version__"):
        const.__version__ = "2026.6.4"

    core = _module("homeassistant.core")
    if not hasattr(core, "HomeAssistant"):
        class HomeAssistant:
            pass

        core.HomeAssistant = HomeAssistant

    exceptions = _module("homeassistant.exceptions")
    if not hasattr(exceptions, "HomeAssistantError"):
        class HomeAssistantError(Exception):
            pass

        exceptions.HomeAssistantError = HomeAssistantError

    helpers = _module("homeassistant.helpers")

    storage = _module("homeassistant.helpers.storage")
    if not hasattr(storage, "Store"):
        class Store:
            """In-memory stand-in for Home Assistant's JSON store."""

            def __init__(self, hass, version, key):
                self.hass = hass
                self.version = version
                self.key = key
                self.data = None

            async def async_load(self):
                return self.data

            async def async_save(self, data):
                self.data = data

            async def async_remove(self):
                self.data = None

        storage.Store = Store
    helpers.storage = storage

    aiohttp_client = _module("homeassistant.helpers.aiohttp_client")
    if not hasattr(aiohttp_client, "async_get_clientsession"):
        aiohttp_client.async_get_clientsession = lambda hass: None
    helpers.aiohttp_client = aiohttp_client

    loader = _module("homeassistant.loader")
    if not hasattr(loader, "async_get_integration"):
        async def async_get_integration(hass, domain):
            raise RuntimeError("no integration in tests unless patched")

        loader.async_get_integration = async_get_integration


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


_install_stubs()
updater = _load("updater")

UpdateError = updater.UpdateError


# --- helpers ----------------------------------------------------------------


def make_keypair():
    """Return (key_id, public_b64, sign_callable)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization

    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_b64 = base64.b64encode(public_raw).decode()

    def sign(payload: bytes) -> str:
        return base64.b64encode(private.sign(payload)).decode()

    return "test-key-1", public_b64, sign


def build_archive(
    path,
    *,
    version="1.5.0",
    domain="ha_dispatch_client",
    nested=False,
    include_manifest=True,
    extra_members=(),
):
    """Write a release archive, with knobs for the malformed cases."""
    prefix = "custom_components/ha_dispatch_client/" if nested else ""
    with zipfile.ZipFile(path, "w") as archive:
        if include_manifest:
            archive.writestr(
                f"{prefix}manifest.json",
                json.dumps({"domain": domain, "version": version, "name": "HA Dispatch Client"}),
            )
        archive.writestr(f"{prefix}__init__.py", "# integration\n")
        for name, content in extra_members:
            archive.writestr(name, content)
    return path


def add_symlink_member(path, name="evil", target="/etc/passwd"):
    """Append a member whose external attributes mark it as a symlink."""
    with zipfile.ZipFile(path, "a") as archive:
        info = zipfile.ZipInfo(name)
        # 0o120777 << 16 -- the high bits are where the POSIX mode lives.
        info.external_attr = (0o120777 << 16)
        archive.writestr(info, target)
    return path


# --- version handling -------------------------------------------------------


class VersionTest(unittest.TestCase):
    def test_parses_plain_versions(self):
        self.assertEqual(updater.parse_version("1.5.0")[:3], (1, 5, 0))
        self.assertEqual(updater.parse_version("v2.0")[:3], (2, 0, 0))
        self.assertEqual(updater.parse_version("3")[:3], (3, 0, 0))

    def test_rejects_nonsense(self):
        for value in ("", "latest", None, "1.2.3.4.5", "abc"):
            self.assertIsNone(updater.parse_version(value), value)

    def test_release_outranks_its_prereleases(self):
        self.assertTrue(updater.is_newer("1.5.0", "1.5.0rc1"))
        self.assertTrue(updater.is_newer("1.5.0b2", "1.5.0b1"))
        self.assertTrue(updater.is_newer("1.5.0rc1", "1.5.0b9"))

    def test_ordering(self):
        self.assertTrue(updater.is_newer("1.5.0", "1.4.9"))
        self.assertTrue(updater.is_newer("1.10.0", "1.9.0"))
        self.assertFalse(updater.is_newer("1.4.0", "1.5.0"))
        self.assertFalse(updater.is_newer("1.5.0", "1.5.0"))

    def test_unparseable_is_never_newer(self):
        # Refusing to act beats guessing: the fallback is a manual update, not
        # an accidental downgrade.
        self.assertFalse(updater.is_newer("garbage", "1.4.0"))
        self.assertFalse(updater.is_newer("1.5.0", "garbage"))


class UpdateWindowTest(unittest.TestCase):
    def test_no_window_always_allows(self):
        self.assertTrue(updater.in_update_window(datetime(2026, 9, 17, 14, 0), None))
        self.assertTrue(updater.in_update_window(datetime(2026, 9, 17, 14, 0), ""))

    def test_simple_window(self):
        window = "03:00-05:00"
        self.assertTrue(updater.in_update_window(datetime(2026, 9, 17, 4, 0), window))
        self.assertFalse(updater.in_update_window(datetime(2026, 9, 17, 14, 0), window))

    def test_window_wrapping_midnight(self):
        window = "23:00-02:00"
        self.assertTrue(updater.in_update_window(datetime(2026, 9, 17, 23, 30), window))
        self.assertTrue(updater.in_update_window(datetime(2026, 9, 17, 1, 0), window))
        self.assertFalse(updater.in_update_window(datetime(2026, 9, 17, 12, 0), window))

    def test_malformed_window_does_not_pin_forever(self):
        # A bad value pushed from the server must not silently freeze an
        # installation on an old version.
        self.assertTrue(updater.in_update_window(datetime(2026, 9, 17, 14, 0), "nonsense"))


# --- signature verification -------------------------------------------------


class SignatureTest(unittest.TestCase):
    def setUp(self):
        self.key_id, self.public_b64, self.sign = make_keypair()
        self.keys = {self.key_id: self.public_b64}
        self.payload = b"release archive bytes"

    def test_valid_signature_passes(self):
        updater.verify_signature(
            self.payload, self.sign(self.payload), self.key_id, self.keys
        )

    def test_tampered_payload_fails(self):
        signature = self.sign(self.payload)
        with self.assertRaises(UpdateError) as ctx:
            updater.verify_signature(
                self.payload + b"evil", signature, self.key_id, self.keys
            )
        self.assertEqual(ctx.exception.phase, updater.PHASE_VERIFY)

    def test_signature_from_another_key_fails(self):
        _, _, other_sign = make_keypair()
        with self.assertRaises(UpdateError):
            updater.verify_signature(
                self.payload, other_sign(self.payload), self.key_id, self.keys
            )

    def test_unknown_key_id_fails(self):
        with self.assertRaises(UpdateError) as ctx:
            updater.verify_signature(
                self.payload, self.sign(self.payload), "not-a-key", self.keys
            )
        self.assertIn("unknown key_id", str(ctx.exception))

    def test_missing_signature_or_key_id_fails(self):
        with self.assertRaises(UpdateError):
            updater.verify_signature(self.payload, None, self.key_id, self.keys)
        with self.assertRaises(UpdateError):
            updater.verify_signature(self.payload, self.sign(self.payload), None, self.keys)

    def test_malformed_signature_encoding_fails(self):
        with self.assertRaises(UpdateError):
            updater.verify_signature(self.payload, "not base64!!", self.key_id, self.keys)

    def test_no_pinned_keys_fails_closed(self):
        # The shipped default. Self-update must be inert, not permissive, until
        # somebody deliberately pins a key.
        with self.assertRaises(UpdateError) as ctx:
            updater.verify_signature(self.payload, self.sign(self.payload), self.key_id, {})
        self.assertIn("No release signing keys", str(ctx.exception))

    def test_shipped_default_pins_no_keys(self):
        from ha_dispatch_client import const

        self.assertEqual(
            const.RELEASE_SIGNING_KEYS,
            {},
            "A key pinned in the repo would be one nobody controls the private half of",
        )


class DigestTest(unittest.TestCase):
    def test_matching_digest_passes(self):
        import hashlib

        payload = b"abc"
        updater.verify_digest(payload, hashlib.sha256(payload).hexdigest())

    def test_mismatched_digest_fails(self):
        with self.assertRaises(UpdateError) as ctx:
            updater.verify_digest(b"abc", "0" * 64)
        self.assertEqual(ctx.exception.phase, updater.PHASE_VERIFY)

    def test_missing_digest_fails(self):
        with self.assertRaises(UpdateError):
            updater.verify_digest(b"abc", None)


# --- archive validation -----------------------------------------------------


class ArchiveValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.archive = self.tmp / "release.zip"

    def test_valid_archive_returns_manifest(self):
        build_archive(self.archive, version="1.5.0")
        manifest = updater.validate_archive(self.archive, expected_version="1.5.0")
        self.assertEqual(manifest["version"], "1.5.0")

    def test_wrong_version_rejected(self):
        # The server offering 1.5.0 and the archive containing 1.4.0 means
        # something is confused upstream; installing either would be wrong.
        build_archive(self.archive, version="1.4.0")
        with self.assertRaises(UpdateError) as ctx:
            updater.validate_archive(self.archive, expected_version="1.5.0")
        self.assertEqual(ctx.exception.phase, updater.PHASE_VALIDATE)

    def test_wrong_domain_rejected(self):
        build_archive(self.archive, domain="some_other_integration")
        with self.assertRaises(UpdateError) as ctx:
            updater.validate_archive(self.archive, expected_version="1.5.0")
        self.assertIn("domain", str(ctx.exception))

    def test_nested_layout_rejected_with_a_useful_hint(self):
        build_archive(self.archive, nested=True)
        with self.assertRaises(UpdateError) as ctx:
            updater.validate_archive(self.archive, expected_version="1.5.0")
        self.assertIn("custom_components", str(ctx.exception))

    def test_missing_manifest_rejected(self):
        build_archive(self.archive, include_manifest=False)
        with self.assertRaises(UpdateError):
            updater.validate_archive(self.archive, expected_version="1.5.0")

    def test_path_traversal_rejected(self):
        build_archive(
            self.archive, extra_members=[("../../../etc/cron.d/pwned", "* * * * * root sh\n")]
        )
        with self.assertRaises(UpdateError) as ctx:
            updater.validate_archive(self.archive, expected_version="1.5.0")
        self.assertIn("traversal", str(ctx.exception))

    def test_absolute_path_rejected(self):
        build_archive(self.archive, extra_members=[("/etc/passwd", "root\n")])
        with self.assertRaises(UpdateError) as ctx:
            updater.validate_archive(self.archive, expected_version="1.5.0")
        self.assertIn("absolute", str(ctx.exception))

    def test_symlink_member_rejected(self):
        build_archive(self.archive)
        add_symlink_member(self.archive)
        with self.assertRaises(UpdateError) as ctx:
            updater.validate_archive(self.archive, expected_version="1.5.0")
        self.assertIn("symlink", str(ctx.exception))

    def test_zip_bomb_rejected(self):
        build_archive(self.archive, extra_members=[("big.bin", "A" * 200_000)])
        with self.assertRaises(UpdateError) as ctx:
            updater.validate_archive(
                self.archive, expected_version="1.5.0", max_uncompressed=1000
            )
        self.assertIn("expands", str(ctx.exception))

    def test_non_zip_rejected(self):
        self.archive.write_bytes(b"this is not a zip file")
        with self.assertRaises(UpdateError):
            updater.validate_archive(self.archive, expected_version="1.5.0")


# --- the swap ---------------------------------------------------------------


class SwapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.live = self.tmp / "custom_components" / "ha_dispatch_client"
        self.live.mkdir(parents=True)
        (self.live / "manifest.json").write_text('{"version": "1.4.0"}')
        (self.live / "marker.txt").write_text("old")

        self.staged = self.tmp / "staging" / "1.5.0"
        self.staged.mkdir(parents=True)
        (self.staged / "manifest.json").write_text('{"version": "1.5.0"}')
        (self.staged / "marker.txt").write_text("new")

        self.backup = self.tmp / "backups" / "1.4.0"

    def test_successful_swap(self):
        updater.swap_integration_dir(self.live, self.staged, self.backup)
        self.assertEqual((self.live / "marker.txt").read_text(), "new")
        self.assertEqual((self.backup / "marker.txt").read_text(), "old")
        self.assertFalse(self.staged.exists())

    def test_failed_swap_restores_the_working_version(self):
        # The case that decides whether a bad update is an inconvenience or an
        # offline instance: if the new copy cannot be moved into place, the old
        # one has to come back.
        original_move = shutil.move
        calls = {"n": 0}

        def flaky_move(src, dst):
            calls["n"] += 1
            if calls["n"] == 2:  # first move backs up, second installs
                raise OSError("disk full")
            return original_move(src, dst)

        shutil.move = flaky_move
        self.addCleanup(lambda: setattr(shutil, "move", original_move))

        with self.assertRaises(UpdateError) as ctx:
            updater.swap_integration_dir(self.live, self.staged, self.backup)

        self.assertEqual(ctx.exception.phase, updater.PHASE_SWAP)
        self.assertTrue(self.live.is_dir(), "the working version must be back in place")
        self.assertEqual((self.live / "marker.txt").read_text(), "old")

    def test_unrecoverable_swap_says_where_the_files_are(self):
        original_move = shutil.move
        calls = {"n": 0}

        def always_fail_after_backup(src, dst):
            calls["n"] += 1
            if calls["n"] == 1:
                return original_move(src, dst)
            raise OSError("filesystem is gone")

        shutil.move = always_fail_after_backup
        self.addCleanup(lambda: setattr(shutil, "move", original_move))

        with self.assertRaises(UpdateError) as ctx:
            updater.swap_integration_dir(self.live, self.staged, self.backup)

        message = str(ctx.exception)
        self.assertIn(str(self.backup), message)
        self.assertIn(str(self.live), message)

    def test_missing_staged_dir_is_refused(self):
        with self.assertRaises(UpdateError):
            updater.swap_integration_dir(self.live, self.tmp / "nope", self.backup)
        self.assertTrue(self.live.is_dir())

    def test_swap_works_when_there_is_nothing_to_back_up(self):
        shutil.rmtree(self.live)
        updater.swap_integration_dir(self.live, self.staged, self.backup)
        self.assertEqual((self.live / "marker.txt").read_text(), "new")


class PruneBackupsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_keeps_the_newest(self):
        for index, name in enumerate(["1.1.0", "1.2.0", "1.3.0", "1.4.0"]):
            path = self.tmp / name
            path.mkdir()
            os.utime(path, (1_000_000 + index * 100, 1_000_000 + index * 100))

        updater.prune_backups(self.tmp, keep=2)

        remaining = sorted(child.name for child in self.tmp.iterdir())
        self.assertEqual(remaining, ["1.3.0", "1.4.0"])

    def test_missing_root_is_not_an_error(self):
        updater.prune_backups(self.tmp / "does-not-exist")


if __name__ == "__main__":
    unittest.main()
