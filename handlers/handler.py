"""Slack Guard - governed Slack operations for RailCall.

Credentials are resolved only through RailCall's injected vault_get("slack")
helper. This module never reads environment variables, credential files, or
command-line arguments for secrets, and never shells out to an external
process. All Slack HTTPS traffic uses urllib.request with a certifi-backed
SSLContext (certificate and hostname verification enabled).

Every write command makes exactly one HTTP attempt. If a timeout, connection
failure, HTTP 5xx response, or unreadable response body prevents confirmation,
the handler raises a clear "outcome is unknown" error instead of assuming
success or retrying automatically.

Slack's Web API always returns HTTP 200 for a well-formed request and signals
failure through a JSON body ({"ok": false, "error": "<code>"}) instead of an
HTTP status code, unlike Notion Guard/Linear Guard's REST APIs. This handler
treats {"ok": false} as the primary, definitive failure signal and HTTP status
as a secondary transport-level one (429 rate limit, 5xx server error).
"""

from __future__ import annotations

import http.client
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

API_HOST = "slack.com"
API_BASE = "https://slack.com/api"

_TLS_CONTEXT = None


def _build_tls_context():
    # Built once and cached: the handler namespace is exec'd once per Station
    # module load/reload and its functions are reused for every subsequent
    # command call, so rebuilding this per call would re-parse the certifi CA
    # bundle on every single invocation for no benefit.
    global _TLS_CONTEXT
    if _TLS_CONTEXT is None:
        try:
            import certifi
        except ImportError as exc:
            raise RuntimeError(
                "The certifi package is required for verified HTTPS. Install with: python -m pip install certifi"
            ) from exc
        _TLS_CONTEXT = ssl.create_default_context(cafile=certifi.where())
    return _TLS_CONTEXT


# Pattern-based redaction layered on top of literal secret substitution, so a
# credential leaking through a differently-cased or differently-sourced string
# than the exact `secret` value passed in still gets caught: Slack bot/user/
# app/refresh token shapes (xoxb-/xoxp-/xoxa-/xoxe-/xoxs-/xapp-), Authorization
# header values, and SLACK_BOT_TOKEN field assignments.
_SLACK_TOKEN_RE = re.compile(r"xox[bpaes]-[A-Za-z0-9-]{10,}|xapp-[A-Za-z0-9-]{10,}")
_AUTH_HEADER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+")
_SECRET_FIELD_RE = re.compile(r"(?i)(SLACK_BOT_TOKEN\s*[:=]\s*)[^\s,;]+")


def _redact(text, secret):
    if not isinstance(text, str):
        text = str(text)
    if secret:
        text = text.replace(secret, "[REDACTED]")
    text = _SLACK_TOKEN_RE.sub("[REDACTED]", text)
    text = _AUTH_HEADER_RE.sub(r"\1[REDACTED]", text)
    text = _SECRET_FIELD_RE.sub(r"\1[REDACTED]", text)
    return text


def _extract_api_key(entry):
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        fields = entry.get("fields")
        if isinstance(fields, dict):
            value = fields.get("SLACK_BOT_TOKEN")
            if isinstance(value, str):
                return value.strip()
        # RailCall Station's credential_resolver.resolve() returns the bare
        # fields dict directly for named credentials saved through Studio
        # Integrations (no "fields" wrapper) - only the legacy keys.local.json
        # path and test fixtures use the wrapped shape.
        value = entry.get("SLACK_BOT_TOKEN")
        if isinstance(value, str):
            return value.strip()
    return ""


def _load_api_key():
    helpers = globals().get("__rc_helpers__")
    if not isinstance(helpers, dict):
        raise RuntimeError(
            "RailCall's vault_get helper is not available. Configure SLACK_BOT_TOKEN on the "
            "muhammad-akif-janjua-slack-guard::slack card in RailCall Studio -> "
            "Integrations before using Slack Guard."
        )
    vault_get = helpers.get("vault_get")
    if vault_get is None:
        raise RuntimeError(
            "vault_get helper is not available. Configure SLACK_BOT_TOKEN on the "
            "muhammad-akif-janjua-slack-guard::slack card in RailCall Studio -> "
            "Integrations before using Slack Guard."
        )
    try:
        entry = vault_get("slack")
    except Exception as exc:
        raise RuntimeError(
            "Could not read the Slack credential from RailCall Vault. Configure "
            "SLACK_BOT_TOKEN on the muhammad-akif-janjua-slack-guard::slack card in "
            "RailCall Studio -> Integrations before using Slack Guard."
        ) from exc
    api_key = _extract_api_key(entry)
    if not api_key:
        raise RuntimeError(
            "Slack credential is not configured. Configure SLACK_BOT_TOKEN on the "
            "muhammad-akif-janjua-slack-guard::slack card in RailCall Studio -> "
            "Integrations before using Slack Guard."
        )
    return api_key


# Reads have no side effect, so a transient failure can be retried safely;
# writes never are, since a retry could duplicate an unknown-outcome mutation.
_READ_RETRY_STATUSES = (429, 502, 503, 504)
_MAX_READ_RETRIES = 2
_MAX_RETRY_DELAY_SECONDS = 10


def _read_retry_delay(attempt_number, status, headers):
    if status == 429 and headers is not None:
        retry_after = headers.get("Retry-After")
        if isinstance(retry_after, str) and retry_after.strip():
            try:
                seconds = float(retry_after.strip())
            except ValueError:
                seconds = None
            if seconds is not None and seconds >= 0:
                return min(seconds, _MAX_RETRY_DELAY_SECONDS)
    return float(attempt_number)


def _attempt_once(method, url_path, data, headers, is_write):
    """One raw HTTP attempt. Returns (status, raw_body, response_headers) for
    any response Slack actually sent - even a non-2xx one - so the caller can
    interpret the status uniformly. A connection-level failure (nothing worth
    interpreting as a status) is converted here into a clean error."""
    try:
        request = urllib.request.Request(
            API_BASE + url_path, data=data, headers=headers, method=method
        )
        context = _build_tls_context()
        with urllib.request.urlopen(request, timeout=25, context=context) as response:
            return response.getcode(), response.read().decode("utf-8"), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace"), exc.headers
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError):
        if is_write:
            raise RuntimeError(
                "The write request to Slack did not complete and the outcome is "
                "unknown. Check Slack directly before retrying."
            ) from None
        raise RuntimeError("Could not reach Slack. Check your network connection and try again.") from None


def _request(method, path, api_key, body=None, query=None, is_write=False):
    """Make an HTTPS request to the Slack Web API. Writes make exactly one
    attempt - a retry could duplicate an unknown-outcome mutation. Reads may
    retry up to _MAX_READ_RETRIES times on a transient HTTP 429/502/503/504
    (honoring Slack's Retry-After value when supplied), since a read has no
    side effect and retrying it is always safe.

    Slack signals failure primarily through a JSON body
    {"ok": false, "error": "<code>"} returned with HTTP 200, not through HTTP
    status - only a true transport-level failure (429 rate limit, 5xx server
    error) is represented as a non-200 status here.
    """
    url_path = "/" + path.lstrip("/")
    data = None
    if method == "GET":
        if query:
            clean_query = {k: v for k, v in query.items() if v is not None}
            if clean_query:
                url_path = url_path + "?" + urllib.parse.urlencode(clean_query)
    else:
        data = json.dumps(body or {}).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json; charset=utf-8",
    }

    retries_left = 0 if is_write else _MAX_READ_RETRIES
    attempt = 0
    while True:
        status, raw, response_headers = _attempt_once(method, url_path, data, headers, is_write)
        if status in _READ_RETRY_STATUSES and not is_write and retries_left > 0:
            attempt += 1
            retries_left -= 1
            time.sleep(_read_retry_delay(attempt, status, response_headers))
            continue
        break

    retried = attempt > 0
    if status == 429:
        retry_after = response_headers.get("Retry-After") if response_headers else None
        if isinstance(retry_after, str) and retry_after.strip():
            if retried:
                raise RuntimeError(
                    f"Slack API rate limit reached. Slack Guard retried but the limit "
                    f"is still in effect; Slack says to wait {retry_after.strip()} seconds "
                    "before trying again."
                ) from None
            raise RuntimeError(
                f"Slack API rate limit reached. Slack says to wait {retry_after.strip()} seconds "
                "before retrying; Slack Guard never automatically retries a write."
            ) from None
        if retried:
            raise RuntimeError(
                "Slack API rate limit reached. Slack Guard retried but the limit is "
                "still in effect. Wait before trying again."
            ) from None
        raise RuntimeError(
            "Slack API rate limit reached. Wait before retrying; Slack Guard "
            "never automatically retries a write."
        ) from None

    if is_write and status >= 500:
        raise RuntimeError(
            "Slack returned a server error and the write outcome is unknown. "
            "Check Slack directly before retrying."
        ) from None

    if status != 200:
        raise RuntimeError(_redact(f"Slack API returned HTTP {status}.", api_key)) from None

    if not raw:
        if is_write:
            raise RuntimeError(
                "Slack returned an empty response and the write outcome is "
                "unknown. Check Slack directly before retrying."
            )
        raise RuntimeError("Slack returned an empty response.")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        if is_write:
            raise RuntimeError(
                "Slack returned an unreadable response and the write outcome is "
                "unknown. Check Slack directly before retrying."
            ) from None
        raise RuntimeError("Slack returned an unreadable response.") from None

    if not isinstance(parsed, dict):
        raise RuntimeError("Slack returned an unexpected response shape.")

    if not parsed.get("ok"):
        error_code = parsed.get("error") or "unknown_error"
        # {"ok": false} is a definitive, confirmed rejection from Slack - not
        # a transport-level ambiguity - so both reads and writes raise a
        # clean, direct error here rather than an "outcome is unknown" one.
        raise RuntimeError(_redact(f"Slack rejected the request: {error_code}", api_key)) from None

    return status, parsed


def slack_get_team_info(inputs, stamp):
    """Retrieve the connected Slack workspace's identity. The connectivity
    smoke test: a successful call confirms the bot token is valid and the
    handler can reach the Slack Web API end to end."""
    api_key = _load_api_key()
    status, data = _request("GET", "/team.info", api_key)
    team = data.get("team")
    if not isinstance(team, dict):
        raise RuntimeError("Slack did not return a team object.")
    icon = team.get("icon")
    icon_url = icon.get("image_132") if isinstance(icon, dict) else None
    return {
        "ok": True,
        "http_status": status,
        "team_id": team.get("id"),
        "team_name": team.get("name"),
        "domain": team.get("domain"),
        "email_domain": team.get("email_domain"),
        "icon_url": icon_url,
    }, None
