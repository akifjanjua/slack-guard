# Changelog

## 0.2.0

All 11 Tier 1 read commands are now live against the real Slack Web API, completing Tier 1 per the build order:

- `slack.list_users` (`users.list`, paginated) and `slack.get_user_info` (`users.info` / `users.lookupByEmail`) — address a person by id or email before a later write needs one.
- `slack.list_channels` (`conversations.list`, paginated), `slack.get_channel_info` (`conversations.info`), `slack.list_channel_members` (`conversations.members`, paginated).
- `slack.get_channel_history` (`conversations.history`, paginated, `oldest`/`latest` bounds) and `slack.get_thread_replies` (`conversations.replies`, paginated) — message text is capped at 1000 characters per item so one oversized message can't blow up a receipt.
- `slack.list_usergroups` (`usergroups.list`, optional `usergroups.users.list` per group via `include_users`).
- `slack.list_files` (`files.list`) — returns each file's own logged-in-workspace `permalink`, never the world-readable secret URL `files.sharedPublicURL` mints (that command is Tier 3 and gets its own dedicated redaction handling when it ships).
- `slack.get_dnd_status` (`dnd.info`).

Added shared helpers (`_require_id`, `_bounded_limit`, `_simplify_user`, `_simplify_channel`, `_simplify_message`) so every new read follows the same input-validation and response-shaping pattern as `slack.get_team_info` rather than each reinventing it. `tools/validate_release.py`'s `EXPECTED_COMMANDS` and `tools/command_logic_test.py` were extended to cover all 11 commands with mocked-transport tests.

README's command table and `module.json`'s description now mark Tier 1 complete and list real example calls for every command.

Next up, per the build order: Tier 2 low-risk writes (`post_message` first), then the approval-airlock plumbing before any Tier 3 command.

## 0.1.0

Initial scaffolding and connectivity milestone. One command against the real Slack Web API:

- `slack.get_team_info` (`team.info`, read, executes immediately) — takes no inputs, returns the connected workspace's id, name, domain, email domain, and icon URL. Doubles as the connectivity smoke test: a successful call proves the bot token is valid and the handler reaches `slack.com` end to end.

Vault-only credentials (`vault_get("slack")`, namespaced to `muhammad-akif-janjua-slack-guard::slack`), certifi-backed TLS, pattern-based token redaction (`xoxb-`/`xoxp-`/`xoxa-`/`xoxe-`/`xoxs-`/`xapp-` shapes plus `Authorization` header and `SLACK_BOT_TOKEN` field matches), and a signed manifest declaring `allowed_destinations: [{"provider":"slack","hosts":["slack.com"]}]` with zero LLM/model-provider egress.

Handles Slack's `{"ok": false, "error": "<code>"}` HTTP-200 failure model as a definitive rejection distinct from a true transport-level failure (429/5xx/connection error), which triggers the write-safety "outcome is unknown" path instead — same one-attempt-only write discipline as Linear Guard and Notion Guard, adapted to Slack's different error signaling.

README documents the full planned 31-command, 3-tier build order and the three governance presets (Observer / Team Copilot / Open Community Hardened) from this first commit, so the roadmap doesn't need retrofitting later the way Notion Guard's command table did.

Next up, per the build order in `README.md`: the remaining Tier 1 reads (`list_users` → `get_user_info` → `list_channels` → `get_channel_info` → `list_channel_members` → `get_channel_history` → `get_thread_replies` → `list_usergroups` → `list_files` → `get_dnd_status`), then Tier 2 low-risk writes, then the approval-airlock plumbing before any Tier 3 command.
