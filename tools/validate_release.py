#!/usr/bin/env python3
"""Static release validation for Slack Guard."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "module.json"
HANDLER_PATH = ROOT / "handlers" / "handler.py"

EXPECTED_COMMANDS = {
    "slack.get_team_info": "read",
}

FORBIDDEN_SOURCE_PATTERNS = {
    "disabled TLS verification": r"\bCERT_NONE\b|check_hostname\s*=\s*False",
    "insecure curl option": r"(?<!\w)--insecure\b|(?<!\w)curl\s+-k\b",
    "credential-file bypass": r"credentials\.local\.json|Path\.home\(\)",
    "environment-secret bypass": r"os\.environ|os\.getenv",
    "subprocess or curl fallback": r"\bsubprocess\b|\bshutil\b",
    "probable embedded Slack token": r"\bxox[bpaes]-[A-Za-z0-9-]{10,}\b",
    "probable private key block": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def load_manifest() -> dict:
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing {MANIFEST_PATH.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"module.json is invalid JSON: {exc}")


def main() -> int:
    if not HANDLER_PATH.is_file():
        fail("missing handlers/handler.py")

    manifest = load_manifest()
    source = HANDLER_PATH.read_text(encoding="utf-8")

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        fail(f"handler.py syntax error: {exc}")

    required_keys = {
        "id",
        "name",
        "version",
        "publisher_pubkey",
        "provider",
        "auth",
        "credential_spec",
        "allowed_destinations",
        "description",
        "commands",
        "homepage",
        "tests_url",
    }
    missing = sorted(required_keys - set(manifest))
    if missing:
        fail(f"module.json is missing keys: {', '.join(missing)}")

    if manifest["id"] != "muhammad-akif-janjua/slack-guard":
        fail("unexpected module id")

    if manifest["provider"] != "slack":
        fail("provider must be slack")

    for link_name in ("homepage", "tests_url"):
        link = manifest.get(link_name)
        if not isinstance(link, str) or not link.startswith("https://"):
            fail(f"{link_name} must be an HTTPS URL")

    expected_auth = {
        "type": "api_key",
        "vault_provider": "slack",
        "header": "Authorization",
        "secret_field": "SLACK_BOT_TOKEN",
        "docs": "https://api.slack.com/apps",
    }
    if manifest.get("auth") != expected_auth:
        fail("auth block does not match the reviewer-required Slack vault declaration")

    expected_allowed_destinations = [{"provider": "slack", "hosts": ["slack.com"]}]
    if manifest.get("allowed_destinations") != expected_allowed_destinations:
        fail(
            "allowed_destinations must declare exactly the Slack API host "
            f"({expected_allowed_destinations!r}) and zero LLM/model-provider entries"
        )

    expected_credential_spec = {
        "provider": "slack",
        "category": "Chat & Collaboration",
        "name": "Slack",
        "required": ["SLACK_BOT_TOKEN"],
        "optional": [],
        "shape": "dict",
        "risk": "high",
        "read_write": "write",
    }
    if manifest.get("credential_spec") != expected_credential_spec:
        fail("credential_spec does not match the reviewer-required Slack declaration")

    if not re.fullmatch(r"\d+\.\d+\.\d+", str(manifest["version"])):
        fail("version must use semantic x.y.z form")

    pubkey = str(manifest["publisher_pubkey"])
    if not re.fullmatch(r"[0-9a-fA-F]{64}", pubkey):
        fail("publisher_pubkey must be 64 hexadecimal characters")

    commands = manifest.get("commands")
    if not isinstance(commands, list):
        fail("commands must be a list")

    command_ids = [command.get("id") for command in commands]
    if len(command_ids) != len(set(command_ids)):
        fail("command ids are not unique")

    if set(command_ids) != set(EXPECTED_COMMANDS):
        missing_ids = sorted(set(EXPECTED_COMMANDS) - set(command_ids))
        extra_ids = sorted(set(command_ids) - set(EXPECTED_COMMANDS))
        fail(f"command set mismatch; missing={missing_ids}, extra={extra_ids}")

    functions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    for command in commands:
        command_id = command["id"]
        expected_mode = EXPECTED_COMMANDS[command_id]

        if command.get("provider") != "slack":
            fail(f"{command_id}: provider must be slack")

        if command.get("mode") != expected_mode:
            fail(f"{command_id}: expected mode {expected_mode!r}")

        if command.get("preview") is not True:
            fail(f"{command_id}: preview must be true")

        if command.get("receipt_required") is not True:
            fail(f"{command_id}: receipt_required must be true")

        function_name = command_id.replace(".", "_")
        if function_name not in functions:
            fail(f"{command_id}: handler function {function_name} not found")

        input_schema = command.get("input_schema") or {}
        for field_name, field_spec in input_schema.items():
            description = field_spec.get("description") if isinstance(field_spec, dict) else None
            if not isinstance(description, str) or not description.strip():
                fail(
                    f"{command_id}.{field_name}: input_schema fields must carry a "
                    "non-empty description so Studio's Sends-tab form shows inline "
                    "guidance instead of a bare field"
                )

        if expected_mode == "write_requires_approval":
            if command.get("risk") not in {"medium", "high"}:
                fail(f"{command_id}: write risk must be medium or high")

    for label, pattern in FORBIDDEN_SOURCE_PATTERNS.items():
        if re.search(pattern, source):
            fail(f"{label} detected in handler.py")

    model_egress_markers = {
        "station_llm": r"\bstation_llm\b",
        "OpenAI SDK/host": r"\bopenai\b|api\.openai\.com",
        "Anthropic SDK/host": r"\banthropic\b|api\.anthropic\.com",
        "Groq SDK/host": r"\bgroq\b|api\.groq\.com",
        "Gemini SDK/host": r"google\.generativeai|generativelanguage\.googleapis\.com",
        "xAI SDK/host": r"\bxai\b|api\.x\.ai",
        "Ollama model endpoint": r"\bollama\b|localhost:11434|127\.0\.0\.1:11434",
    }
    for label, pattern in model_egress_markers.items():
        if re.search(pattern, source, flags=re.IGNORECASE):
            fail(f"undeclared model-provider egress marker detected: {label}")

    if "https://slack.com/api" not in source:
        fail("Slack API base URL not found")

    if 'vault_get("slack")' not in source:
        fail('vault_get("slack") is not used')

    if "certifi.where()" not in source:
        fail("certifi-backed SSL context is not configured")

    # No write command exists yet in v0.1.0 (Tier 1 read only), so no call
    # site passes is_write=True literally - but the write-safety path itself
    # (the is_write parameter and its "outcome is unknown" handling in
    # _request) must already exist in the transport layer, ready for the
    # first write command the build order adds.
    if "is_write" not in source or "outcome is unknown" not in source:
        fail("unknown-outcome handling for writes is missing")

    if 'parsed.get("ok")' not in source:
        fail("Slack's ok:false failure signal is not handled")

    release_builder = ROOT / "tools" / "build_release.py"
    release_acceptance = ROOT / "tools" / "release_acceptance_test.py"
    signature_verifier = ROOT / "tools" / "verify_module_tree.py"
    ci_workflow = ROOT / ".github" / "workflows" / "slack-guard-tests.yml"

    for path in (
        release_builder,
        release_acceptance,
        signature_verifier,
        ci_workflow,
    ):
        if not path.is_file():
            fail(f"missing release pipeline file: {path.relative_to(ROOT)}")

    workflow_text = ci_workflow.read_text(encoding="utf-8")
    for command in (
        "python tools/command_logic_test.py",
        "python tools/egress_contract_test.py",
        "python tools/verify_module_tree.py .",
        "python tools/build_release.py",
        "python tools/release_acceptance_test.py",
    ):
        if command not in workflow_text:
            fail(f"CI workflow does not run: {command}")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if len(readme.split()) > 500:
        fail("README exceeds the contest's 500-word limit")

    listing = (ROOT / "MARKETPLACE_LISTING.md").read_text(encoding="utf-8")
    raw_template_headers = (
        "## Listing title",
        "## Short tagline",
        "## Pricing recommendation",
    )
    if any(header in listing for header in raw_template_headers):
        fail("marketplace listing still contains raw template headings")
    if "contest:2026Q3" not in listing:
        fail("marketplace listing is missing contest:2026Q3")

    print("PASS: module.json is valid")
    print("PASS: handler.py parses")
    print(f"PASS: {len(EXPECTED_COMMANDS)} expected command(s) are present")
    print("PASS: homepage and tests_url are declared")
    print("PASS: signed manifest declares its egress destination (slack.com only, zero LLM/model-provider destinations)")
    print("PASS: credential_spec matches the reviewer-required Slack declaration")
    print("PASS: all write commands require approval")
    print("PASS: previews and signed receipts are required")
    print("PASS: reviewer-blocked credential and subprocess paths are absent")
    print("PASS: vault-only auth, certifi TLS, and unknown-write handling are present")
    print("PASS: Slack's ok:false failure signal is handled")
    print("PASS: README is within 500 words and listing copy is clean")
    print("PASS: signed-tree verification, archive generation, and acceptance checks are wired into CI")
    print(f"PASS: Slack Guard {manifest['version']} is ready for security tests and signing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
