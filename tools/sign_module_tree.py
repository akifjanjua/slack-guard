#!/usr/bin/env python3
"""Sign the RailCall v2 module tree over the exact bytes already on disk.

`railcall market module sign` unconditionally rewrites module.json with
`json.dump(..., indent=2)` in text mode before signing. On Windows that
introduces CRLF and destroys the newline-free manifest representation this
module relies on. The manifest has to stay free of physical newline bytes
because a station writes it from a wire string in text mode at install time,
so any LF becomes CRLF (and any CRLF becomes CR CR LF) on a Windows buyer's
machine, invalidating the signature the moment the module lands.

This signer produces the identical payload but never touches module.json:

    canonical(module.json) + b"\\n" + tree_manifest

It refuses to sign a tree that would not reproduce on a clean checkout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_module_tree import (  # noqa: E402
    assert_local_tree_matches_head,
    canonical_manifest,
    fail,
    is_git_worktree,
    signed_tree,
)


PUBLISHER_PATH = Path.home() / ".railcall" / "marketplace_publisher.json"


def load_publisher() -> tuple[str, str]:
    if not PUBLISHER_PATH.is_file():
        fail(
            f"publisher identity not found at {PUBLISHER_PATH}; "
            "run `railcall market publisher init` first"
        )
    publisher = json.loads(PUBLISHER_PATH.read_text(encoding="utf-8"))
    seed_hex = str(publisher.get("seed_hex") or "").strip()
    pubkey_hex = str(publisher.get("pubkey_hex") or "").strip()
    if len(seed_hex) != 64 or len(pubkey_hex) != 64:
        fail("publisher identity is missing valid Ed25519 key material")
    return seed_hex, pubkey_hex


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    manifest_path = root / "module.json"
    signature_path = root / "module.sig"

    if not manifest_path.is_file():
        fail("missing module.json")

    manifest_bytes = manifest_path.read_bytes()
    if b"\n" in manifest_bytes or b"\r" in manifest_bytes:
        fail(
            "module.json contains physical newline bytes; the marketplace "
            "install path rewrites it in text mode, so newlines would break "
            "the signature on a buyer's machine. Re-minify it before signing."
        )

    manifest = json.loads(manifest_bytes.decode("utf-8"))
    if int(manifest.get("manifest_version") or 1) < 2:
        fail("module is not using RailCall v2 tree signing")

    # A signature only reproduces in CI if the signed tree is exactly the
    # committed tree. Untracked strays are the whole reason this script exists.
    if is_git_worktree(root):
        assert_local_tree_matches_head(root)
    else:
        print("WARNING: not a Git working tree; skipping HEAD comparison")

    seed_hex, pubkey_hex = load_publisher()
    if str(manifest.get("publisher_pubkey") or "").strip() != pubkey_hex:
        fail("module.json publisher_pubkey does not match the local identity")

    tree = signed_tree(root)
    tree_manifest = "".join(
        f"{relative}\t{digest}\n"
        for relative, digest in tree
    ).encode("utf-8")
    payload = canonical_manifest(manifest) + b"\n" + tree_manifest

    signature = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(seed_hex)
    ).sign(payload)
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex)).verify(
        signature, payload
    )

    # Binary write: module.sig stays LF on every platform.
    signature_path.write_bytes(signature.hex().encode("ascii") + b"\n")

    if manifest_path.read_bytes() != manifest_bytes:
        fail("module.json changed during signing")

    print("PASS: signed the on-disk RailCall v2 module tree")
    print(f"Version: {manifest.get('version')}")
    print(f"Signed tree files: {len(tree)}")
    print(f"Manifest bytes: {len(manifest_bytes)} (newline-free)")
    print(f"Publisher fingerprint: {pubkey_hex[:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
