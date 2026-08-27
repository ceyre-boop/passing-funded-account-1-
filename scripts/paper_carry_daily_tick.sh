#!/bin/zsh
# One daily tick for the paper carry loop (2026-08-26 dispatch: "the thing
# that turns n=411 into n growing"). Same shape as daytrade/operator_tick.sh:
# a thin shell wrapper a launchd job calls, all real logic in Python.
#
# scripts/paper_carry_daily.py --risk is REQUIRED and has no code default —
# `eval_size()` (scripts/ruin_engine.py, in flight) has not landed, and
# CLAUDE.md rule 3/5 forbid inventing a size here. This wrapper reads
# PAPER_CARRY_RISK from .env and refuses to run without it — that number is
# Colin's to set, not this script's to guess.
#
# NOT INSTALLED BY THIS REPO. To schedule:
#   1. Set PAPER_CARRY_RISK=<fraction> in .env (e.g. 0.0075).
#   2. Fill REPO_ROOT into scheduling/com.alta.paper-carry-daily.plist.template
#      and copy it to ~/Library/LaunchAgents/com.alta.paper-carry-daily.plist.
#   3. launchctl load ~/Library/LaunchAgents/com.alta.paper-carry-daily.plist
# All three are manual, deliberate steps — same seriousness as arming
# daytrade/paper_carry_runner.py.

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

if [[ -f "$REPO/.env" ]]; then
  set -a; source "$REPO/.env"; set +a
fi

if [[ -z "${PAPER_CARRY_RISK:-}" ]]; then
  echo "PAPER_CARRY_RISK not set in $REPO/.env -- refusing to run with an invented size." >&2
  exit 1
fi

cd "$REPO"
mkdir -p "$REPO/data/agent"
echo "--- paper carry tick $(date -u +%FT%TZ)"
python3 scripts/paper_carry_daily.py --risk "$PAPER_CARRY_RISK" --firm "${PAPER_CARRY_FIRM:-cti_1step}"
