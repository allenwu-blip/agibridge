#!/usr/bin/env bash
#
# migrate_to_new_repo.sh — automate bootstrap migration into the new
# `agibridge-saas` GitHub repo per MIGRATION_TODO.md (Cycle W).
#
# Source-grounded references:
#   - MIGRATION_TODO.md "Migration sequence" section (lines 57-69)
#   - DR-012 (DECISIONS.md:94-98) — new repo, clean history
#   - gh CLI `gh auth status`:
#       https://cli.github.com/manual/gh_auth_status (accessed 2026-05-14)
#   - gh CLI `gh api`:
#       https://cli.github.com/manual/gh_api (accessed 2026-05-14)
#   - GitHub REST: GET /repos/{owner}/{repo}:
#       https://docs.github.com/en/rest/repos/repos#get-a-repository (accessed 2026-05-14)
#   - GitHub REST: GET /repos/{owner}/{repo}/commits:
#       https://docs.github.com/en/rest/commits/commits#list-commits (accessed 2026-05-14)
#   - GitHub REST: GET /repos/{owner}/{repo}/contents/{path}:
#       https://docs.github.com/en/rest/repos/contents#get-repository-content (accessed 2026-05-14)
#
# Usage:
#   ./scripts/migrate_to_new_repo.sh <TARGET_REPO_URL>            # dry-run (default)
#   ./scripts/migrate_to_new_repo.sh <TARGET_REPO_URL> --apply    # execute
#
# Bash strict mode per project standard.
set -euo pipefail

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
BOOTSTRAP_DIR="/Users/allenwu/embodied-data-hosted/agibridge-saas-bootstrap"
EXPECTED_TOPLEVEL=(
  "app"
  "frontend"
  ".github"
  "alembic"
  "tests"
  "pyproject.toml"
  "uv.lock"
  "Dockerfile"
  ".pre-commit-config.yaml"
)
EXPECTED_WORKFLOWS=("ci.yml" "pre-merge-check.yml" "hf-sync.yml")
COMMIT_MSG_TITLE="Initial commit: agibridge-saas bootstrap"
COMMIT_COAUTHOR="Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
ERR_COUNT=0
ERR_THRESHOLD=2

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
log()  { printf '[migrate] %s\n' "$*" >&2; }
ok()   { printf '[migrate]   OK: %s\n' "$*" >&2; }
warn() { printf '[migrate] WARN: %s\n' "$*" >&2; }
err()  {
  printf '[migrate]  ERR: %s\n' "$*" >&2
  ERR_COUNT=$((ERR_COUNT + 1))
  if [ "$ERR_COUNT" -gt "$ERR_THRESHOLD" ]; then
    printf '[migrate] FATAL: error threshold (%d) exceeded — aborting partial run.\n' "$ERR_THRESHOLD" >&2
    exit 2
  fi
}
fatal() {
  printf '[migrate] FATAL: %s\n' "$*" >&2
  exit 2
}

usage() {
  cat >&2 <<'EOF'
Usage:
  migrate_to_new_repo.sh <TARGET_REPO_URL> [--apply]

  <TARGET_REPO_URL>   HTTPS clone URL of the freshly-created empty repo,
                      e.g. https://github.com/allenwu-blip/agibridge-saas.git
  --apply             Actually execute mutations. Without this flag, runs in
                      dry-run mode (prints all actions, performs no writes
                      to disk outside of TMPDIR introspection).

Examples:
  ./scripts/migrate_to_new_repo.sh https://github.com/allenwu-blip/agibridge-saas.git
  ./scripts/migrate_to_new_repo.sh https://github.com/allenwu-blip/agibridge-saas.git --apply
EOF
}

# Parse <owner>/<repo> from an https GitHub clone URL.
# Accepts both https://github.com/OWNER/REPO and https://github.com/OWNER/REPO.git
parse_owner_repo() {
  local url="$1"
  local trimmed="${url#https://github.com/}"
  trimmed="${trimmed%.git}"
  printf '%s' "$trimmed"
}

# -----------------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------------
if [ "$#" -lt 1 ]; then
  usage
  fatal "missing TARGET_REPO_URL"
fi

TARGET_REPO_URL="$1"

# Reject the dangerous `--force` flag explicitly BEFORE general flag parsing
# so the rejection message is clear ("not implemented" rather than "unknown flag").
for arg in "$@"; do
  if [ "$arg" = "--force" ]; then
    fatal "--force is not implemented in MVP (would risk overwriting non-empty repo). Refuse."
  fi
done

MODE="dry-run"
if [ "$#" -ge 2 ]; then
  case "$2" in
    --apply)   MODE="apply" ;;
    --dry-run) MODE="dry-run" ;;
    *)         usage; fatal "unknown flag: $2" ;;
  esac
fi

if [[ "$TARGET_REPO_URL" != https://github.com/*/*.git ]] \
   && [[ "$TARGET_REPO_URL" != https://github.com/*/* ]]; then
  fatal "TARGET_REPO_URL must be an https GitHub URL (got: $TARGET_REPO_URL)"
fi

OWNER_REPO="$(parse_owner_repo "$TARGET_REPO_URL")"
OWNER="${OWNER_REPO%%/*}"
REPO="${OWNER_REPO##*/}"

log "Mode:              $MODE"
log "Target URL:        $TARGET_REPO_URL"
log "Owner/Repo:        $OWNER/$REPO"
log "Bootstrap source:  $BOOTSTRAP_DIR"

# -----------------------------------------------------------------------------
# Pre-flight checks
# -----------------------------------------------------------------------------
log "=== Pre-flight ==="

# 1. gh auth status
# https://cli.github.com/manual/gh_auth_status — `gh auth status` exits non-zero
# when the user is not authenticated.
if ! gh auth status >/dev/null 2>&1; then
  fatal "gh auth status failed — run \`gh auth login\` first."
fi
ok "gh CLI authenticated"

# 2. Bootstrap source dir exists with expected layout
if [ ! -d "$BOOTSTRAP_DIR" ]; then
  fatal "bootstrap source dir missing: $BOOTSTRAP_DIR"
fi
for entry in "${EXPECTED_TOPLEVEL[@]}"; do
  if [ ! -e "$BOOTSTRAP_DIR/$entry" ]; then
    err "expected bootstrap entry missing: $entry"
  fi
done
for wf in "${EXPECTED_WORKFLOWS[@]}"; do
  if [ ! -f "$BOOTSTRAP_DIR/.github/workflows/$wf" ]; then
    err "expected workflow missing: .github/workflows/$wf"
  fi
done
if [ "$ERR_COUNT" -gt 0 ]; then
  fatal "bootstrap layout checks failed ($ERR_COUNT error(s)) — see above."
fi
ok "bootstrap layout matches expected"

# 3. Target repo exists
# https://docs.github.com/en/rest/repos/repos#get-a-repository
if ! gh api "repos/$OWNER/$REPO" >/dev/null 2>&1; then
  fatal "target repo $OWNER/$REPO not reachable via gh api — does it exist? are you authed for it?"
fi
ok "target repo $OWNER/$REPO exists and is reachable"

# 4. Target repo must be empty (0 commits) OR have only an initial commit (1 commit).
# https://docs.github.com/en/rest/commits/commits#list-commits
# A truly empty repo returns 409 Conflict ("Git Repository is empty.") on list-commits.
# We treat both "empty" and "exactly 1 commit" as acceptable; anything more aborts.
COMMITS_JSON="$(gh api "repos/$OWNER/$REPO/commits?per_page=2" 2>/dev/null || true)"
if [ -z "$COMMITS_JSON" ] || printf '%s' "$COMMITS_JSON" | grep -q '"Git Repository is empty"'; then
  ok "target repo is empty (0 commits)"
  TARGET_HAS_INITIAL_COMMIT=0
else
  # Count commits returned (max 2 because per_page=2).
  COMMIT_COUNT=$(printf '%s' "$COMMITS_JSON" | grep -c '"sha":' || true)
  if [ "$COMMIT_COUNT" -le 1 ]; then
    ok "target repo has $COMMIT_COUNT initial commit(s) only — acceptable"
    TARGET_HAS_INITIAL_COMMIT=1
  else
    fatal "target repo has >1 commit — refusing to operate on non-empty repo. Use a fresh repo."
  fi
fi

# -----------------------------------------------------------------------------
# TMPDIR + cleanup trap
# -----------------------------------------------------------------------------
WORK_TMPDIR="$(mktemp -d -t agibridge-saas-migrate.XXXXXX)"
FRESH_DIR="$WORK_TMPDIR/agibridge-saas-fresh"
RUN_SUCCEEDED=0
cleanup() {
  if [ "$RUN_SUCCEEDED" -eq 1 ]; then
    log "Cleanup: removing $WORK_TMPDIR (success)"
    rm -rf "$WORK_TMPDIR"
  else
    warn "Preserving $WORK_TMPDIR for debug (run failed or aborted)"
  fi
}
trap cleanup EXIT
log "TMPDIR: $WORK_TMPDIR"

# -----------------------------------------------------------------------------
# Dry-run summary path
# -----------------------------------------------------------------------------
if [ "$MODE" = "dry-run" ]; then
  log "=== DRY-RUN — actions that would be performed ==="
  cat >&2 <<EOF
  1. git clone $TARGET_REPO_URL $FRESH_DIR
  2. cp -r $BOOTSTRAP_DIR/. $FRESH_DIR/   (note '.' to include dotfiles)
  3. rm $FRESH_DIR/MIGRATION_TODO.md      (strip from initial commit)
  4. cd $FRESH_DIR && git add -A
  5. git commit -m "$COMMIT_MSG_TITLE" --trailer "$COMMIT_COAUTHOR"
       (or amend if target already has empty-initial commit only)
  6. git push origin <default-branch>
  7. Post-flight: gh api repos/$OWNER/$REPO  (verify commit landed)
  8. Post-flight: gh api repos/$OWNER/$REPO/contents/.github/workflows
       (verify 3 YAMLs landed: ${EXPECTED_WORKFLOWS[*]})

Safety guards in effect:
  - bash strict mode (set -euo pipefail)
  - --force rejected
  - target-repo emptiness check (commit count <= 1)
  - rate-limit: aborts after $ERR_THRESHOLD errors
  - never --no-verify on commit; never --force on push
  - dry-run is the default; --apply required to mutate

Re-run with --apply to execute.
EOF
  RUN_SUCCEEDED=1
  exit 0
fi

# -----------------------------------------------------------------------------
# Apply mode — execute mutations
# -----------------------------------------------------------------------------
log "=== APPLY — executing mutations (set -x) ==="
set -x

# Step 1: clone target
git clone "$TARGET_REPO_URL" "$FRESH_DIR"
cd "$FRESH_DIR"

# Determine default branch (may be 'main' or 'master' depending on org defaults).
DEFAULT_BRANCH="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
if [ -z "$DEFAULT_BRANCH" ]; then
  # Empty repo with no commits: HEAD is unborn. Default to 'main'.
  DEFAULT_BRANCH="main"
  git checkout -b "$DEFAULT_BRANCH"
fi

# Step 2: copy bootstrap contents (including dotfiles, per the '.' trailing form).
cp -R "$BOOTSTRAP_DIR/." "$FRESH_DIR/"

# Step 3: filter — strip MIGRATION_TODO.md from initial commit (default behavior).
if [ -f "$FRESH_DIR/MIGRATION_TODO.md" ]; then
  rm "$FRESH_DIR/MIGRATION_TODO.md"
fi

# Also defensively strip any caches that may have been copied through (idempotency).
rm -rf "$FRESH_DIR/__pycache__" "$FRESH_DIR/.pytest_cache" "$FRESH_DIR/.ruff_cache" \
       "$FRESH_DIR/.venv" "$FRESH_DIR/.coverage" 2>/dev/null || true

# Step 4-5: stage + commit. Idempotency: if `git diff --cached` is empty after add,
# don't create a duplicate commit.
git add -A
if git diff --cached --quiet; then
  set +x
  warn "Nothing to commit — staging area is empty. Already migrated?"
  set -x
else
  git commit -m "$COMMIT_MSG_TITLE" --trailer "$COMMIT_COAUTHOR"
fi

# Step 6: push. Explicitly NOT --force; refuse if remote is ahead.
# `git push origin <branch>` fails if remote has non-fast-forwardable commits —
# that's the safety we want.
git push origin "$DEFAULT_BRANCH"

set +x

# Step 7: post-flight — verify commit landed.
LATEST_SHA="$(gh api "repos/$OWNER/$REPO/commits?per_page=1" --jq '.[0].sha' 2>/dev/null || true)"
if [ -z "$LATEST_SHA" ]; then
  fatal "post-flight failed: could not read latest commit SHA from gh api"
fi
ok "post-flight: latest commit SHA = $LATEST_SHA"

# Step 8: post-flight — confirm 3 YAMLs landed.
# https://docs.github.com/en/rest/repos/contents#get-repository-content
WORKFLOWS_JSON="$(gh api "repos/$OWNER/$REPO/contents/.github/workflows" 2>/dev/null || true)"
if [ -z "$WORKFLOWS_JSON" ]; then
  err "post-flight: .github/workflows not readable on remote — check push"
else
  for wf in "${EXPECTED_WORKFLOWS[@]}"; do
    if printf '%s' "$WORKFLOWS_JSON" | grep -q "\"name\": *\"$wf\""; then
      ok "post-flight: .github/workflows/$wf present on remote"
    else
      err "post-flight: .github/workflows/$wf MISSING on remote"
    fi
  done
fi

RUN_SUCCEEDED=1
log "=== DONE ==="
log "Repo URL:    https://github.com/$OWNER/$REPO"
log "Initial SHA: $LATEST_SHA"
log "Branch:      $DEFAULT_BRANCH"
