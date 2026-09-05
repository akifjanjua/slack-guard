# Slack Guard

Governed Slack access for teams: visibility first, then approval-gated writes as each tier ships — every risky change previewed, approved, and receipted before it touches your workspace.

## What it does

v0.4.1 ships all 39 commands against the real Slack Web API, across all 3 risk tiers: workspace/user/channel discovery, message history, posting and replying, reactions, pins, bookmarks, reminders, channel management, scheduled messages, channel deletion/archival, membership changes, public file sharing, and break-glass token/app revocation.

## Governance posture

Tier 1 reads execute immediately (no side effect). Tier 2 writes are `write_requires_approval`, risk `low` — Preview → Approve → Execute, same ceremony as a read. Tier 3 writes are `write_requires_approval`, risk `high`, and the module's own **Open Community Hardened** preset additionally hard-blocks the four highest-abuse Tier 3 commands (`kick_user_from_channel`, `invite_to_channel`, `update_usergroup_members`, `share_file_publicly`) outright — in the handler itself, before any network call, regardless of approval. Set via the optional `SLACK_GUARD_PRESET` field on the same Integrations card as the bot token. The signed manifest declares `allowed_destinations: [{"provider":"slack","hosts":["slack.com"]}]` — exactly the Slack API host, zero LLM/model-provider egress. Credentials are vault-only; there is no environment-variable or credential-file fallback.

## Setup

Create a custom Slack app at `api.slack.com/apps` in the workspace you want it to govern, add the Bot Token Scopes each command needs, install it to your workspace, and save the Bot User OAuth Token as `SLACK_BOT_TOKEN` in RailCall Studio → Integrations. Search "slack" — use the `muhammad-akif-janjua-slack-guard::slack` card, not the plain `slack` one (Station auto-namespaces this module's slot because its declared provider collides with Station's built-in Slack integration).

## Pricing

Free (`license_required: false`) at launch — full command-list visibility is core to a governance module's pitch, and a paid listing on this platform currently hides the structured command breakdown. Revisit after the contest judging window.

`contest:2026Q3`
