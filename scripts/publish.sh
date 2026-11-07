#!/usr/bin/env bash
# Push this checkout to the public GitHub repo DebuG6290/review-desk
# (the empty repo already exists; creates it with the GitHub CLI if it does not).
set -euo pipefail
cd "$(dirname "$0")/.."
if command -v gh >/dev/null && ! gh repo view DebuG6290/review-desk >/dev/null 2>&1; then
  gh repo create DebuG6290/review-desk --public --source . --push \
    --description "Turn a maintainer's PR review into a tested, human-approved follow-up commit, in one Switch room"
  exit 0
fi
git remote get-url origin >/dev/null 2>&1 || git remote add origin https://github.com/DebuG6290/review-desk.git
git push -u origin main
