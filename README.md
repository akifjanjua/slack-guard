# Slack Guard

[![Slack Guard Tests](https://github.com/akifjanjua/slack-guard/actions/workflows/slack-guard-tests.yml/badge.svg)](https://github.com/akifjanjua/slack-guard/actions/workflows/slack-guard-tests.yml)

Slack Guard is a governance-first RailCall module for Slack workspaces. It is being built command-by-command against a 31-command, 3-tier plan; this table is kept current with every version, not written after the fact.

## Commands

| Tier | Status | Commands |
|---|---|---|
| 1 — read, executes immediately | **`slack.get_team_info` LIVE (v0.1.0)** | `list_users`, `get_user_info`, `list_channels`, `get_channel_info`, `list_channel_members`, `get_channel_history`, `get_thread_replies`, `list_usergroups`, `list_files`, `get_dnd_status` — planned |
| 2 — low-risk write, receipted | planned | `post_message`, `post_ephemeral`, `add_reaction`/`remove_reaction`, `add_pin`/`remove_pin`, `add_bookmark`/`edit_bookmark`/`remove_bookmark`, `add_reminder`/`complete_reminder`, `set_channel_topic`/`set_channel_purpose`, `create_channel`, `join_channel`, `schedule_message`/`cancel_scheduled_message` |
| 3 — high-risk write, human airlock | planned | `rename_channel`, `archive_channel`/`unarchive_channel`, `delete_message`, `kick_user_from_channel`, `invite_to_channel`, `delete_file`, `update_usergroup_members`, `share_file_publicly`, `uninstall_app`/`revoke_token` (break-glass, last) |

`slack.get_team_info` takes no inputs and doubles as the connectivity smoke test: a successful call proves the bot token is valid and confirms the module reaches the real Slack Web API end to end.

## Governance presets

- **Observer** (recommended first-install default) — every write, Tier 2 included, requires the approval airlock. Zero trust in agent judgment until an operator has watched it work.
- **Team Copilot** — the tiered split above once Tier 2/3 ship: reads free, low-risk writes receipted, high-risk writes airlocked. Becomes the shipped default at that point.
- **Open Community Hardened** — Team Copilot, plus channel-membership and usergroup-membership changes are hard-blocked rather than merely airlocked, and `share_file_publicly` is disabled outright. For large open/public workspaces, where the blast radius of those actions is worse than in a closed team and an approval prompt alone isn't a strong enough gate.

## Egress contract

The signed manifest declares `allowed_destinations: [{"provider":"slack","hosts":["slack.com"]}]` — Slack only, zero LLM/model-provider destinations, enforced by Station at load time. See [SECURITY.md](SECURITY.md).

## Install

```bash
python -m pip install certifi
git clone https://github.com/akifjanjua/slack-guard.git
```

Copy the cloned folder's contents into `~/.railcall/station/modules/muhammad-akif-janjua-slack-guard/` (the folder name is the module slug). Open RailCall Studio, reload **Modules**, and confirm **Slack Guard v0.1.0**, **signature verified**, **1 command**.

Post-publish, this will work instead: `railcall market install muhammad-akif-janjua/slack-guard`. Slack Guard is free (`license_required: false`).

## Configure credentials

Create a custom Slack app at `api.slack.com/apps` → **From scratch**, in the workspace you want it to govern. Under **OAuth & Permissions**, add the Bot Token Scope `team:read` (more scopes are added as Tier 2/3 commands ship), then **Install to Workspace** and copy the **Bot User OAuth Token** (`xoxb-...`).

In Studio → **Integrations**, search "slack" and save the token as `SLACK_BOT_TOKEN` on the `muhammad-akif-janjua-slack-guard::slack` card.

## Run a command

Open Studio's **Sends** tab (`#/sends?module=slack`), pick `slack.get_team_info`, click **Fire**, then **1. Preview → 2. Approve → 3. Execute**. Both reads and writes produce a signed receipt.

## Limitations

Scoped entirely to a single-workspace custom-app bot token — the no-review, PAT-equivalent install path. `admin.*` endpoints (org-wide user provisioning, audit logs, DLP information barriers) require an Enterprise Grid org-level admin install and are out of scope for this module.

`contest:2026Q3`
