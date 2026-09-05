# RailCall Contest Entry — Slack Guard

## Title

Slack Guard — Governed Slack Integration

## Repository

https://github.com/akifjanjua/slack-guard (branch `master`) — repository visibility and public link to be confirmed once the remote is created.

## Description

Slack Guard is a governance-first RailCall module for Slack workspaces, built against a public 31-command, 3-tier plan (reads, low-risk writes, human-airlocked high-risk writes). v0.1.0 ships the scaffolding and one command against the real Slack Web API: `slack.get_team_info`, which confirms the connected workspace's identity and doubles as the connectivity smoke test.

Slack Guard resolves credentials only through RailCall's vault helper, uses certifi-backed verified HTTPS, never invokes curl or another subprocess, and actively redacts credentials from errors. `module.json` declares a `requires` sandbox block (`network: ["slack.com"]`, `subprocess: false`, `filesystem_writes: []`) that RailCall Station enforces at handler-load time. Unlike Notion Guard's/Linear Guard's REST APIs, Slack's Web API always returns HTTP 200 and signals failure through `{"ok": false, "error": "<code>"}`; the handler treats that as a definitive rejection distinct from a true transport-level failure.

This entry is being filed while the module is still in active build-out — see `CHANGELOG.md` for exactly what is live. Full end-to-end verification evidence (signature, real Slack call, signed receipt) will be added here as each build-order step completes.

`contest:2026Q3`

## Verification evidence

- **Signature**: signed with the real registered publisher key (fingerprint `e469d55383447fc6b95cbffb786fee7c…`, the same identity used for Linear Guard and Notion Guard) via `tools/sign_module_tree.py`. Independently verified both by this repo's own `tools/verify_module_tree.py` (`PASS: RailCall v2 module tree signature is valid`, 23 signed tree files) and by RailCall's own CLI (`railcall market module verify .`): `✓ signature valid`, `ownership: ✓ signed by your local key`.
- **Module loaded in a real Station**: boot log confirms `muhammad-akif-janjua/slack-guard v0.1.0 · slack.get_team_info` loaded clean (`loaded=3 rejected=4`, this module among the 3 loaded).
- **Sandbox enforcement, not just declaration**: Station's own boot log shows the gate actually installed for this module: `network gate armed — allow: ['slack.com']`, `subprocess gate CLOSED`, `filesystem-write gate active — allow: (none)`.
- **Real end-to-end read with a signed receipt**: executed `slack.get_team_info` through Studio's Sends tab against a real Slack workspace (team `Slack Guard Dev`, `T0BVA62BUCU`), walking the full preview → approve → execute ceremony (receipt `cmd_20260905T165726Z_slack_get_team_info_87d4489c_executed_0007.json`, `result_status: "executed"`, `http_status: 200`, `approval.method: "ui_click"`, `external_api_touched: true`). Independently re-verified offline with `railcall verify <receipt>`: `✓ SIGNATURE VALID`, signer `ed25519 key_id ecac7bc46608cb35`, checked against this install's `signing_pubkey.json` with no network call.

## Trust declaration

Slack Guard's signed manifest declares `allowed_destinations` as exactly the Slack API host (`slack.com`) and zero LLM/model-provider entries. The module talks only to the Slack Web API and does not send workspace data to a model-provider SDK or RailCall model-completion primitive.
