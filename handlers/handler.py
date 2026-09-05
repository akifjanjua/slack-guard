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
