#!/usr/bin/env python3
"""
Safe local smoke test for Slack Guard.

Executes slack.get_team_info (the only command so far - a read with no side
effect) against a running RailCall Studio instance and confirms the receipt
looks right. There are no write commands yet to preview.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_ENDPOINT = "http://127.0.0.1:8799"
DEFAULT_WORKSPACE = Path.home() / ".railcall" / "station" / ".railcall_workspace"


class SmokeFailure(RuntimeError):
    pass


def _discover_session_token(workspace: Path) -> str:
    """Same discovery order RailCall's own MCP server uses in
    mcp_server.py's _station_execute_command: try the CLI-specific token
    first, then the main per-startup session token. Both are same-user,
    0600 local files under the workspace directory Studio was started with."""
    for name in ("cli_session_token", "session_token"):
        path = workspace / name
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if token:
            return token
    raise SmokeFailure(
        f"No session token found under {workspace}. Is RailCall Studio running? "
        "Pass --session-token or --workspace to point at the right instance."
    )


def call(endpoint: str, route: str, payload: dict[str, Any], session_token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        endpoint.rstrip("/") + route,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Origin": endpoint.rstrip("/"),
            "X-RailCall-Session": session_token,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SmokeFailure(f"HTTP {exc.code} from {route}: {body[:400]}") from exc
    except urllib.error.URLError as exc:
        raise SmokeFailure(f"Could not reach RailCall Studio: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"RailCall returned invalid JSON from {route}") from exc


def execute(endpoint: str, command_id: str, inputs: dict[str, Any], session_token: str) -> dict[str, Any]:
    return call(
        endpoint,
        "/api/commands/execute",
        {"command_id": command_id, "inputs": inputs, "intent": f"Slack Guard smoke test: {command_id}"},
        session_token,
    )


def receipt_output(result: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt = result.get("receipt")
    if not isinstance(receipt, dict):
        raise SmokeFailure("response contains no receipt object")
    output = receipt.get("output")
    if output is None:
        output = {}
    if not isinstance(output, dict):
        raise SmokeFailure("receipt output is not an object")
    return receipt, output


def require_executed(name: str, result: dict[str, Any]) -> dict[str, Any]:
    receipt, output = receipt_output(result)
    status = receipt.get("result_status")
    if status not in {"executed", "ok"}:
        raise SmokeFailure(f"{name} did not execute successfully: {status!r}")
    if output.get("http_status") != 200:
        raise SmokeFailure(f"{name} returned HTTP {output.get('http_status')!r}")
    print(f"PASS read: {name}")
    return receipt, output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument(
        "--workspace",
        default=str(DEFAULT_WORKSPACE),
        help="RailCall Studio workspace dir (.railcall_workspace) to read the session token from.",
    )
    parser.add_argument(
        "--session-token",
        default=None,
        help="Session token to send as X-RailCall-Session. Auto-discovered from --workspace if omitted.",
    )
    parser.add_argument(
        "--report",
        default="slack-guard-smoke-report.json",
        help="Path for the redacted JSON report.",
    )
    args = parser.parse_args()

    session_token = args.session_token or _discover_session_token(Path(args.workspace))

    report: dict[str, Any] = {"endpoint": args.endpoint, "reads": {}}

    result = execute(args.endpoint, "slack.get_team_info", {}, session_token)
    receipt, output = require_executed("slack.get_team_info", result)
    report["reads"]["get_team_info"] = {
        "team_id_present": bool(output.get("team_id")),
        "result_status": receipt.get("result_status"),
        "has_signature": bool((receipt.get("signature") or {}).get("sig")),
    }

    report_path = Path(args.report)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print()
    print("SAFE SMOKE TEST PASSED")
    print("No write command was approved or executed (none exist yet).")
    print(f"Redacted report: {report_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
