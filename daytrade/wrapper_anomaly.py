#!/usr/bin/env python3
"""THE WRAPPER ANOMALY — a perihelion test on our own ontology.

Newton predicted Mercury's orbit and missed by 43 arcseconds per century.
The value was never in the prediction being right; it was that the residual
was PRECISE, STABLE, and unexplainable by the existing theory — which is what
forced a new one.

Our theory says asset class determines exit behaviour. But SPY and ES=F are
the same market in two wrappers (delta-correlation 0.93, 100% direction
agreement), so the theory predicts IDENTICAL exit quality. Measured:
CASH_INDEX 0.84, FUTURES 0.43. A 2x discrepancy where the theory demands
zero.

Either the class carving is confounded (CL=F is in FUTURES and is not an
index at all), or the same underlying genuinely behaves differently through
different wrappers — which would mean "asset class" is the wrong joint and
something else, most likely SESSION STRUCTURE, is the right one.

This measures the residual PAIRWISE on the same underlying and the same day,
where the theory has nowhere to hide. Read-only.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bars import load_sessions, BarDataError                       # noqa: E402
from splits import tune_sessions                                   # noqa: E402
from ceiling import find_entry, simulate, wide_space               # noqa: E402
import frozen_policy                                               # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "daytrade" / "wrapper_anomaly.json"
N_PERM = 2000
SEED = 43        # the arcseconds

# Same underlying, two wrappers. The theory predicts no difference.
TWINS = [("SPY", "ES=F", "S&P 500"), ("QQQ", "NQ=F", "Nasdaq 100"),
         ("IWM", "RTY=F", "Russell 2000")]


def per_session(sym: str, cls: str) -> dict:
    """session -> the facts, under the PINNED policy (SF-FROZEN-001)."""
    try:
        sessions = tune_sessions(load_sessions(sym, "5m", allow_fetch=False))
    except BarDataError as e:
        print(f"  !! {sym}: {e} — excluded loudly")
        return {}
    grid = list(wide_space().values())
    pol = frozen_policy.policy(cls)
    out = {}
    for s in sessions:
        e = find_entry(s)
        if e is None:
            out[str(s.day)] = {"entered": False}
            continue
        realized = simulate(s, e, dict(pol))
        oracle = max(simulate(s, e, dict(c)) for c in grid)
        out[str(s.day)] = {
            "entered": True, "entry_et": e.ts[11:16],
            "direction": e.direction, "risk_pct": e.risk / e.entry * 100,
            "realized_r": realized, "oracle_r": oracle,
            "efficiency": (realized / oracle) if oracle > 0 else None,
            "winnable": oracle > 0}
    return out


def main() -> int:
    import random
    rng = random.Random(SEED)
    results, all_pairs = [], []

    for cash, fut, name in TWINS:
        a = per_session(cash, "CASH_INDEX")
        b = per_session(fut, "FUTURES")
        both = sorted(set(a) & set(b))
        if not both:
            continue

        # Where the theory has nowhere to hide: same day, same underlying.
        traded_both = [d for d in both if a[d]["entered"] and b[d]["entered"]]
        entry_disagree = [d for d in both
                          if a[d]["entered"] != b[d]["entered"]]
        dir_disagree = [d for d in traded_both
                        if a[d]["direction"] != b[d]["direction"]]
        time_delta = [
            (int(b[d]["entry_et"][:2]) * 60 + int(b[d]["entry_et"][3:]))
            - (int(a[d]["entry_et"][:2]) * 60 + int(a[d]["entry_et"][3:]))
            for d in traded_both]
        r_delta = [b[d]["realized_r"] - a[d]["realized_r"] for d in traded_both]
        eff_pairs = [(a[d]["efficiency"], b[d]["efficiency"])
                     for d in traded_both
                     if a[d]["efficiency"] is not None
                     and b[d]["efficiency"] is not None]
        eff_delta = [y - x for x, y in eff_pairs]
        winnable_a = sum(1 for d in traded_both if a[d]["winnable"])
        winnable_b = sum(1 for d in traded_both if b[d]["winnable"])

        # paired sign-flip null: if the wrapper carries no information, the
        # sign of each day's delta is exchangeable
        obs = statistics.mean(eff_delta) if eff_delta else 0.0
        hits = 0
        for _ in range(N_PERM):
            flipped = [d if rng.random() < 0.5 else -d for d in eff_delta]
            if abs(statistics.mean(flipped)) >= abs(obs):
                hits += 1
        p = (hits + 1) / (N_PERM + 1) if eff_delta else 1.0

        all_pairs += eff_delta
        results.append({
            "underlying": name, "cash": cash, "futures": fut,
            "n_common_sessions": len(both), "n_traded_both": len(traded_both),
            "entry_disagreements": len(entry_disagree),
            "direction_disagreements": len(dir_disagree),
            "median_entry_time_delta_min": (statistics.median(time_delta)
                                            if time_delta else None),
            "mean_realized_delta_r": (round(statistics.mean(r_delta), 4)
                                      if r_delta else None),
            "mean_efficiency_delta": round(obs, 4),
            "n_efficiency_pairs": len(eff_delta),
            "p_paired_signflip": round(p, 4),
            "winnable_cash": winnable_a, "winnable_futures": winnable_b,
        })
        print(f"\n  {name}: {cash} vs {fut}")
        print(f"    common sessions {len(both)}, both traded {len(traded_both)}")
        print(f"    entry DISAGREEMENTS (one traded, other did not): "
              f"{len(entry_disagree)}")
        print(f"    direction disagreements: {len(dir_disagree)}")
        print(f"    median entry-time delta: {statistics.median(time_delta) if time_delta else None} min")
        print(f"    winnable days: cash {winnable_a} vs futures {winnable_b}")
        print(f"    mean efficiency delta (fut - cash): {obs:+.4f}  "
              f"p={p:.4f}")

    pooled = statistics.mean(all_pairs) if all_pairs else None
    hits = 0
    for _ in range(N_PERM):
        flipped = [d if rng.random() < 0.5 else -d for d in all_pairs]
        if abs(statistics.mean(flipped)) >= abs(pooled or 0):
            hits += 1
    p_pooled = (hits + 1) / (N_PERM + 1) if all_pairs else 1.0

    verdict = ("RESIDUAL_CONFIRMED" if p_pooled < 0.05 else
               "NO_RESIDUAL" if all_pairs else "NO_DATA")
    print(f"\n  POOLED over {len(all_pairs)} same-underlying same-day pairs:")
    print(f"    mean efficiency delta {pooled:+.4f}   p={p_pooled:.4f}")
    print(f"    VERDICT: {verdict}")
    if verdict == "NO_RESIDUAL":
        print("    -> the 0.84 vs 0.43 class gap is NOT the same market behaving\n"
              "       differently through two wrappers. It is composition: the\n"
              "       FUTURES class contains CL=F, which is not an index at all.\n"
              "       The anomaly dissolves into a confounded carving.")

    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question": "same underlying, two wrappers — does exit quality differ?",
        "theory_predicts": "no difference (delta-correlation 0.93)",
        "compared_against": frozen_policy.CHECKPOINT_ID,
        "pooled_mean_efficiency_delta": pooled,
        "p_pooled": p_pooled, "verdict": verdict, "twins": results,
    }, indent=1))
    print(f"  report: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
