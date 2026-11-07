#!/usr/bin/env bash
# Build the local SANDBOX remotes Review Desk pushes to. Nothing here touches GitHub.
#   scripts/make_sandbox.sh <sandbox-dir> [bambi-source-clone]
# bambi-source-clone: an existing local clone of bambinos/bambi that contains
# commit 137788f8 (the head of PR #1090). It is only read, never modified.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SB="${1:?sandbox dir}"; BAMBI_SRC="${2:-}"
mkdir -p "$SB/remotes"
export GIT_AUTHOR_NAME="new-contributor" GIT_AUTHOR_EMAIL="new-contributor@example.com"
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME" GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"

# Toy repo: main + the PR branch add-tip.
rm -rf "$SB/remotes/billsplit.git" "$SB/tmp-billsplit"
git init -q --bare -b main "$SB/remotes/billsplit.git"
cp -r "$ROOT/demo/toy-repo-src" "$SB/tmp-billsplit"
( cd "$SB/tmp-billsplit" && git init -q -b main && git add -A && git commit -qm "billsplit: even split" \
  && git checkout -qb add-tip && cp -r "$ROOT/demo/toy-pr-head/." . && git add -A \
  && git commit -qm "Add tip support to split_bill" \
  && git push -q "$SB/remotes/billsplit.git" main add-tip )
rm -rf "$SB/tmp-billsplit"
echo "billsplit sandbox remote ready"

# bambi: a bare copy holding the PR #1090 head (137788f8) as 1061-bayesian-r2.
if [ -n "$BAMBI_SRC" ]; then
  rm -rf "$SB/remotes/bambi.git"
  git init -q --bare -b main "$SB/remotes/bambi.git"
  git -C "$BAMBI_SRC" push -q "$SB/remotes/bambi.git" 137788f8:refs/heads/1061-bayesian-r2 7f12dd1a:refs/heads/main
  echo "bambi sandbox remote ready (PR head 137788f8)"
fi
