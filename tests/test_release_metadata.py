"""The release metadata invariants, which nothing else in the suite guards.

Two files carry this client's version and the project's own CLAUDE.md says
they "must stay in sync": the `VERSION` file at the repo root and `version` in
`manifest.json`. `scripts/release.sh` refuses to build when they disagree, so
until now the only thing that noticed a drift was a release attempt -- which
is late, because by then the version has already been deployed by `deploy.sh`
and reported to the Dispatch server.

The second test is why this matters beyond tidiness. The Home 3D room-plan
card is registered as a Lovelace resource stamped `?v=<client version>`, and
Home Assistant caches frontend resources as hard as it caches `manifest.json`.
A wall tablet only picks up a changed card when that stamp changes, so the
version is not decoration here: it is the cache key. A batch that changes the
card and leaves the version alone re-registers a URL identical to the one
already on the tablet, and the homeowner keeps the card they downloaded
before.

The third place the version lives is prose: the header of CLAUDE.md, the top
entry of CHANGELOG.md, and the release notes in docs/user/updating.md -- the
text the homeowner's Settings -> Updates page is written from. A release
whose notes describe the version before it is the kind of drift nobody
notices until a homeowner reads it, so those files are read here too.
"""
import importlib
import json
import os
import re
import sys
import types
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPONENT = os.path.join(REPO_ROOT, "custom_components", "ha_dispatch_client")


def _load(module: str):
    if "ha_dispatch_client" not in sys.modules:
        pkg = types.ModuleType("ha_dispatch_client")
        pkg.__path__ = [COMPONENT]
        sys.modules["ha_dispatch_client"] = pkg
    return importlib.import_module(f"ha_dispatch_client.{module}")


def _version_file() -> str:
    with open(os.path.join(REPO_ROOT, "VERSION"), encoding="utf-8") as handle:
        return handle.read().strip()


def _manifest_version() -> str:
    with open(os.path.join(COMPONENT, "manifest.json"), encoding="utf-8") as handle:
        return json.load(handle)["version"]


class ReleaseMetadataTest(unittest.TestCase):
    def test_the_version_file_and_the_manifest_say_the_same_thing(self):
        # Read from the two files independently -- neither is derived from the
        # other, and neither is derived from a constant in the code, so this
        # can actually fail.
        self.assertEqual(
            _version_file(),
            _manifest_version(),
            "VERSION and custom_components/ha_dispatch_client/manifest.json "
            "disagree; scripts/release.sh refuses to build in this state",
        )

    def test_the_version_is_three_numbers(self):
        # release.sh turns this into a git tag (`v<version>`) and the server
        # compares it against the release it is offering, so "1.8" or
        # "1.8.0-rc1" is not something this client should be shipping.
        parts = _version_file().split(".")
        self.assertEqual(len(parts), 3, f"not a three-part version: {_version_file()}")
        for part in parts:
            self.assertTrue(part.isdigit(), f"not a number: {part!r}")

    def test_the_cards_cache_stamp_is_the_version_both_files_agree_on(self):
        # The chain that actually reaches a wall tablet: VERSION ->
        # manifest.json -> the Lovelace resource URL's query string. The
        # expectation is hand-written rather than built from
        # FLOORPLAN_CARD_FILENAME, so a rename of the card has to be made
        # deliberately here too.
        floorplan = _load("floorplan")
        version = _version_file()
        self.assertEqual(
            floorplan.card_resource_url(version),
            f"/local/ha_dispatch/floorplan/ayvra-room-plan.js?v={version}",
        )

    def test_the_docs_name_the_same_version(self):
        # Each file is read for the version it actually states; nothing here
        # is derived from a constant in the code.
        version = _version_file()

        with open(os.path.join(REPO_ROOT, "CLAUDE.md"), encoding="utf-8") as handle:
            claude_md = handle.read()
        header = re.search(r"^- \*\*Version:\*\* (\S+)", claude_md, re.MULTILINE)
        self.assertIsNotNone(header, "CLAUDE.md has no '- **Version:** x.y.z' line")
        self.assertEqual(header.group(1), version, "CLAUDE.md's header names another version")

        with open(os.path.join(REPO_ROOT, "CHANGELOG.md"), encoding="utf-8") as handle:
            changelog = handle.read()
        top_entry = re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)
        self.assertIsNotNone(top_entry, "CHANGELOG.md has no '## [x.y.z]' entry")
        self.assertEqual(top_entry.group(1), version, "CHANGELOG.md's newest entry is not this version")

        with open(os.path.join(REPO_ROOT, "docs", "user", "updating.md"), encoding="utf-8") as handle:
            updating = handle.read()
        notes = re.search(r"^### (\d+\.\d+\.\d+)", updating, re.MULTILINE)
        self.assertIsNotNone(notes, "docs/user/updating.md has no '### x.y.z' release-notes heading")
        self.assertEqual(notes.group(1), version, "docs/user/updating.md's newest release notes are not this version")


if __name__ == "__main__":
    unittest.main()
