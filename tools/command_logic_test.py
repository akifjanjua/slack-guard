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


def test_post_message(h) -> None:
    def fake_request(method, path, api_key, body=None, query=None, is_write=False):
        assert method == "POST" and path == "/chat.postMessage" and is_write is True
        assert body == {"channel": "C1", "text": "hi", "thread_ts": "1.1"}
        return 200, {"ok": True, "channel": "C1", "ts": "2.2"}

    h._request = fake_request
    out, _ = h.slack_post_message({"channel_id": "C1", "text": "hi", "thread_ts": "1.1"}, None)
    assert out["channel_id"] == "C1" and out["message_ts"] == "2.2"

    try:
        h.slack_post_message({"channel_id": "C1"}, None)
        raise AssertionError("expected missing text rejection")
    except RuntimeError as exc:
        assert "text" in str(exc)
    print("PASS: slack_post_message (thread_ts passthrough, required text)")


def test_post_ephemeral(h) -> None:
    def fake_request(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/chat.postEphemeral" and is_write is True
        assert body == {"channel": "C1", "user": "U1", "text": "hi"}
        return 200, {"ok": True, "message_ts": "3.3"}

    h._request = fake_request
    out, _ = h.slack_post_ephemeral({"channel_id": "C1", "user_id": "U1", "text": "hi"}, None)
    assert out["message_ts"] == "3.3"
    print("PASS: slack_post_ephemeral")


def test_reactions(h) -> None:
    def fake_request(method, path, api_key, body=None, query=None, is_write=False):
        assert is_write is True
        assert body == {"channel": "C1", "timestamp": "1.1", "name": "thumbsup"}
        return 200, {"ok": True}

    h._request = fake_request
    out, _ = h.slack_add_reaction({"channel_id": "C1", "timestamp": "1.1", "name": "thumbsup"}, None)
    assert out["name"] == "thumbsup"
    out, _ = h.slack_remove_reaction({"channel_id": "C1", "timestamp": "1.1", "name": "thumbsup"}, None)
    assert out["name"] == "thumbsup"
    print("PASS: slack_add_reaction / slack_remove_reaction")


def test_pins(h) -> None:
    def fake_request(method, path, api_key, body=None, query=None, is_write=False):
        assert is_write is True
        assert body == {"channel": "C1", "timestamp": "1.1"}
        return 200, {"ok": True}

    h._request = fake_request
    out, _ = h.slack_add_pin({"channel_id": "C1", "timestamp": "1.1"}, None)
    assert out["timestamp"] == "1.1"
    out, _ = h.slack_remove_pin({"channel_id": "C1", "timestamp": "1.1"}, None)
    assert out["timestamp"] == "1.1"
    print("PASS: slack_add_pin / slack_remove_pin")


def test_bookmarks(h) -> None:
    def fake_add(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/bookmarks.add" and is_write is True
        assert body == {"channel_id": "C1", "title": "Runbook", "link": "https://x/y", "type": "link"}
        return 200, {"ok": True, "bookmark": {"id": "Bk1", "title": "Runbook", "link": "https://x/y"}}

    h._request = fake_add
    out, _ = h.slack_add_bookmark({"channel_id": "C1", "title": "Runbook", "link": "https://x/y"}, None)
    assert out["bookmark_id"] == "Bk1"

    def fake_edit(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/bookmarks.edit" and body["bookmark_id"] == "Bk1" and body["title"] == "New title"
        return 200, {"ok": True, "bookmark": {"id": "Bk1", "title": "New title", "link": "https://x/y"}}

    h._request = fake_edit
    out, _ = h.slack_edit_bookmark({"channel_id": "C1", "bookmark_id": "Bk1", "title": "New title"}, None)
    assert out["title"] == "New title"

    try:
        h.slack_edit_bookmark({"channel_id": "C1", "bookmark_id": "Bk1"}, None)
        raise AssertionError("expected at-least-one-field rejection")
    except RuntimeError as exc:
        assert "at least one" in str(exc)

    def fake_remove(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/bookmarks.remove" and body == {"channel_id": "C1", "bookmark_id": "Bk1"}
        return 200, {"ok": True}

    h._request = fake_remove
    out, _ = h.slack_remove_bookmark({"channel_id": "C1", "bookmark_id": "Bk1"}, None)
    assert out["bookmark_id"] == "Bk1"
    print("PASS: slack_add_bookmark / slack_edit_bookmark (at-least-one-field validation) / slack_remove_bookmark")


def test_reminders(h) -> None:
    def fake_add(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/reminders.add" and body == {"text": "Standup", "time": "in 5 minutes", "user": "U1"}
        return 200, {"ok": True, "reminder": {"id": "Rm1", "text": "Standup", "time": 1234}}

    h._request = fake_add
    out, _ = h.slack_add_reminder({"text": "Standup", "time": "in 5 minutes", "user_id": "U1"}, None)
    assert out["reminder_id"] == "Rm1"

    def fake_complete(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/reminders.complete" and body == {"reminder": "Rm1"}
        return 200, {"ok": True}

    h._request = fake_complete
    out, _ = h.slack_complete_reminder({"reminder_id": "Rm1"}, None)
    assert out["reminder_id"] == "Rm1"
    print("PASS: slack_add_reminder (optional user) / slack_complete_reminder")


def test_channel_topic_purpose(h) -> None:
    def fake_topic(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/conversations.setTopic" and body == {"channel": "C1", "topic": "New topic"}
        return 200, {"ok": True, "topic": "New topic"}

    h._request = fake_topic
    out, _ = h.slack_set_channel_topic({"channel_id": "C1", "topic": "New topic"}, None)
    assert out["topic"] == "New topic"

    def fake_purpose(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/conversations.setPurpose" and body == {"channel": "C1", "purpose": "New purpose"}
        return 200, {"ok": True, "purpose": "New purpose"}

    h._request = fake_purpose
    out, _ = h.slack_set_channel_purpose({"channel_id": "C1", "purpose": "New purpose"}, None)
    assert out["purpose"] == "New purpose"
    print("PASS: slack_set_channel_topic / slack_set_channel_purpose")


def test_create_and_join_channel(h) -> None:
    def fake_create(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/conversations.create" and body == {"name": "incident-1", "is_private": False}
        return 200, {"ok": True, "channel": {"id": "C9", "name": "incident-1", "is_private": False}}

    h._request = fake_create
    out, _ = h.slack_create_channel({"name": "incident-1"}, None)
    assert out["channel_id"] == "C9" and out["is_private"] is False

    def fake_join(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/conversations.join" and body == {"channel": "C9"}
        return 200, {"ok": True, "channel": {"id": "C9", "name": "incident-1"}}

    h._request = fake_join
    out, _ = h.slack_join_channel({"channel_id": "C9"}, None)
    assert out["channel_id"] == "C9" and out["name"] == "incident-1"
    print("PASS: slack_create_channel / slack_join_channel")


def test_schedule_message(h) -> None:
    def fake_schedule(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/chat.scheduleMessage" and body == {"channel": "C1", "text": "hi", "post_at": 1893456000}
        return 200, {"ok": True, "channel": "C1", "scheduled_message_id": "Q1", "post_at": 1893456000}

    h._request = fake_schedule
    out, _ = h.slack_schedule_message({"channel_id": "C1", "text": "hi", "post_at": 1893456000}, None)
    assert out["scheduled_message_id"] == "Q1"

    try:
        h.slack_schedule_message({"channel_id": "C1", "text": "hi", "post_at": -5}, None)
        raise AssertionError("expected non-positive post_at rejection")
    except RuntimeError as exc:
        assert "positive" in str(exc)

    def fake_cancel(method, path, api_key, body=None, query=None, is_write=False):
        assert path == "/chat.deleteScheduledMessage" and body == {"channel": "C1", "scheduled_message_id": "Q1"}
        return 200, {"ok": True}

    h._request = fake_cancel
    out, _ = h.slack_cancel_scheduled_message({"channel_id": "C1", "scheduled_message_id": "Q1"}, None)
    assert out["scheduled_message_id"] == "Q1"
    print("PASS: slack_schedule_message (post_at validation) / slack_cancel_scheduled_message")


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
    test_post_message(h)
    test_post_ephemeral(h)
    test_reactions(h)
    test_pins(h)
    test_bookmarks(h)
    test_reminders(h)
    test_channel_topic_purpose(h)
    test_create_and_join_channel(h)
    test_schedule_message(h)
    print("COMMAND LOGIC TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
