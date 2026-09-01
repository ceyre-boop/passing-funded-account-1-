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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "daytrade"))

from stockfish_exit import (INTENT, Stage, TradeState, apply_actions,  # noqa: E402
                            decide_exit, policy_params)

N_PATHS, N_BARS, SIGMA = 4000, 78, 0.0018
N_EVAL = 3000        # paths actually run through the engine per policy
DRIFTS = (0.0, 0.0002, 0.0005)          # per bar; 0.0 is the martingale control
ENTRY, K_STOP_ATR = 100.0, 1.0


def paths(rng, drift):
    """Ito correction is load-bearing. exp(cumsum(N(0, sigma))) is NOT a price
    martingale -- zero drift in LOG space is +sigma^2/2 in price space, and the
    first version of this control showed every policy 'beating' hold by ~0.1R
    under what was supposed to be a no-edge process. Subtracting sigma^2/2
    makes drift=0 an actual martingale, which is the only way the control can
    do its job."""
    steps = rng.normal(drift - 0.5 * SIGMA ** 2, SIGMA, size=(N_PATHS, N_BARS))
    return ENTRY * np.exp(np.cumsum(steps, axis=1))


def run_policy(path, params, risk):
    """One trade through the real engine. Returns realized R.

    FRACTION TRACKING IS LOAD-BEARING. The first version banked the partial and
    then credited the remainder as a fixed (1 - goal_fraction), which
    double-counted for policies that exit fully at TP2 and mis-weighted the
    runner. It showed every policy beating hold by ~0.16R UNDER A MARTINGALE --
    i.e. it violated optional stopping, which is a theorem. The control caught
    it, which is the entire reason a martingale arm is run at all.
    """
    st = TradeState(direction=1, entry=ENTRY, qty=100, price=ENTRY,
                    sl=ENTRY - risk, tp1=ENTRY + risk, tp2=ENTRY + 3 * risk,
                    trail_dist=0.5 * risk, **params)
    banked, frac_left = 0.0, 1.0
    for px in path:
        st.price = float(px)
        acts = decide_exit(st)
        for a in acts:
            if a.kind == "TAKE_PARTIAL":
                banked += frac_left * a.fraction * (st.price - ENTRY) / risk
                frac_left *= (1.0 - a.fraction)
            elif a.kind == "EXIT_ALL":
                banked += frac_left * (st.price - ENTRY) / risk
                frac_left = 0.0
        apply_actions(st, acts)
        if frac_left <= 0.0:
            return banked
    return banked + frac_left * (float(path[-1]) - ENTRY) / risk


# The control tolerance is DERIVED, not chosen. |policy - hold| is a paired
# difference over the same paths, so its standard error is sd(diff)/sqrt(n).
# A fixed constant (the first version used 0.02) either rejects noise or
# accepts bias depending on how many paths happen to be run -- which makes the
# control's verdict a function of the sample size rather than of the harness.
CONTROL_SIGMAS = 3.0


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
            params = {k: v for k, v in policy_params(name).items() if k != "exit_policy"}
            rs = np.array([run_policy(p, params, risk) for p in P[:N_EVAL]])
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
