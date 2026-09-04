#!/usr/bin/env python3
"""Accept the committed Slack Guard release archive before publication."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from verify_module_tree import committed_module_paths

ROOT = Path(__file__).resolve().parents[1]
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

FORBIDDEN_BASENAMES = {
    ".env",
    "approve_token.json",
    "credentials.local.json",
    "keys.local.json",
    "slack-guard-smoke-report.json",
}
FORBIDDEN_SUFFIXES = {".key", ".patch", ".pyc"}


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


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


def official_railcall_command() -> list[str] | None:
    # Prefer invoking the CLI's own Python entry point with this interpreter
    # directly. The installed `railcall` wrapper script on some platforms
    # shells out to a bare `python3`, which on Windows can resolve to the
    # Microsoft Store app-execution-alias stub instead of a real interpreter
    # even when a working Python is on PATH. Calling railcall_cli.py with
    # sys.executable sidesteps that entirely.
    cli_entry_point = Path.home() / ".railcall" / "railcall_cli.py"
    if cli_entry_point.is_file():
        return [sys.executable, str(cli_entry_point)]

    candidates = [
        Path.home() / ".railcall" / "bin" / "railcall",
        Path.home() / ".railcall" / "bin" / "railcall.exe",
    ]
    cli = next((candidate for candidate in candidates if candidate.is_file()), None)
    if cli is None:
        found = shutil.which("railcall")
        cli = Path(found) if found else None
    if cli is None:
        return None

    if os.name == "nt" and cli.suffix.lower() != ".exe":
        bash = shutil.which("bash") or shutil.which("bash.exe")
        if not bash:
            fail(
                "RailCall CLI is a shell script on Windows, but bash was not found"
            )
        return [bash, str(cli)]

    return [str(cli)]


def main() -> int:
    status = run_git("status", "--porcelain", "--untracked-files=all")
    if status.strip():
        fail("release acceptance requires a clean committed tree")

    manifest = json.loads(committed_bytes("module.json").decode("utf-8"))
    version = str(manifest["version"])
    archive_path = ROOT / "dist" / f"slack-guard-v{version}.zip"
    files_path = ROOT / "dist" / f"slack-guard-v{version}.files.json"

    if not archive_path.is_file():
        fail(f"missing release archive: {archive_path}")
    if not files_path.is_file():
        fail(f"missing external file manifest: {files_path}")

    expected_paths = committed_module_paths(ROOT, include_signature=True)
    expected_set = set(expected_paths)
    release_manifest = json.loads(files_path.read_text(encoding="utf-8"))

    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            fail("release archive contains duplicate paths")
        if set(names) != expected_set:
            fail(
                "archive file set differs from committed HEAD; "
                f"missing={sorted(expected_set - set(names))}, "
                f"extra={sorted(set(names) - expected_set)}"
            )

        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts:
                fail(f"unsafe archive path: {info.filename}")
            if info.date_time != FIXED_ZIP_TIMESTAMP:
                fail(f"non-deterministic timestamp: {info.filename}")
            if path.name in FORBIDDEN_BASENAMES:
                fail(f"forbidden file packaged: {info.filename}")
            if path.suffix.lower() in FORBIDDEN_SUFFIXES:
                fail(f"forbidden file type packaged: {info.filename}")
            if any(part in {".git", "dist", "__pycache__", ".pytest_cache", "receipts"} for part in path.parts):
                fail(f"forbidden directory packaged: {info.filename}")

        for relative in expected_paths:
            packaged = archive.read(relative)
            committed = committed_bytes(relative)
            if packaged != committed:
                fail(f"packaged bytes differ from Git HEAD: {relative}")

    archive_hash = sha256_bytes(archive_path.read_bytes())
    if release_manifest.get("archive_sha256") != archive_hash:
        fail("external manifest archive SHA-256 mismatch")
    if release_manifest.get("git_commit") != run_git("rev-parse", "HEAD").strip():
        fail("external manifest Git commit mismatch")
    if set(release_manifest.get("files") or {}) != expected_set:
        fail("external manifest file set differs from committed HEAD")
    for relative, expected_hash in release_manifest["files"].items():
        if expected_hash != sha256_bytes(committed_bytes(relative)):
            fail(f"external manifest file hash mismatch: {relative}")

    with tempfile.TemporaryDirectory(prefix="slack-guard-v1-") as temp_dir:
        extracted = Path(temp_dir)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(extracted)

        subprocess.run(
            [sys.executable, str(extracted / "tools" / "validate_release.py")],
            cwd=extracted,
            check=True,
        )
        subprocess.run(
            [sys.executable, str(extracted / "tools" / "verify_module_tree.py"), str(extracted)],
            cwd=extracted,
            check=True,
        )

        cli_command = official_railcall_command()
        if cli_command is not None:
            subprocess.run(
                [*cli_command, "market", "module", "verify", str(extracted)],
                cwd=extracted,
                check=True,
            )
            official_status = "PASS: official RailCall verifier accepted the extracted archive"
        else:
            official_status = "INFO: official RailCall CLI unavailable; independent v2 verification passed"

    first = archive_path.read_bytes()
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_release.py")],
        cwd=ROOT,
        check=True,
    )
    second = archive_path.read_bytes()
    if first != second:
        fail("release archive is not byte-for-byte reproducible")

    print("PASS: archive contains exactly the committed RailCall module tree")
    print("PASS: every packaged byte matches its committed Git blob")
    print("PASS: deterministic ZIP metadata and byte-for-byte rebuild verified")
    print("PASS: external per-file manifest and archive SHA-256 verified")
    print("PASS: extracted static validation and Ed25519 tree verification passed")
    print(official_status)
    print("PASS: credentials, receipts, patches, caches, and local output are absent")
    print("RELEASE ACCEPTANCE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
