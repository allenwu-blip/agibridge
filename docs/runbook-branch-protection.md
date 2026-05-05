# Runbook — main branch protection (DoD #6)

**When to run**: after Allen merges all 4 W1 branches (`backend/v0-skeleton`,
`docs/readme-landing-v0`, `frontend/v0-spa`, `devops/v0-pipeline`) into `main`.
Before that, `main` doesn't exist and `gh api .../branches/main/protection`
returns 404.

## Required state (Phase D A3.2 + DoD #6)

- Required status checks (all must pass before merge):
  - `lint` (the `lint` job in `.github/workflows/ci.yml`)
  - `test` (the `test` job)
  - `f1-banned-words` (the `f1-banned-words` job — both .py + .sh runs)
  - `uv-lock-check` (the `uv-lock-check` job)
  - `docker-build` (the `docker build + smoke test` job — DoD #5)
- `strict: true` (branches must be up to date before merging — solo guard
  against "merging stale main")
- `enforce_admins: true` (the brief's "solo-founder guard" intent)
- `allow_force_pushes: false`
- `allow_deletions: false`
- Required signed commits (separate endpoint per GitHub REST API)

## Source-grounded references

- Branch protection PUT body schema:
  <https://docs.github.com/en/rest/branches/branch-protection?apiVersion=2022-11-28#update-branch-protection>
- Required signatures (separate endpoint):
  <https://docs.github.com/en/rest/branches/branch-protection?apiVersion=2022-11-28#create-commit-signature-protection>

## Commands (run as repo admin / Allen)

```bash
# 1. Apply the protection rules (status checks + force-push lock).
#    The contexts list MUST match the `name:` field of each job in ci.yml.
#    GitHub identifies status checks by their job name, not the workflow file.
#
#    Note on `required_pull_request_reviews=null` and `restrictions=null`:
#    the API schema marks both as "required (object or null)" — passing them
#    as empty strings via `gh api -F` shorthand serializes to `""` and the
#    PUT request rejects with a 422 schema error. Literal `null` clears the
#    field correctly. See the PUT body schema linked above.
gh api -X PUT \
  /repos/allenwu-blip/agibridge/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  -F required_status_checks[strict]=true \
  -F 'required_status_checks[contexts][]=ruff check (lint)' \
  -F 'required_status_checks[contexts][]=pytest' \
  -F 'required_status_checks[contexts][]=F-1 banned-word grep (BOTH scripts — see W2 note)' \
  -F 'required_status_checks[contexts][]=uv lock --check (lockfile freshness)' \
  -F 'required_status_checks[contexts][]=docker build + smoke test' \
  -F enforce_admins=true \
  -F required_pull_request_reviews=null \
  -F restrictions=null \
  -F allow_force_pushes=false \
  -F allow_deletions=false \
  -F required_linear_history=false

# 2. Enable required signed commits (separate endpoint).
gh api -X POST \
  /repos/allenwu-blip/agibridge/branches/main/protection/required_signatures \
  -H "Accept: application/vnd.github+json"

# 3. Verify — should print the configured ruleset, not 404.
gh api /repos/allenwu-blip/agibridge/branches/main/protection \
  -H "Accept: application/vnd.github+json" | jq '{
    required_status_checks: .required_status_checks,
    enforce_admins: .enforce_admins.enabled,
    allow_force_pushes: .allow_force_pushes.enabled,
    allow_deletions: .allow_deletions.enabled,
    required_signatures: .required_signatures.enabled
  }'
```

## Expected verification output

```json
{
  "required_status_checks": {
    "url": "...",
    "strict": true,
    "contexts": [
      "ruff check (lint)",
      "pytest",
      "F-1 banned-word grep (BOTH scripts — see W2 note)",
      "uv lock --check (lockfile freshness)",
      "docker build + smoke test"
    ],
    "checks": [...]
  },
  "enforce_admins": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_signatures": true
}
```

## Caveats

- **Status check context names must match exactly.** If you rename a job in
  `ci.yml`, update step 1 above OR the gate silently passes (because GitHub
  can't find a check with that name to require it). Keep these in sync.
- **Force push to main is now disabled even for admins.** If you ever need to
  rewrite history on main (don't), `gh api -X DELETE .../protection` first.
- **Required signed commits**: Allen needs `git config commit.gpgsign true` +
  a registered GPG key on his GitHub profile. If signed commits are blocking
  CI dispatches because the workflow bot's commits aren't signed, narrow the
  rule via `restrictions` instead of disabling. (HF sync workflow doesn't
  push to main — it pushes to HF — so this isn't an issue for our pipeline.)
