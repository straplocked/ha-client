#!/usr/bin/env python3
"""Generate an Ed25519 release signing keypair for the HA Dispatch client.

The private key signs release archives. The public key is pinned in
const.py::RELEASE_SIGNING_KEYS, which is what stops a compromised Dispatch
server from serving arbitrary code to the fleet -- so the private key must live
somewhere the Dispatch server cannot reach. An offline workstation or a release
pipeline secret, never the server.

Usage:
    python3 scripts/generate_signing_key.py --key-id hadc-2026-01

Writes the private key to the path given by --out (default: ./hadc-signing.key,
which .gitignore already excludes) and prints the const.py entry to add.
"""
import argparse
import base64
import os
import stat
import sys
from pathlib import Path

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:
    sys.exit("cryptography is required: pip install cryptography")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--key-id",
        required=True,
        help="Identifier the server quotes in its release payload, e.g. hadc-2026-01",
    )
    parser.add_argument(
        "--out",
        default="hadc-signing.key",
        help="Where to write the private key (default: hadc-signing.key)",
    )
    args = parser.parse_args()

    out = Path(args.out)
    if out.exists():
        sys.exit(f"{out} already exists -- refusing to overwrite a signing key")

    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    # Written 0600 before any bytes land, so the key is never briefly readable.
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "wb") as handle:
        handle.write(pem)

    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_b64 = base64.b64encode(public_raw).decode()

    print(f"Private key written to {out} (mode 0600). Keep it off the Dispatch server.")
    print()
    print("Add this to RELEASE_SIGNING_KEYS in")
    print("custom_components/ha_dispatch_client/const.py:")
    print()
    print(f'    "{args.key_id}": "{public_b64}",')
    print()
    print("Then ship a release signed by an ALREADY-TRUSTED key that contains the")
    print("new entry, and only start signing with this key once the fleet has it.")
    print("See docs/technical/self-update.md section 7.3 for the rotation order.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
