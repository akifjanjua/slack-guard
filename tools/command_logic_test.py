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
import json
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


def test_list_users(h) -> None:
    def fake_request(method, path, api_key, body=None, query=None, is_write=False):
        assert method == "GET" and path == "/users.list"
        assert query["limit"] == 50
        return 200, {
            "ok": True,
            "members": [
                {
                    "id": "U1",
                    "name": "akif",
                    "real_name": "Akif",
                    "is_bot": False,
                    "is_admin": True,
                    "deleted": False,
                    "profile": {"display_name": "Akif J", "email": "akif@example.com"},
                },
                {"id": "U2", "name": "bot", "is_bot": True, "profile": {}},
            ],
            "response_metadata": {"next_cursor": "abc123"},
        }

    h._request = fake_request
    out, _ = h.slack_list_users({"limit": 50}, None)
    users = json.loads(out["users_json"])
    assert out["returned_count"] == 2
    assert users[0]["email"] == "akif@example.com"
    assert "email" not in users[1]
    assert out["next_cursor"] == "abc123"
    assert out["has_more"] is True
    print("PASS: slack_list_users (email surfaced when present, cursor pagination)")


def test_get_user_info(h) -> None:
    def fake_request_by_id(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/users.info" and query == {"user": "U1"}
        return 200, {"ok": True, "user": {"id": "U1", "name": "akif", "profile": {}}}

    h._request = fake_request_by_id
    out, _ = h.slack_get_user_info({"user_id": "U1"}, None)
    assert out["user_id"] == "U1" and out["name"] == "akif"

    def fake_request_by_email(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/users.lookupByEmail" and query == {"email": "a@example.com"}
        return 200, {"ok": True, "user": {"id": "U2", "name": "someone", "profile": {}}}

    h._request = fake_request_by_email
    out, _ = h.slack_get_user_info({"email": "a@example.com"}, None)
    assert out["user_id"] == "U2"

    try:
        h.slack_get_user_info({}, None)
        raise AssertionError("expected missing user_id/email rejection")
    except RuntimeError as exc:
        assert "user_id or email" in str(exc)
    print("PASS: slack_get_user_info (id path, email path, missing-both rejection)")


def test_list_channels(h) -> None:
    def fake_request(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/conversations.list"
        assert query["types"] == "public_channel,private_channel"
        return 200, {
            "ok": True,
            "channels": [
                {
                    "id": "C1",
                    "name": "general",
                    "is_private": False,
                    "is_archived": False,
                    "is_member": True,
                    "num_members": 12,
                    "topic": {"value": "General chat"},
                    "purpose": {"value": "Company-wide"},
                }
            ],
            "response_metadata": {"next_cursor": ""},
        }

    h._request = fake_request
    out, _ = h.slack_list_channels({}, None)
    channels = json.loads(out["channels_json"])
    assert channels[0] == {
        "id": "C1", "name": "general", "is_private": False, "is_archived": False,
        "is_member": True, "num_members": 12, "topic": "General chat", "purpose": "Company-wide",
    }
    assert out["has_more"] is False
    print("PASS: slack_list_channels (topic/purpose unpacking, empty-cursor has_more)")


def test_get_channel_info(h) -> None:
    def fake_request(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/conversations.info" and query["channel"] == "C1"
        return 200, {
            "ok": True,
            "channel": {
                "id": "C1", "name": "general", "is_private": False, "is_archived": False,
                "is_member": True, "num_members": 12, "topic": {"value": "T"}, "purpose": {"value": "P"},
                "created": 1700000000, "creator": "U1",
            },
        }

    h._request = fake_request
    out, _ = h.slack_get_channel_info({"channel_id": "C1"}, None)
    assert out["created"] == 1700000000 and out["creator"] == "U1"

    try:
        h.slack_get_channel_info({}, None)
        raise AssertionError("expected missing channel_id rejection")
    except RuntimeError:
        pass
    print("PASS: slack_get_channel_info (created/creator passthrough, required channel_id)")


def test_list_channel_members(h) -> None:
    def fake_request(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/conversations.members" and query["channel"] == "C1"
        return 200, {"ok": True, "members": ["U1", "U2"], "response_metadata": {"next_cursor": ""}}

    h._request = fake_request
    out, _ = h.slack_list_channel_members({"channel_id": "C1"}, None)
    assert json.loads(out["member_ids_json"]) == ["U1", "U2"]
    assert out["returned_count"] == 2
    print("PASS: slack_list_channel_members (bare id list)")


def test_get_channel_history(h) -> None:
    def fake_request(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/conversations.history" and query["channel"] == "C1"
        long_text = "x" * 2000
        return 200, {
            "ok": True,
            "messages": [
                {"ts": "1.1", "user": "U1", "text": long_text, "type": "message"},
            ],
            "has_more": False,
            "response_metadata": {"next_cursor": ""},
        }

    h._request = fake_request
    out, _ = h.slack_get_channel_history({"channel_id": "C1"}, None)
    messages = json.loads(out["messages_json"])
    assert len(messages[0]["text"]) == h._MAX_MESSAGE_TEXT_CHARS
    print("PASS: slack_get_channel_history (oversized message text capped)")


def test_get_thread_replies(h) -> None:
    def fake_request(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/conversations.replies"
        assert query["channel"] == "C1" and query["ts"] == "1.1"
        return 200, {
            "ok": True,
            "messages": [
                {"ts": "1.1", "user": "U1", "text": "parent", "reply_count": 1},
                {"ts": "1.2", "user": "U2", "text": "reply", "thread_ts": "1.1"},
            ],
            "response_metadata": {"next_cursor": ""},
        }

    h._request = fake_request
    out, _ = h.slack_get_thread_replies({"channel_id": "C1", "thread_ts": "1.1"}, None)
    messages = json.loads(out["messages_json"])
    assert messages[0]["text"] == "parent" and messages[1]["thread_ts"] == "1.1"

    try:
        h.slack_get_thread_replies({"channel_id": "C1"}, None)
        raise AssertionError("expected missing thread_ts rejection")
    except RuntimeError as exc:
        assert "thread_ts" in str(exc)
    print("PASS: slack_get_thread_replies (parent + reply shape, required thread_ts)")


def test_list_usergroups(h) -> None:
    calls = []

    def fake_request(method, path, api_key, body=None, query=None, is_write=False):
        calls.append(path)
        if path == "/usergroups.list":
            return 200, {
                "ok": True,
                "usergroups": [
                    {"id": "S1", "name": "Eng", "handle": "eng", "description": "Engineering", "user_count": 2},
                ],
            }
        assert path == "/usergroups.users.list" and query == {"usergroup": "S1"}
        return 200, {"ok": True, "users": ["U1", "U2"]}

    h._request = fake_request
    out, _ = h.slack_list_usergroups({"include_users": True}, None)
    groups = json.loads(out["usergroups_json"])
    assert groups[0]["user_ids"] == ["U1", "U2"]
    assert calls == ["/usergroups.list", "/usergroups.users.list"]

    h._request = fake_request
    out, _ = h.slack_list_usergroups({}, None)
    groups = json.loads(out["usergroups_json"])
    assert "user_ids" not in groups[0]
    print("PASS: slack_list_usergroups (include_users fan-out, default omits member ids)")


def test_list_files(h) -> None:
    def fake_request(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/files.list"
        return 200, {
            "ok": True,
            "files": [
                {
                    "id": "F1", "name": "report.pdf", "title": "Report", "mimetype": "application/pdf",
                    "filetype": "pdf", "size": 1024, "is_public": False,
                    "permalink": "https://acme.slack.com/files/U1/F1/report.pdf",
                    "url_private": "https://files.slack.com/files-pri/T1-F1/report.pdf",
                }
            ],
            "paging": {"page": 1, "pages": 1, "total": 1},
        }

    h._request = fake_request
    out, _ = h.slack_list_files({}, None)
    files = json.loads(out["files_json"])
    assert files[0]["permalink"] == "https://acme.slack.com/files/U1/F1/report.pdf"
    assert "url_private" not in files[0]
    assert out["total"] == 1
    print("PASS: slack_list_files (permalink surfaced, url_private omitted, paging passthrough)")


def test_get_dnd_status(h) -> None:
    def fake_request(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/dnd.info" and query == {"user": "U1"}
        return 200, {"ok": True, "dnd_enabled": True, "next_dnd_start_ts": 100, "next_dnd_end_ts": 200}

    h._request = fake_request
    out, _ = h.slack_get_dnd_status({"user_id": "U1"}, None)
    assert out["dnd_enabled"] is True
    assert out["next_dnd_start_ts"] == 100

    try:
        h.slack_get_dnd_status({}, None)
        raise AssertionError("expected missing user_id rejection")
    except RuntimeError:
        pass
    print("PASS: slack_get_dnd_status (field passthrough, required user_id)")


def test_require_id_validation(h) -> None:
    def must_reject(value, expected_fragment):
        try:
            h._require_id(value, "channel_id")
            raise AssertionError(f"expected rejection for {value!r}")
        except RuntimeError as exc:
            assert expected_fragment in str(exc), f"{value!r} -> {exc}"

    for bad in ("abc\r\nX-Injected: evil", "abc def", "abc\x00def"):
        must_reject(bad, "control character or space")
    for bad in ("../../v1/users", "abc/def", "abc\\def"):
        must_reject(bad, "path separator")
    must_reject("a" * 201, "too long")
    assert h._require_id("C0123ABCD", "channel_id") == "C0123ABCD"
    print("PASS: _require_id (control characters, path separators, oversized ids rejected; valid ids pass)")


def test_bounded_limit(h) -> None:
    assert h._bounded_limit(None, default=50, maximum=200) == 50
    assert h._bounded_limit(500, default=50, maximum=200) == 200
    assert h._bounded_limit(10, default=50, maximum=200) == 10
    try:
        h._bounded_limit(-1, default=50, maximum=200)
        raise AssertionError("expected rejection of non-positive limit")
    except RuntimeError:
        pass
    try:
        h._bounded_limit(1.5, default=50, maximum=200)
        raise AssertionError("expected rejection of non-integer float limit")
    except RuntimeError:
        pass
    print("PASS: _bounded_limit (default, cap, positive-integer validation)")


def main() -> int:
    h = load_handler()
    test_get_team_info(h)
    test_get_team_info_missing_icon(h)
    test_get_team_info_malformed_response(h)
    test_list_users(h)
    test_get_user_info(h)
    test_list_channels(h)
    test_get_channel_info(h)
    test_list_channel_members(h)
    test_get_channel_history(h)
    test_get_thread_replies(h)
    test_list_usergroups(h)
    test_list_files(h)
    test_get_dnd_status(h)
    test_require_id_validation(h)
    test_bounded_limit(h)
    print("COMMAND LOGIC TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
