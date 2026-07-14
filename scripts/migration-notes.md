# `migrate_to_new_repo.sh` — Operator Notes

**Created**: 2026-05-14 (Cycle W, agibridge Wave 2)
**Script**: `/Users/allenwu/embodied-data-hosted/agibridge-saas-bootstrap/scripts/migrate_to_new_repo.sh`
**Manual fallback**: `MIGRATION_TODO.md:57-69`
**Source decision**: DR-012 (`/Users/allenwu/.plans/agibridge-2026/DECISIONS.md:94-98`)

## What it does

Automates the "Migration sequence" in `MIGRATION_TODO.md`: clones the empty `agibridge-saas` GitHub repo into a tempdir, copies the bootstrap directory in (dotfiles included via the trailing `.`), strips `MIGRATION_TODO.md` from the initial commit, commits with a `Co-Authored-By` trailer, pushes to `main`, then verifies via `gh api` that the commit landed and all three CI YAMLs (`ci.yml`, `pre-merge-check.yml`, `hf-sync.yml`) are visible on the remote. Mode is **dry-run by default** — `--apply` is required to mutate.

## Safety design (8 guards)

| # | Guard | Mechanism |
|---|---|---|
| 1 | Strict mode | `set -euo pipefail` |
| 2 | Default preview | `dry-run` default; `--apply` opt-in |
| 3 | `--force` rejected | Explicit pre-parse check with "not in MVP" error |
| 4 | No `--no-verify` | Plain `git commit -m`; pre-commit hooks run normally |
| 5 | No `git push --force` | Plain `git push`; git refuses non-fast-forward |
| 6 | Empty-repo check | `gh api .../commits` enforces `<=1` existing commit |
| 7 | Auth check | `gh auth status` must succeed first |
| 8 | Layout check | Pre-flight verifies `app/`, `frontend/`, `.github/`, etc. + 3 workflow YAMLs |

Defense-in-depth (not counted): idempotent re-run (`git diff --cached --quiet` skip), cache strip (`__pycache__`/`.venv`/etc.), self rate-limit (abort after >2 non-fatal errors per `feedback_no_padding_lists`), tempdir preservation on failure, `set -x` during mutations.

## Pre-conditions Allen must verify before `--apply`

1. **Repo created and empty.** `gh repo create allenwu-blip/agibridge-saas --private` (no auto-init `README`/`LICENSE` or push will be non-fast-forward — see Failure Modes).
2. **Bootstrap content reviewed.** Skim `PROJECT_STATUS.md` for last-minute drift.
3. **No uncommitted secrets.** `grep -RIn -E '(sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})' /Users/allenwu/embodied-data-hosted/agibridge-saas-bootstrap/ || echo CLEAN` — script does no secret-scanning today.
4. **Default-branch is `main`.** Script falls back to `main` on unborn HEAD; edit `DEFAULT_BRANCH` if you used `master`.
5. **Dry-run passed.** Run once without `--apply` and confirm the 8-step plan + safety summary.

## Failure modes + recovery

| Failure | Recovery |
|---|---|
| `gh auth status` fails | `gh auth login`, retry. |
| Target repo unreachable | `gh repo view OWNER/REPO` with the same identity to confirm. |
| Target repo has >1 commit | Recreate empty, or extend with `--allow-existing-history` (deferred). |
| Non-fast-forward push | Repo was auto-init'd. In preserved tempdir: `git pull --rebase origin main && git push origin main`, or recreate without auto-init. |
| Pre-commit hook fails | Fix lint inside preserved tempdir, re-stage, create a NEW commit (not `--amend`, not `--no-verify`), push. |
| Push OK but post-flight errors | Push likely landed; verify with `gh api repos/$OWNER/$REPO/commits?per_page=1`. Post-flight errors are cosmetic but ERR_COUNT may exit non-zero. |
| Network drop mid-push | Re-run with `--apply`. The empty-repo check tolerates the 1-commit state and `git add -A` will be empty → "Nothing to commit" warn + clean exit (idempotent). |
| Want `MIGRATION_TODO.md` shipped | Comment out the `rm "$FRESH_DIR/MIGRATION_TODO.md"` line before `--apply`. Default-strip per `MIGRATION_TODO.md:65` "Allen's call". |

## Manual fallback

If the script fails in a way not worth debugging, the 4-line manual sequence from `MIGRATION_TODO.md:57-69` still works:

```bash
git clone https://github.com/allenwu-blip/agibridge-saas.git
cd agibridge-saas
cp -r /Users/allenwu/embodied-data-hosted/agibridge-saas-bootstrap/. .
rm MIGRATION_TODO.md   # optional
git add -A
git commit -m "Initial commit: agibridge-saas bootstrap"
git push origin main
```

## Future enhancements (deferred)

- **`--force` / `--allow-existing-history` mode.** Out of MVP: legitimate re-run case is covered by the idempotent "Nothing to commit" path, and `--force` near `main` deserves a separately-designed workflow.
- **Partial re-migration / diff mode.** Day-2 changes will flow through normal PRs, not this script.
- **Secret-scanning pre-flight.** Add `trufflehog`/`gitleaks` when a real leak is observed; bootstrap is hand-audited today.
- **Default-branch auto-detection** from `gh api repos/.../` `default_branch` field instead of falling back to `main`.
- **Dockerfile recovery.** `MIGRATION_TODO.md:25-29` notes Dockerfile must be recovered from prior F-1 history; handled by D4 outside this script.

## Test status

- Dry-run pre-flight bails correctly at "target repo not reachable" when the GitHub repo doesn't exist yet — confirms the existence check fires before any clone. Argument validation (no-args, bad URL, `--force` rejection) all return the expected `FATAL:` messages.
- **Apply path is untested end-to-end** because `allenwu-blip/agibridge-saas` doesn't exist yet. First real `--apply` is the integration test; the tempdir-preserved-on-failure invariant exists for this reason.

[DECISION NEEDED]: Ship `MIGRATION_TODO.md` in initial commit (history snapshot) or strip (current default)? Default-strip chosen because the doc references the prior F-1 repo path which is internal context not useful to future contributors. Easy to flip — comment out one `rm`.
