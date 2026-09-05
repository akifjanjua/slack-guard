# Evidence and Screenshot Checklist

## Strongest evidence set

1. **Real Slack result** — `slack.get_team_info` returning the real connected workspace's id/name/domain.
2. **Module loaded** — Studio's Modules tab or boot log showing Slack Guard's current version, signature verified, all commands registered, `loaded=1 rejected=0`.
3. **Approved execution** — a receipt showing `"result_status": "executed"`, `"http_status": 200`, and a non-null `signature.sig`.
4. **Independent receipt verification** — `tools/verify_module_tree.py` and/or `railcall market module verify` reporting `✓ signature valid`.
5. **Safe smoke test** — `tools/smoke_test.py` passes against a running Studio instance.
6. **Public marketplace listing** — creator, version, command count, governance posture (once published).

## Redact

Hide API keys, approval codes, personal email addresses, full team/user IDs beyond what's needed to show the result, raw signatures, unrelated browser notifications, and local credential paths. Keep command names, status labels, HTTP status, workspace name used only for the demo, governance result, and verification success visible.

## Video evidence

Not yet recorded — will show: module loaded → exact payload preview → real Slack result → signed receipt verification. All 3 tiers are live as of v0.4.1, so this is now unblocked; the video and `VIDEO_SCRIPT.md` are the remaining pre-publish item (see `PUBLISH_CHECKLIST.md`).
