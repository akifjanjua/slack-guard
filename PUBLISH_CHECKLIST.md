# Publish Checklist

Current state: scaffolding + `slack.get_team_info` committed, not yet signed with the real publisher key, no credential configured, no end-to-end execution yet. This document is the exact procedure to follow, in order — same procedure Linear Guard and Notion Guard used, adapted to this repo's names.

**Publishing (`railcall market publish`) requires explicit go-ahead from the module owner before it runs, every time — this checklist gets you signed, verified, and CI-green, not published.**

## 1. Prepare and commit every signed source change

Work on `master` (or a release branch). Run the complete offline suite before touching signing:

```bash
python tools/validate_release.py
python tools/security_test.py
python tools/command_logic_test.py
python tools/egress_contract_test.py
```

Commit every change except `module.sig`:

```bash
git add -A
git restore --staged module.sig
git commit -m "Prepare Slack Guard vX.Y.Z signed-tree release"
```

The working tree must be clean before signing.

## 2. Sign with the hand-built signer, not `railcall market module sign`

**Do not run `railcall market module sign`.** It is known to rewrite `module.json` in Windows text mode (CRLF), which corrupts the intentional zero-newline-byte format the signed tree hash depends on. Sign with the same Ed25519 signer used throughout this project instead:

```bash
python tools/sign_module_tree.py .
```

Confirm `module.json`'s `publisher_pubkey` matches the registered key's `pubkey_hex` (`~/.railcall/marketplace_publisher.json`) before signing — the script checks this itself and fails otherwise.

Commit the signature:

```bash
git add module.sig
git commit -m "Sign Slack Guard vX.Y.Z module tree"
```

## 3. Verify the committed tree — both verifiers must pass

```bash
python tools/verify_module_tree.py .
python "$HOME/.railcall/railcall_cli.py" market module verify .
```

Both must report a valid v2 tree signature with the expected command count and `ownership: ✓ signed by your local key`. **Do not proceed if either fails.**

## 4. Build and accept the release

```bash
python tools/build_release.py
python tools/release_acceptance_test.py
```

Expected assets (version segment tracks `module.json`'s `version` field):

```text
dist/slack-guard-v0.4.2.zip
dist/slack-guard-v0.4.2.files.json
```

**Do not proceed if `release_acceptance_test.py` fails for any reason**, including the official-CLI check being unavailable — investigate and fix, don't skip.

## 5. Push, review, and merge

Push and wait for Python 3.10, 3.12, and 3.13 CI to go green on `.github/workflows/slack-guard-tests.yml`. Merge only after every check passes.

After merging, update local `master` and repeat steps 3–4 against the merged commit before moving on.

## 6. Remaining before an actual `railcall market publish`

- [ ] Flip the GitHub repo from private to public (or grant reviewer access) — `README.md`/CI links won't resolve to reviewers otherwise.
- [ ] Get a real Slack workspace bot token configured and confirm `slack.get_team_info` executes end-to-end with a signed receipt (module operator's own action — Claude cannot create the Slack app or enter the token).
- [ ] Build out the remaining Tier 1 reads, Tier 2 writes, the approval-airlock plumbing, and Tier 3 writes per the build order in `README.md`, updating README/CHANGELOG/module.json alongside each one.
- [ ] Record the demo video and fill in `VIDEO_SCRIPT.md`/`CONTEST_SUBMISSION.md`.
- [ ] Fill in the marketplace listing URL in `CONTEST_SUBMISSION.md` once published.

## 7. Publish once

Publish only from a verified, clean `master` where steps 1–5 above all passed on the latest commit, and only after explicit sign-off from the module owner:

```bash
railcall market publish . --type=module --price=0
```

Slack Guard launches free (`license_required: false`, `price_cents: 0`) — `--price=0` must still be passed explicitly on every publish, not omitted; `price_cents` has no "preserve current value" fallback.

**The marketplace requires a strictly-increasing `version` to accept a republish** — bump `module.json`'s `version` (and this checklist's dist filenames) before every republish, even a metadata-only change.

**Do not repeatedly publish while debugging.** Preserve the marketplace output and the signed receipt evidence. This step requires explicit sign-off — confirm before running it, every time.
