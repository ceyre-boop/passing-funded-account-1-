#!/bin/zsh
# One launchd tick for the alpha operator (spec 023).
# Fires every 5 minutes; this guard makes off-hours ticks free — the operator's
# own trigger dedup already makes quiet in-hours ticks free (I5).
# Window 08:00-16:30 ET Mon-Fri: premarket trigger needs pre-09:30 runs, and
# one post-close tick lets the resolver close the day's last forecasts.

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

ET_DOW=$(TZ=America/New_York date +%u)      # 1=Mon .. 7=Sun
ET_HM=$(TZ=America/New_York date +%H%M)
if [[ $ET_DOW -gt 5 || $ET_HM -lt 0800 || $ET_HM -gt 1630 ]]; then
  exit 0                                    # outside the window: silent, free
fi

set -a; source "$REPO/.env"; set +a
cd "$REPO/daytrade"

echo "--- tick $(date -u +%FT%TZ) (ET $ET_HM)"
# The resolver runs REGARDLESS of the operator call's fate — a spend-cap or
# network failure on `run` must not stall the learning loop (review fix 3).
set +e
# SHADOW SOAK (spec 024 I22): full judgment, sealed records, forecasts —
# zero directive writes. Remove --shadow deliberately, as its own decision,
# after the soak has proven the directive vocabulary.
python3 alpha_operator.py run --symbol NVDA --shadow
RUN_RC=$?
if [[ $RUN_RC -ne 0 ]]; then
  echo "!! OPERATOR RUN FAILED (exit $RUN_RC) — resolver still running"
fi
python3 alpha_operator.py resolve
RES_RC=$?
if [[ $RES_RC -ne 0 ]]; then
  echo "!! RESOLVER FAILED (exit $RES_RC)"
fi
# --- daily dashboard publish (spec 024 dashboard) — at most once per ET day,
# on the last tick(s) of the window. Stamp written only after a successful
# build; commit skipped when data is unchanged; a push failure echoes and
# never affects the run/resolve exit accounting.
STAMP="$REPO/data/daytrade/operator/.dashboard_published"
ET_DATE=$(TZ=America/New_York date +%F)
if [[ $ET_HM -ge 1625 && "$(cat "$STAMP" 2>/dev/null)" != "$ET_DATE" ]]; then
  if python3 "$REPO/daytrade/build_dashboard_data.py"; then
    echo "$ET_DATE" > "$STAMP"
    ( cd "$REPO" \
      && git add docs/data.json \
      && { git diff --cached --quiet \
           || { git commit -m "dashboard: $ET_DATE post-close data refresh" \
                && git push origin main; }; } ) \
      || echo "!! DASHBOARD PUBLISH FAILED (git) — data built, push did not land"
  else
    echo "!! DASHBOARD BUILD FAILED — not publishing"
  fi
fi

exit $(( RUN_RC || RES_RC ))
