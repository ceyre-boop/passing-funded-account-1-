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
# Mechanical R-geometry plan (spec 024 prereg scoring) — no-op before 10:00,
# idempotent per day, never touches a hand-written plan.
# Non-fatal for THIS tick (a plan failure must not stall the resolver below),
# but its return code is captured and folded into the tick's own exit status
# so it is never silently invisible across ticks (review fix: 98 consecutive
# failures were previously swallowed here without a trace).
PLAN_STATE_DIR="$REPO/data/daytrade/operator"
PLAN_FAIL_FILE="$PLAN_STATE_DIR/plan_writer_fails"
mkdir -p "$PLAN_STATE_DIR"

python3 write_baseline_plan.py NVDA && PLAN_RC=0 || PLAN_RC=$?
if [[ $PLAN_RC -ne 0 ]]; then
  echo "!! plan writer failed (non-fatal, exit $PLAN_RC)"
  PLAN_FAILS=$(( $(cat "$PLAN_FAIL_FILE" 2>/dev/null || echo 0) + 1 ))
  echo "$PLAN_FAILS" > "$PLAN_FAIL_FILE"
  if [[ $PLAN_FAILS -ge 3 ]]; then
    echo "!! PERSISTENT: plan writer has failed $PLAN_FAILS consecutive ticks"
  fi
else
  echo 0 > "$PLAN_FAIL_FILE"
fi

# Spec 030: OBSERVE the decision channel every tick — cheap, no model, no API
# cost, so it runs whether or not a judgment happens. The soak proved that a
# system not observing 09:30-11:00 ET cannot learn from it.
python3 decision_ledger.py capture --symbol NVDA || echo "!! capture failed (non-fatal)"
# Catch-up: if the host slept through the morning, reconstruct today's missed
# decision points from point-in-time sources the moment it wakes. Marked
# source=backfill — knowable-state evidence, never "what the model saw".
python3 decision_ledger.py backfill --symbol NVDA --days 1 \
    || echo "!! backfill failed (non-fatal)"
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
# Dashboard publishing moved to its own scheduled job (2nd review): a
# market-session tick must not mutate remote git state as a side effect.
# See ~/Library/LaunchAgents/com.alta.dashboard-publish.plist ->
# daytrade/dashboard_publish.sh (16:40 ET weekdays).

exit $(( RUN_RC || RES_RC || PLAN_RC ))
