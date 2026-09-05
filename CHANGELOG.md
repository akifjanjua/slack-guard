# Changelog

## 0.4.0

All 11 Tier 3 high-risk writes are now live, completing the full 39-command build order:

- `slack.rename_channel`, `slack.archive_channel`/`slack.unarchive_channel`, `slack.delete_message`, `slack.delete_file` — irreversible or high-blast-radius, `write_requires_approval`, risk `high`, no additional preset restriction.
- `slack.kick_user_from_channel`, `slack.invite_to_channel`, `slack.update_usergroup_members`, `slack.share_file_publicly` — the four highest-abuse commands, now **hard-blocked in the handler itself** under the Open Community Hardened preset, before any network call and regardless of approval.
- `slack.uninstall_app` / `slack.revoke_token` — break-glass, last. Both require an exact confirmation phrase (`UNINSTALL`/`REVOKE`) in a `confirm` input, checked before any network call, on top of the normal approval ceremony. `uninstall_app` needs new `SLACK_CLIENT_ID`/`SLACK_CLIENT_SECRET` vault fields (from the Slack app's Basic Information page) — vault-only, like every other credential this module uses, never accepted as a command input.

**Governance presets are now real, not just documentation.** Added `_active_preset()`/`_enforce_preset_block()`, reading an optional `SLACK_GUARD_PRESET` field off the same vault entry as the bot token (`observer` / `team_copilot` / `open_community_hardened`, defaulting to `team_copilot` — the least restrictive — when unset or unrecognized, so existing installs see no behavior change). Investigated Station's own `approval_policy.py` engine first (block/require_human/auto_approve rules keyed by connector+verb) and confirmed it governs Station's built-in providers and MCP/workflow execution, not third-party module commands dispatched through Sends — there is no module-extensible hook into it for this. So the hard block lives in the module's own code instead: `_enforce_preset_block()` runs as the first statement of the four affected command functions and raises before any network call. `tools/validate_release.py` gained a regression check that fails the build if any of those four functions stops calling it; `tools/command_logic_test.py` gained `test_hardened_preset_blocks_the_four_commands`, asserting the transport is never reached under the hardened preset and that all four still execute under the other two.

`slack.share_file_publicly` is a deliberate, documented exception to the redaction rule: its success `output` keeps the real `permalink_public` URL (it's the human-approved deliverable of the command — redacting it there would make the command useless), plus a `warning` field calling it out as bearer-style. The URL's `pub_secret` component is redacted from error/note text the same way a bot token would be, as defense in depth (`test_share_file_publicly_output_and_redaction`).

Corrected a labeling inconsistency present since 0.1.0: README/module.json/marketplace docs called this a "31-command, 3-tier plan" throughout, but the fully-expanded one-verb-per-command build (e.g. `add_reaction`/`remove_reaction` as two separate commands, not one) always totaled more than that once every tier was specified. Now consistently described as 39 commands everywhere current-state docs are checked. Historical CHANGELOG entries from earlier versions are left as originally written.

## 0.3.0

All 17 Tier 2 low-risk writes are now live against the real Slack Web API, completing Tier 2 per the build order:

- `slack.post_message` (`chat.postMessage`, optional `thread_ts` reply) and `slack.post_ephemeral` (`chat.postEphemeral`).
- `slack.add_reaction` / `slack.remove_reaction` (`reactions.add` / `remove`).
- `slack.add_pin` / `slack.remove_pin` (`pins.add` / `remove`).
- `slack.add_bookmark` / `slack.edit_bookmark` / `slack.remove_bookmark` (`bookmarks.add` / `edit` / `remove`) — `edit_bookmark` requires at least one of `title`/`link`/`emoji`.
- `slack.add_reminder` / `slack.complete_reminder` (`reminders.add` / `complete`).
- `slack.set_channel_topic` / `slack.set_channel_purpose` (`conversations.setTopic` / `setPurpose`).
- `slack.create_channel` (`conversations.create`) and `slack.join_channel` (`conversations.join`).
- `slack.schedule_message` / `slack.cancel_scheduled_message` (`chat.scheduleMessage` / `deleteScheduledMessage`) — `post_at` validated as a positive integer Unix timestamp before any network call.

All Tier 2 commands are `write_requires_approval`, `risk: "low"` — the same Preview → Approve → Execute ceremony as a read, no additional airlock. `tools/validate_release.py`'s write-risk check was relaxed from "must be medium/high" (a Notion Guard-specific assumption that doesn't fit Slack Guard's deliberate 3-tier risk model) to "must be low/medium/high, matching the declared mode."

Moved the growing Bot Token Scope table out of `README.md` into a dedicated [docs/SCOPES.md](docs/SCOPES.md) to stay under the contest's 500-word README cap as the command count grows; README now links to it.

Verified live against the real workspace: firing `slack.list_users` earlier (v0.2.0 testing) had already surfaced a real `missing_scope` failure and confirmed the `failed_safely` receipt path works correctly against Slack's actual API, not just mocked tests — the same transport code now backs all 17 new writes.

Next up, per the build order: the approval-airlock plumbing, then Tier 3 high-risk writes, least-to-most catastrophic, starting with `rename_channel`.

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
