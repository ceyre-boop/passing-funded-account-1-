#!/usr/bin/env python3
"""Mechanical baseline plan writer — the OR-break plan, from the same entry
rule the furnace uses (ceiling.find_entry, one implementation).

Purpose: prereg R-band scoring needs a plan with entry/sl on file (spec 024's
resolver refuses to invent R — "no plan = R undefined" is honest but leaves
the gradebook empty). This writes data/daytrade/plan.json mechanically once
the opening range has formed and broken, giving every judgment that day a
real R geometry to be scored against.

Rules:
  - Runs from the tick; does nothing before 10:00 ET, nothing twice a day,
    nothing if today's session has no OR break yet.
  - qty is a NOMINAL 1.0 — this plan prices R geometry for scoring; it is
    not an order and the runner is not pointed at it during the soak.
  - Overwrites only a plan it wrote itself (marker field); a hand-written
    plan.json is never touched.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bars import load_sessions, BarDataError                       # noqa: E402
from ceiling import find_entry, TP1_R                              # noqa: E402

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "data" / "daytrade" / "plan.json"
MARKER = "mechanical-or-break-v1"


def main(symbol: str = "NVDA") -> int:
    now = datetime.now(ET)
    today = str(now.date())
    if now.strftime("%H:%M") < "10:00":
        print("  before 10:00 ET — opening range not complete, no plan")
        return 0
    if PLAN.exists():
        try:
            cur = json.loads(PLAN.read_text())
        except json.JSONDecodeError:
            print(f"  !! {PLAN.name} unreadable — refusing to touch it")
            return 1
        if cur.get("_writer") != MARKER:
            print("  hand-written plan on file — never overwritten")
            return 0
        if cur.get("_session") == today:
            return 0                        # today's plan already written
    try:
        sessions = load_sessions(symbol, "5m", allow_fetch=False)
    except BarDataError as e:
        print(f"  !! {e}")
        return 1
    sess = next((s for s in sessions if str(s.day) == today), None)
    if sess is None:
        print(f"  no complete session for {today} in cache yet")
        return 0
    e = find_entry(sess)
    if e is None:
        print(f"  {today}: no OR break (yet) — no plan")
        return 0
    PLAN.write_text(json.dumps({
        "_writer": MARKER, "_session": today,
        "_note": "R-geometry plan for prereg scoring; NOT an order",
        "symbol": symbol, "direction": "long" if e.direction > 0 else "short",
        "entry": e.entry, "sl": e.stop, "qty": 1.0,
        "tp1": e.tp1, "tp2": e.tp2, "trail_dist": e.trail_dist,
        "exit_policy": "DEFAULT",
    }, indent=1))
    print(f"  plan written: {symbol} {'LONG' if e.direction > 0 else 'SHORT'} "
          f"entry {e.entry:.2f} sl {e.stop:.2f} (risk {e.risk:.2f}) — {e.ts}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "NVDA"))
