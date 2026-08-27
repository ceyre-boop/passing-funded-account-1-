#!/usr/bin/env python3
"""Session-close hook for the streak tracker (spec 004).

Called once per trading day, at session close, from `dashboard_publish.sh`
(16:40 ET weekdays) — the correct session-close hook per that script's own
comment ("Post-close dashboard publish"). This is the one call site on the
live path where `streak.update()` can legitimately fire.

Tries to build a real `day_result` from the one place in this repo that
claims to record REAL executed shots — `data/shot_ledger.csv` (hand-logged;
THE_SHOT.md / MONDAY_OPEN.md: "every shot logged in data/shot_ledger.csv win
or lose"). `operator_tick.sh`'s chain (bars -> plan writer -> news archive ->
decision ledger -> alpha_operator --shadow -> resolve) never places an order
and never produces a real fill or a real $ pnl — see NEXT.md 2026-08-26:
"This repo is a research instrument of unusual quality that has never placed
an order." So the shadow/forecast path is NOT an honest source of a real
day_result; the manually-maintained shot ledger is the only artifact in this
repo that claims to be one.

HONESTY, not convenience: `shot_ledger.csv`'s header today is
`date,release,or_high,or_low,trigger_bar_time,direction,entry,stop,target,
outcome_R,setup_grade,cooloff_honored,notes` — it carries NO `pnl`,
`campaign_cushion`, or `distance_to_goal` columns. `streak.update()` requires
all six `day_result` fields and CLAUDE.md rule 3 forbids defaulting any
missing one to 0. Until the ledger schema (or an operator-maintained
`data/daytrade/campaign_config.json`, see `write_baseline_plan.py`) carries
those account-level fields for real, this script SKIPS on every run, on
purpose, and prints exactly why instead of writing a fabricated day. A
fabricated trade has burned this repo before (see MEMORY: "spec 021 week one
completion" — the fabricated trade #1 and its reversion).
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import streak as streak_mod                                        # noqa: E402

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
SHOT_LEDGER = ROOT / "data" / "shot_ledger.csv"

# day_result fields streak.update() requires that shot_ledger.csv's current
# schema does not carry. See REQUIRED_FIELDS in streak.py for the full list;
# `date` and `outcome` (derived from outcome_R) ARE sourceable from the
# ledger today — these three are not.
_MISSING_FROM_LEDGER_SCHEMA = ("pnl", "campaign_cushion", "distance_to_goal")


def _today_row(today: str) -> dict | None:
    if not SHOT_LEDGER.exists():
        return None
    with SHOT_LEDGER.open(newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("date") == today:
                return row
    return None


def _outcome_from_row(row: dict) -> str | None:
    """green/red/flat from the ledger's outcome_R column. None means
    unparseable — the caller must SKIP, never guess."""
    raw = (row.get("outcome_R") or "").strip()
    if raw == "" or raw.upper() == "NO_TRADE":
        return "flat"
    try:
        r = float(raw)
    except ValueError:
        return None
    if r > 0:
        return "green"
    if r < 0:
        return "red"
    return "flat"


def main() -> int:
    today = datetime.now(ET).date().isoformat()
    row = _today_row(today)
    if row is None:
        print(f"  streak: SKIP — no {SHOT_LEDGER.relative_to(ROOT)} row for "
              f"{today} (no real session result to log; never fabricated)")
        return 0

    outcome = _outcome_from_row(row)
    if outcome is None:
        print(f"  streak: SKIP — {today} outcome_R {row.get('outcome_R')!r} "
              "in the ledger is unparseable, refusing to guess")
        return 0

    missing = [f for f in _MISSING_FROM_LEDGER_SCHEMA
               if row.get(f) in (None, "")]
    if missing:
        print(f"  streak: SKIP — {today} has an outcome ({outcome}) but "
              f"{SHOT_LEDGER.relative_to(ROOT)} carries no honest "
              f"{', '.join(missing)}; streak.update() refuses to default any "
              "of them to 0 (CLAUDE.md rule 3) — extend the ledger schema or "
              "add data/daytrade/campaign_config.json before this can run "
              "for real")
        return 0

    try:
        day_result = {
            "date": today,
            "outcome": outcome,
            "pnl": float(row["pnl"]),
            "result_R": float(row["outcome_R"]) if outcome != "flat" else 0.0,
            "distance_to_goal": float(row["distance_to_goal"]),
            "campaign_cushion": float(row["campaign_cushion"]),
        }
    except ValueError as exc:
        print(f"  streak: SKIP — {today} ledger row has an unparseable "
              f"numeric field: {exc}")
        return 0

    state = streak_mod.update(day_result)
    print(f"  streak updated: {today} {outcome} — green_streak "
          f"{state.green_streak}, consecutive_red_days "
          f"{state.consecutive_red_days}, cooloff_until {state.cooloff_until}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
