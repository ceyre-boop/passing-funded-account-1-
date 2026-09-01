#!/usr/bin/env python3
"""scripts/null_diagnostic.py — is the entry study's null misspecified, and which way?

THE CLAIM UNDER TEST
    A permutation test is exactly level-alpha only under exchangeability. On an
    autocorrelated series the conventional permutation null is misspecified. The
    review argued this could make "best real cell below the null" a bug
    signature rather than a null result.

WHAT THE ENTRY STUDY ACTUALLY USED
    Not a naive permutation. `az/prereg.py::max_stat_null` is a CIRCULAR
    DAY-SHIFT: it rotates the outcome vector against the cell-assignment vector
    by whole days, which preserves within-day structure and cross-cell
    correlation. Both are the things a naive shuffle destroys.

    What it does NOT preserve is DAY-TO-DAY dependence -- runs of consecutive
    days sharing a regime. So the open question is narrower than the review put
    it, and it has a direction: is day-shift conservative or anti-conservative
    against a null that also preserves across-day dependence?

WHY SYNTHETIC
    The entry study's per-candidate arrays were never persisted -- only the
    summary. So the null cannot be re-derived from committed data, which is the
    same reproducibility gap already closed for spy_macro_decay and NOT closed
    here. This measures the DIRECTION and SIZE of the misspecification on data
    with known autocorrelation; applying it to the real study requires
    regenerating 104,680 candidates first, and that is a named follow-up rather
    than something this script pretends to have done.

PRE-SPECIFIED THRESHOLD-CROSSING RULE (declared before the numbers below)
    The entry closure is reconsidered ONLY IF a correctly-specified null's p95
    falls BELOW the observed best cell of +0.0956. If the correctly-specified
    null is HIGHER than the day-shift null, the observed result sits even
    further below it and the closure is STRENGTHENED, not weakened.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "az"))

OBSERVED_BEST_CELL = 0.0956      # SPY, from artifacts/RESULT_2026-08-30.md
DAYSHIFT_NULL_P95 = 0.4310       # the null on record

N_DAYS, N_SLOTS, N_CELLS, DRAWS = 400, 24, 60, 600
RHO = 0.85                        # day-level autocorrelation


def synth(rng):
    """Outcomes with a persistent DAY-LEVEL component: consecutive days share a
    regime, which is exactly the dependence a day-shift cannot preserve."""
    day_effect, x = np.empty(N_DAYS), 0.0
    for d in range(N_DAYS):
        x = RHO * x + rng.normal()
        day_effect[d] = x
    out = (day_effect[:, None] * 0.6 + rng.normal(size=(N_DAYS, N_SLOTS)))
    cells = rng.integers(0, N_CELLS, size=(N_DAYS, N_SLOTS))
    return cells, out


def best_cell(cells, outs):
    flat_c, flat_o = cells.ravel(), outs.ravel()
    uniq, inv = np.unique(flat_c, return_inverse=True)
    sums = np.bincount(inv, weights=flat_o, minlength=len(uniq))
    cnts = np.bincount(inv, minlength=len(uniq))
    ok = cnts >= 30
    return float(np.max(sums[ok] / cnts[ok])) if ok.any() else float("nan")


def null_dayshift(cells, outs, rng):
    k = rng.integers(1, N_DAYS)
    return best_cell(cells, np.roll(outs, k, axis=0))


def null_naive(cells, outs, rng):
    flat = outs.ravel().copy()
    rng.shuffle(flat)
    return best_cell(cells, flat.reshape(outs.shape))


def null_stationary(cells, outs, rng):
    """Resample whole days in geometric blocks (Politis & Romano over the day
    axis) — preserves runs of consecutive days, which day-shift does not."""
    mean_block = max(1.0, N_DAYS ** (1 / 3))
    p, idx, i = 1.0 / mean_block, [], int(rng.integers(N_DAYS))
    while len(idx) < N_DAYS:
        idx.append(i)
        i = int(rng.integers(N_DAYS)) if rng.random() < p else (i + 1) % N_DAYS
    return best_cell(cells, outs[idx])


def main() -> int:
    rng = np.random.default_rng(20260901)
    cells, outs = synth(rng)
    print(__doc__.split("PRE-SPECIFIED")[0].strip()[:0] or "", end="")
    print("NULL DIAGNOSTIC — which null, and which way is it wrong?\n")
    print(f"  synthetic: {N_DAYS} days x {N_SLOTS} slots, {N_CELLS} cells, "
          f"day-level AR({RHO})\n")
    res = {}
    for name, fn in (("naive permutation", null_naive),
                     ("circular day-shift  (what the study used)", null_dayshift),
                     ("stationary block over days", null_stationary)):
        draws = np.array([fn(cells, outs, rng) for _ in range(DRAWS)])
        res[name] = np.nanpercentile(draws, 95)
        print(f"  {name:<42} p95 = {res[name]:+.4f}")

    naive = res["naive permutation"]
    shift = res["circular day-shift  (what the study used)"]
    stat = res["stationary block over days"]
    print(f"\n  naive vs stationary : {naive - stat:+.4f} "
          f"({'naive is LOWER — anti-conservative' if naive < stat else 'naive is higher'})")
    print(f"  day-shift vs stationary: {shift - stat:+.4f} "
          f"({'day-shift is LOWER — anti-conservative' if shift < stat else 'day-shift is HIGHER — conservative'})")

    print("\n  THRESHOLD-CROSSING RULE, pre-specified:")
    print(f"    reconsider the entry closure only if a correctly-specified null's")
    print(f"    p95 falls BELOW the observed best cell (+{OBSERVED_BEST_CELL}).")
    direction = ("HIGHER" if stat > shift else "LOWER")
    print(f"\n  RESULT: the dependence-preserving null is {direction} than day-shift.")
    if stat > shift:
        print(f"    The study's null ({DAYSHIFT_NULL_P95:+.4f}) is therefore if anything")
        print(f"    TOO LOW, and the observed +{OBSERVED_BEST_CELL} sits even further")
        print( "    below a correctly-specified one. The closure is STRENGTHENED.")
        print( "    Threshold not crossed. No reconsideration.")
    else:
        print( "    Day-shift may be conservative; the real arrays must be")
        print( "    regenerated to settle it. Threshold-crossing undetermined.")
    print("\n  CAVEAT, load-bearing: this is synthetic. The entry study's 104,680")
    print("  candidate arrays were never persisted, so its null cannot be re-derived")
    print("  from committed data. That reproducibility gap is real and is NOT closed")
    print("  by this script.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
