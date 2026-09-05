# Slack Guard

[![Slack Guard Tests](https://github.com/akifjanjua/slack-guard/actions/workflows/slack-guard-tests.yml/badge.svg)](https://github.com/akifjanjua/slack-guard/actions/workflows/slack-guard-tests.yml)

Slack Guard is a governance-first RailCall module for Slack. Built command-by-command against a 39-command, 3-tier plan; this table stays current every version, not written after the fact.

## Commands

| Tier | Status | Commands |
|---|---|---|
| 1 — read, executes immediately | **All 11 LIVE** | `get_team_info`, `list_users`, `get_user_info`, `list_channels`, `get_channel_info`, `list_channel_members`, `get_channel_history`, `get_thread_replies`, `list_usergroups`, `list_files`, `get_dnd_status` |
| 2 — low-risk write, receipted | **All 17 LIVE** | `post_message`, `post_ephemeral`, `add_reaction`/`remove_reaction`, `add_pin`/`remove_pin`, `add_bookmark`/`edit_bookmark`/`remove_bookmark`, `add_reminder`/`complete_reminder`, `set_channel_topic`/`set_channel_purpose`, `create_channel`, `join_channel`, `schedule_message`/`cancel_scheduled_message` |
| 3 — high-risk write, human airlock | **All 11 LIVE (v0.4.0)** | `rename_channel`, `archive_channel`/`unarchive_channel`, `delete_message`, `kick_user_from_channel`\*, `invite_to_channel`\*, `delete_file`, `update_usergroup_members`\*, `share_file_publicly`\*, `uninstall_app`/`revoke_token` (break-glass, last) |

\* Hard-blocked under **Open Community Hardened** — see below.

`get_team_info` doubles as the connectivity smoke test. The discovery reads let a write address a real channel/user/group by id instead of guessing. Reads paginate via `next_cursor`. Tier 2 is risk `low`; Tier 3 is risk `high` and irreversible or high-blast-radius. `uninstall_app`/`revoke_token` also require typing `UNINSTALL`/`REVOKE` in a `confirm` field, on top of approval.

## Governance presets

Set via an optional `SLACK_GUARD_PRESET` field on the bot-token card (`observer` / `team_copilot` / `open_community_hardened`; defaults to `team_copilot`).

- **Observer** — every write needs approval. No additional hard blocks.
- **Team Copilot** (default) — reads free, low-risk writes receipted, high-risk writes airlocked. No additional hard blocks.
- **Open Community Hardened** — Team Copilot, but `kick_user_from_channel`, `invite_to_channel`, `update_usergroup_members`, and `share_file_publicly` are **hard-blocked in the handler itself**, before any network call, regardless of approval — real enforcement, not a documented label (see `test_hardened_preset_blocks_the_four_commands`). For open/public workspaces with a bigger abuse surface than a closed team.

## Egress contract

The signed manifest declares `allowed_destinations: [{"provider":"slack","hosts":["slack.com"]}]` — Slack only, zero LLM egress, enforced by Station at load time. See [SECURITY.md](SECURITY.md).

## Install

```bash
python -m pip install certifi
git clone https://github.com/akifjanjua/slack-guard.git
```

Copy the cloned folder's contents into `~/.railcall/station/modules/muhammad-akif-janjua-slack-guard/` (the folder name is the module slug). Open RailCall Studio, reload **Modules**, confirm **Slack Guard v0.4.0**, **signature verified**, **39 commands**.

Post-publish: `railcall market install muhammad-akif-janjua/slack-guard`. Free (`license_required: false`).

## Configure credentials

Create a custom Slack app at `api.slack.com/apps` → **From scratch**, in the workspace to govern. Under **OAuth & Permissions**, add the Bot Token Scopes each command you plan to use needs — see [docs/SCOPES.md](docs/SCOPES.md) for the full table — then **Install to Workspace** and copy the **Bot User OAuth Token** (`xoxb-...`).

In Studio → **Integrations**, search "slack" and save the token as `SLACK_BOT_TOKEN` on the `muhammad-akif-janjua-slack-guard::slack` card.

## Run a command

Open Studio's **Sends** tab (`#/sends?module=slack`), pick a command, click **Fire**, then **1. Preview → 2. Approve → 3. Execute**. Every command produces a signed receipt, even a rejected one — a missing scope fails cleanly as `failed_safely`, never a false success.

## Limitations

Scoped to a single-workspace custom-app bot token, the no-review install path. `admin.*` endpoints (org-wide provisioning, audit, DLP barriers) need an Enterprise Grid admin install and are out of scope.

`contest:2026Q3`
