#!/bin/sh
# Point git at the repo's tracked hooks (git never tracks .git/hooks itself).
# Run once per checkout/worktree: sh scripts/install_hooks.sh
set -e
ROOT="$(git rev-parse --show-toplevel)"
git -C "$ROOT" config core.hooksPath .githooks
echo "installed: core.hooksPath -> .githooks (trade-evidence freeze commit-msg hook active)"
