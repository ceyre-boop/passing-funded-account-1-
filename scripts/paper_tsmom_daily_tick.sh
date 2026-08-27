#!/bin/zsh
# One daily tick for the paper TSMOM loop (sovereign/trend/SPEC.md). Same
# shape as scripts/paper_carry_daily_tick.sh: a thin shell wrapper a launchd
# job calls, all real logic in Python.
#
# Unlike the carry tick, this one needs NO risk env var -- TSMOM sizing
# (notional_frac, from VOL_TARGET / realized_vol, capped at MAX_NOTIONAL) is
# fully specified by sovereign/trend/SPEC.md with zero free parameters.
# There is nothing for an operator to set here.
#
# NOT INSTALLED BY THIS REPO. To schedule:
#   1. Fill REPO_ROOT into
#      scheduling/com.alta.paper-tsmom-daily.plist.template and copy it to
#      ~/Library/LaunchAgents/com.alta.paper-tsmom-daily.plist.
#   2. launchctl load ~/Library/LaunchAgents/com.alta.paper-tsmom-daily.plist
# Both are manual, deliberate steps -- same seriousness as the carry tick.

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

if [[ -f "$REPO/.env" ]]; then
  set -a; source "$REPO/.env"; set +a
fi

cd "$REPO"
mkdir -p "$REPO/data/agent"
echo "--- paper tsmom tick $(date -u +%FT%TZ)"
python3 scripts/paper_tsmom_daily.py --firm "${PAPER_TSMOM_FIRM:-cti_1step}"
