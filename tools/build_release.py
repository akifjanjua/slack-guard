#!/usr/bin/env python3
"""Build a deterministic archive from the exact committed RailCall module tree."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

from verify_module_tree import (
    assert_local_tree_matches_head,
    committed_module_paths,
)

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

FORBIDDEN_BASENAMES = {
    ".env",
    "approve_token.json",
    "credentials.local.json",
    "keys.local.json",
    "slack-guard-smoke-report.json",
}
FORBIDDEN_SUFFIXES = {".key", ".patch", ".pyc"}


def fail(message: str) -> int:
    print(f"FAIL: {message}")
    return 1


def run_git(*args: str, binary: bool = False):
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=not binary,
    )
    return result.stdout


def committed_bytes(relative: str) -> bytes:
    return run_git("show", f"HEAD:{relative}", binary=True)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_entry(archive: zipfile.ZipFile, relative: str, data: bytes) -> None:
    info = zipfile.ZipInfo(relative, date_time=FIXED_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


def main() -> int:
    try:
        head = run_git("rev-parse", "HEAD").strip()
        status = run_git("status", "--porcelain", "--untracked-files=all")
    except (OSError, subprocess.CalledProcessError) as exc:
        return fail(f"Git inspection failed: {exc}")

    if status.strip():
        print("FAIL: the release must be built from a clean committed tree:")
        print(status.rstrip())
        return 1

    assert_local_tree_matches_head(ROOT)
    paths = committed_module_paths(ROOT, include_signature=True)
    required = {"module.json", "module.sig", "handlers/handler.py"}
    missing = sorted(required - set(paths))
    if missing:
        return fail(f"committed tree is missing required files: {missing}")

    for relative in paths:
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts:
            return fail(f"unsafe tracked path: {relative}")
        if path.name in FORBIDDEN_BASENAMES:
            return fail(f"forbidden tracked file: {relative}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            return fail(f"forbidden tracked file type: {relative}")
        if any(part in {".git", "dist", "__pycache__", ".pytest_cache", "receipts"} for part in path.parts):
            return fail(f"forbidden tracked directory: {relative}")

    blobs = {relative: committed_bytes(relative) for relative in paths}
    manifest = json.loads(blobs["module.json"].decode("utf-8"))
    version = str(manifest["version"])

    DIST.mkdir(exist_ok=True)
    archive_path = DIST / f"slack-guard-v{version}.zip"
    files_path = DIST / f"slack-guard-v{version}.files.json"

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for relative in paths:
            write_entry(archive, relative, blobs[relative])

    release_manifest = {
        "schema": "slack-guard-release-files.v1",
        "module_id": manifest.get("id"),
        "module_version": version,
        "command_count": len(manifest.get("commands") or []),
        "git_commit": head,
        "archive": archive_path.name,
        "archive_sha256": sha256_bytes(archive_path.read_bytes()),
        "railcall_module_tree_exact": True,
        "files": {
            relative: sha256_bytes(blobs[relative])
            for relative in paths
        },
    }
    files_path.write_text(
        json.dumps(release_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(f"Built: {archive_path}")
    print(f"File manifest: {files_path}")
    print(f"Git commit: {head}")
    print(f"Module version: {version}")
    print(f"Command count: {release_manifest['command_count']}")
    print(f"Module files packaged: {len(paths)}")
    print(f"Archive SHA-256: {release_manifest['archive_sha256']}")
    print("PASS: archive bytes came directly from immutable Git HEAD blobs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
