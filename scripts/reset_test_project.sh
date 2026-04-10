#!/usr/bin/env bash
set -euo pipefail

target="${1:-.}"

if [[ ! -d "$target" ]]; then
  echo "Error: $target is not a directory" >&2
  exit 1
fi

cd "$target"
echo "Resetting project in $(pwd)"

# Remove git worktrees before deleting .git
if [[ -d .git ]]; then
  git worktree list --porcelain 2>/dev/null | grep '^worktree ' | awk '{print $2}' | while read -r wt; do
    [[ "$wt" == "$(pwd)" ]] && continue
    echo "  Removing worktree: $wt"
    git worktree remove --force "$wt" 2>/dev/null || rm -rf "$wt"
  done
fi

# Remove jig state
if [[ -d .jig ]]; then
  echo "  Removing .jig/"
  rm -rf .jig
fi

# Remove git and reinitialize
if [[ -d .git ]]; then
  echo "  Removing .git/"
  rm -rf .git
fi
echo "  Initializing fresh git repo"
git init -b develop

echo "Done."
