# Marketplace Catalog Submission

What it takes to get HA Dispatch Client into the Home Assistant Marketplace
catalog (the former HACS default list), what this repository already does
about it, and the steps that are **yours to do by hand**.

Nothing in this document has been done. Every outward-facing step below
(repository settings, a release, a pull request to another repository) is a
decision for the owner.

---

## 1. Research record (read 2026-11-11)

| Question | Answer | Source |
|---|---|---|
| Is there still a separate default list? | The catalog is still fed by a pull request to the `hacs/default` repository. HACS became the built-in Marketplace in Home Assistant 2026.11 | <https://www.home-assistant.io/blog/2026/11/04/release-202611/> and <https://www.hacs.xyz/docs/publish/include/> |
| What must a repository meet? | Public on GitHub, passes the HACS action with no ignores, passes hassfest, has a full GitHub **release** (not just a tag), submitted by the owner from a personal account | <https://www.hacs.xyz/docs/publish/include/> |
| Automated checks | brands, core-domain conflict, manifest, hacs-validation, `hacs.json` has a `name`, not archived, at least one release, owner, OSI licence (GitHub must detect it), description + issues enabled + topics, JSON lint and sorted-order lint on the PR | same |
| Integration layout | one integration per repository under `custom_components/<domain>/`; `README.md` and `hacs.json` in the root; manifest keys `domain`, `documentation`, `issue_tracker`, `codeowners`, `name`, `version` | <https://www.hacs.xyz/docs/publish/integration/> |
| `hacs.json` keys | `name` (required), `homeassistant` (minimum version), `zip_release` + `filename`, `content_in_root`, `country`, and others | <https://www.hacs.xyz/docs/publish/start/> |
| Brands: is a `home-assistant/brands` PR still needed? | **No.** From Home Assistant 2026.3 a custom integration ships its own images in `custom_components/<domain>/brand/` and they take priority. The HACS `brands` check accepts a `brand/` directory holding at least `icon.png` | <https://contributors.home-assistant.io/blog/2026/02/24/brands-proxy-api>, <https://contributors.home-assistant.io/core/integrations/brands/>, <https://www.hacs.xyz/docs/publish/integration/> |
| The two actions | `hacs/action` with `category: integration`; `home-assistant/actions/hassfest` | <https://www.hacs.xyz/docs/publish/action/> |
| Manifest rules (hassfest) | `domain` first, `name` second, remaining keys alphabetical; `documentation` must be https and a custom integration's own; `iot_class` required; `version` required for a custom integration; never list `persistent_notification` or `homeassistant` as dependencies | <https://contributors.home-assistant.io/core/integrations/manifest/> |

Gaps in what could be read, so check them on the day: the exact text of the
"coming from HACS" publishing section of the Marketplace documentation was
not retrievable, and the release post says little about publishing. The
`hacs/default` PR template was read as a summary, not verbatim (section 4).

---

## 2. What this repository already does

| Requirement | Where |
|---|---|
| HACS action and hassfest on every PR to `main`, every push to `main`, and on demand | `.github/workflows/validate.yml`. It deliberately has **no** bare `push:` and no schedule, so batch and feature branches never spend CI minutes |
| Manifest valid, keys in hassfest order, https documentation and issue tracker, `iot_class`, `integration_type`, `version` | `custom_components/ha_dispatch_client/manifest.json`, pinned by `tests/test_marketplace_metadata.py` |
| `hacs.json` with a name and a Home Assistant floor (`2025.1.0`) | `hacs.json` |
| Brand icon inside the integration | `custom_components/ha_dispatch_client/brand/icon.png` (256 px) and `icon@2x.png` (512 px) |
| Runtime translations | `custom_components/ha_dispatch_client/translations/en.json`, held byte-for-byte equal in content to `strings.json` by a test |
| README for the current version, install from the Marketplace | `README.md` |
| OSI licence | `LICENSE` (MIT) |
| A release whose archive carries the signed files | `scripts/release.sh` (unchanged) |

The Home Assistant floor is `2025.1.0` because that is where the backup
manager's agent API appears (the backup reporter degrades gracefully before it)
and where the update-entity install path the client uses has been stable since
2024.11. It was chosen from reading the code, not from running the client on
older versions: nobody has, so say so if a house on 2024.x reports a problem.

Not done here, because nothing can run it: neither hassfest nor the HACS
action has been run against this tree. Home Assistant is not installed in the
development environment. The first real run is the first `workflow_dispatch`
(step 3 below), and it is the real check. Two things most likely to need a
look: hassfest reading `marketplace` under `after_dependencies` (it is a
core integration from 2026.11, and the hassfest image follows Core), and the
HACS `brands` check reading the `brand/` folder.

---

## 3. Your checklist, in order

### a. Repository settings (the `topics` and `description` checks)

```bash
gh repo edit straplocked/ha-client \
  --description "Home Assistant integration that reports to your installer's HA Dispatch dashboard, with every remote action approved by you first" \
  --add-topic home-assistant \
  --add-topic hacs \
  --add-topic homeassistant-integration \
  --add-topic custom-component
gh repo view straplocked/ha-client --json hasIssuesEnabled,description,repositoryTopics,licenseInfo
```

Confirm in the output: issues enabled, a description, topics present, and
`licenseInfo.key` is `mit` (not `NOASSERTION`).

### b. Merge this work to `main`

The validation workflow only runs from `main`. Merging the integration branch
is a separate decision from this checklist.

### c. Run the validation once, by hand

```bash
gh workflow run validate.yml --repo straplocked/ha-client --ref main
gh run list --workflow validate.yml --repo straplocked/ha-client --limit 3
```

Both jobs (**HACS validation**, **Hassfest**) must be green, with **no
`ignore:` key** in the HACS step. Copy the URL of each green run: the PR
template asks for them.

### d. Brands

Nothing to submit. Since Home Assistant 2026.3 the images in `brand/` are
used directly. Do **not** open a pull request to `home-assistant/brands`. If
the HACS `brands` check nevertheless fails on the first run, say what it says
and fix the folder; do not add `brands` to an `ignore:`, because the catalog
requires every check to pass unless agreed otherwise with its maintainers.

### e. Cut a full release (not just a tag)

The catalog and the self-updater both read GitHub releases. Use the existing
tooling, which signs the archive with the key you hold:

```bash
git switch main && git pull
git tag v1.10.0 && git push origin v1.10.0
./scripts/release.sh --key hadc-signing.key --key-id hadc-2026-01 --publish
```

Check that the release shows `ha_dispatch_client.zip`, `.zip.sha256` and
`.zip.sig`. The tag must equal `v` plus `VERSION` (`v1.10.0`), which is
also the `manifest.json` version. Run the validation again afterwards; the
HACS action reads the latest release.

### f. The pull request to `hacs/default`

1. Fork <https://github.com/hacs/default> **from your personal account**
   (not an organisation: the PR must stay editable).
2. In the fork, branch from `master`; do not commit to `master`.
3. Edit the file named `integration`. It is a JSON array of `"owner/repo"`
   strings in case-insensitive alphabetical order. Insert
   `"straplocked/ha-client",` in its sorted place, not at the end, with the
   same two-space indent and trailing comma as its neighbours. Nothing else
   in the file changes (the lint checks reject unsorted or invalid JSON).
4. Open the PR as a **draft** first, fill in the template, then mark it ready.
   Do not request reviews (the template says not to); a review can take
   months.

Title: `Add straplocked/ha-client`

Body (the template's own checklist and links are what it asks for; keep the
template's wording where it differs from this and paste the answers below
into its fields):

```text
Repository: https://github.com/straplocked/ha-client
Category: integration

Checklist
- [x] I read the publishing documentation.
- [x] The HACS action is added to the repository and passes, with no ignore key.
- [x] The hassfest action is added to the repository and passes.
- [x] A full release was created after the validation actions succeeded.

Links
- Release: https://github.com/straplocked/ha-client/releases/tag/v1.10.0
- HACS action run: <URL of the green "HACS validation" run>
- Hassfest run: <URL of the green "Hassfest" run>

Notes for the reviewer
- Brand images ship in custom_components/ha_dispatch_client/brand/
  (Home Assistant 2026.3 and later), so there is no brands-repository entry.
- The integration's domain is ha_dispatch_client; it does not override or
  test any core integration.
- Release archives are Ed25519-signed and the integration verifies them
  against a key built into it before installing a self-update. The README
  explains how that coexists with installing from the catalog.
```

Replace the two `<URL ...>` placeholders with the real run links, and the
release link if the tag differs. Do not tick a box that is not true: a
misrepresented statement closes the PR without notice.

### g. After it merges

The repository is picked up on the catalog's next scheduled scan. Then
confirm that **HA Dispatch Client** is found by name in the Marketplace on a
Home Assistant 2026.11 install, and that installing from it and then taking a
self-update leaves the Marketplace showing the new version installed (the
sync in `community_store.py` is what does this).

---

## 4. Open questions for the owner

- **`zip_release`.** `hacs.json` names `ha_dispatch_client.zip` but does not
  set `zip_release: true`, so the Marketplace installs the files from the tag
  and ignores `filename`. Setting `zip_release: true` would make it install
  exactly the signed archive attached to the release, at the cost that a
  release without the asset breaks catalog installs. It was left as it was in
  this batch because it changes what every existing custom-repository install
  downloads.
- **Floor.** `2025.1.0` (section 2). Raise it if you only support recent
  releases.
- **Workflow actions are on moving refs** (`hacs/action@main`,
  `home-assistant/actions/hassfest@master`, as their documentation shows). Pin
  them to a release once the first run is green if you prefer.
