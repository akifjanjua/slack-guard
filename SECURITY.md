# Security

## Vault-only credentials

Slack Guard resolves `SLACK_BOT_TOKEN` exclusively through RailCall's injected `vault_get("slack")` helper. It does not inspect `credentials.local.json`, other RailCall files, process environment variables, command-line arguments, or command inputs for secrets.

Because this module's declared provider (`slack`) collides with Station's own built-in Slack integration, RailCall auto-namespaces the actual vault slot to `muhammad-akif-janjua-slack-guard::slack` (a distinct card from the plain `slack` one in Studio's Integrations page). The handler's `vault_get("slack")` call is unaffected — RailCall's per-module vault shim transparently routes it to the namespaced slot — but an operator saving the credential in Studio must use the namespaced card, or the module will report the credential as not configured. See `README.md` → Configure credentials.

Never publish API keys, approval codes, local receipt archives, `.env` files, or RailCall credential files.

## HTTPS transport

All Slack requests use Python `urllib.request` with certificate and hostname verification enabled through a certifi-backed `SSLContext`. The module does not invoke curl, a shell, or another subprocess and never disables TLS verification.

## Model-provider egress contract

`module.json` declares `"allowed_destinations": [{"provider":"slack","hosts":["slack.com"]}]` — exactly the Slack API host and nothing else. The signed declaration means the module permits no LLM/model-provider calls; the Slack Web API is the module's only business integration endpoint and is not routed through any model-completion primitive. A dedicated CI test rejects imports, hostnames, or source references associated with supported model providers and rejects any use of `station_llm`.

## Sandbox posture

`module.json` also declares a `requires` block that RailCall Station enforces at handler-load time: `network: ["slack.com"]` (a contextvar-scoped gate — any request to a host outside this allowlist raises `SandboxViolation`), `subprocess: false` (subprocess/`os.system`/`os.exec*` are blocked in the handler's namespace), and `filesystem_writes: []` (no filesystem writes are permitted at all). This matches Slack Guard's actual behavior — one HTTPS host, no subprocess use, no local file writes — as an enforced guarantee rather than only a documented one.

## Governance

Every Tier 1 read is a `read` with no side effect (subject to Station's own network-capable-module read-upgrade policy — see `docs/TROUBLESHOOTING.md`). Every Tier 2/3 write is `write_requires_approval`: RailCall binds approval to the exact previewed payload before any external write is attempted. Tier 3 is additionally risk `high` and covers irreversible or high-blast-radius actions (channel deletion/archival, membership changes, public file sharing, break-glass revocation); `slack.uninstall_app`/`slack.revoke_token` also require an exact confirmation phrase (`UNINSTALL`/`REVOKE`) in a `confirm` input, checked before any network call, on top of the normal approval ceremony.

## Governance presets and real command-level enforcement

Slack Guard ships three named presets (Observer, Team Copilot, Open Community Hardened — see README), selected via an optional `SLACK_GUARD_PRESET` field on the same vault entry as `SLACK_BOT_TOKEN` (defaults to `team_copilot`, the least restrictive, when unset or unrecognized, so an operator who never touches this setting sees no behavior change).

Station's own approval-policy engine (`approval_policy.py`, `WS/approval_policy.json`) governs its own built-in providers and MCP/workflow execution paths, but does not gate third-party module commands dispatched through Studio's Sends tab — those already always require approval via the module's declared `write_requires_approval` mode, with no additional module-extensible hook into Station's policy engine. So the Open Community Hardened preset's restriction is enforced **inside the handler itself**: `_enforce_preset_block()` runs as the first statement of `slack.kick_user_from_channel`, `slack.invite_to_channel`, `slack.update_usergroup_members`, and `slack.share_file_publicly`, and raises before `_load_api_key()` or any network call whenever the active preset is `open_community_hardened` — regardless of whether the action was already approved. This is real, testable, functional enforcement, not a documented label: `tools/command_logic_test.py`'s `test_hardened_preset_blocks_the_four_commands` asserts the network transport is never reached under that preset, and that all four execute normally under the other two. `tools/validate_release.py` additionally fails the build if any of the four command functions doesn't call `_enforce_preset_block`.

Observer and Team Copilot do not differ in what this module enforces — both rely entirely on RailCall's own universal per-write approval requirement, with no additional hard blocks. The distinction between them is a posture recommendation for the operator, not a difference in code.

## Slack's error model

Unlike Notion Guard's/Linear Guard's REST APIs, the Slack Web API always returns HTTP 200 for a well-formed request and signals failure through a JSON body — `{"ok": false, "error": "<code>"}` — rather than an HTTP status code. The handler treats `{"ok": false}` as a definitive, confirmed rejection (not a transport-level ambiguity) for both reads and writes; only a true transport-level failure (HTTP 429 rate limit, 5xx server error, connection failure) triggers the "outcome is unknown" write-safety path described below.

## Retry and unknown-outcome policy

Every write performs exactly one HTTP attempt and is never automatically retried, since a retry could duplicate an unknown-outcome mutation. If a timeout, connection failure, unreadable response, or HTTP 5xx response prevents confirmation of a write, the handler reports that the write outcome is unknown and instructs the user to check Slack before retrying. Reads have no side effect, so a read may automatically retry up to twice on a transient HTTP 429/502/503/504 (honoring Slack's `Retry-After` header when supplied) before raising an error.

## Error handling and redaction

HTTP status and the response's `ok` field are checked on every response; Slack's own error codes are surfaced with a clear message. The active bot token is redacted from any error text before it is raised — by literal substring match and by pattern (Slack's `xoxb-`/`xoxp-`/`xoxa-`/`xoxe-`/`xoxs-`/`xapp-` token shapes, `Authorization` header values, `SLACK_BOT_TOKEN` field assignments) — so a network error or Slack error response cannot leak the credential. `slack.uninstall_app`'s `SLACK_CLIENT_SECRET` is redacted the same way from any error its own call raises.

**`slack.share_file_publicly`'s public URL is a deliberate exception, by design, not an oversight.** `files.sharedPublicURL` mints a bearer-style secret embedded in the file's `permalink_public` URL as a `pub_secret` query value — anyone holding it can read the file with no login, which is exactly what the brief's own research flagged as the module's single highest data-leak-risk surface. Two decisions, tested explicitly (`test_share_file_publicly_output_and_redaction`):

1. **The success `output` deliberately still returns the full URL.** It is the human-approved deliverable of this exact command — redacting it there would make the command useless, since the whole point of approving it is to get a link back. The `output` also carries a `warning` field stating plainly that the URL must be treated as a credential.
2. **The `pub_secret` value is redacted from any error/note text the same way a bot token would be.** There is no legitimate reason for a failure path to echo it, and the structural design of `_request`'s write-failure handling (a clean "outcome is unknown" message, no raw exception chained) already prevents it from leaking there in practice — the redaction pattern is defense in depth on top of that.

## Responsible disclosure

Report security issues with a redacted reproduction. Do not include credentials, private channel/message contents, personal email addresses, approval codes, signatures, or full sensitive identifiers.
