#!/usr/bin/env bash
#
# Build and sign a client release archive.
#
# Produces the three assets the updater and HACS both consume:
#   ha_dispatch_client.zip         integration files at the archive ROOT
#   ha_dispatch_client.zip.sha256  hex digest
#   ha_dispatch_client.zip.sig     base64 Ed25519 signature over the zip bytes
#
# Usage:
#   scripts/release.sh --key hadc-signing.key --key-id hadc-2026-01 [--publish]
#
# --publish uploads to a GitHub release via gh. Without it, the assets are left
# in dist/ for inspection -- the default, because signing and publishing are
# worth doing as two separate decisions.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="$REPO_ROOT/custom_components/ha_dispatch_client"
DIST_DIR="$REPO_ROOT/dist"
ARCHIVE_NAME="ha_dispatch_client.zip"

KEY_PATH=""
KEY_ID=""
PUBLISH=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --key)     KEY_PATH="$2"; shift 2 ;;
    --key-id)  KEY_ID="$2";   shift 2 ;;
    --publish) PUBLISH=1;     shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$KEY_PATH" ]] || { echo "ERROR: --key is required" >&2; exit 2; }
[[ -n "$KEY_ID"   ]] || { echo "ERROR: --key-id is required" >&2; exit 2; }
[[ -f "$KEY_PATH" ]] || { echo "ERROR: signing key not found: $KEY_PATH" >&2; exit 2; }

# --- Version consistency ----------------------------------------------------
# A version mismatch here becomes a client that downloads an archive and then
# rejects it, so check all three before building anything.

VERSION_FILE="$(tr -d '[:space:]' < "$REPO_ROOT/VERSION")"
VERSION_MANIFEST="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['version'])" "$SRC_DIR/manifest.json")"

if [[ "$VERSION_FILE" != "$VERSION_MANIFEST" ]]; then
  echo "ERROR: VERSION ($VERSION_FILE) != manifest.json ($VERSION_MANIFEST)" >&2
  exit 1
fi

TAG="v$VERSION_FILE"
if git -C "$REPO_ROOT" rev-parse "$TAG" >/dev/null 2>&1; then
  echo "Tag $TAG already exists locally."
else
  echo "NOTE: tag $TAG does not exist yet; create it before publishing."
fi

echo "Building $ARCHIVE_NAME for version $VERSION_FILE"

# --- Build ------------------------------------------------------------------

rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR/staging"

# Integration files sit at the archive root -- the layout HACS expects for a
# release asset, and what validate_archive() enforces client-side.
rsync -a --exclude='__pycache__' --exclude='*.pyc' "$SRC_DIR/" "$DIST_DIR/staging/"

( cd "$DIST_DIR/staging" && zip -qr "../$ARCHIVE_NAME" . -x '.*' )
rm -rf "$DIST_DIR/staging"

ARCHIVE="$DIST_DIR/$ARCHIVE_NAME"

python3 - "$ARCHIVE" "$VERSION_FILE" <<'PY'
import json, sys, zipfile
archive, expected = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(archive) as zf:
    names = zf.namelist()
    if "manifest.json" not in names:
        sys.exit("ERROR: manifest.json is not at the archive root")
    manifest = json.loads(zf.read("manifest.json"))
if manifest["version"] != expected:
    sys.exit(f"ERROR: archived manifest says {manifest['version']}, expected {expected}")
print(f"Archive validated: {len(names)} members, version {manifest['version']}")
PY

# --- Sign -------------------------------------------------------------------

sha256sum "$ARCHIVE" | awk '{print $1}' > "$ARCHIVE.sha256"
echo "sha256: $(cat "$ARCHIVE.sha256")"

python3 - "$ARCHIVE" "$KEY_PATH" <<'PY'
import base64, sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

archive, key_path = sys.argv[1], sys.argv[2]
with open(key_path, "rb") as handle:
    key = serialization.load_pem_private_key(handle.read(), password=None)
if not isinstance(key, Ed25519PrivateKey):
    sys.exit("ERROR: signing key is not an Ed25519 private key")

with open(archive, "rb") as handle:
    payload = handle.read()

with open(archive + ".sig", "w", encoding="utf-8") as handle:
    handle.write(base64.b64encode(key.sign(payload)).decode())
print("Signature written")
PY

echo
echo "Assets in $DIST_DIR:"
ls -1 "$DIST_DIR"
echo
echo "key_id for the release body: $KEY_ID"

# --- Publish ----------------------------------------------------------------

if [[ "$PUBLISH" -eq 1 ]]; then
  command -v gh >/dev/null || { echo "ERROR: gh is not installed" >&2; exit 1; }
  echo "Publishing GitHub release $TAG"
  gh release create "$TAG" \
    "$ARCHIVE" "$ARCHIVE.sha256" "$ARCHIVE.sig" \
    --title "$TAG" \
    --notes "key_id: $KEY_ID"
  echo "Published. The server ingests the assets on its next GitHub poll."
else
  echo "Not published (pass --publish to upload with gh)."
fi
