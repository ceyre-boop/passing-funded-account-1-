#!/usr/bin/env python3
"""scripts/ceiling_10y.py — recompute the exit ceiling on 10.7 years, not 24 days.

The "3 of 24 days any config could win" figure that every exit conclusion rests on
was computed over 24 tune sessions of the NVDA-shaped basket. This reruns the SAME
scoring — `ceiling.policy_ceiling` and `ceiling.report`, unmodified — over SPY's
premarket cache: 2,656 complete RTH sessions, of which 2,618 fall BEFORE
`splits.TUNE_END`.

That is not an unsealing. `TUNE_END = 2026-07-06` splits tune from sealed holdout;
every session used here is on the tune side of that line. `sealed_sessions()` is
never called and nothing after TUNE_END is read.

Session construction mirrors `bars.load_sessions` exactly, with one documented
difference: `load_sessions` RAISES on a session with an internal gap, while its own
docstring says such sessions are "excluded and the reason is printed". Over ten
years a single missing bar would abort the load, so this follows the DOCSTRING —
gapped days are excluded and counted, never interpolated, never repaired.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "daytrade"))

import bars                                                          # noqa: E402
from bars import ET, MIN_SESSION_BARS_5M, RTH_CLOSE, RTH_OPEN, Session, _internal_gaps  # noqa: E402
from ceiling import find_entry, narrow_space, policy_ceiling, report, wide_space        # noqa: E402
from splits import TUNE_END                                          # noqa: E402


def load_premarket_sessions(symbol: str) -> tuple[list[Session], dict]:
    candidates = [ROOT / "data" / "daytrade" / d / f"{symbol}_5m.parquet"
                  for d in ("bars_premarket", "bars_extended")]
    path = next((c for c in candidates if c.exists()), None)
    if path is None:
        raise SystemExit("HALT: no deep cache for " + symbol + " in " +
                         ", ".join(str(c.parent.name) for c in candidates))
    print(f"  cache: {path.relative_to(ROOT)}")
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(ET)
    t = df.index.strftime("%H:%M")
    rth = df[(t >= RTH_OPEN) & (t <= RTH_CLOSE)]
    sessions, half, gapped = [], [], []
    for day, chunk in rth.groupby(rth.index.date):
        if len(chunk) < MIN_SESSION_BARS_5M:
            half.append(str(day)); continue
        if _internal_gaps(chunk, "5m"):
            gapped.append(str(day)); continue
        sessions.append(Session(symbol, day, chunk))
    return sessions, {"half_days_excluded": len(half), "gapped_excluded": len(gapped),
                      "gapped_days": gapped}


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    sessions, excl = load_premarket_sessions(symbol)
    cut = dt.date.fromisoformat(str(TUNE_END))
    tune = [s for s in sessions if s.day < cut]
    if not tune:
        print("HALT: no sessions before TUNE_END", file=sys.stderr); return 2

    print(f"{symbol}: {len(sessions)} complete sessions; {len(tune)} before TUNE_END "
          f"({tune[0].day} -> {tune[-1].day})")
    print(f"  excluded: {excl['half_days_excluded']} half-days, {excl['gapped_excluded']} gapped")

    narrow = policy_ceiling(tune, narrow_space(), f"{symbol}-10y-narrow")
    wide = policy_ceiling(tune, wide_space(), f"{symbol}-10y-wide")
    rep = report(narrow, wide)
    rep["_population"] = {
        "symbol": symbol, "sessions_complete": len(sessions), "sessions_used": len(tune),
        "span": [str(tune[0].day), str(tune[-1].day)], "tune_end": str(TUNE_END),
        "sealed_never_read": True, **excl,
    }
    out = ROOT / "artifacts" / f"ceiling_10y_{symbol}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    slim = {k: v for k, v in rep.items() if not k.startswith("_") or k == "_population"}
    out.write_text(json.dumps(slim, indent=2, default=str) + "\n")

    n = rep["n_entries"]; live = rep["days_with_any_winning_config"]
    print()
    print(f"  entries                       {n:,}")
    print(f"  days any config could win     {live:,} / {n:,}  ({live/max(n,1):.1%})")
    print(f"  Z best fixed (wide)           {rep['Z_wide']:+.2f} R   per trade {rep['Z_wide']/max(n,1):+.4f}")
    print(f"  W oracle (wide)               {rep['W_wide']:+.2f} R")
    print(f"  prize (wide)                  {rep['prize_wide_R']:+.2f} R   per trade {rep['prize_wide_R_per_trade']:+.4f}")
    print(f"  best fixed policy             {rep['best_fixed_wide']}")
    print(f"  random picker                 {rep['random_picker_R']:+.2f} R")
    print(f"  anti-oracle                   {rep['anti_oracle_R']:+.2f} R")
    print(f"  verdict                       {rep['verdict']}")
    print(f"-> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
