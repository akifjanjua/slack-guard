#!/usr/bin/env python3
"""Verify and enumerate the exact RailCall v2 module tree."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


DEFAULT_IGNORED_DIRS = {".git"}
DEFAULT_IGNORED_FILES = {".gitignore", "module.sig"}


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def canonical_manifest(manifest: dict) -> bytes:
    body = {key: value for key, value in manifest.items() if key != "signature"}
    return json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def parse_moduleignore(data: bytes | str | None) -> list[str]:
    if data is None:
        return []
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    patterns: list[str] = []
    for raw in text.splitlines():
        line = raw.strip().replace("\\", "/")
        if not line or line.startswith("#"):
            continue
        while line.startswith("./"):
            line = line[2:]
        patterns.append(line)
    return patterns


def _pattern_matches(relative: str, is_dir: bool, pattern: str) -> bool:
    rel = relative.strip("/")
    pat = pattern.strip()
    if not pat:
        return False

    directory_pattern = pat.endswith("/")
    pat = pat.strip("/")
    if not pat:
        return False

    if directory_pattern:
        return rel == pat or rel.startswith(pat + "/")

    if "/" in pat:
        return fnmatch.fnmatchcase(rel, pat)

    parts = PurePosixPath(rel).parts
    return any(fnmatch.fnmatchcase(part, pat) for part in parts)


def path_is_ignored(relative: str, is_dir: bool, patterns: list[str]) -> bool:
    rel = relative.replace("\\", "/").strip("/")
    parts = PurePosixPath(rel).parts
    if any(part in DEFAULT_IGNORED_DIRS for part in parts):
        return True
    if not is_dir and rel in DEFAULT_IGNORED_FILES:
        return True
    return any(_pattern_matches(rel, is_dir, pattern) for pattern in patterns)


def local_moduleignore_patterns(root: Path) -> list[str]:
    path = root / ".moduleignore"
    return parse_moduleignore(path.read_bytes() if path.is_file() else None)


def committed_bytes(root: Path, relative: str) -> bytes:
    return subprocess.check_output(
        ["git", "show", f"HEAD:{relative}"],
        cwd=root,
    )


def committed_paths(root: Path) -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        cwd=root,
        text=True,
    )
    return sorted(line.strip() for line in output.splitlines() if line.strip())


def committed_moduleignore_patterns(root: Path) -> list[str]:
    try:
        data = committed_bytes(root, ".moduleignore")
    except subprocess.CalledProcessError:
        return []
    return parse_moduleignore(data)


def committed_module_paths(root: Path, *, include_signature: bool) -> list[str]:
    patterns = committed_moduleignore_patterns(root)
    selected: list[str] = []
    for relative in committed_paths(root):
        if relative == "module.sig" and include_signature:
            selected.append(relative)
            continue
        if path_is_ignored(relative, False, patterns):
            continue
        selected.append(relative)
    return sorted(selected)


def signed_tree(root: Path) -> list[tuple[str, str]]:
    patterns = local_moduleignore_patterns(root)
    files: list[tuple[str, str]] = []
    root = root.resolve()

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        kept_dirs: list[str] = []
        for dirname in dirnames:
            relative = (current / dirname).relative_to(root).as_posix()
            if not path_is_ignored(relative, True, patterns):
                kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in filenames:
            path = current / filename
            relative = path.relative_to(root).as_posix()
            if path_is_ignored(relative, False, patterns):
                continue
            files.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))

    files.sort(key=lambda item: item[0])
    return files


def is_git_worktree(root: Path) -> bool:
    """True when root sits inside a Git working tree.

    release_acceptance_test.py runs this verifier against an extracted archive
    in a temp directory, which carries no Git metadata. The HEAD comparison is
    meaningless there and must be skipped rather than crash.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def assert_local_tree_matches_head(root: Path) -> None:
    local = {relative for relative, _digest in signed_tree(root)}
    committed = set(committed_module_paths(root, include_signature=False))
    if local != committed:
        fail(
            "local RailCall tree differs from committed module tree; "
            f"local_only={sorted(local - committed)}, "
            f"HEAD_only={sorted(committed - local)}"
        )


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    manifest_path = root / "module.json"
    signature_path = root / "module.sig"
    handler_path = root / "handlers" / "handler.py"

    for path in (manifest_path, signature_path, handler_path):
        if not path.is_file():
            fail(f"missing required module file: {path.relative_to(root)}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("manifest_version") or 1) < 2:
        fail("module is not using RailCall v2 tree signing")

    pubkey_hex = str(manifest.get("publisher_pubkey") or "").strip()
    signature_hex = signature_path.read_text(encoding="ascii").strip()
    if len(pubkey_hex) != 64:
        fail("publisher_pubkey must contain 64 hexadecimal characters")
    if len(signature_hex) != 128:
        fail("module.sig must contain 128 hexadecimal characters")

    tree = signed_tree(root)
    tree_manifest = "".join(
        f"{relative}\t{digest}\n"
        for relative, digest in tree
    ).encode("utf-8")
    payload = canonical_manifest(manifest) + b"\n" + tree_manifest

    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex))
        public_key.verify(bytes.fromhex(signature_hex), payload)
    except (ValueError, InvalidSignature) as exc:
        fail(f"RailCall v2 module tree signature is invalid: {type(exc).__name__}")

    print("PASS: RailCall v2 module tree signature is valid")
    print(f"Module ID: {manifest.get('id')}")
    print(f"Version: {manifest.get('version')}")
    print(f"Commands: {len(manifest.get('commands') or [])}")
    print(f"Signed tree files: {len(tree)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
