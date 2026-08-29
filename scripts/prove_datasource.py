#!/usr/bin/env python3
"""PROOF — the DataSource seam, end-to-end, on one real strategy.

WHAT THIS PROVES
----------------
1. The vendor swap is confined to daytrade/datasource.py. Nothing below is
   vendor-aware: bars.load_sessions, ceiling.find_entry, ceiling.simulate and
   ceiling.policy_ceiling are called UNMODIFIED on both populations.
2. The unblock is real: yfinance caps 5m intraday at ~60 days, so every ceiling
   number in this repo has been measured on ~74 sessions. Alpaca serves the same
   bars back to 2016.
3. The measurement changes. That is the point — a policy ceiling computed on 74
   sessions was never a stable estimate of anything, and the comparison here says
   by how much it moved.

The extended history is written to data/daytrade/bars_extended/ — a SEPARATE
cache. The live cache under bars/ is not touched, because the spec 008 tune/
sealed split is defined over that population and silently growing it backwards
would change the identity of an already-measured holdout.

    python3 scripts/prove_datasource.py --symbol NVDA --start 2024-01-01
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from daytrade import bars, ceiling, datasource  # noqa: E402

EXTENDED = ROOT / "data" / "daytrade" / "bars_extended"


def _run(sessions, label):
    """ceiling.policy_ceiling, unmodified, on whatever population is handed in."""
    return ceiling.policy_ceiling(sessions, ceiling.narrow_space(), label)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="NVDA")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--tf", default="5m")
    a = ap.parse_args(argv)
    end = a.end or date.today().isoformat()

    print("=" * 72)
    print(f"DATASOURCE PROOF — {a.symbol} {a.tf}")
    print("=" * 72)

    # ---- 1. the incumbent population, exactly as it stands today -------------
    live = bars.load_sessions(a.symbol, a.tf, allow_fetch=False)
    print(f"\n[1] yfinance cache (data/daytrade/bars/)")
    print(f"    sessions={len(live)}  {live[0].day} .. {live[-1].day}")

    # ---- 2. the same loader, a different vendor behind the interface ---------
    src = datasource.AlpacaSource()
    print(f"\n[2] pulling via DataSource: {src.describe()}")
    print(f"    window {a.start} .. {end}")
    EXTENDED.mkdir(parents=True, exist_ok=True)
    orig_cache = bars.CACHE
    bars.CACHE = EXTENDED                      # separate namespace, live cache untouched
    try:
        res = bars.refresh_cache(a.symbol, a.tf, start=a.start, end=end, source=src)
        print(f"    added={res['added']} total_bars={res['total_bars']}")
        print(f"    provenance -> {Path(res['provenance']).name}")
        ext = bars.load_sessions(a.symbol, a.tf, allow_fetch=False)
    finally:
        bars.CACHE = orig_cache

    print(f"    sessions={len(ext)}  {ext[0].day} .. {ext[-1].day}")
    print(f"    >>> {len(ext)/max(len(live),1):.1f}x the population, same loader")

    # ---- 3. one real strategy, unmodified, on both --------------------------
    print(f"\n[3] ceiling.policy_ceiling (narrow space) — unmodified engine")
    out = {}
    for label, pop in (("yfinance_60d", live), ("alpaca_extended", ext)):
        r = _run(pop, label)
        out[label] = r
        print(f"\n  --- {label} ---")
        print(f"    entry days      : {r['n_entries']}")
        print(f"    best fixed      : {r['best_fixed_policy']}  "
              f"{r['Z_best_fixed_R']:+.2f}R  "
              f"({r['Z_R_per_trade']:+.4f} R/trade)")
        print(f"    oracle W        : {r['W_oracle_R']:+.2f}R")
        print(f"    prize           : {r['prize_R']:+.2f}R  "
              f"({r['prize_R_per_trade']:+.4f} R/trade)")
        print(f"    random baseline : {r['random_picker_R']:+.2f}R  "
              f"({r['random_picker_R_per_trade']:+.4f} R/trade)")
        print(f"    anti-oracle     : {r['anti_oracle_R']:+.2f}R")
        print(f"    live days       : {r['days_with_any_winning_config']}"
              f"/{r['n_entries']}")

    # ---- 4. did the answer change? ------------------------------------------
    y, x = out["yfinance_60d"], out["alpaca_extended"]
    print("\n" + "=" * 72)
    print("VERDICT")
    print("=" * 72)
    same = y["best_fixed_policy"] == x["best_fixed_policy"]
    print(f"  best fixed policy: {y['best_fixed_policy']} -> "
          f"{x['best_fixed_policy']}  {'(UNCHANGED)' if same else '(CHANGED)'}")
    yr, xr = y["Z_R_per_trade"], x["Z_R_per_trade"]
    print(f"  R per entry day  : {yr:+.4f} -> {xr:+.4f}   delta {xr - yr:+.4f}")
    print(f"  prize R/trade    : {y['prize_R_per_trade']:+.4f} -> "
          f"{x['prize_R_per_trade']:+.4f}")
    print(f"  sample           : {y['n_entries']} -> {x['n_entries']} entry days")
    if not same:
        print("\n  The 60-day window was choosing a different policy than the "
              "full history does.\n  That is the cost of the yfinance cap, "
              "measured.")

    dest = ROOT / "data" / "daytrade" / "datasource_proof.json"
    dest.write_text(json.dumps(
        {"symbol": a.symbol, "tf": a.tf, "window": [a.start, end],
         "source": src.describe(),
         "populations": {k: {kk: v[kk] for kk in (
             "n_entries", "best_fixed_policy", "Z_best_fixed_R", "Z_R_per_trade",
             "W_oracle_R", "prize_R", "prize_R_per_trade", "random_picker_R",
             "random_picker_R_per_trade", "anti_oracle_R",
             "days_with_any_winning_config", "per_config_total_R",
             "oracle_policy_hist")}
                         for k, v in out.items()}}, indent=1))
    print(f"\n  written: {dest.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
