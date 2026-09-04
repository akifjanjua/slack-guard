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

`slack.get_team_info` is a read with no side effect and executes immediately (subject to Station's own network-capable-module read-upgrade policy — see `docs/TROUBLESHOOTING.md`). Every Tier 2/3 write command, as it ships, will be `write_requires_approval`: RailCall binds approval to the exact previewed payload, and Tier 3 commands additionally require the human approval airlock before any external write is attempted.

## Slack's error model

Unlike Notion Guard's/Linear Guard's REST APIs, the Slack Web API always returns HTTP 200 for a well-formed request and signals failure through a JSON body — `{"ok": false, "error": "<code>"}` — rather than an HTTP status code. The handler treats `{"ok": false}` as a definitive, confirmed rejection (not a transport-level ambiguity) for both reads and writes; only a true transport-level failure (HTTP 429 rate limit, 5xx server error, connection failure) triggers the "outcome is unknown" write-safety path described below.

## Retry and unknown-outcome policy

Every write performs exactly one HTTP attempt and is never automatically retried, since a retry could duplicate an unknown-outcome mutation. If a timeout, connection failure, unreadable response, or HTTP 5xx response prevents confirmation of a write, the handler reports that the write outcome is unknown and instructs the user to check Slack before retrying. Reads have no side effect, so a read may automatically retry up to twice on a transient HTTP 429/502/503/504 (honoring Slack's `Retry-After` header when supplied) before raising an error.

## Error handling and redaction

HTTP status and the response's `ok` field are checked on every response; Slack's own error codes are surfaced with a clear message. The active bot token is redacted from any error text before it is raised — by literal substring match and by pattern (Slack's `xoxb-`/`xoxp-`/`xoxa-`/`xoxe-`/`xoxs-`/`xapp-` token shapes, `Authorization` header values, `SLACK_BOT_TOKEN` field assignments) — so a network error or Slack error response cannot leak the credential. Once Tier 3's `share_file_publicly` ships, the same redaction path is extended to cover the shared-file URL it returns, since that URL is itself bearer-style secret material.

## Responsible disclosure

Report security issues with a redacted reproduction. Do not include credentials, private channel/message contents, personal email addresses, approval codes, signatures, or full sensitive identifiers.
