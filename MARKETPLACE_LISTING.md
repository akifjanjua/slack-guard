# Slack Guard

Governed Slack access for teams: visibility first, then approval-gated writes as each tier ships — every risky change previewed, approved, and receipted before it touches your workspace.

## What it does

v0.1.0 ships one command against the real Slack Web API: `slack.get_team_info`, a read that confirms the connected workspace's identity and doubles as the connectivity smoke test. It is being built out against a public 31-command, 3-tier plan (reads, low-risk writes, human-airlocked high-risk writes) — see the README for the full roadmap and what's live in each version.

## Governance posture

`slack.get_team_info` executes immediately (it has no side effect). Every write command, as it ships, will be `write_requires_approval` at minimum, with Tier 3 (channel deletion/archival, membership changes, public file sharing, break-glass token revocation) additionally requiring RailCall's human approval airlock. The signed manifest declares `allowed_destinations: [{"provider":"slack","hosts":["slack.com"]}]` — exactly the Slack API host, zero LLM/model-provider egress. Credentials are vault-only; there is no environment-variable or credential-file fallback.

## Setup

Create a custom Slack app at `api.slack.com/apps` in the workspace you want it to govern, add the Bot Token Scopes each command needs, install it to your workspace, and save the Bot User OAuth Token as `SLACK_BOT_TOKEN` in RailCall Studio → Integrations. Search "slack" — use the `muhammad-akif-janjua-slack-guard::slack` card, not the plain `slack` one (Station auto-namespaces this module's slot because its declared provider collides with Station's built-in Slack integration).

## Pricing

Free (`license_required: false`) at launch — full command-list visibility is core to a governance module's pitch, and a paid listing on this platform currently hides the structured command breakdown. Revisit after the contest judging window.

`contest:2026Q3`
