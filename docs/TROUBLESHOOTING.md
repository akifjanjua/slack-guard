# Troubleshooting

**Saved a token in Integrations, but Slack Guard still says "not configured"** — Search Integrations for "slack"; you'll see two cards. The plain `slack` card is Station's own built-in integration, not this module's. Because this module's provider collides with it, RailCall auto-namespaces the real credential slot to `muhammad-akif-janjua-slack-guard::slack` — save `SLACK_BOT_TOKEN` on that card instead.

**"Slack credential is not configured"** — Save your Bot User OAuth Token as `SLACK_BOT_TOKEN` on the `muhammad-akif-janjua-slack-guard::slack` card in RailCall Studio → Integrations (see above — not the plain `slack` card), then reload Modules.

**`Slack rejected the request: invalid_auth` or `not_authed`** — The bot token is missing, malformed, or was revoked/regenerated in your Slack app's OAuth settings since it was saved. Copy a fresh Bot User OAuth Token from `api.slack.com/apps` → your app → OAuth & Permissions, and re-save it.

**`Slack rejected the request: missing_scope`** — The Slack app is missing a required Bot Token Scope for the command you ran (e.g. `team:read` for `slack.get_team_info`). Add the scope under OAuth & Permissions, then reinstall the app to your workspace — Slack requires a reinstall for new scopes to take effect, not just a save.

**`Slack rejected the request: account_inactive`** — The token's associated app was uninstalled from the workspace, or the installing user's account was deactivated. Reinstall the app and generate a fresh token.

**`blocked_by_policy`** — For a write command, this is expected once Tier 2/3 ship: open Studio → Sends, inspect the exact previewed payload, and approve it. For `slack.get_team_info` (a read with no side effect), this can *also* happen: Station's network-capable-module read-upgrade policy can upgrade every command on a network-capable module to `write_requires_approval` by default, because a manifest cannot prove a command is really read-only. Until an operator explicitly allowlists the command's exact id in Settings → Live Execution, it needs the same manual preview → approve → execute as a write.

**"the write outcome is unknown"** — The request either timed out, the connection failed, or Slack returned a server error (5xx) after the request was sent. Check Slack directly before retrying; the write may or may not have applied. This is distinct from `Slack rejected the request: <code>`, which means Slack definitively processed and rejected the request — no ambiguity, safe to fix and retry.

**"Slack API rate limit reached"** — For a read, Slack Guard already retried up to twice (honoring Slack's `Retry-After` value when supplied) before raising this; the message says "Slack Guard retried but the limit is still in effect" to confirm that happened. For a write, Slack Guard never automatically retries at all, since a retry could duplicate an unknown-outcome mutation; wait before retrying yourself.

**`No module named certifi`** — Install certifi with the same Python RailCall uses:

```bash
python -m pip install certifi
python -c "import certifi; print(certifi.where())"
```

**`CERTIFICATE_VERIFY_FAILED`** — Confirm certifi is installed and current, then check the computer's date and time. Slack Guard never disables certificate or hostname verification and never falls back to curl.

**Why does a governance-first Slack module have only one command so far?** — Slack Guard is being built command-by-command against a public 31-command, 3-tier build order (see `README.md`), with each new command's tests, docs, and governance wiring shipped together rather than bulk-added at the end. `CHANGELOG.md` tracks exactly what is live in each version.
