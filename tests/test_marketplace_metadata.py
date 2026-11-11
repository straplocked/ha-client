"""What the Marketplace catalog's checks look at, held in the suite.

The catalog's own checks (the HACS action and hassfest) run on GitHub, on
main and on pull requests into it -- deliberately never on a batch branch --
so without these tests a drift would be found only after it had been merged
and the workflow had spent its minutes. Everything here is something those
checks reject, read from the shipped files:

* `translations/en.json` is what Home Assistant loads at runtime for a custom
  integration; `strings.json` is the file everyone edits. They must say the
  same thing, or the homeowner sees stale consent wording in Repairs.
* The manifest keys the catalog requires, in the order hassfest requires, with
  no requirement Home Assistant already ships and every optional component
  it reaches for declared.
* No URL in a translation string (hassfest wants a placeholder).
* `hacs.json` carries a name and a Home Assistant floor.
* The brand icon the catalog requires is shipped inside the integration.
* The workflow runs on main and on demand only.

Expected values are hand-written, not derived from the files they check.
"""
import json
import os
import re
import struct
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPONENT = os.path.join(REPO_ROOT, "custom_components", "ha_dispatch_client")
WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "validate.yml")


def _json(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as handle:
        return json.load(handle)


def _walk(node, path=()):
    """Yield (path, leaf) for every string leaf, so a diff names its location."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, path + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, path + (index,))
    else:
        yield path, node


class TranslationParityTest(unittest.TestCase):
    def test_runtime_translations_say_what_strings_json_says(self):
        strings = _json(COMPONENT, "strings.json")
        english = _json(COMPONENT, "translations", "en.json")
        if strings == english:
            return
        a, b = dict(_walk(strings)), dict(_walk(english))
        differing = sorted(
            ".".join(map(str, path))
            for path in set(a) | set(b)
            if a.get(path, "<missing>") != b.get(path, "<missing>")
        )
        self.fail(
            "translations/en.json and strings.json differ at: "
            + ", ".join(differing[:10])
            + " -- edit strings.json, then copy it to translations/en.json"
        )

    def test_the_runtime_file_has_no_core_only_references(self):
        # `[%key:...%]` resolves only inside Home Assistant core's own build;
        # in a custom integration's translations/ it would be shown verbatim.
        with open(os.path.join(COMPONENT, "translations", "en.json"), encoding="utf-8") as handle:
            self.assertNotIn("[%key", handle.read())

    def test_the_runtime_file_carries_the_three_sections_the_integration_uses(self):
        self.assertEqual(
            sorted(_json(COMPONENT, "translations", "en.json")),
            ["config", "issues", "services"],
        )


class ManifestForTheCatalogTest(unittest.TestCase):
    def setUp(self):
        self.manifest = _json(COMPONENT, "manifest.json")

    def test_every_key_the_catalog_requires_is_present(self):
        for key in ("domain", "name", "version", "documentation", "issue_tracker",
                    "codeowners", "iot_class"):
            self.assertIn(key, self.manifest, f"manifest.json lacks {key!r}")

    def test_hassfest_key_order_is_domain_name_then_alphabetical(self):
        keys = list(self.manifest)
        self.assertEqual(keys[:2], ["domain", "name"])
        self.assertEqual(keys[2:], sorted(keys[2:]), "manifest.json keys are not sorted")

    def test_no_requirement_is_one_home_assistant_already_ships(self):
        # hassfest refuses aiohttp and cryptography here: both are Home
        # Assistant's own dependencies.
        self.assertEqual(self.manifest["requirements"], ["psutil>=5.9.0"])

    def test_every_optional_component_it_reaches_for_is_an_after_dependency(self):
        # components.py and updates.py ask the Supervisor (hassio), floorplan.py
        # registers a Lovelace resource; a Core-only install has no Supervisor,
        # so neither may be a hard dependency.
        self.assertEqual(self.manifest["after_dependencies"], ["hassio", "lovelace", "marketplace"])
        self.assertEqual(self.manifest["dependencies"], ["repairs"])

    def test_the_domain_is_the_folder_name(self):
        self.assertEqual(self.manifest["domain"], "ha_dispatch_client")
        self.assertEqual(os.path.basename(COMPONENT), "ha_dispatch_client")

    def test_the_documentation_and_issue_links_are_https_on_the_repository(self):
        self.assertEqual(self.manifest["documentation"], "https://github.com/straplocked/ha-client")
        self.assertEqual(self.manifest["issue_tracker"], "https://github.com/straplocked/ha-client/issues")

    def test_the_iot_class_is_one_home_assistant_knows(self):
        self.assertIn(
            self.manifest["iot_class"],
            {"local_polling", "local_push", "cloud_polling", "cloud_push", "assumed_state", "calculated"},
        )

    def test_the_version_is_one_the_self_updater_reads(self):
        # The updater compares this against the release it is offered, and
        # release.sh turns it into a tag: exactly three numbers.
        self.assertRegex(self.manifest["version"], r"^\d+\.\d+\.\d+$")


class NoUrlInTranslationsTest(unittest.TestCase):
    def test_no_string_carries_a_url(self):
        # hassfest: a URL belongs in description_placeholders, not the text.
        for name in ("strings.json", os.path.join("translations", "en.json")):
            for path, leaf in _walk(_json(COMPONENT, name)):
                if isinstance(leaf, str):
                    self.assertNotRegex(
                        leaf, r"https?://", f"{name} has a URL at {'.'.join(map(str, path))}"
                    )


class HacsJsonTest(unittest.TestCase):
    def test_a_name_and_a_home_assistant_floor_are_declared(self):
        hacs = _json(REPO_ROOT, "hacs.json")
        self.assertEqual(hacs["name"], "HA Dispatch Client")
        self.assertEqual(hacs["homeassistant"], "2025.1.0")


class BrandIconTest(unittest.TestCase):
    """The catalog's `brands` check wants an icon.png in the integration's
    `brand/` folder (Home Assistant 2026.3 and later serves it from there), so
    no separate brands-repository submission is needed."""

    def _png_size(self, name):
        with open(os.path.join(COMPONENT, "brand", name), "rb") as handle:
            head = handle.read(24)
        self.assertEqual(head[:8], b"\x89PNG\r\n\x1a\n", f"{name} is not a PNG")
        return struct.unpack(">II", head[16:24])

    def test_icon_is_a_256_square_png(self):
        self.assertEqual(self._png_size("icon.png"), (256, 256))

    def test_icon_2x_is_a_512_square_png(self):
        self.assertEqual(self._png_size("icon@2x.png"), (512, 512))


class ValidateWorkflowTest(unittest.TestCase):
    def setUp(self):
        with open(WORKFLOW, encoding="utf-8") as handle:
            self.text = handle.read()

    def _on_block(self):
        match = re.search(r"^on:\n((?:[ \t]+.*\n|\n)+)", self.text, re.MULTILINE)
        self.assertIsNotNone(match, "validate.yml has no top-level `on:` block")
        return match.group(1)

    def test_it_runs_on_pull_requests_to_main_pushes_to_main_and_on_demand_only(self):
        triggers = re.findall(r"^  ([a-z_]+):", self._on_block(), re.MULTILINE)
        self.assertEqual(sorted(triggers), ["pull_request", "push", "workflow_dispatch"])

    def test_push_and_pull_request_are_both_limited_to_main(self):
        block = self._on_block()
        for trigger in ("pull_request", "push"):
            section = re.search(
                rf"^  {trigger}:\n((?:    .*\n)+)", block, re.MULTILINE
            )
            self.assertIsNotNone(section, f"{trigger} has no branch filter -- it would run on every branch")
            self.assertRegex(section.group(1), r"branches:\n\s+- main\n?$")

    def test_there_is_no_schedule(self):
        self.assertNotIn("schedule:", self.text)

    def test_both_checks_the_catalog_requires_are_run_for_an_integration(self):
        self.assertIn("uses: hacs/action@", self.text)
        self.assertRegex(self.text, r"category: integration")
        self.assertIn("uses: home-assistant/actions/hassfest@", self.text)


if __name__ == "__main__":
    unittest.main()
