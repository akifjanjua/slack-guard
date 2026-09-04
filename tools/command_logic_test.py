#!/usr/bin/env python3
"""Command-logic tests for Slack Guard.

Mocks the transport layer (_request) with realistic Slack Web API response
shapes, then calls the real command functions directly, asserting on actual
computed output. Offline only - no network access, no credentials. Mirrors
the pattern in Notion Guard's/Linear Guard's own command-logic suites (mock
the transport, exercise the real handler functions, assert on real output).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDLER = ROOT / "handlers" / "handler.py"


def load_handler():
    spec = importlib.util.spec_from_file_location("slack_guard_handler", HANDLER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.__rc_helpers__ = {"vault_get": lambda provider: "xoxb-test-token"}
    return module


def test_get_team_info(h) -> None:
    def fake_request(method, path, api_key, body=None, query=None, is_write=False):
        assert method == "GET" and path == "/team.info"
        return 200, {
            "ok": True,
            "team": {
                "id": "T0123ABCD",
                "name": "Acme Corp",
                "domain": "acme",
                "email_domain": "acme.com",
                "icon": {"image_132": "https://a.slack-edge.com/acme-132.png"},
            },
        }

    h._request = fake_request
    out, extra = h.slack_get_team_info({}, None)
    assert extra is None
    assert out["team_id"] == "T0123ABCD"
    assert out["team_name"] == "Acme Corp"
    assert out["domain"] == "acme"
    assert out["email_domain"] == "acme.com"
    assert out["icon_url"] == "https://a.slack-edge.com/acme-132.png"
    assert out["http_status"] == 200
    print("PASS: slack_get_team_info (team field extraction, icon_url unpacking)")


def test_get_team_info_missing_icon(h) -> None:
    def fake_request(method, path, api_key, body=None, query=None, is_write=False):
        return 200, {"ok": True, "team": {"id": "T1", "name": "No Icon Team"}}

    h._request = fake_request
    out, _ = h.slack_get_team_info({}, None)
    assert out["team_id"] == "T1"
    assert out["icon_url"] is None
    print("PASS: slack_get_team_info tolerates a missing icon block")


def test_get_team_info_malformed_response(h) -> None:
    def fake_request(method, path, api_key, body=None, query=None, is_write=False):
        return 200, {"ok": True, "team": "not-a-dict"}

    h._request = fake_request
    try:
        h.slack_get_team_info({}, None)
        raise AssertionError("expected a malformed-team rejection")
    except RuntimeError as exc:
        assert "team object" in str(exc)
    print("PASS: slack_get_team_info rejects a malformed team object cleanly")


def main() -> int:
    h = load_handler()
    test_get_team_info(h)
    test_get_team_info_missing_icon(h)
    test_get_team_info_malformed_response(h)
    print("COMMAND LOGIC TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
