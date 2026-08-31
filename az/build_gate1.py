#!/usr/bin/env python3
"""az/build_gate1.py — run Gate 1: occupancy at each declared granularity. Spec 048."""
from __future__ import annotations
import sys, json
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "daytrade"))
from az.report import Tally                                                # noqa: E402
from az.state import (GRANULARITIES, GRID_HHMM, MIN_DAYS, StateError,      # noqa: E402
                      assert_no_lookahead, audit, truncate_at)
from bars import ET, MIN_SESSION_BARS_5M, RTH_CLOSE, RTH_OPEN, _internal_gaps  # noqa: E402
from splits import TUNE_END                                                # noqa: E402


def sessions_for(path: Path):
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(ET)
    t = df.index.strftime("%H:%M")
    rth = df[(t >= RTH_OPEN) & (t <= RTH_CLOSE)]
    out = []
    for day, chunk in rth.groupby(rth.index.date):
        if len(chunk) < MIN_SESSION_BARS_5M or _internal_gaps(chunk, "5m"):
            continue
        out.append((day, chunk))
    return [(d, c) for d, c in out if d <= TUNE_END]


def rows_for(symbols, sub, *, guard_every: int = 250):
    rows, guarded, skipped = [], 0, 0
    for sym in symbols:
        sess = sessions_for(ROOT / f"data/daytrade/{sub}/{sym}_5m.parquet")
        prev_close = None
        for i, (day, chunk) in enumerate(sess):
            for hhmm in GRID_HHMM:
                hist = truncate_at(chunk, hhmm)
                try:
                    if i % guard_every == 0 and hhmm == "11:00":
                        raw = assert_no_lookahead(chunk, hhmm, prev_close); guarded += 1
                    else:
                        from az.state import raw_features
                        raw = raw_features(hist, prev_close)
                except StateError:
                    skipped += 1; continue
                rows.append({"day": f"{sym}:{day}", "hhmm": hhmm, "raw": raw, "sym": sym})
            prev_close = float(chunk["Close"].iloc[-1])
    return rows, guarded, skipped


def main() -> int:
    for label, syms, sub in (("DECLARED LANE  SPY+QQQ", ["SPY", "QQQ"], "bars_premarket"),
                             ("GENERALISATION bars/", ["NVDA", "SPY", "QQQ", "AAPL", "AMD", "TSLA",
                                                       "META", "MSFT", "AMZN", "GOOGL", "DIA", "IWM"], "bars")):
        rows, guarded, skipped = rows_for(syms, sub)
        tally = Tally(n_days=len({r["day"].split(":")[1] for r in rows}),
                      n_candidates=len(rows),
                      extra={"symbol-days": f"{len({r['day'] for r in rows}):,}",
                             "lookahead-guarded": guarded, "skipped(StateError)": skipped})
        print(f"\n{label}")
        print("  " + tally.header())
        print(f"  {'granularity':<10} {'cells':>7} {'valued':>7} {'frac_cand_in_valued':>21} {'verdict':>9}")
        for g, edges in GRANULARITIES.items():
            occ = audit(rows, g, edges, min_days=MIN_DAYS)
            print(f"  {g:<10} {occ.cells:>7,} {occ.cells_valued:>7,} {occ.frac_candidates_in_valued_cells:>21.4f} {occ.verdict():>9}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
