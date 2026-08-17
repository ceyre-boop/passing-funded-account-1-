#!/usr/bin/env python3
"""STOCKFISH TUNE — the iron furnace for the mechanical exit engine.

Lineage: spec 008's ceiling machinery, repurposed from measurement to tuning.
Every decision function is IMPORTED from ceiling.py (entry rule, pessimistic
simulator, config grid) — rule 1, one implementation. What this file adds is
scale (a basket of liquid symbols, every tune-split session of each) and a
PRE-REGISTERED selection rule.

Discipline, restated where it is enforced:
  - TUNE SPLIT ONLY (splits.tune_sessions, TUNE_END 2026-07-06). The sealed
    holdout is never imported here; validating the winner on it is a separate,
    deliberate, one-shot act after the config is frozen and committed.
  - The selection rule below was written BEFORE the multi-symbol sweep was
    first run. Changing it after reading results is the laundering spec 008
    exists to prevent — re-register with a dated note, or don't.

PRE-REGISTERED SELECTION RULE (written 2026-08-17, before first basket run):
  Rank candidate configs by MEAN R/TRADE across all entries, subject to ALL of:
    R1. n_entries >= 100 total (else the run reports INSUFFICIENT_SAMPLE)
    R2. positive total R in >= half of the symbols traded (breadth — a config
        that only works on one name is a fit to that name)
    R3. worst single trade >= -2.0 R (tail control; the plan stop is -1R plus
        gap risk, so worse than -2R means the config defeats its own stop)
    R4. median R/trade > the all-config median-of-medians (not just mean-driven
        by a few outlier winners)
  The WINNER is the top-ranked config satisfying R1-R4. The three shipped
  policies (STATIC / TRAIL_WIDE / TRAIL_TIGHT) are always reported beside it —
  if the winner does not beat the best shipped policy by >= +0.05 R/trade,
  the verdict is KEEP_SHIPPED (a null result is a result).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bars import load_sessions, BarDataError                       # noqa: E402
from splits import tune_sessions, TUNE_END                         # noqa: E402
from ceiling import (find_entry, simulate, wide_space, narrow_space,  # noqa: E402
                     COST_PER_SHARE, OR_START, OR_END, TRIGGER_END, TP2_R)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "daytrade" / "stockfish_tune_report.json"

BASKET = ("NVDA", "SPY", "QQQ", "AMD", "TSLA", "META", "MSFT", "AAPL",
          "AMZN", "GOOGL")

MIN_ENTRIES = 100                 # R1
WORST_TRADE_FLOOR = -2.0          # R3
BEAT_SHIPPED_BY = 0.05            # R/trade margin for a CHANGE verdict


def collect_entries(symbols: list[str]) -> list[tuple]:
    """(symbol, session, entry) for every tune-split session with a valid
    OR-break entry. Sealed sessions are excluded by construction — this
    function never touches splits.sealed_sessions."""
    out = []
    for sym in symbols:
        try:
            sessions = load_sessions(sym, "5m", allow_fetch=False)
        except BarDataError as e:
            print(f"  !! {sym}: {e} — excluded from the basket, loudly")
            continue
        tune = tune_sessions(sessions)
        n = 0
        for s in tune:
            e = find_entry(s)
            if e is not None:
                out.append((sym, s, e))
                n += 1
        print(f"  {sym}: {len(sessions)} cached sessions, {len(tune)} tune, "
              f"{n} entries")
    return out


def _run_config(args):
    """One config over every entry. Top-level for ProcessPoolExecutor."""
    name, cfg, entries = args
    rows = [(sym, e.day, simulate(s, e, cfg)) for sym, s, e in entries]
    return name, rows


def sweep(entries: list[tuple], space: dict[str, dict],
          workers: int = 1) -> dict[str, list[tuple]]:
    jobs = [(name, cfg, entries) for name, cfg in space.items()]
    results: dict[str, list[tuple]] = {}
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for name, rows in ex.map(_run_config, jobs):
                results[name] = rows
    else:
        for job in jobs:
            name, rows = _run_config(job)
            results[name] = rows
    return results


def score_config(rows: list[tuple]) -> dict:
    rs = [r for _, _, r in rows]
    by_sym: dict[str, float] = defaultdict(float)
    for sym, _, r in rows:
        by_sym[sym] += r
    return {"n": len(rs), "total_R": round(sum(rs), 3),
            "mean_R": round(sum(rs) / len(rs), 4),
            "median_R": round(statistics.median(rs), 4),
            "worst_R": round(min(rs), 3), "best_R": round(max(rs), 3),
            "stdev_R": round(statistics.pstdev(rs), 4),
            "win_rate": round(sum(1 for r in rs if r > 0) / len(rs), 3),
            "positive_symbols": sum(1 for v in by_sym.values() if v > 0),
            "n_symbols": len(by_sym),
            "per_symbol_R": {k: round(v, 2) for k, v in sorted(by_sym.items())}}


def apply_rule(scores: dict[str, dict], shipped: dict[str, dict]) -> dict:
    """The pre-registered rule, exactly as written in the module docstring."""
    n_total = next(iter(scores.values()))["n"] if scores else 0
    if n_total < MIN_ENTRIES:
        return {"verdict": "INSUFFICIENT_SAMPLE",
                "detail": f"{n_total} entries < pre-registered floor {MIN_ENTRIES}"}

    med_of_meds = statistics.median(s["median_R"] for s in scores.values())
    eligible = {}
    rejected: dict[str, str] = {}
    for name, s in scores.items():
        if s["positive_symbols"] < s["n_symbols"] / 2:
            rejected[name] = "R2_breadth"
        elif s["worst_R"] < WORST_TRADE_FLOOR:
            rejected[name] = "R3_tail"
        elif s["median_R"] <= med_of_meds:
            rejected[name] = "R4_median"
        else:
            eligible[name] = s

    if not eligible:
        return {"verdict": "NO_ELIGIBLE_CONFIG",
                "detail": "every config failed R2/R3/R4",
                "rejected_counts": dict(
                    sorted(((v, list(rejected.values()).count(v))
                            for v in set(rejected.values()))))}

    winner = max(eligible, key=lambda n: scores[n]["mean_R"])
    best_shipped = max(shipped, key=lambda n: shipped[n]["mean_R"])
    margin = scores[winner]["mean_R"] - shipped[best_shipped]["mean_R"]

    if margin < BEAT_SHIPPED_BY:
        verdict = "KEEP_SHIPPED"
        detail = (f"winner {winner} beats best shipped ({best_shipped}) by only "
                  f"{margin:+.4f} R/trade < the pre-registered {BEAT_SHIPPED_BY} "
                  "margin — a null result is a result; keep the shipped policy")
    else:
        verdict = "CANDIDATE_CONFIG"
        detail = (f"{winner} clears every gate and beats {best_shipped} by "
                  f"{margin:+.4f} R/trade on the tune split. NEXT, in order: "
                  "freeze it, commit it, let it stand one commit, then the "
                  "one-shot sealed-split read (splits.sealed_sessions) — "
                  "no live change before that number exists")
    return {"verdict": verdict, "detail": detail, "winner": winner,
            "winner_score": scores[winner], "best_shipped": best_shipped,
            "best_shipped_score": shipped[best_shipped],
            "margin_R_per_trade": round(margin, 4),
            "n_eligible": len(eligible)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Stockfish exit-config furnace (tune split only)")
    ap.add_argument("--symbols", default=",".join(BASKET))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args(argv)
    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]

    print(f"  furnace: OR({OR_START}-{OR_END}) break by {TRIGGER_END}, TP2 {TP2_R}R, "
          f"cost ${COST_PER_SHARE}/sh · TUNE split only (<= {TUNE_END})")
    entries = collect_entries(symbols)
    print(f"  TOTAL: {len(entries)} entries across "
          f"{len({sym for sym, _, _ in entries})} symbols\n")

    space = wide_space()
    shipped_space = narrow_space()
    print(f"  sweeping {len(space)} wide configs + {len(shipped_space)} shipped "
          f"({a.workers} workers) …")
    t0 = datetime.now()
    wide_rows = sweep(entries, space, workers=a.workers)
    shipped_rows = sweep(entries, shipped_space, workers=1)
    dt = (datetime.now() - t0).total_seconds()

    scores = {n: score_config(r) for n, r in wide_rows.items()}
    shipped = {n: score_config(r) for n, r in shipped_rows.items()}
    ruling = apply_rule(scores, shipped)

    sims = (len(space) + len(shipped_space)) * len(entries)
    print(f"  {sims:,} simulations in {dt:.1f}s ({sims/dt:,.0f}/s)\n")

    print("  shipped policies:")
    for n, s in sorted(shipped.items(), key=lambda kv: -kv[1]["mean_R"]):
        print(f"    {n:12s} mean {s['mean_R']:+.4f}  med {s['median_R']:+.4f}  "
              f"worst {s['worst_R']:+.2f}  win {s['win_rate']:.0%}  "
              f"+syms {s['positive_symbols']}/{s['n_symbols']}")

    print(f"\n  top {a.top} of {len(scores)} configs by mean R/trade "
          f"(rule gates shown):")
    med_of_meds = statistics.median(s["median_R"] for s in scores.values())
    for n, s in sorted(scores.items(), key=lambda kv: -kv[1]["mean_R"])[:a.top]:
        gates = []
        if s["positive_symbols"] < s["n_symbols"] / 2: gates.append("R2✗")
        if s["worst_R"] < WORST_TRADE_FLOOR: gates.append("R3✗")
        if s["median_R"] <= med_of_meds: gates.append("R4✗")
        print(f"    {n:34s} mean {s['mean_R']:+.4f}  med {s['median_R']:+.4f}  "
              f"worst {s['worst_R']:+.2f}  win {s['win_rate']:.0%}  "
              f"+syms {s['positive_symbols']}/{s['n_symbols']}  "
              f"{' '.join(gates) or 'PASS'}")

    print(f"\n  VERDICT: {ruling['verdict']}")
    print(f"  {ruling['detail']}")

    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tune_end": str(TUNE_END), "symbols": symbols,
        "n_entries": len(entries), "n_configs": len(space),
        "selection_rule": {"min_entries": MIN_ENTRIES,
                           "worst_trade_floor_R": WORST_TRADE_FLOOR,
                           "beat_shipped_by_R": BEAT_SHIPPED_BY,
                           "registered": "2026-08-17, before first basket run"},
        "shipped": shipped, "ruling": ruling,
        "configs": {n: scores[n] for n in
                    sorted(scores, key=lambda n: -scores[n]["mean_R"])},
    }, indent=1))
    print(f"  report: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
