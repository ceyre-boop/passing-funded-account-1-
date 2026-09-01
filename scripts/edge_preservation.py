#!/usr/bin/env python3
"""scripts/edge_preservation.py — which exit PRESERVES an edge, given one exists?

WHY 0 OF 396 SAYS NOTHING ABOUT THIS
    Under a near-martingale, "exits redistribute R and cannot create it" is a
    theorem, not a finding -- it is Doob's optional stopping. So the sweep
    finding 0 of 396 exit configurations profitable on SPY is EXPECTED, and it
    is not identified: the entries carried no edge, and under negative-
    expectancy entries no exit configuration can be profitable, tautologically.

    That closure stands. It answers "can exits manufacture an edge" (no). It
    was never evidence about "which exit keeps one", because the sample had
    none to keep.

WHAT THIS MEASURES INSTEAD
    Inject a known drift, then ask each policy what fraction of the available
    edge it returns:

        preserved_edge = mean R under the policy / mean R holding to horizon

    Hold-to-horizon captures the full injected edge by construction, so it is
    the denominator. A policy at 1.0 keeps everything; below 1.0 it is paying
    for its protection; above 1.0 it is shaping the distribution favourably.

    This is a FORWARD-LOOKING column. It says nothing about whether an edge
    exists, and it must not be read as evidence that one does. It exists so
    that if one is ever found, the choice of exit is not made blind -- which is
    exactly the position the frozen defaults were fitted from.

DISCIPLINE
    Runs the REAL engine: daytrade/stockfish_exit.decide_exit over synthetic
    paths. No second implementation of the decision logic, and the frozen file
    is not touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "daytrade"))

from bars import Session  # noqa: E402
from ceiling import Entry, simulate  # noqa: E402
from stockfish_exit import INTENT, policy_params  # noqa: E402

N_PATHS, N_BARS, SIGMA = 4000, 78, 0.0018
DRIFTS = (0.0, 0.0002, 0.0005)          # per bar; 0.0 is the martingale control
ENTRY, K_STOP_ATR = 100.0, 1.0

N_EVAL = 1500        # paths actually simulated per policy

# The control tolerance is DERIVED, not chosen. |policy - hold| is a paired
# difference over the same paths, so its standard error is sd(diff)/sqrt(n).
# A fixed constant (an earlier version used 0.02) either rejects noise or
# accepts bias depending on how many paths happen to be run -- which makes the
# control's verdict a function of the sample size rather than of the harness.
CONTROL_SIGMAS = 3.0


def paths(rng, drift):
    """Ito correction is load-bearing. exp(cumsum(N(0, sigma))) is NOT a price
    martingale -- zero drift in LOG space is +sigma^2/2 in price space, and an
    earlier version of this control showed every policy 'beating' hold by ~0.16R
    under what was supposed to be a no-edge process. Subtracting sigma^2/2 makes
    drift=0 an actual martingale, which is the only way the control can work."""
    steps = rng.normal(drift - 0.5 * SIGMA ** 2, SIGMA, size=(N_PATHS, N_BARS))
    return ENTRY * np.exp(np.cumsum(steps, axis=1))


def as_session(path, day_idx):
    """Wrap a synthetic path as a real Session so the decision loop runs through
    the SANCTIONED simulator.

    RULE 1, "one implementation": an earlier version of this script drove
    decide_exit through its own per-bar loop and was correctly flagged by
    daytrade/test_one_implementation.py. That guard exists because a second loop
    produces numbers that are quietly incomparable to every other measurement in
    the repo -- which is exactly what a preservation column must not be. Routing
    through ceiling.simulate also means these figures carry the same pessimistic
    bar ordering and the same cost constant as the ceiling record."""
    idx = pd.date_range(f"2026-01-{day_idx % 28 + 1:02d} 09:30",
                        periods=len(path), freq="5min", tz="America/New_York")
    px = np.asarray(path, dtype=float)
    df = pd.DataFrame({"Open": px, "High": px * 1.0006, "Low": px * 0.9994,
                       "Close": px, "Volume": 1000.0}, index=idx)
    return Session(symbol="SYN", day=idx[0].date(), df=df)


def cfg_for(name):
    """INTENT -> the simulator's cfg vocabulary. Strict keys: simulate() refuses
    an unrecognised one rather than dropping it."""
    prm = policy_params(name)
    # simulate() reads every key explicitly, so all of them must be present --
    # a missing one is a KeyError rather than a silent default, which is the
    # same fail-loud discipline as its unknown-key refusal.
    return {"partial_frac": 0.5, "trail_mult": prm["trail_mult"],
            "be_arm_frac": prm["be_arm_frac"],
            "hold_past_tp2": prm["hold_past_tp2"],
            "flatten_et": None, "vol_k": None, "atr": None}


def entry_for(path, risk):
    return Entry(day="2026-01-01", ts="09:30", time_block="OPEN", direction=1,
                 entry=ENTRY, stop=ENTRY - risk, risk=risk,
                 tp1=ENTRY + risk, tp2=ENTRY + 3 * risk, trail_dist=0.5 * risk)


def main() -> int:
    rng = np.random.default_rng(20260901)
    risk = K_STOP_ATR * ENTRY * SIGMA * 8      # a plausible stop width
    # EVENT is excluded, and by the engine's own refusal rather than by taste:
    # policy_params("EVENT") raises without `flatten_at_et`, because EVENT means
    # "be out before a known catalyst" and a synthetic path has no catalyst. A
    # default clock here would be inventing the thing the policy exists to
    # respect. The refusal is correct; the exclusion is honest.
    names = ["DEFEND", "HARVEST", "RIDE", "SALVAGE"]

    print("EDGE PRESERVATION — which exit keeps an injected edge?\n")
    print("0/396 answered 'can exits create an edge' (no, and that is a theorem).")
    print("This answers 'which exit keeps one', which that sweep could not.\n")

    control_fail = []
    for drift in DRIFTS:
        P = paths(rng, drift)
        hold = np.array([(float(p[-1]) - ENTRY) / risk for p in P])
        tag = "MARTINGALE CONTROL" if drift == 0 else f"drift {drift:+.4f}/bar"
        print(f"  {tag}   hold-to-horizon mean R = {hold.mean():+.4f}")
        print(f"    {'policy':<10} {'mean R':>9} {'preserved':>11}  {'vs hold':>9}")
        for name in names:
            cfg = cfg_for(name)
            rs = np.array([simulate(as_session(p, i), entry_for(p, risk), cfg)
                           for i, p in enumerate(P[:N_EVAL])])
            h = hold[:N_EVAL]
            # A ratio against a near-zero denominator is noise with a decimal
            # point. Under the control the denominator IS near zero by design,
            # so the ratio is suppressed rather than printed as a number.
            if abs(h.mean()) < 0.02:
                pres = "     n/a"
            else:
                pres = f"{rs.mean() / h.mean():>9.2f}x"
            delta = rs.mean() - h.mean()
            print(f"    {name:<10} {rs.mean():>+9.4f} {pres:>11} {delta:>+9.4f}")
            # Optional stopping is a theorem: under drift=0 no policy may beat
            # hold. A control that fails means the harness is wrong, not that a
            # policy is good, so the run refuses to report.
            if drift == 0.0:
                diff = rs - h                       # paired, same paths
                se = diff.std(ddof=1) / np.sqrt(len(diff))
                if abs(delta) > CONTROL_SIGMAS * se:
                    control_fail.append(
                        f"{name}: {delta:+.4f}R vs hold under a martingale "
                        f"= {abs(delta)/se:.1f} SE (limit {CONTROL_SIGMAS})")
        print()

    if control_fail:
        print("CONTROL FAILED — the drifted numbers above are NOT trustworthy.")
        for line in control_fail:
            print(f"  {line}")
        return 2

    print("READ THIS COLUMN CORRECTLY")
    print("  Under the martingale control every policy should sit near zero — that")
    print("  is optional stopping, and it is the check that this harness is honest.")
    print("  Only the drifted rows carry information, and only about PRESERVATION.")
    print("  Nothing here is evidence that an edge exists.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
