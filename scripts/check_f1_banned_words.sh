#!/usr/bin/env bash
# CI grep for F-1 hobby-framing banned words.
# Sources the canonical regex from scripts/f1-banned-words.regex (C4 — single
# source of truth shared with backend pre-commit hook + DevOps CI workflow).
#
# Exit 0 = clean. Exit 1 = at least one banned word found (build fails).
#
# Usage from repo root:
#   bash scripts/check_f1_banned_words.sh
#
# Or in CI: set REGEX_PATH to point at the bundled copy.

set -uo pipefail

# Default to the canonical workspace path; override with REGEX_PATH for CI.
REGEX_PATH="${REGEX_PATH:-../scripts/f1-banned-words.regex}"

if [ ! -f "$REGEX_PATH" ]; then
  echo "ERROR: canonical F-1 regex not found at $REGEX_PATH" >&2
  echo "Set REGEX_PATH to the location of f1-banned-words.regex." >&2
  exit 2
fi

# Targets: README + landing + HF Space README + QUICKSTART.
# Backend route descriptions and openapi.json are checked separately by the
# DevOps CI workflow per spec §10.
#
# EXEMPT from this grep:
#   - landing/voice-guide.md — contributor-facing style guide that lists banned
#     phrases as examples of what NOT to write; matches spec §10 "legitimate
#     technical references, NOT user-facing" exception.
TARGETS=(
  "README.md"
  "QUICKSTART.md"
  "landing/index.mdx"
  "hf-space/README.md"
)

# Build a single OR-joined regex by stripping comments and blank lines.
PATTERNS=$(grep -E "^[^#]" "$REGEX_PATH" | grep -v '^$')

if [ -z "$PATTERNS" ]; then
  echo "ERROR: regex file $REGEX_PATH contained no patterns" >&2
  exit 2
fi

EXIT=0
for f in "${TARGETS[@]}"; do
  if [ ! -f "$f" ]; then
    echo "WARN: target $f does not exist (skipped)" >&2
    continue
  fi
  HITS=$(grep -nEi -f <(echo "$PATTERNS") "$f" || true)
  if [ -n "$HITS" ]; then
    echo "FAIL: F-1 banned word(s) in $f:" >&2
    echo "$HITS" >&2
    EXIT=1
  else
    echo "OK:   $f"
  fi
done

if [ $EXIT -eq 0 ]; then
  echo ""
  echo "All ${#TARGETS[@]} surfaces clean against $(echo "$PATTERNS" | wc -l | tr -d ' ') canonical patterns."
fi

exit $EXIT
