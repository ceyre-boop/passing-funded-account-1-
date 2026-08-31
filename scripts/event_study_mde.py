#!/usr/bin/env python3
"""scripts/event_study_mde.py — MDE-at-discovery across the ten event studies.

THE UNIT PROBLEM, AND WHY IT DISSOLVES
    The studies report effects in incompatible units: fractional returns, an
    expansion ratio, a whipsaw ratio, basis points. Averaging or ranking across
    those units would be meaningless, so this script never does.

    It does not have to. The MDE test is unit-free. An effect clears its own
    detection floor iff

        |effect| >= MDE = (Z_alpha + Z_power) * SE          [mechanisms.mde]
        <=>  |effect| / SE  >=  2.487
        <=>  |t|            >=  2.487

    so ratio = MDE / |effect| = 2.487 / |t|. Every study stores a welch_t, so
    every ratio below is computed without converting a single unit.

    Independently confirmed: spy_range_expansion stores its OWN precomputed
    mde_expansion_units (0.049239, from mechanisms.mde with pooled_sd=0.49798,
    n_effective=632.64). Its ratio by that route is 0.4734x; by 2.487/|t| it is
    0.4782x. Two independent methods, 1.0% apart.

WHY t AND NOT COHEN'S d
    Every study also stores cohens_d, and the two disagree -- on FOMC by 1.87x.
    Cohen's d divides by a POOLED sd; welch_t does not assume equal variance.
    Event days are more volatile than control days by construction, so the pooled
    sd understates the real SE and d overstates the effect, worst exactly where
    the event is most violent. The MDE must be computed in the metric of the test
    that was actually run, and these studies ran Welch. Using d would inflate
    every ratio toward "clears the floor".

TWO FLOORS, NEVER CONFLATED
    detection floor -- could the study see what it reported?     (this script)
    economic floor  -- is the effect big enough to be worth trading?
    spy_range_expansion clears the first and fails the second; its own summary
    records "SIGNIFICANT AND USELESS". Clearing detection is NECESSARY, NOT
    SUFFICIENT, and this script never reports it as a pass.
"""
from __future__ import annotations

import glob
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "daytrade"))
from mechanisms import Z_ALPHA, Z_POWER  # noqa: E402

CRIT = Z_ALPHA + Z_POWER          # 2.487 -- the same constant mechanisms.mde uses
ES = ROOT / "data" / "daytrade" / "event_study"
ABOVE, BELOW = "ABOVE FLOOR", "BELOW NOISE FLOOR"


def find_welch(obj, path=""):
    """Yield every (path, dict) carrying a welch_t. bracket_harvest buries its
    two arms' tests one level down, so a top-level-only read silently drops it."""
    if isinstance(obj, dict):
        if "welch_t" in obj and obj.get("welch_t") is not None:
            yield path, obj
        for k, v in obj.items():
            yield from find_welch(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from find_welch(v, f"{path}[{i}]")


def row(name, arm, n1, n2, t, d, label=""):
    r = CRIT / abs(t) if t else float("inf")
    return dict(name=name, arm=arm, n1=n1, n2=n2, t=t, d=d, ratio=r, label=label,
                verdict=ABOVE if r <= 1.0 else BELOW)


# The pre-registered test is the bare PRIMARY block. bracket_harvest has no bare
# PRIMARY -- its designated primary is the 08:30 arm. Everything else in these
# files is a robustness variant or is explicitly marked NO_INFERENTIAL_WEIGHT,
# and mixing those into the ranking is exactly the selection error the MDE rule
# exists to catch. One range_expansion sub-block is even named
# "CONFOUNDED_raw_range_never_primary".
PRIMARY_PATHS = {"PRIMARY", "PRIMARY_ARM_0830.event_vs_control_days_welch"}


def main() -> int:
    rows, secondary, exploratory, special = [], [], 0, []
    for f in sorted(glob.glob(str(ES / "*_summary.json"))):
        doc = json.load(open(f))
        name = os.path.basename(f).replace("_study_summary.json", "").replace("_event", "")
        if "by_release_horizon_metric" in doc:            # nvda: 72 tests
            special.append((name, doc)); continue
        for path, o in find_welch(doc):
            rec = row(name, path, o.get("event_n"), o.get("control_n"),
                      float(o["welch_t"]), o.get("cohens_d"), str(o.get("label") or ""))
            if path in PRIMARY_PATHS:
                rows.append(rec)
            elif "EXPLORATORY" in path.upper() or "NO_INFERENTIAL_WEIGHT" in path.upper():
                exploratory += 1
            else:
                secondary.append(rec)

    rows.sort(key=lambda r: r["ratio"])
    print("MDE-AT-DISCOVERY -- the ten pre-registered event studies")
    print(f"critical |t| = Z(0.95) + Z(0.80) = {Z_ALPHA} + {Z_POWER} = {CRIT:.3f}\n")
    print("An effect clears its own DETECTION floor iff |t| >= 2.487.")
    print("Unit-free: ratio = MDE/|effect| = 2.487/|t|. No unit conversion performed.\n")
    hdr = f"{'study':<24} {'n_ev':>6} {'n_ctl':>6} {'|t|':>7} {'ratio':>7} {'cohens_d':>9}  verdict"
    print(hdr); print("-" * len(hdr))
    for r in rows:
        dd = f"{r['d']:+.4f}" if r["d"] is not None else "       --"
        print(f"{r['name']:<24} {r['n1'] or 0:>6,} {r['n2'] or 0:>6,} {abs(r['t']):>7.3f} "
              f"{r['ratio']:>6.2f}x {dd:>9}  {r['verdict']}")
    n_above = sum(r["ratio"] <= 1 for r in rows)
    print(f"\n  {n_above} of {len(rows)} pre-registered primary tests clear their own detection floor.")

    print(f"\nNOT IN THE RANKING -- {len(secondary)} robustness variants, "
          f"{exploratory} tests marked NO_INFERENTIAL_WEIGHT / EXPLORATORY.")
    print("  Robustness variants of studies that already cleared (shown, not ranked):")
    for r in sorted(secondary, key=lambda r: r["ratio"]):
        print(f"    {r['name']:<22} {r['ratio']:>5.2f}x  |t|={abs(r['t']):>6.3f}  {r['label'][:46] or r['arm'][:46]}")

    # ---- nvda: the max of 72 is a selection statistic, not a test -------------
    for name, doc in special:
        tests = doc["by_release_horizon_metric"]
        k = len(tests)
        best = max(tests, key=lambda r: abs(r.get("welch_t") or 0.0))
        # a max-of-k needs a multiplicity-adjusted critical value, not 2.487
        alpha_adj = 0.05 / k
        z_adj = math.sqrt(2) * _erfinv(1 - 2 * alpha_adj)
        crit_adj = z_adj + Z_POWER
        t = abs(best["welch_t"])
        print(f"\n{name.upper()} -- reported separately: {k} tests, not one pre-registered effect")
        print(f"  best arm: {best.get('release_name')} / {best.get('metric')}  "
              f"n {best.get('event_n')}/{best.get('control_n')}  |t|={t:.3f}")
        print(f"  naive ratio 2.487/|t| = {CRIT/t:.2f}x  <-- WOULD READ AS A PASS, AND IS NOT ONE")
        print(f"  the max of {k} tests needs a multiplicity-adjusted floor: "
              f"|t| >= {z_adj:.2f} + {Z_POWER} = {crit_adj:.2f}")
        print(f"  adjusted ratio = {crit_adj/t:.2f}x -> {BELOW}")
        mt = doc.get("multiple_testing", {})
        print(f"  agrees with its own record: {mt.get('significant_raw_p_lt_0.05')} raw-significant "
              f"(vs {mt.get('expected_by_chance_at_alpha_0.05')} expected by chance), "
              f"{mt.get('significant_after_bh_correction')} surviving BH.")

    # ---- the second floor ----------------------------------------------------
    print("\nTHE SECOND FLOOR -- clearing detection is necessary, not sufficient")
    for f in sorted(glob.glob(str(ES / "*_summary.json"))):
        doc = json.load(open(f)); p = doc.get("PRIMARY") or {}
        if p.get("economic_floor") is not None:
            n = os.path.basename(f).replace("_study_summary.json", "")
            print(f"  {n}: economic_floor={p['economic_floor']}, "
                  f"clears_economic_floor={p.get('clears_economic_floor')}")
            print(f"    verdict on record: {p.get('verdict')}")
    return 0


def _erfinv(y: float) -> float:
    """Inverse error function (Newton on erf) -- avoids a scipy dependency."""
    x = 0.0
    for _ in range(60):
        err = math.erf(x) - y
        d = 2.0 / math.sqrt(math.pi) * math.exp(-x * x)
        x -= err / d
    return x


if __name__ == "__main__":
    sys.exit(main())
