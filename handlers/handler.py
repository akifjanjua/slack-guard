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
# files.sharedPublicURL mints a bearer-style secret embedded in the file's
# permalink_public URL as a `pub_secret` query value - anyone holding it can
# read the file with no login. Redacted from error/note text the same way a
# token would be (see docs/TROUBLESHOOTING.md and SECURITY.md); the SUCCESS
# output of slack.share_file_publicly deliberately still returns the full
# URL, since that link is the actual, human-approved deliverable of the
# command - redacting it there would make the command useless.
_SLACK_PUBLIC_SHARE_SECRET_RE = re.compile(r"(?i)(pub_secret=)[A-Za-z0-9]+")


def _redact(text, secret):
    if not isinstance(text, str):
        text = str(text)
    if secret:
        text = text.replace(secret, "[REDACTED]")
    text = _SLACK_TOKEN_RE.sub("[REDACTED]", text)
    text = _AUTH_HEADER_RE.sub(r"\1[REDACTED]", text)
    text = _SECRET_FIELD_RE.sub(r"\1[REDACTED]", text)
    text = _SLACK_PUBLIC_SHARE_SECRET_RE.sub(r"\1[REDACTED]", text)
    return text


def _extract_field(entry, field_name):
    """Read one named field off a vault_get("slack") entry. Handles the
    bare-string shape (SLACK_BOT_TOKEN saved as a plain secret), the
    wrapped {"fields": {...}} shape (legacy keys.local.json / test
    fixtures), and the bare-fields-dict shape RailCall Station's
    credential_resolver.resolve() actually returns for named credentials
    saved through Studio Integrations."""
    if field_name == "SLACK_BOT_TOKEN" and isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        fields = entry.get("fields")
        if isinstance(fields, dict):
            value = fields.get(field_name)
            if isinstance(value, str):
                return value.strip()
        value = entry.get(field_name)
        if isinstance(value, str):
            return value.strip()
    return ""


def _extract_api_key(entry):
    return _extract_field(entry, "SLACK_BOT_TOKEN")


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


def _vault_entry():
    """Raw vault_get("slack") entry, for reading optional fields alongside
    the bot token (SLACK_GUARD_PRESET, SLACK_CLIENT_ID, SLACK_CLIENT_SECRET).
    Returns None on any resolution failure rather than raising - callers that
    need the entry to exist go through _load_api_key first, which already
    raises a clear error naming the credential card."""
    helpers = globals().get("__rc_helpers__")
    if not isinstance(helpers, dict):
        return None
    vault_get = helpers.get("vault_get")
    if vault_get is None:
        return None
    try:
        return vault_get("slack")
    except Exception:
        return None


_VALID_PRESETS = {"observer", "team_copilot", "open_community_hardened"}
_DEFAULT_PRESET = "team_copilot"

# Hard-blocked outright under the Open Community Hardened preset, regardless
# of approval: the two channel-membership actions and the one usergroup-
# membership action (Slack Guard's own §4 governance design - README's
# "Governance presets" section), plus the one command that turns a private
# file into a world-readable link. On a large open/public workspace the
# abuse surface of these four is qualitatively worse than in a closed team,
# and an approval prompt alone isn't judged a strong enough gate for them.
_HARDENED_BLOCKED_COMMANDS = {
    "slack.kick_user_from_channel",
    "slack.invite_to_channel",
    "slack.update_usergroup_members",
    "slack.share_file_publicly",
}


def _active_preset():
    """Which governance preset is active for this install. Read from the
    SAME vault_get("slack") credential entry as the bot token (an optional
    SLACK_GUARD_PRESET field on the same Studio Integrations card) so there
    is nothing new to configure beyond the one card the operator already
    fills in. Defaults to "team_copilot" (the balanced default, no hard
    blocks) whenever the field is unset, unreadable, or unrecognized -
    fails toward the LESS restrictive preset on purpose, since an operator
    who never touched this setting should never have existing behavior
    silently change under them."""
    preset = _extract_field(_vault_entry(), "SLACK_GUARD_PRESET").lower()
    return preset if preset in _VALID_PRESETS else _DEFAULT_PRESET


def _enforce_preset_block(command_id):
    """Real, functional enforcement - not just a documented label. Under the
    Open Community Hardened preset, the highest-abuse Tier 3 commands refuse
    to run at all: this runs before any network call and before approval is
    even relevant, so no amount of approving can make a hard-blocked command
    execute on this preset."""
    if _active_preset() == "open_community_hardened" and command_id in _HARDENED_BLOCKED_COMMANDS:
        raise RuntimeError(
            f"Blocked by the Open Community Hardened preset: {command_id} is disabled "
            "outright on this install, regardless of approval, because its abuse "
            "surface (channel/usergroup membership changes, public file sharing) is "
            "too high for a large open workspace. Switch SLACK_GUARD_PRESET to "
            "team_copilot or observer on the muhammad-akif-janjua-slack-guard::slack "
            "card if this is actually a closed team."
        )


def _load_client_credentials():
    """SLACK_CLIENT_ID/SLACK_CLIENT_SECRET, needed only by slack.uninstall_app.
    Vault-only, same as the bot token - never accepted as command inputs,
    since an input value is exactly what a preview/approval receipt persists
    to disk, and a client secret has no business landing in a receipt file."""
    entry = _vault_entry()
    client_id = _extract_field(entry, "SLACK_CLIENT_ID")
    client_secret = _extract_field(entry, "SLACK_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "slack.uninstall_app needs SLACK_CLIENT_ID and SLACK_CLIENT_SECRET "
            "configured on the muhammad-akif-janjua-slack-guard::slack card (from "
            "your Slack app's Basic Information page), in addition to SLACK_BOT_TOKEN."
        )
    return client_id, client_secret


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


def _require(value, field_name):
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{field_name} is required.")
    return value.strip()


_UNSAFE_ID_RE = re.compile(r"[\x00-\x20\x7f]")


def _require_id(value, field_name):
    value = _require(value, field_name)
    if len(value) > 200:
        raise RuntimeError(
            f"{field_name} is too long ({len(value)} characters; max 200), which is not a valid Slack ID."
        )
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        raise RuntimeError(f"{field_name} must contain only ASCII characters, which is not a valid Slack ID.") from None
    if _UNSAFE_ID_RE.search(value):
        raise RuntimeError(f"{field_name} contains a control character or space, which is not a valid Slack ID.")
    if "/" in value or "\\" in value:
        raise RuntimeError(f"{field_name} contains a path separator, which is not a valid Slack ID.")
    return value


def _bounded_limit(value, default, maximum):
    if value is None or value == "":
        return default
    if isinstance(value, float) and not value.is_integer():
        raise RuntimeError("limit must be an integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise RuntimeError("limit must be an integer.")
    if parsed <= 0:
        raise RuntimeError("limit must be a positive integer.")
    return min(parsed, maximum)


def _simplify_user(user):
    if not isinstance(user, dict):
        return {}
    profile = user.get("profile") or {}
    entry = {
        "id": user.get("id"),
        "name": user.get("name"),
        "real_name": user.get("real_name") or profile.get("real_name"),
        "display_name": profile.get("display_name"),
        "is_bot": bool(user.get("is_bot")),
        "is_admin": bool(user.get("is_admin")),
        "deleted": bool(user.get("deleted")),
    }
    email = profile.get("email")
    if isinstance(email, str) and email:
        entry["email"] = email
    return entry


def _simplify_channel(channel):
    if not isinstance(channel, dict):
        return {}
    topic = channel.get("topic") or {}
    purpose = channel.get("purpose") or {}
    return {
        "id": channel.get("id"),
        "name": channel.get("name"),
        "is_private": bool(channel.get("is_private")),
        "is_archived": bool(channel.get("is_archived")),
        "is_member": bool(channel.get("is_member")),
        "num_members": channel.get("num_members"),
        "topic": topic.get("value"),
        "purpose": purpose.get("value"),
    }


_MAX_MESSAGE_TEXT_CHARS = 1000


def _simplify_message(message):
    if not isinstance(message, dict):
        return {}
    text = message.get("text")
    if isinstance(text, str) and len(text) > _MAX_MESSAGE_TEXT_CHARS:
        text = text[:_MAX_MESSAGE_TEXT_CHARS]
    return {
        "ts": message.get("ts"),
        "user": message.get("user"),
        "text": text,
        "type": message.get("type"),
        "subtype": message.get("subtype"),
        "thread_ts": message.get("thread_ts"),
        "reply_count": message.get("reply_count"),
    }


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


def slack_list_users(inputs, stamp):
    """List workspace members, so a caller can address people by name or id
    before running a write command that takes a user_id."""
    limit = _bounded_limit(inputs.get("limit"), default=100, maximum=200)
    query = {"limit": limit}
    cursor = inputs.get("cursor")
    if isinstance(cursor, str) and cursor.strip():
        query["cursor"] = cursor.strip()
    api_key = _load_api_key()
    status, data = _request("GET", "/users.list", api_key, query=query)
    members = data.get("members")
    if not isinstance(members, list):
        raise RuntimeError("Slack did not return a members list.")
    simplified = [_simplify_user(m) for m in members if isinstance(m, dict)]
    next_cursor = (data.get("response_metadata") or {}).get("next_cursor") or ""
    return {
        "ok": True,
        "http_status": status,
        "returned_count": len(simplified),
        "users_json": json.dumps(simplified, ensure_ascii=False),
        "next_cursor": next_cursor,
        "has_more": bool(next_cursor),
    }, None


def slack_get_user_info(inputs, stamp):
    """Look up one user by id, or by email if id is not known."""
    user_id = inputs.get("user_id")
    email = inputs.get("email")
    has_id = isinstance(user_id, str) and user_id.strip()
    has_email = isinstance(email, str) and email.strip()
    if not has_id and not has_email:
        raise RuntimeError("Provide user_id or email.")
    api_key = _load_api_key()
    if has_id:
        status, data = _request(
            "GET", "/users.info", api_key, query={"user": _require_id(user_id, "user_id")}
        )
    else:
        status, data = _request(
            "GET", "/users.lookupByEmail", api_key, query={"email": email.strip()}
        )
    user = data.get("user")
    if not isinstance(user, dict):
        raise RuntimeError("Slack did not return a user object.")
    simplified = _simplify_user(user)
    return {
        "ok": True,
        "http_status": status,
        "user_id": simplified.get("id"),
        "name": simplified.get("name"),
        "real_name": simplified.get("real_name"),
        "display_name": simplified.get("display_name"),
        "email": simplified.get("email"),
        "is_bot": simplified.get("is_bot"),
        "is_admin": simplified.get("is_admin"),
        "deleted": simplified.get("deleted"),
    }, None


def slack_list_channels(inputs, stamp):
    """List channels visible to the bot (public, plus private ones it has been invited to)."""
    types = inputs.get("types") or "public_channel,private_channel"
    if not isinstance(types, str) or not types.strip():
        raise RuntimeError("types must be a non-empty comma-separated string.")
    limit = _bounded_limit(inputs.get("limit"), default=100, maximum=200)
    query = {"types": types.strip(), "limit": limit}
    exclude_archived = inputs.get("exclude_archived")
    if exclude_archived is not None:
        query["exclude_archived"] = "true" if exclude_archived else "false"
    cursor = inputs.get("cursor")
    if isinstance(cursor, str) and cursor.strip():
        query["cursor"] = cursor.strip()
    api_key = _load_api_key()
    status, data = _request("GET", "/conversations.list", api_key, query=query)
    channels = data.get("channels")
    if not isinstance(channels, list):
        raise RuntimeError("Slack did not return a channels list.")
    simplified = [_simplify_channel(c) for c in channels if isinstance(c, dict)]
    next_cursor = (data.get("response_metadata") or {}).get("next_cursor") or ""
    return {
        "ok": True,
        "http_status": status,
        "returned_count": len(simplified),
        "channels_json": json.dumps(simplified, ensure_ascii=False),
        "next_cursor": next_cursor,
        "has_more": bool(next_cursor),
    }, None


def slack_get_channel_info(inputs, stamp):
    """Retrieve one channel's metadata."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    api_key = _load_api_key()
    status, data = _request(
        "GET", "/conversations.info", api_key,
        query={"channel": channel_id, "include_num_members": "true"},
    )
    channel = data.get("channel")
    if not isinstance(channel, dict):
        raise RuntimeError("Slack did not return a channel object.")
    simplified = _simplify_channel(channel)
    return {
        "ok": True,
        "http_status": status,
        "channel_id": simplified.get("id"),
        "name": simplified.get("name"),
        "is_private": simplified.get("is_private"),
        "is_archived": simplified.get("is_archived"),
        "is_member": simplified.get("is_member"),
        "num_members": simplified.get("num_members"),
        "topic": simplified.get("topic"),
        "purpose": simplified.get("purpose"),
        "created": channel.get("created"),
        "creator": channel.get("creator"),
    }, None


def slack_list_channel_members(inputs, stamp):
    """List a channel's member user ids."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    limit = _bounded_limit(inputs.get("limit"), default=100, maximum=200)
    query = {"channel": channel_id, "limit": limit}
    cursor = inputs.get("cursor")
    if isinstance(cursor, str) and cursor.strip():
        query["cursor"] = cursor.strip()
    api_key = _load_api_key()
    status, data = _request("GET", "/conversations.members", api_key, query=query)
    members = data.get("members")
    if not isinstance(members, list):
        raise RuntimeError("Slack did not return a members list.")
    member_ids = [m for m in members if isinstance(m, str)]
    next_cursor = (data.get("response_metadata") or {}).get("next_cursor") or ""
    return {
        "ok": True,
        "http_status": status,
        "returned_count": len(member_ids),
        "member_ids_json": json.dumps(member_ids, ensure_ascii=False),
        "next_cursor": next_cursor,
        "has_more": bool(next_cursor),
    }, None


def slack_get_channel_history(inputs, stamp):
    """Read a channel's recent messages, newest first."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    limit = _bounded_limit(inputs.get("limit"), default=50, maximum=200)
    query = {"channel": channel_id, "limit": limit}
    for field in ("oldest", "latest"):
        value = inputs.get(field)
        if isinstance(value, str) and value.strip():
            query[field] = value.strip()
    cursor = inputs.get("cursor")
    if isinstance(cursor, str) and cursor.strip():
        query["cursor"] = cursor.strip()
    api_key = _load_api_key()
    status, data = _request("GET", "/conversations.history", api_key, query=query)
    messages = data.get("messages")
    if not isinstance(messages, list):
        raise RuntimeError("Slack did not return a messages list.")
    simplified = [_simplify_message(m) for m in messages if isinstance(m, dict)]
    next_cursor = (data.get("response_metadata") or {}).get("next_cursor") or ""
    return {
        "ok": True,
        "http_status": status,
        "returned_count": len(simplified),
        "messages_json": json.dumps(simplified, ensure_ascii=False),
        "next_cursor": next_cursor,
        "has_more": bool(data.get("has_more")) or bool(next_cursor),
    }, None


def slack_get_thread_replies(inputs, stamp):
    """Read every reply in one message thread (the first item is the parent message)."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    thread_ts = _require(inputs.get("thread_ts"), "thread_ts")
    limit = _bounded_limit(inputs.get("limit"), default=50, maximum=200)
    query = {"channel": channel_id, "ts": thread_ts, "limit": limit}
    cursor = inputs.get("cursor")
    if isinstance(cursor, str) and cursor.strip():
        query["cursor"] = cursor.strip()
    api_key = _load_api_key()
    status, data = _request("GET", "/conversations.replies", api_key, query=query)
    messages = data.get("messages")
    if not isinstance(messages, list):
        raise RuntimeError("Slack did not return a messages list.")
    simplified = [_simplify_message(m) for m in messages if isinstance(m, dict)]
    next_cursor = (data.get("response_metadata") or {}).get("next_cursor") or ""
    return {
        "ok": True,
        "http_status": status,
        "returned_count": len(simplified),
        "messages_json": json.dumps(simplified, ensure_ascii=False),
        "next_cursor": next_cursor,
        "has_more": bool(next_cursor),
    }, None


def slack_list_usergroups(inputs, stamp):
    """List usergroups (@-mention groups), optionally with each one's member ids."""
    include_users = bool(inputs.get("include_users"))
    query = {"include_disabled": "true" if inputs.get("include_disabled") else "false"}
    api_key = _load_api_key()
    status, data = _request("GET", "/usergroups.list", api_key, query=query)
    groups = data.get("usergroups")
    if not isinstance(groups, list):
        raise RuntimeError("Slack did not return a usergroups list.")
    simplified = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        simplified.append({
            "id": group.get("id"),
            "name": group.get("name"),
            "handle": group.get("handle"),
            "description": group.get("description"),
            "is_external": bool(group.get("is_external")),
            "user_count": group.get("user_count"),
        })
    if include_users:
        for entry in simplified:
            group_id = entry.get("id")
            if not isinstance(group_id, str) or not group_id:
                continue
            _, users_data = _request(
                "GET", "/usergroups.users.list", api_key, query={"usergroup": group_id}
            )
            users = users_data.get("users")
            entry["user_ids"] = users if isinstance(users, list) else []
    return {
        "ok": True,
        "http_status": status,
        "returned_count": len(simplified),
        "usergroups_json": json.dumps(simplified, ensure_ascii=False),
    }, None


def slack_list_files(inputs, stamp):
    """List files shared in the workspace, optionally scoped to one channel or user."""
    page = _bounded_limit(inputs.get("page"), default=1, maximum=1000)
    count = _bounded_limit(inputs.get("count"), default=20, maximum=100)
    query = {"page": page, "count": count}
    channel_id = inputs.get("channel_id")
    if isinstance(channel_id, str) and channel_id.strip():
        query["channel"] = channel_id.strip()
    user_id = inputs.get("user_id")
    if isinstance(user_id, str) and user_id.strip():
        query["user"] = user_id.strip()
    api_key = _load_api_key()
    status, data = _request("GET", "/files.list", api_key, query=query)
    files = data.get("files")
    if not isinstance(files, list):
        raise RuntimeError("Slack did not return a files list.")
    simplified = []
    for f in files:
        if not isinstance(f, dict):
            continue
        simplified.append({
            "id": f.get("id"),
            "name": f.get("name"),
            "title": f.get("title"),
            "mimetype": f.get("mimetype"),
            "filetype": f.get("filetype"),
            "size": f.get("size"),
            "is_public": bool(f.get("is_public")),
            # Slack's own logged-in-workspace link, distinct from the
            # world-readable secret URL files.sharedPublicURL mints (that
            # command, Tier 3, gets its own dedicated redaction handling).
            "permalink": f.get("permalink"),
        })
    paging = data.get("paging") or {}
    return {
        "ok": True,
        "http_status": status,
        "returned_count": len(simplified),
        "files_json": json.dumps(simplified, ensure_ascii=False),
        "page": paging.get("page"),
        "pages": paging.get("pages"),
        "total": paging.get("total"),
    }, None


def slack_get_dnd_status(inputs, stamp):
    """Check whether a user currently has Do Not Disturb enabled."""
    user_id = _require_id(inputs.get("user_id"), "user_id")
    api_key = _load_api_key()
    status, data = _request("GET", "/dnd.info", api_key, query={"user": user_id})
    return {
        "ok": True,
        "http_status": status,
        "dnd_enabled": bool(data.get("dnd_enabled")),
        "next_dnd_start_ts": data.get("next_dnd_start_ts"),
        "next_dnd_end_ts": data.get("next_dnd_end_ts"),
        "snooze_enabled": bool(data.get("snooze_enabled")),
    }, None


# --- Tier 2: low-risk writes, execute with a receipt (no additional airlock) ---


def slack_post_message(inputs, stamp):
    """Post a message to a channel, or reply in a thread if thread_ts is given."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    text = _require(inputs.get("text"), "text")
    body = {"channel": channel_id, "text": text}
    thread_ts = inputs.get("thread_ts")
    if isinstance(thread_ts, str) and thread_ts.strip():
        body["thread_ts"] = thread_ts.strip()
    api_key = _load_api_key()
    status, data = _request("POST", "/chat.postMessage", api_key, body=body, is_write=True)
    return {
        "ok": True,
        "http_status": status,
        "channel_id": data.get("channel"),
        "message_ts": data.get("ts"),
    }, None


def slack_post_ephemeral(inputs, stamp):
    """Post a message visible to only one user in a channel."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    user_id = _require_id(inputs.get("user_id"), "user_id")
    text = _require(inputs.get("text"), "text")
    body = {"channel": channel_id, "user": user_id, "text": text}
    api_key = _load_api_key()
    status, data = _request("POST", "/chat.postEphemeral", api_key, body=body, is_write=True)
    return {
        "ok": True,
        "http_status": status,
        "message_ts": data.get("message_ts"),
    }, None


def slack_add_reaction(inputs, stamp):
    """Add an emoji reaction to a message."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    timestamp = _require(inputs.get("timestamp"), "timestamp")
    name = _require(inputs.get("name"), "name")
    body = {"channel": channel_id, "timestamp": timestamp, "name": name}
    api_key = _load_api_key()
    status, _data = _request("POST", "/reactions.add", api_key, body=body, is_write=True)
    return {"ok": True, "http_status": status, "channel_id": channel_id, "timestamp": timestamp, "name": name}, None


def slack_remove_reaction(inputs, stamp):
    """Remove an emoji reaction from a message."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    timestamp = _require(inputs.get("timestamp"), "timestamp")
    name = _require(inputs.get("name"), "name")
    body = {"channel": channel_id, "timestamp": timestamp, "name": name}
    api_key = _load_api_key()
    status, _data = _request("POST", "/reactions.remove", api_key, body=body, is_write=True)
    return {"ok": True, "http_status": status, "channel_id": channel_id, "timestamp": timestamp, "name": name}, None


def slack_add_pin(inputs, stamp):
    """Pin a message to a channel."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    timestamp = _require(inputs.get("timestamp"), "timestamp")
    body = {"channel": channel_id, "timestamp": timestamp}
    api_key = _load_api_key()
    status, _data = _request("POST", "/pins.add", api_key, body=body, is_write=True)
    return {"ok": True, "http_status": status, "channel_id": channel_id, "timestamp": timestamp}, None


def slack_remove_pin(inputs, stamp):
    """Unpin a message from a channel."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    timestamp = _require(inputs.get("timestamp"), "timestamp")
    body = {"channel": channel_id, "timestamp": timestamp}
    api_key = _load_api_key()
    status, _data = _request("POST", "/pins.remove", api_key, body=body, is_write=True)
    return {"ok": True, "http_status": status, "channel_id": channel_id, "timestamp": timestamp}, None


def slack_add_bookmark(inputs, stamp):
    """Add a link bookmark to a channel."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    title = _require(inputs.get("title"), "title")
    link = _require(inputs.get("link"), "link")
    body = {"channel_id": channel_id, "title": title, "link": link, "type": "link"}
    emoji = inputs.get("emoji")
    if isinstance(emoji, str) and emoji.strip():
        body["emoji"] = emoji.strip()
    api_key = _load_api_key()
    status, data = _request("POST", "/bookmarks.add", api_key, body=body, is_write=True)
    bookmark = data.get("bookmark") or {}
    return {
        "ok": True,
        "http_status": status,
        "bookmark_id": bookmark.get("id"),
        "title": bookmark.get("title"),
        "link": bookmark.get("link"),
    }, None


def slack_edit_bookmark(inputs, stamp):
    """Edit an existing channel bookmark's title, link, or emoji."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    bookmark_id = _require(inputs.get("bookmark_id"), "bookmark_id")
    title = inputs.get("title")
    link = inputs.get("link")
    emoji = inputs.get("emoji")
    if not any(isinstance(v, str) and v.strip() for v in (title, link, emoji)):
        raise RuntimeError("Provide at least one of title, link, or emoji to edit.")
    body = {"channel_id": channel_id, "bookmark_id": bookmark_id}
    if isinstance(title, str) and title.strip():
        body["title"] = title.strip()
    if isinstance(link, str) and link.strip():
        body["link"] = link.strip()
    if isinstance(emoji, str) and emoji.strip():
        body["emoji"] = emoji.strip()
    api_key = _load_api_key()
    status, data = _request("POST", "/bookmarks.edit", api_key, body=body, is_write=True)
    bookmark = data.get("bookmark") or {}
    return {
        "ok": True,
        "http_status": status,
        "bookmark_id": bookmark.get("id"),
        "title": bookmark.get("title"),
        "link": bookmark.get("link"),
    }, None


def slack_remove_bookmark(inputs, stamp):
    """Remove a bookmark from a channel."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    bookmark_id = _require(inputs.get("bookmark_id"), "bookmark_id")
    body = {"channel_id": channel_id, "bookmark_id": bookmark_id}
    api_key = _load_api_key()
    status, _data = _request("POST", "/bookmarks.remove", api_key, body=body, is_write=True)
    return {"ok": True, "http_status": status, "channel_id": channel_id, "bookmark_id": bookmark_id}, None


def slack_add_reminder(inputs, stamp):
    """Create a reminder for a user. time accepts a Unix timestamp or a
    Slack-understood phrase such as 'in 5 minutes' or 'tomorrow at 9am'."""
    text = _require(inputs.get("text"), "text")
    time_value = _require(inputs.get("time"), "time")
    user_id = inputs.get("user_id")
    body = {"text": text, "time": time_value}
    if isinstance(user_id, str) and user_id.strip():
        body["user"] = _require_id(user_id, "user_id")
    api_key = _load_api_key()
    status, data = _request("POST", "/reminders.add", api_key, body=body, is_write=True)
    reminder = data.get("reminder") or {}
    return {
        "ok": True,
        "http_status": status,
        "reminder_id": reminder.get("id"),
        "text": reminder.get("text"),
        "time": reminder.get("time"),
    }, None


def slack_complete_reminder(inputs, stamp):
    """Mark a reminder as complete."""
    reminder_id = _require(inputs.get("reminder_id"), "reminder_id")
    body = {"reminder": reminder_id}
    api_key = _load_api_key()
    status, _data = _request("POST", "/reminders.complete", api_key, body=body, is_write=True)
    return {"ok": True, "http_status": status, "reminder_id": reminder_id}, None


def slack_set_channel_topic(inputs, stamp):
    """Set a channel's topic."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    topic = _require(inputs.get("topic"), "topic")
    body = {"channel": channel_id, "topic": topic}
    api_key = _load_api_key()
    status, data = _request("POST", "/conversations.setTopic", api_key, body=body, is_write=True)
    return {"ok": True, "http_status": status, "channel_id": channel_id, "topic": data.get("topic")}, None


def slack_set_channel_purpose(inputs, stamp):
    """Set a channel's purpose."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    purpose = _require(inputs.get("purpose"), "purpose")
    body = {"channel": channel_id, "purpose": purpose}
    api_key = _load_api_key()
    status, data = _request("POST", "/conversations.setPurpose", api_key, body=body, is_write=True)
    return {"ok": True, "http_status": status, "channel_id": channel_id, "purpose": data.get("purpose")}, None


def slack_create_channel(inputs, stamp):
    """Create a new channel."""
    name = _require(inputs.get("name"), "name")
    is_private = bool(inputs.get("is_private"))
    body = {"name": name, "is_private": is_private}
    api_key = _load_api_key()
    status, data = _request("POST", "/conversations.create", api_key, body=body, is_write=True)
    channel = data.get("channel") or {}
    return {
        "ok": True,
        "http_status": status,
        "channel_id": channel.get("id"),
        "name": channel.get("name"),
        "is_private": bool(channel.get("is_private")),
    }, None


def slack_join_channel(inputs, stamp):
    """Join a public channel."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    body = {"channel": channel_id}
    api_key = _load_api_key()
    status, data = _request("POST", "/conversations.join", api_key, body=body, is_write=True)
    channel = data.get("channel") or {}
    return {
        "ok": True,
        "http_status": status,
        "channel_id": channel.get("id") or channel_id,
        "name": channel.get("name"),
    }, None


def slack_schedule_message(inputs, stamp):
    """Schedule a message to post at a future Unix timestamp."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    text = _require(inputs.get("text"), "text")
    post_at = inputs.get("post_at")
    if isinstance(post_at, float) and not post_at.is_integer():
        raise RuntimeError("post_at must be an integer Unix timestamp.")
    try:
        post_at = int(post_at)
    except (TypeError, ValueError):
        raise RuntimeError("post_at must be an integer Unix timestamp.") from None
    if post_at <= 0:
        raise RuntimeError("post_at must be a positive Unix timestamp.")
    body = {"channel": channel_id, "text": text, "post_at": post_at}
    api_key = _load_api_key()
    status, data = _request("POST", "/chat.scheduleMessage", api_key, body=body, is_write=True)
    return {
        "ok": True,
        "http_status": status,
        "channel_id": data.get("channel"),
        "scheduled_message_id": data.get("scheduled_message_id"),
        "post_at": data.get("post_at"),
    }, None


def slack_cancel_scheduled_message(inputs, stamp):
    """Cancel a previously scheduled message before it posts."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    scheduled_message_id = _require(inputs.get("scheduled_message_id"), "scheduled_message_id")
    body = {"channel": channel_id, "scheduled_message_id": scheduled_message_id}
    api_key = _load_api_key()
    status, _data = _request("POST", "/chat.deleteScheduledMessage", api_key, body=body, is_write=True)
    return {
        "ok": True,
        "http_status": status,
        "channel_id": channel_id,
        "scheduled_message_id": scheduled_message_id,
    }, None


# --- Tier 3: high-risk writes, human approval airlock ---


def slack_rename_channel(inputs, stamp):
    """Rename a channel."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    name = _require(inputs.get("name"), "name")
    body = {"channel": channel_id, "name": name}
    api_key = _load_api_key()
    status, data = _request("POST", "/conversations.rename", api_key, body=body, is_write=True)
    channel = data.get("channel") or {}
    return {
        "ok": True,
        "http_status": status,
        "channel_id": channel.get("id") or channel_id,
        "name": channel.get("name"),
    }, None


def slack_archive_channel(inputs, stamp):
    """Archive a channel. Members can no longer post; use
    slack.unarchive_channel to reverse."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    body = {"channel": channel_id}
    api_key = _load_api_key()
    status, _data = _request("POST", "/conversations.archive", api_key, body=body, is_write=True)
    return {"ok": True, "http_status": status, "channel_id": channel_id}, None


def slack_unarchive_channel(inputs, stamp):
    """Restore a previously archived channel."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    body = {"channel": channel_id}
    api_key = _load_api_key()
    status, _data = _request("POST", "/conversations.unarchive", api_key, body=body, is_write=True)
    return {"ok": True, "http_status": status, "channel_id": channel_id}, None


def slack_delete_message(inputs, stamp):
    """Permanently delete a message. Irreversible - Slack has no undo."""
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    timestamp = _require(inputs.get("timestamp"), "timestamp")
    body = {"channel": channel_id, "ts": timestamp}
    api_key = _load_api_key()
    status, _data = _request("POST", "/chat.delete", api_key, body=body, is_write=True)
    return {"ok": True, "http_status": status, "channel_id": channel_id, "timestamp": timestamp}, None


def slack_kick_user_from_channel(inputs, stamp):
    """Remove a member from a channel. Hard-blocked under the Open Community
    Hardened preset (see README - Governance presets): a membership change,
    socially loaded and easy to abuse on a large open workspace."""
    _enforce_preset_block("slack.kick_user_from_channel")
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    user_id = _require_id(inputs.get("user_id"), "user_id")
    body = {"channel": channel_id, "user": user_id}
    api_key = _load_api_key()
    status, _data = _request("POST", "/conversations.kick", api_key, body=body, is_write=True)
    return {"ok": True, "http_status": status, "channel_id": channel_id, "user_id": user_id}, None


def slack_invite_to_channel(inputs, stamp):
    """Add one or more members to a channel. Hard-blocked under the Open
    Community Hardened preset: adds people to potentially sensitive content,
    same reasoning as the kick side of a membership change."""
    _enforce_preset_block("slack.invite_to_channel")
    channel_id = _require_id(inputs.get("channel_id"), "channel_id")
    user_ids = inputs.get("user_ids")
    if not isinstance(user_ids, str) or not user_ids.strip():
        raise RuntimeError("user_ids is required (comma-separated Slack user IDs).")
    body = {"channel": channel_id, "users": user_ids.strip()}
    api_key = _load_api_key()
    status, data = _request("POST", "/conversations.invite", api_key, body=body, is_write=True)
    channel = data.get("channel") or {}
    return {"ok": True, "http_status": status, "channel_id": channel.get("id") or channel_id}, None


def slack_delete_file(inputs, stamp):
    """Permanently delete a file. Irreversible."""
    file_id = _require(inputs.get("file_id"), "file_id")
    body = {"file": file_id}
    api_key = _load_api_key()
    status, _data = _request("POST", "/files.delete", api_key, body=body, is_write=True)
    return {"ok": True, "http_status": status, "file_id": file_id}, None


def slack_update_usergroup_members(inputs, stamp):
    """Replace a usergroup's ENTIRE membership list - not additive; omitting
    an existing member removes them. Hard-blocked under the Open Community
    Hardened preset: rewrites who a broadcast @-mention group pings, high
    blast radius, easy to abuse for spam or exclusion."""
    _enforce_preset_block("slack.update_usergroup_members")
    usergroup_id = _require(inputs.get("usergroup_id"), "usergroup_id")
    user_ids = inputs.get("user_ids")
    if not isinstance(user_ids, str) or not user_ids.strip():
        raise RuntimeError(
            "user_ids is required (comma-separated Slack user IDs) - this replaces "
            "the usergroup's ENTIRE membership, not just adds to it."
        )
    body = {"usergroup": usergroup_id, "users": user_ids.strip()}
    api_key = _load_api_key()
    status, data = _request("POST", "/usergroups.users.update", api_key, body=body, is_write=True)
    users = data.get("users")
    return {
        "ok": True,
        "http_status": status,
        "usergroup_id": usergroup_id,
        "user_count": len(users) if isinstance(users, list) else None,
    }, None


def slack_share_file_publicly(inputs, stamp):
    """Make a private file world-readable via a public link - the single
    highest data-leak-risk command in this module. The returned URL is
    bearer-style: anyone holding it can read the file, no Slack login
    required. Hard-blocked under the Open Community Hardened preset.

    The success output deliberately still returns the full URL (it is the
    human-approved deliverable of this exact command - redacting it there
    would make the command useless), but the pub_secret component is
    redacted from any error/note text the same way a token would be (see
    _redact / SECURITY.md), since that path has no legitimate reason to
    echo it."""
    _enforce_preset_block("slack.share_file_publicly")
    file_id = _require(inputs.get("file_id"), "file_id")
    body = {"file": file_id}
    api_key = _load_api_key()
    status, data = _request("POST", "/files.sharedPublicURL", api_key, body=body, is_write=True)
    f = data.get("file") or {}
    return {
        "ok": True,
        "http_status": status,
        "file_id": f.get("id") or file_id,
        "permalink_public": f.get("permalink_public"),
        "warning": (
            "This URL is bearer-style: anyone who has it can read the file with "
            "no login. Treat it as a credential - never post it somewhere untrusted."
        ),
    }, None


def slack_uninstall_app(inputs, stamp):
    """Uninstall this Slack app from the workspace - a break-glass,
    self-disabling action. Requires typing the literal confirmation phrase
    'UNINSTALL' in addition to the normal approval ceremony, since this ends
    the module's ability to do anything else in this workspace. Not
    hard-blocked under any preset - a legitimate operator must always be
    able to reach their own break-glass switch."""
    if inputs.get("confirm") != "UNINSTALL":
        raise RuntimeError(
            "confirm must be exactly 'UNINSTALL' to uninstall the Slack app. "
            "This is irreversible: the app loses access to this workspace immediately."
        )
    client_id, client_secret = _load_client_credentials()
    api_key = _load_api_key()
    body = {"client_id": client_id, "client_secret": client_secret}
    try:
        status, _data = _request("POST", "/apps.uninstall", api_key, body=body, is_write=True)
    except RuntimeError as exc:
        raise RuntimeError(_redact(_redact(str(exc), api_key), client_secret)) from None
    return {"ok": True, "http_status": status}, None


def slack_revoke_token(inputs, stamp):
    """Revoke this module's own bot token - a break-glass, self-disabling
    action. Requires typing the literal confirmation phrase 'REVOKE' in
    addition to the normal approval ceremony, since this ends the module's
    ability to do anything else in this workspace. Not hard-blocked under
    any preset - a legitimate operator must always be able to reach their
    own break-glass switch."""
    if inputs.get("confirm") != "REVOKE":
        raise RuntimeError(
            "confirm must be exactly 'REVOKE' to revoke the bot token. "
            "This is irreversible: every command in this module stops working immediately."
        )
    api_key = _load_api_key()
    status, data = _request("POST", "/auth.revoke", api_key, body={}, is_write=True)
    return {"ok": True, "http_status": status, "revoked": bool(data.get("revoked"))}, None
