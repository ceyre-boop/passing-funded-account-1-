#!/bin/zsh
# Post-close dashboard publish — its OWN scheduled job (16:40 ET weekdays),
# deliberately separate from operator_tick.sh so the trading/research loop
# never mutates remote git state as a side effect of a market tick.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

ET_DOW=$(TZ=America/New_York date +%u)
[[ $ET_DOW -gt 5 ]] && exit 0

STAMP="$REPO/data/daytrade/operator/.dashboard_published"
ET_DATE=$(TZ=America/New_York date +%F)
[[ "$(cat "$STAMP" 2>/dev/null)" == "$ET_DATE" ]] && exit 0

if python3 "$REPO/daytrade/build_dashboard_data.py"; then
  echo "$ET_DATE" > "$STAMP"
  cd "$REPO" \
    && git add docs/data.json \
    && { git diff --cached --quiet \
         || { git commit -m "dashboard: $ET_DATE post-close data refresh" \
              && git push origin main; }; } \
    || echo "!! DASHBOARD PUBLISH FAILED (git) — data built, push did not land"
else
  echo "!! DASHBOARD BUILD FAILED — not publishing"
fi
