#!/usr/bin/env python3
"""Focused offline security tests for Slack Guard."""

from __future__ import annotations

import importlib.util
import io
import urllib.error
from pathlib import Path

# Synthetic fixture assembled in pieces to avoid secret-scanner false positives.
TEST_SLACK_TOKEN = "".join(("xoxb", "-", "9999999999999", "-", "testfixturetoken1234"))


ROOT = Path(__file__).resolve().parents[1]
HANDLER = ROOT / "handlers" / "handler.py"


def load_handler():
    spec = importlib.util.spec_from_file_location("slack_guard_handler", HANDLER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def expect_runtime_error(callable_, contains: str) -> None:
    try:
        callable_()
    except RuntimeError as exc:
        if contains.lower() not in str(exc).lower():
            raise AssertionError(f"expected {contains!r} in {exc!r}") from exc
    else:
        raise AssertionError("expected RuntimeError")


class _FakeResponse:
    def __init__(self, code, body, headers=None):
        self._code = code
        self._body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getcode(self):
        return self._code

    def read(self):
        return self._body


class _FakeHTTPError(urllib.error.HTTPError):
    def __init__(self, code, body, headers=None):
        super().__init__("http://x", code, "msg", headers or {}, io.BytesIO(body))


def main() -> int:
    source = HANDLER.read_text(encoding="utf-8")
    forbidden = [
        "credentials.local.json",
        "subprocess",
        "shutil",
        "os.environ",
        "os.getenv",
    ]
    for text in forbidden:
        assert text not in source, f"forbidden source text remains: {text}"
    print("PASS: no credential-file, environment, or subprocess fallback")

    h = load_handler()
    sleep_calls = []
    h.time.sleep = lambda seconds: sleep_calls.append(seconds)

    h.__rc_helpers__ = {"vault_get": lambda provider: TEST_SLACK_TOKEN}
    assert h._load_api_key() == TEST_SLACK_TOKEN
    assert h._extract_api_key({"fields": {"SLACK_BOT_TOKEN": " key "}}) == "key"
    # RailCall Station's credential_resolver.resolve() returns the bare
    # fields dict directly for named credentials saved through Studio
    # Integrations (no "fields" wrapper) - this must also resolve.
    assert h._extract_api_key({"SLACK_BOT_TOKEN": " key2 "}) == "key2"
    print("PASS: vault_get supplies string, wrapped-fields, and bare-fields shapes")

    h.__rc_helpers__ = {"vault_get": lambda provider: None}
    expect_runtime_error(h._load_api_key, "not configured")
    expect_runtime_error(h._load_api_key, "muhammad-akif-janjua-slack-guard::slack")
    h.__rc_helpers__ = {}
    expect_runtime_error(h._load_api_key, "vault_get")
    expect_runtime_error(h._load_api_key, "muhammad-akif-janjua-slack-guard::slack")
    print("PASS: missing vault configuration fails clearly, naming the namespaced credential card")

    secret = TEST_SLACK_TOKEN
    redacted = h._redact(f"Authorization: Bearer {secret}", secret)
    assert secret not in redacted
    assert "[REDACTED]" in redacted
    print("PASS: active secret redaction")

    # Pattern-based redaction must catch a credential-shaped string even when
    # it is NOT the exact `secret` argument passed in (defense in depth),
    # not just literal substring replacement of a known value.
    leaked_token = "xoxb-" + "1234567890-abcdefghijklmnop"
    assert leaked_token not in h._redact(f"leak: {leaked_token}", "unrelated")
    assert "some-header-value" not in h._redact(
        "Authorization: some-header-value", "unrelated"
    )
    assert "field-value-here" not in h._redact(
        "SLACK_BOT_TOKEN=field-value-here", "unrelated"
    )
    print("PASS: pattern-based redaction (token shape, Authorization header, field name)")

    calls = {"count": 0}
    original_urlopen = h.urllib.request.urlopen

    def failing_urlopen(*args, **kwargs):
        calls["count"] += 1
        raise urllib.error.URLError("timed out")

    h.urllib.request.urlopen = failing_urlopen
    try:
        try:
            h._request("POST", "/chat.postMessage", secret, body={}, is_write=True)
        except RuntimeError as exc:
            assert "outcome is unknown" in str(exc)
            assert exc.__cause__ is None
        else:
            raise AssertionError("expected unknown write outcome")
        assert calls["count"] == 1, "write transport was retried"
    finally:
        h.urllib.request.urlopen = original_urlopen
    print("PASS: mutation transport makes one attempt and hides low-level cause")

    calls["count"] = 0
    h.urllib.request.urlopen = failing_urlopen
    try:
        try:
            h._request("GET", "/team.info", secret)
        except RuntimeError as exc:
            assert "check your network connection" in str(exc).lower()
            assert "errno" not in str(exc).lower()
            assert exc.__cause__ is None
        else:
            raise AssertionError("expected a network-failure error")
        assert calls["count"] == 1, "a connection-level failure should not be retried"
    finally:
        h.urllib.request.urlopen = original_urlopen
    print("PASS: read connection failures get a clean plain-English message, not a raw exception, and are not retried")

    calls["count"] = 0

    def rate_limited_urlopen(*args, **kwargs):
        calls["count"] += 1
        raise _FakeHTTPError(429, b'{"ok": false, "error": "rate_limited"}')

    h.urllib.request.urlopen = rate_limited_urlopen
    try:
        expect_runtime_error(lambda: h._request("GET", "/team.info", secret), "rate limit")
        assert calls["count"] == 1 + h._MAX_READ_RETRIES, "read did not retry the expected number of times"
        expect_runtime_error(
            lambda: h._request("GET", "/team.info", secret), "retried but the limit is still in effect"
        )
    finally:
        h.urllib.request.urlopen = original_urlopen
    print("PASS: HTTP 429 on a read retries the bounded number of times, then gets a clear rate-limit message")

    calls["count"] = 0
    sleep_calls.clear()

    def rate_limited_with_retry_after(*args, **kwargs):
        calls["count"] += 1
        raise _FakeHTTPError(
            429, b'{"ok": false, "error": "rate_limited"}', headers={"Retry-After": "30"}
        )

    h.urllib.request.urlopen = rate_limited_with_retry_after
    try:
        expect_runtime_error(lambda: h._request("GET", "/team.info", secret), "30 seconds")
        assert sleep_calls == [h._MAX_RETRY_DELAY_SECONDS] * h._MAX_READ_RETRIES
    finally:
        h.urllib.request.urlopen = original_urlopen
    print("PASS: HTTP 429 with a Retry-After header surfaces the actual wait time and caps the honored delay")

    calls["count"] = 0

    def always_503_urlopen(*args, **kwargs):
        calls["count"] += 1
        raise _FakeHTTPError(503, b"")

    h.urllib.request.urlopen = always_503_urlopen
    try:
        expect_runtime_error(lambda: h._request("GET", "/team.info", secret), "503")
        assert calls["count"] == 1 + h._MAX_READ_RETRIES
    finally:
        h.urllib.request.urlopen = original_urlopen
    print("PASS: a read retries on HTTP 503 too, then fails with the usual HTTP-error message")

    calls["count"] = 0

    def flaky_then_ok_urlopen(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] < 3:
            raise _FakeHTTPError(503, b"")
        return _FakeResponse(200, b'{"ok": true, "team": {"id": "T1"}}')

    h.urllib.request.urlopen = flaky_then_ok_urlopen
    try:
        status, data = h._request("GET", "/team.info", secret)
        assert status == 200 and data == {"ok": True, "team": {"id": "T1"}}
        assert calls["count"] == 3, "expected two failed attempts before the third succeeded"
    finally:
        h.urllib.request.urlopen = original_urlopen
    print("PASS: a read that fails twice then succeeds returns the successful result on the retry")

    calls["count"] = 0
    h.urllib.request.urlopen = always_503_urlopen
    try:
        try:
            h._request("POST", "/chat.postMessage", secret, body={}, is_write=True)
        except RuntimeError as exc:
            assert "outcome is unknown" in str(exc)
        else:
            raise AssertionError("expected unknown write outcome")
        assert calls["count"] == 1, "a write must never be retried, even on a retryable status"
    finally:
        h.urllib.request.urlopen = original_urlopen
    print("PASS: a write never retries on HTTP 503, unlike a read")

    def not_ok_urlopen(*args, **kwargs):
        return _FakeResponse(200, b'{"ok": false, "error": "invalid_auth"}')

    h.urllib.request.urlopen = not_ok_urlopen
    try:
        try:
            h._request("GET", "/team.info", secret)
        except RuntimeError as exc:
            assert "invalid_auth" in str(exc)
        else:
            raise AssertionError("expected a Slack ok:false rejection")
    finally:
        h.urllib.request.urlopen = original_urlopen
    print("PASS: Slack's HTTP-200 {ok: false} failure body is treated as a definitive rejection, not a network error")

    def not_ok_write_urlopen(*args, **kwargs):
        return _FakeResponse(200, b'{"ok": false, "error": "channel_not_found"}')

    h.urllib.request.urlopen = not_ok_write_urlopen
    try:
        try:
            h._request("POST", "/chat.postMessage", secret, body={}, is_write=True)
        except RuntimeError as exc:
            assert "channel_not_found" in str(exc)
            assert "outcome is unknown" not in str(exc)
        else:
            raise AssertionError("expected a Slack ok:false rejection")
    finally:
        h.urllib.request.urlopen = original_urlopen
    print("PASS: a write rejected via ok:false is a clean rejection, not an unknown-outcome write error")

    def slack_get_team_info_ok_urlopen(*args, **kwargs):
        return _FakeResponse(
            200,
            b'{"ok": true, "team": {"id": "T123", "name": "Acme", "domain": "acme",'
            b' "email_domain": "acme.com", "icon": {"image_132": "https://x/icon.png"}}}',
        )

    h.urllib.request.urlopen = slack_get_team_info_ok_urlopen
    try:
        h.__rc_helpers__ = {"vault_get": lambda provider: TEST_SLACK_TOKEN}
        out, err = h.slack_get_team_info({}, None)
        assert err is None
        assert out["team_id"] == "T123"
        assert out["team_name"] == "Acme"
        assert out["domain"] == "acme"
        assert out["email_domain"] == "acme.com"
        assert out["icon_url"] == "https://x/icon.png"
        assert out["http_status"] == 200
    finally:
        h.urllib.request.urlopen = original_urlopen
    print("PASS: slack_get_team_info (real command function, end-to-end field mapping)")

    context = h._build_tls_context()
    assert context.verify_mode != 0 and context.check_hostname is True
    print("PASS: certifi-backed TLS verification is enabled")

    print("SECURITY TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
