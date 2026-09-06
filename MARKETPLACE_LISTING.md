# Slack Guard

Governed Slack access for teams: every command previewed, human-approved where it matters, and receipted — visibility by default, writes on an airlock, the riskiest actions hard-blocked by preset before they can even reach Slack.

## What it does

v0.4.1 ships all 39 commands against the real Slack Web API, across 3 risk tiers.

| Tier | Mode | Commands |
|---|---|---|
| **1 — read** (executes immediately, no side effect) | `read` | `get_team_info`, `list_users`, `get_user_info`, `list_channels`, `get_channel_info`, `list_channel_members`, `get_channel_history`, `get_thread_replies`, `list_usergroups`, `list_files`, `get_dnd_status` |
| **2 — low-risk write** (`write_requires_approval`, risk `low` — same Preview → Approve → Execute ceremony as a read) | `write_requires_approval` | `post_message`, `post_ephemeral`, `add_reaction`, `remove_reaction`, `add_pin`, `remove_pin`, `add_bookmark`, `edit_bookmark`, `remove_bookmark`, `add_reminder`, `complete_reminder`, `set_channel_topic`, `set_channel_purpose`, `create_channel`, `join_channel`, `schedule_message`, `cancel_scheduled_message` |
| **3 — high-risk write** (`write_requires_approval`, risk `high`, irreversible or high-blast-radius) | `write_requires_approval` | `rename_channel`, `archive_channel`, `unarchive_channel`, `delete_message`, `kick_user_from_channel`\*, `invite_to_channel`\*, `delete_file`, `update_usergroup_members`\*, `share_file_publicly`\*, `uninstall_app`†, `revoke_token`† |

\* Hard-blocked under **Open Community Hardened** — see below.
† Break-glass, last resort — also require typing an exact confirmation phrase (`UNINSTALL` / `REVOKE`) before any network call, on top of approval.

`get_team_info` doubles as the connectivity smoke test. The discovery reads let a write address a real channel/user/group by id instead of guessing. Every read paginates via `next_cursor` where Slack itself paginates.

## Governance presets — real enforcement, not documentation

Set via an optional `SLACK_GUARD_PRESET` field on the same Integrations card as the bot token (`observer` / `team_copilot` / `open_community_hardened`; defaults to `team_copilot` when unset, so an operator who never touches this sees no behavior change).

- **Observer** — every write needs approval. No additional hard blocks.
- **Team Copilot** (default) — reads free, low-risk writes receipted, high-risk writes airlocked. No additional hard blocks.
- **Open Community Hardened** — everything Team Copilot does, plus `kick_user_from_channel`, `invite_to_channel`, `update_usergroup_members`, and `share_file_publicly` are **hard-blocked inside the handler itself**, before any network call, regardless of approval already granted. Built for open/public workspaces with a bigger abuse surface than a closed team.

Observer and Team Copilot enforce identically in code — RailCall's own universal per-write approval covers both; the distinction is an operator posture recommendation. Open Community Hardened is the only preset with additional code-level restriction, and it's tested to prove it: `test_hardened_preset_blocks_the_four_commands` asserts the network transport is never reached for any of the four under that preset, and `tools/validate_release.py` fails the build if any of the four functions stops calling the enforcement check. See [SECURITY.md](SECURITY.md) for why this lives in the module's own code rather than RailCall's platform-level approval-policy engine (which doesn't gate third-party module commands).

## Scopes

Bot Token Scopes are additive per command you plan to use — see [docs/SCOPES.md](docs/SCOPES.md) for the full table, one row per Slack scope naming exactly which commands need it. Add only what you'll use; an unused scope is unused blast radius.

## Egress and sandbox

The signed manifest declares `allowed_destinations: [{"provider":"slack","hosts":["slack.com"]}]` — Slack only, zero LLM/model-provider egress — and a `requires` block (`network: ["slack.com"]`, `subprocess: false`, `filesystem_writes: []`) that RailCall Station enforces at handler-load time, confirmed live against a real Station boot log. Credentials are vault-only (`vault_get("slack")`); there is no environment-variable or credential-file fallback. See [SECURITY.md](SECURITY.md).

## What's been tested how — an honest breakdown

**Live-fired against a real Slack workspace, with independently-signature-verified receipts:** `get_team_info`, `list_users` (a real `missing_scope` rejection, proving the failure path — not just success), `list_channels`, `create_channel`, `post_message`, and `kick_user_from_channel` fired twice — once hard-blocked under Open Community Hardened with a human approval already granted (`external_api_touched: false`, confirmed blocked before any network attempt), once reaching Slack for real under Team Copilot (Slack's own `user_not_found` came back, proving the preset genuinely gates the network call rather than just labeling risk). Full evidence, receipt IDs, and independent `railcall verify` output for all of this is in `CONTEST_SUBMISSION.md`.

**Everything else — the remaining 33 commands, every preset combination beyond the one live-fired contrast, approval freshness, malformed-response handling, redaction — is covered by an extensive mocked-transport test suite (`tools/command_logic_test.py`, `tools/security_test.py`, `tools/egress_contract_test.py`) plus an independent third-party conformance check (`shweta/conformance`'s `check_all`, a separate marketplace module), but has not been fired against a live workspace.** The mocked tests model Slack's actual documented request/response shapes and its `{"ok": false, "error": "<code>"}` failure model, and the live-fire pass above found no daylight between mocked and real behavior — but "extensively tested offline" and "proven live" are different claims, and this listing keeps them separate rather than blurring them.

## Pricing

Free (`license_required: false`) at launch. A paid listing on this platform currently hides the structured command breakdown platform-wide, and full command-list visibility — exactly what's in the table above — is core to how a governance module makes its case to a prospective installer: they should be able to see precisely what it can and cannot do, and which of that is hard-blocked under which preset, before they ever configure a credential. Revisit pricing after the contest judging window; nothing about this decision is hardcoded into the module's payment handling, it's a listing-time choice.

## Install

```bash
python -m pip install certifi
git clone https://github.com/akifjanjua/slack-guard.git
```

Copy the cloned folder's contents into `~/.railcall/station/modules/muhammad-akif-janjua-slack-guard/`, open RailCall Studio, reload **Modules**, confirm **Slack Guard v0.4.1**, **signature verified**, **39 commands**. Post-publish: `railcall market install muhammad-akif-janjua/slack-guard`.

## Setup

Create a custom Slack app at `api.slack.com/apps` → **From scratch**, in the workspace to govern. Add the Bot Token Scopes you need (see `docs/SCOPES.md`), **Install to Workspace**, copy the **Bot User OAuth Token** (`xoxb-...`). In Studio → **Integrations**, search "slack" and save it as `SLACK_BOT_TOKEN` on the `muhammad-akif-janjua-slack-guard::slack` card — not the plain `slack` one, which is Station's own built-in integration.

## Limitations

Scoped to a single-workspace custom-app bot token, the no-review install path. `admin.*` endpoints (org-wide provisioning, audit, DLP barriers) need an Enterprise Grid admin install and are out of scope for this module.

`contest:2026Q3`
