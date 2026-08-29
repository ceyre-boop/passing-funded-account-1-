#!/usr/bin/env python3
"""scripts/build_exit_snapshots.py — the snapshot frame state_space.py audit() expects.

One row per open-position snapshot, with the six discretisation inputs plus the
identifiers needed to count INDEPENDENT bets rather than rows:

    trade_id  — one entry (symbol + session day). All bars and all configs of one
                entry are the same bet observed repeatedly.
    day       — calendar date. Same-day cross-symbol entries are nearly one bet;
                this is the repo's own grouping convention (GroupKFold on date).
    config    — which exit config produced this replay. Six configs replay the SAME
                entry, so config multiplies rows without adding information.

Reuses exit_evaluator's observer hook and simulate() — one implementation of the
decision path, per repo rule 1. Emits artifacts/exit_snapshots.parquet.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "daytrade"))

from bars import load_sessions                                    # noqa: E402
from ceiling import find_entry, simulate                          # noqa: E402
from exit_evaluator import LOG_CONFIGS, SYM_CLASS                 # noqa: E402
from splits import tune_sessions                                  # noqa: E402
from stockfish_exit import Stage                                  # noqa: E402

COST_PER_SHARE = 0.01


def collect_entries():
    out = []
    for sym in SYM_CLASS:
        try:
            sessions = load_sessions(sym, "5m", allow_fetch=False)
        except Exception:
            continue
        for s in tune_sessions(sessions):
            e = find_entry(s)
            if e is not None:
                out.append((sym, s, e))
    return out


def snapshots(entries) -> pd.DataFrame:
    rows = []
    for sym, s, e in entries:
        day = str(s.day)
        trade_id = f"{sym}:{day}"
        for cname, cfg in LOG_CONFIGS.items():
            bar_rows: list[dict] = []
            tr5: list[float] = []

            def obs(ts, bar, st, realized, held, _c=cname, _b=bar_rows, _t=tr5):
                if st.stage in (Stage.CLOSING, Stage.CLOSED) or held <= 0:
                    return
                px = float(bar["Open"])
                tr = (float(bar["High"]) - float(bar["Low"])) / e.risk
                _t.append(tr)
                atr_now = sum(_t[-5:]) / min(len(_t), 5)
                hhmm = ts.strftime("%H:%M")
                mins = int(hhmm[:2]) * 60 + int(hhmm[3:])
                _b.append({
                    "trade_id": trade_id, "day": day, "sym": sym,
                    "cls": SYM_CLASS[sym], "config": _c,
                    "unrealized_r": (px - e.entry) * e.direction / e.risk,
                    "bars_held": len(_b),
                    "atr_now": atr_now,
                    "carry_r": 0.0,          # intraday: no swap, no financing
                    "session": "ny_am" if mins < 12 * 60 else "ny_pm",
                    "weekend_exposure": False,
                    "minutes_to_close": (15 * 60 + 55) - mins,
                })

            simulate(s, e, dict(cfg), observer=obs)
            if bar_rows:
                atr0 = bar_rows[0]["atr_now"] or np.nan
                for r in bar_rows:
                    r["atr_ratio"] = (r["atr_now"] / atr0) if atr0 and atr0 == atr0 else np.nan
                rows.extend(bar_rows)
    return pd.DataFrame(rows)


def main() -> int:
    entries = collect_entries()
    if not entries:
        print("HALT: no tune entries found — bar cache missing?", file=sys.stderr)
        return 2
    df = snapshots(entries)
    if df.empty:
        print("HALT: entries found but zero snapshots emitted", file=sys.stderr)
        return 2
    out = ROOT / "artifacts" / "exit_snapshots.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"snapshots     {len(df):,}")
    print(f"entries       {df.trade_id.nunique():,}")
    print(f"days          {df.day.nunique():,}")
    print(f"configs       {df.config.nunique()}")
    print(f"symbols       {df.sym.nunique()}")
    print(f"rows/entry    {len(df)/df.trade_id.nunique():.1f}")
    print(f"-> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
