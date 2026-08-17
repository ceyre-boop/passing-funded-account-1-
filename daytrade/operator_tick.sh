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
exit $(( RUN_RC || RES_RC ))
