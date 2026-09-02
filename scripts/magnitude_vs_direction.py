#!/usr/bin/env python3
"""scripts/magnitude_vs_direction.py — does the one surviving positive finding hold here?

THE FINDING UNDER TEST
    The event studies produced exactly one asymmetry that survived its own
    detection floor: MAGNITUDE is measurable and DIRECTION is not. Four unsigned
    studies cleared their floor (0.22x-0.48x); all three directional ones failed
    (2.37x, 4.08x, 29.27x).

    Every model built since has tried to predict DIRECTION, and every one has
    returned null. This asks whether the same 13 features that cannot predict
    which way the market goes can predict HOW FAR it goes.

WHY IT MATTERS IF TRUE
    A direction-free edge is not tradable with a directional instrument, which
    is all this repo has ever used. It would point at straddles, strangles, or
    anything whose payoff depends on distance rather than sign -- a class this
    project has never touched.

WHY IT MATTERS IF FALSE
    Then the magnitude finding does not survive into this feature set, and the
    last positive result in the repo is confined to the scheduled-event windows
    it was measured in. That closes it too, and closing it is worth as much as
    confirming it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "gate"), str(ROOT / "daytrade")]

from body.features import FEATURE_NAMES  # noqa: E402
from discovery import Finding  # noqa: E402

EXP = ROOT / "data" / "daytrade" / "experience.npz"


def report(label, X, y, names, n_sessions):
    """Strongest single-feature correlation, with the floor beside it."""
    best_c, best_n = 0.0, ""
    for i, n in enumerate(names):
        c = np.corrcoef(X[:, i], y)[0, 1]
        if abs(c) > abs(best_c):
            best_c, best_n = c, n
    # n is SESSIONS, not bars: bars within a day are one bet.
    f = Finding(name=label, observed_effect=abs(best_c), per_unit_sd=1.0,
                n_units=n_sessions, unit="sessions", source="experience.npz")
    print(f"  {label:<34}{best_c:>+9.4f}  ({best_n:<16}) "
          f"floor {f.mde:.4f}  {f.ratio:>6.2f}x  {f.verdict}")
    return abs(best_c), f


def main() -> int:
    d = np.load(EXP)
    X, rl, rs, day = d["X"], d["r_long"], d["r_short"], d["day"]
    n_sess = int(d["n_sessions"])
    print("MAGNITUDE vs DIRECTION — does the one surviving finding hold here?\n")
    print(f"  {X.shape[0]} decision points over {n_sess} sessions "
          f"(sealed holdout: {int(d['n_sealed'])} sessions, untouched)\n")
    print(f"  {'target':<34}{'best |corr|':>9}  {'feature':<19}"
          f"{'floor':>11}{'ratio':>8}  verdict")

    signed_l, _ = report("DIRECTION  r_long (signed)", X, rl, FEATURE_NAMES, n_sess)
    signed_s, _ = report("DIRECTION  r_short (signed)", X, rs, FEATURE_NAMES, n_sess)
    best_side = np.maximum(rl, rs)
    mag, f_mag = report("MAGNITUDE  max(long, short)", X, best_side, FEATURE_NAMES, n_sess)
    spread, _ = report("MAGNITUDE  |long - short|", X, np.abs(rl - rs), FEATURE_NAMES, n_sess)

    print()
    best_dir = max(signed_l, signed_s)
    best_mag = max(mag, spread)
    print(f"  best DIRECTIONAL predictability: {best_dir:.4f}")
    print(f"  best MAGNITUDE   predictability: {best_mag:.4f}")
    print(f"  magnitude / direction ratio:     {best_mag / best_dir:.2f}x")

    print()
    if f_mag.proceeds or best_mag > best_dir * 2:
        print("  ASYMMETRY PRESENT. Magnitude is more predictable than direction in")
        print("  this feature set, consistent with the event studies. That points at")
        print("  direction-free instruments — a class this repo has never used.")
        print("  NOT an edge yet: it must clear its floor on the SEALED sessions,")
        print("  pre-registered, before anything is built on it.")
    else:
        print("  NO ASYMMETRY THAT SURVIVES THE FLOOR. Magnitude is no more")
        print("  predictable than direction here, and neither clears its own")
        print("  detection floor at n = sessions. The event studies' magnitude")
        print("  finding does not carry into these features — it stays confined to")
        print("  the scheduled-event windows where it was measured.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
