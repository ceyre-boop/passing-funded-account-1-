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
import bars as bars_mod                                            # noqa: E402
from bars import load_sessions, BarDataError                       # noqa: E402
from splits import tune_sessions, TUNE_END                         # noqa: E402
from ceiling import (find_entry, simulate, wide_space, narrow_space,  # noqa: E402
                     COST_PER_SHARE, OR_START, OR_END, TRIGGER_END, TP2_R)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "daytrade" / "stockfish_tune_report.json"

# Widened 2026-08-17 on Colin's call: a NARROW universe of related pairs —
# the equity-index complex (futures + cash ETFs), crude, and NVDA as the
# original single-name. YM=F / GC=F / SI=F are deliberately absent: each has
# a vendor gap inside an RTH session and bars.py raises on gaps rather than
# repairing them (metals RTH 5m liquidity is too thin for this cache).
BASKET = ("NVDA", "SPY", "QQQ", "DIA", "IWM",
          "ES=F", "NQ=F", "RTY=F", "CL=F")
# Prior 10-single-name basket (first furnace run, KEEP_SHIPPED verdict):
# NVDA SPY QQQ AMD TSLA META MSFT AAPL AMZN GOOGL — report archived in git.

MIN_ENTRIES = 100                 # R1
WORST_TRADE_FLOOR = -2.0          # R3
BEAT_SHIPPED_BY = 0.05            # R/trade margin for a CHANGE verdict

# --- PER-ASSET-CLASS SPLIT (registered 2026-08-17, before the first
# per-class run; prompted by the two-universe finding that exit style is
# universe-dependent). Same gates R2-R4 applied WITHIN the class; R1 floor
# per class is 60 (smaller than the global 100 because a class is one third
# of the basket — registered here, not tuned after reading). One additional
# INFORMATIONAL (non-gating) readout: the winner's mean on a date-median
# half-split of its class, to expose configs that won on one fortnight.
CLASS_MIN_ENTRIES = 60
CLASSES = {
    "SINGLE_NAME": ("NVDA", "AMD", "TSLA", "META", "MSFT", "AAPL",
                    "AMZN", "GOOGL"),
    "CASH_INDEX": ("SPY", "QQQ", "DIA", "IWM"),
    "FUTURES": ("ES=F", "NQ=F", "RTY=F", "CL=F"),
}
UNION_BASKET = tuple(dict.fromkeys(
    s for syms in CLASSES.values() for s in syms))


def collect_entries(symbols: list[str], cache: Path | None = None) -> list[tuple]:
    """(symbol, session, entry) for every tune-split session with a valid
    OR-break entry. Sealed sessions are excluded by construction — this
    function never touches splits.sealed_sessions.

    `cache` redirects bars.CACHE for the duration and is ALWAYS restored,
    same discipline as oracle_audit.py's `_collect` — a faulted module
    global outliving this call would silently repoint every later reader in
    the process at the wrong parquet tree."""
    if cache is not None and not any(cache.glob("*_5m.parquet")):
        raise BarDataError(
            f"--cache {cache} holds no *_5m.parquet. Refusing to fall back to "
            f"{bars_mod.CACHE} — a silent fallback would label a run with one "
            f"population's name and another's data.")
    orig = bars_mod.CACHE
    if cache is not None:
        bars_mod.CACHE = cache
    try:
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
    finally:
        bars_mod.CACHE = orig


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


def null_leak_k(wide_rows: dict[str, list[tuple]], best_shipped_mean: float,
                n_draws: int = 200, seed: int = 26) -> dict:
    """The K=len(wide_rows) equivalent of oracle_audit.py's Move 2 null oracle.

    Same convention, reused rather than reinvented (spec 040 / oracle_audit.py
    docstring Move 2): each config's R column permuted independently across
    entries (destroys day<->config alignment, preserves marginals), take the
    per-entry max across the permuted columns, average — that is what a
    no-skill sweep of this width would have produced. null_leak = that value
    minus the best FIXED (shipped) policy's mean, so it lives on the same
    scale as apply_rule()'s margin_R_per_trade and can be compared directly.

    This null does NOT gate the pre-registered rule (R1-R4 + BEAT_SHIPPED_BY
    are untouched) — it is reported beside the winner's margin so a margin
    that is smaller than the null premium can be recognised as having
    demonstrated nothing, per specs/040's finding that this premium does not
    shrink with n; it is a property of the max-over-K statistic itself.
    """
    import numpy as np
    names = list(wide_rows)
    R = np.array([[r for _, _, r in wide_rows[name]] for name in names]).T
    k = R.shape[1]
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(n_draws):
        P = np.column_stack([rng.permutation(R[:, j]) for j in range(k)])
        draws.append(float(P.max(axis=1).mean()))
    null_max_mean = float(np.mean(draws))
    null_leak = null_max_mean - best_shipped_mean
    return {"k": k, "n_draws": n_draws, "seed": seed,
            "null_max_mean_R": round(null_max_mean, 4),
            "best_shipped_mean_R": round(best_shipped_mean, 4),
            "null_leak_R": round(null_leak, 4),
            "convention": "oracle_audit.py Move 2 (per-column independent "
                          "permutation, mean of per-entry max)"}


def slice_rows(rows_by_cfg: dict[str, list[tuple]],
               symbols: tuple) -> dict[str, list[tuple]]:
    """Slice one sweep's per-entry rows down to a symbol subset — the whole
    reason per-class analysis costs zero extra simulations."""
    want = set(symbols)
    return {n: [r for r in rows if r[0] in want]
            for n, rows in rows_by_cfg.items()}


def half_split_means(rows: list[tuple]) -> tuple[float, float] | None:
    """Informational stability: mean R in the older vs newer date half."""
    if len(rows) < 8:
        return None
    days = sorted({d for _, d, _ in rows})
    cut = days[len(days) // 2]
    a = [r for _, d, r in rows if d <= cut]
    b = [r for _, d, r in rows if d > cut]
    if not a or not b:
        return None
    return (round(sum(a) / len(a), 4), round(sum(b) / len(b), 4))


def class_report(cls: str, wide_rows: dict, shipped_rows: dict,
                 top: int) -> dict:
    rows = {n: r for n, r in wide_rows.items() if r}
    if not rows or not any(shipped_rows.values()):
        return {"class": cls, "verdict": "NO_ENTRIES"}
    scores = {n: score_config(r) for n, r in rows.items()}
    shipped = {n: score_config(r) for n, r in shipped_rows.items() if r}

    n_total = next(iter(scores.values()))["n"]
    med_of_meds = statistics.median(s["median_R"] for s in scores.values())
    eligible = {n: s for n, s in scores.items()
                if s["positive_symbols"] >= s["n_symbols"] / 2
                and s["worst_R"] >= WORST_TRADE_FLOOR
                and s["median_R"] > med_of_meds}
    best_shipped = max(shipped, key=lambda n: shipped[n]["mean_R"])

    if n_total < CLASS_MIN_ENTRIES:
        verdict, winner, margin = "INSUFFICIENT_SAMPLE", None, None
    elif not eligible:
        verdict, winner, margin = "NO_ELIGIBLE_CONFIG", None, None
    else:
        winner = max(eligible, key=lambda n: scores[n]["mean_R"])
        margin = round(scores[winner]["mean_R"]
                       - shipped[best_shipped]["mean_R"], 4)
        verdict = ("CANDIDATE_CONFIG" if margin >= BEAT_SHIPPED_BY
                   else "KEEP_SHIPPED")

    print(f"\n  ── {cls} ── {n_total} entries · best shipped {best_shipped} "
          f"{shipped[best_shipped]['mean_R']:+.4f} R/trade")
    for n, s in sorted(scores.items(), key=lambda kv: -kv[1]["mean_R"])[:top]:
        ok = n in eligible
        hs = half_split_means(rows[n])
        hs_s = f"halves {hs[0]:+.3f}/{hs[1]:+.3f}" if hs else ""
        print(f"    {n:34s} mean {s['mean_R']:+.4f}  med {s['median_R']:+.4f}  "
              f"worst {s['worst_R']:+.2f}  win {s['win_rate']:.0%}  "
              f"{'PASS' if ok else 'gated'}  {hs_s}")
    print(f"    VERDICT: {verdict}"
          + (f" — {winner} beats {best_shipped} by {margin:+.4f} R/trade"
             if winner else ""))

    out = {"class": cls, "n_entries": n_total, "verdict": verdict,
           "best_shipped": best_shipped,
           "best_shipped_score": shipped[best_shipped],
           "shipped": shipped}
    if winner:
        out.update({"winner": winner, "winner_score": scores[winner],
                    "margin_R_per_trade": margin,
                    "winner_half_split": half_split_means(rows[winner])})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Stockfish exit-config furnace (tune split only)")
    ap.add_argument("--symbols", default=",".join(UNION_BASKET))
    ap.add_argument("--no-classes", action="store_true",
                    help="skip the per-class slice (whole-basket ruling only)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--cache", default=None,
                    help="bar cache dir; default: bars.CACHE (original behaviour)")
    ap.add_argument("--out", default=None,
                    help="output json; default: data/daytrade/stockfish_tune_report.json "
                         "— pass a NEW path to avoid overwriting the pinned report "
                         "(specs/033 pinned-opponent discipline)")
    ap.add_argument("--null-draws", type=int, default=200,
                    help="permutation draws for the K=n_configs null-oracle "
                         "leak, reusing oracle_audit.py's Move 2 convention")
    a = ap.parse_args(argv)
    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    cache = Path(a.cache).resolve() if a.cache else None
    out = Path(a.out).resolve() if a.out else OUT

    print(f"  furnace: OR({OR_START}-{OR_END}) break by {TRIGGER_END}, TP2 {TP2_R}R, "
          f"cost ${COST_PER_SHARE}/sh · TUNE split only (<= {TUNE_END})")
    entries = collect_entries(symbols, cache=cache)
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

    # K=n_configs null-oracle leak (oracle_audit.py Move 2 convention, reused
    # not reinvented) — a max-over-K statistic carries a structural selection
    # premium that does NOT shrink with n (specs/040). Reported beside the
    # winner's margin so a margin smaller than this premium is recognisable
    # as noise, not signal. best_shipped denominator matches apply_rule()'s
    # margin so the two numbers live on the same scale.
    best_shipped_name = max(shipped, key=lambda n: shipped[n]["mean_R"])
    null = null_leak_k(wide_rows, shipped[best_shipped_name]["mean_R"],
                       n_draws=a.null_draws)
    clears_null = ruling.get("margin_R_per_trade") is not None and \
        ruling["margin_R_per_trade"] > null["null_leak_R"]
    print(f"  K={null['k']} null-oracle leak ({null['n_draws']} perms, "
          f"vs {best_shipped_name}): {null['null_leak_R']:+.4f} R/trade")
    if ruling.get("margin_R_per_trade") is not None:
        print(f"  winner margin {ruling['margin_R_per_trade']:+.4f} R/trade "
              f"{'CLEARS' if clears_null else 'does NOT clear'} the K={null['k']} "
              f"null premium")
    else:
        clears_null = None

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

    print(f"\n  WHOLE-BASKET VERDICT: {ruling['verdict']}")
    print(f"  {ruling['detail']}")

    # Per-class slice — zero extra simulations; the same rows, resliced.
    class_rulings = []
    if not a.no_classes:
        for cls, syms in CLASSES.items():
            class_rulings.append(class_report(
                cls, slice_rows(wide_rows, syms),
                slice_rows(shipped_rows, syms), top=5))

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tune_end": str(TUNE_END), "symbols": symbols,
        "cache": str(cache.relative_to(ROOT)) if cache else "default",
        "n_entries": len(entries), "n_configs": len(space),
        "selection_rule": {"min_entries": MIN_ENTRIES,
                           "worst_trade_floor_R": WORST_TRADE_FLOOR,
                           "beat_shipped_by_R": BEAT_SHIPPED_BY,
                           "registered": "2026-08-17, before first basket run"},
        "shipped": shipped, "ruling": ruling,
        "null_oracle_k": null,
        "winner_margin_clears_null": clears_null,
        "class_rulings": class_rulings,
        "class_rule": {"min_entries": CLASS_MIN_ENTRIES,
                       "registered": "2026-08-17, before first per-class run"},
        "configs": {n: scores[n] for n in
                    sorted(scores, key=lambda n: -scores[n]["mean_R"])},
    }, indent=1))
    print(f"  report: {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
