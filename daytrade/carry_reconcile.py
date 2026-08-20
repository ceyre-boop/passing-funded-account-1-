#!/usr/bin/env python3
"""CARRY RECONCILIATION — can carry-exit-v1 express what the proven edge does?

The stack exam's SF-2 asks for zero exit-path divergence: every exit should
reproduce when replayed against the same bar stream. For the intraday engine
that is already true by construction. For a NEW evaluator the question is
different and harder: replay the 411 sealed v015 trades through
`decide_carry_exit` and ask how often it reaches the same exit, on the same
day, for the same stated reason.

This is a COMPLETENESS test, not a fitting exercise. A low match rate means
the evaluator cannot express the strategy and needs another term — it does
NOT mean the parameters should be tuned until the number goes up. Tuning
inside the evaluation layer is exactly what stops it being the yardstick
everything else is measured against.

Reads: the sealed CSV (already read by G1) and the FX state ledger.
Writes: one report. Trades nothing, changes no gate.
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chain                                                       # noqa: E402
import fx_state as fx                                              # noqa: E402
from carry_exit import (CarryState, CarryStage, decide_carry_exit,  # noqa: E402
                        apply_carry_action, CarryStateError)

ROOT = Path(__file__).resolve().parents[1]
TRADES = ROOT / "data" / "proof" / "backtest_trades_v015_2015_2024.csv"
OUT = ROOT / "data" / "daytrade" / "carry_reconcile.json"
DAY_TOLERANCE = 1          # "same day" allows +/-1 session of slippage


def _state_index() -> dict:
    """(pair, session_date) -> the FX state row knowable that day."""
    idx = {}
    for r in chain.rows(fx.LEDGER):
        idx[(r["pair"], r["session_date"])] = r
    return idx


def _bars(pair: str):
    import pandas as pd
    p = fx.BARS / f"{pair.replace('=', '_')}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p).sort_index()
    df.index = pd.to_datetime(df.index)
    return df


def replay(trade: dict, states: dict, df, *, trail_first: bool = False) -> dict | None:
    """One sealed trade, forward through carry-exit-v1 on daily bars."""
    pair = trade["pair"]
    entry_d = datetime.fromisoformat(trade["entry_date"]).date()
    exit_d = datetime.fromisoformat(trade["exit_date"]).date()
    direction = int(trade["direction"])
    entry = float(trade["entry"])
    risk_frac = float(trade["risk_pct"])
    if risk_frac <= 0:
        return None
    # the sealed CSV states risk as a fraction of price; the stop follows
    stop = entry * (1 - direction * risk_frac)

    fwd = df[df.index.map(lambda t: t.date() > entry_d)]
    if not len(fwd):
        return None
    s0 = states.get((pair, entry_d.isoformat()))
    rd_entry = s0.get("rate_diff") if s0 else None

    st = CarryState(
        pair=pair, direction=direction, entry=entry, price=entry, sl=stop,
        swap_accrual_r_per_day=fx.swap_per_day(),
        rate_diff=rd_entry, rate_diff_at_entry=rd_entry,
        trail_outranks_time=trail_first,
        atr14=None, hwm=entry)

    prev = entry_d
    for ts, bar in fwd.iterrows():
        d = ts.date()
        st.days_held = (d - entry_d).days
        if (d - prev).days > 1:
            st.weekends_crossed += 1
            st.weekend_next_session = False
        prev = d
        row = states.get((pair, d.isoformat()))
        if row:
            st.rate_diff = row.get("rate_diff")
            st.rate_diff_stale_days = row.get("rate_diff_stale_days")
            atrp = row.get("atr14_pct")
            st.atr14 = (atrp / 100.0 * float(bar["Close"])) if atrp else None
        st.price = float(bar["Close"])
        for a in decide_carry_exit(st):
            apply_carry_action(st, a)
            if a.kind == "EXIT_ALL":
                return {"pair": pair, "entry_date": entry_d.isoformat(),
                        "sealed_exit": exit_d.isoformat(),
                        "sealed_reason": trade["exit_reason"],
                        "sealed_hold_days": int(float(trade["hold_days"])),
                        "engine_exit": d.isoformat(),
                        "engine_reason": a.exit_reason,
                        "engine_hold_days": st.days_held,
                        "day_delta": (d - exit_d).days,
                        "reason_match": a.exit_reason == trade["exit_reason"],
                        "day_match": abs((d - exit_d).days) <= DAY_TOLERANCE,
                        "net_r": round(st.net_r, 4),
                        "swap_paid_r": round(st.swap_paid_r, 4)}
        if st.stage is CarryStage.CLOSED:
            break
    return {"pair": pair, "entry_date": entry_d.isoformat(),
            "sealed_exit": exit_d.isoformat(),
            "sealed_reason": trade["exit_reason"],
            "sealed_hold_days": int(float(trade["hold_days"])),
            "engine_exit": None, "engine_reason": "NEVER_EXITED",
            "engine_hold_days": st.days_held, "day_delta": None,
            "reason_match": False, "day_match": False,
            "net_r": round(st.net_r, 4), "swap_paid_r": round(st.swap_paid_r, 4)}


TRAIL_FIRST = "--trail-first" in sys.argv


def main() -> int:
    states = _state_index()
    if not states:
        print("  no FX state rows — run fx_state.py build first")
        return 1
    trades = list(csv.DictReader(TRADES.open()))
    bars = {p: _bars(p) for p in fx.PAIRS}

    rows, skipped = [], 0
    for t in trades:
        df = bars.get(t["pair"])
        if df is None:
            skipped += 1
            continue
        try:
            r = replay(t, states, df, trail_first=TRAIL_FIRST)
        except CarryStateError as e:
            print(f"  !! {t['pair']} {t['entry_date'][:10]}: {e}")
            skipped += 1
            continue
        if r is None:
            skipped += 1
        else:
            rows.append(r)

    if not rows:
        print("  nothing replayed")
        return 1

    reason_hits = sum(1 for r in rows if r["reason_match"])
    day_hits = sum(1 for r in rows if r["day_match"])
    both = sum(1 for r in rows if r["reason_match"] and r["day_match"])
    deltas = [r["day_delta"] for r in rows if r["day_delta"] is not None]

    by_reason = {}
    for sealed in sorted({r["sealed_reason"] for r in rows}):
        sub = [r for r in rows if r["sealed_reason"] == sealed]
        by_reason[sealed] = {
            "n": len(sub),
            "engine_said": dict(Counter(r["engine_reason"] for r in sub)),
            "reason_match_rate": round(
                sum(1 for r in sub if r["reason_match"]) / len(sub), 3)}

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluator": "carry-exit-v1",
        "trail_outranks_time": TRAIL_FIRST,
        "note": "COMPLETENESS test, not a fit. A low match means a missing "
                "term, never a reason to tune the evaluator.",
        "n_sealed": len(trades), "n_replayed": len(rows), "n_skipped": skipped,
        "reason_match_rate": round(reason_hits / len(rows), 3),
        "day_match_rate": round(day_hits / len(rows), 3),
        "both_match_rate": round(both / len(rows), 3),
        "median_day_delta": statistics.median(deltas) if deltas else None,
        "mean_swap_paid_r": round(
            sum(r["swap_paid_r"] for r in rows) / len(rows), 4),
        "by_sealed_reason": by_reason,
        "rows": rows[:80],
    }
    OUT.write_text(json.dumps(summary, indent=1))

    print(f"  replayed {len(rows)}/{len(trades)} sealed trades "
          f"({skipped} skipped)")
    print(f"  exit-reason match   {summary['reason_match_rate']:.1%}")
    print(f"  exit-day match ±{DAY_TOLERANCE}   {summary['day_match_rate']:.1%}")
    print(f"  both                {summary['both_match_rate']:.1%}")
    print(f"  median day delta    {summary['median_day_delta']}")
    print(f"  mean financing paid {summary['mean_swap_paid_r']:+.4f} R\n")
    print(f"  {'sealed reason':16s} {'n':>4s}  engine said")
    for sealed, d in by_reason.items():
        top = ", ".join(f"{k} {v}" for k, v in
                        sorted(d["engine_said"].items(), key=lambda kv: -kv[1]))
        print(f"  {sealed:16s} {d['n']:4d}  {top}   "
              f"[match {d['reason_match_rate']:.0%}]")
    print(f"\n  report: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
