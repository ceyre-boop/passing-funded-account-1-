#!/usr/bin/env python3
"""scripts/build_experience.py — the environment, precomputed once.

WHAT THIS IS
    For every decision bar of every session: the state the agent can see, and
    what the FROZEN exit engine would have returned had the agent entered long
    or short there. That is the environment's response to each legal action.

    Precomputing it is what makes self-improvement affordable. simulate() costs
    0.46 ms, so the whole table is ~11 s; after that, evaluating a candidate
    policy is a dot product and the learner can try millions.

WHY IT IS NOT CHEATING
    The outcome table is the ENVIRONMENT, not a label the policy gets to see.
    The policy sees `features` only. `r_long` / `r_short` are the reward it
    receives AFTER acting, exactly as a game engine returns a score after a
    move. A policy that could read them would be reading the future, and the
    learner never passes them in.

RULE 1 — ONE IMPLEMENTATION
    Outcomes come from `daytrade/ceiling.py::simulate`, the sanctioned
    backtest path, under a fixed exit config. No second exit loop is written
    here; the exit is the frozen engine and only the ENTRY is being learned.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "daytrade")]

from bars import Session  # noqa: E402
from ceiling import Entry, simulate  # noqa: E402

from body.features import MIN_BARS, N_FEATURES, observable  # noqa: E402

OUT = ROOT / "data" / "daytrade" / "experience.npz"
BARS = ROOT / "data" / "daytrade" / "bars_premarket" / "SPY_5m.parquet"
RTH_OPEN, RTH_CLOSE = "09:30", "15:55"

# The FIXED exit. Not learned, not tuned — the shipped policy for cash index.
# Only the entry decision is under search.
EXIT_CFG = {"partial_frac": 0.5, "trail_mult": None, "be_arm_frac": 1.0,
            "hold_past_tp2": True, "flatten_et": None, "vol_k": None, "atr": None}
K_STOP_ATR = 1.0
LAST_ENTRY_BAR = 12       # need room for the trade to resolve before the close


class _Bar:
    __slots__ = ("open", "high", "low", "close", "volume")

    def __init__(self, o, h, l, c, v):
        self.open, self.high, self.low, self.close, self.volume = o, h, l, c, v


def sessions(limit: int):
    df = pd.read_parquet(BARS)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert("America/New_York")
    hh = df.index.strftime("%H:%M")
    rth = df[(hh >= RTH_OPEN) & (hh <= RTH_CLOSE)]
    out = [(d, c) for d, c in rth.groupby(rth.index.date) if len(c) >= 40]
    return out[-limit:]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="precompute the environment")
    ap.add_argument("--sessions", type=int, default=400)
    ap.add_argument("--seal-holdout", type=int, default=100,
                    help="most-recent sessions withheld entirely, never in the table")
    a = ap.parse_args(argv)

    X, RL, RS, DAY, TS = [], [], [], [], []
    # THE SEALED HOLDOUT. "The refinement is the overfitting": this table has
    # been queried already, so anything built from here needs data nobody has
    # looked at. The most recent N sessions are withheld BEFORE the table is
    # built, not split out afterwards, and the boundary is written into the
    # archive so a later run cannot quietly move it.
    all_sess = sessions(a.sessions + a.seal_holdout)
    keep = len(all_sess) - a.seal_holdout if a.seal_holdout else len(all_sess)
    sess, sealed = all_sess[:keep], all_sess[keep:]
    if sealed:
        print(f"SEALED HOLDOUT: {len(sealed)} sessions withheld "
              f"({sealed[0][0]} .. {sealed[-1][0]}) — absent from this table")
    print(f"building experience over {len(sess)} sessions "
          f"({sess[0][0]} .. {sess[-1][0]})")

    for si, (day, chunk) in enumerate(sess):
        s = Session(symbol="SPY", day=day, df=chunk)
        bars = [_Bar(*row) for row in
                chunk[["Open", "High", "Low", "Close", "Volume"]].to_numpy()]
        n = len(bars)
        for i in range(MIN_BARS, n - LAST_ENTRY_BAR):
            feats = observable(bars[: i + 1], session_len=n)
            if feats is None:
                continue
            px = float(bars[i].close)
            atr = sum(b.high - b.low for b in bars[i - 23: i + 1]) / 24
            risk = K_STOP_ATR * atr
            if not (risk > 0):
                continue
            # ISO, not "HH:MM". simulate() does `session.after(e.ts[11:16])`,
            # so a bare "10:30" slices to the EMPTY STRING and `after("")`
            # matches every bar — every trade silently replayed from the
            # session open. Half the outcomes came back below -1R on a 1R stop
            # before this was caught. Fail-loud would have been better than a
            # slice that quietly matched everything.
            # VERIFY THE MACHINE RAN. simulate() slices e.ts[11:16] for its
            # start time; a value that does not slice to HH:MM silently means
            # "after nothing" — the whole session. Not hypothetical: it happened,
            # half the outcomes came back below -1R on a 1R stop, nothing raised.
            iso = chunk.index[i].isoformat()
            if not (len(iso) > 16 and iso[13] == ":"):
                raise ValueError(
                    f"ts {iso!r} does not slice to HH:MM at [11:16] — simulate() "
                    "would replay the whole session from the open")
            rs = []
            for d in (1, -1):
                e = Entry(day=str(day), ts=iso, time_block="OPEN", direction=d,
                          entry=px, stop=px - d * risk, risk=risk,
                          tp1=px + d * risk, tp2=px + d * 3 * risk,
                          trail_dist=0.5 * risk)
                rs.append(float(simulate(s, e, EXIT_CFG)))
            X.append(feats); RL.append(rs[0]); RS.append(rs[1])
            DAY.append(si); TS.append(int(chunk.index[i].value))
        if (si + 1) % 100 == 0:
            print(f"  {si + 1}/{len(sess)} sessions, {len(X)} decision points")

    X = np.asarray(X, dtype=np.float32)
    np.savez_compressed(OUT, X=X, r_long=np.asarray(RL, dtype=np.float32),
                        r_short=np.asarray(RS, dtype=np.float32),
                        day=np.asarray(DAY, dtype=np.int32),
                        ts=np.asarray(TS, dtype=np.int64),
                        n_sessions=np.int32(len(sess)),
                        sealed_from=np.str_(str(sealed[0][0]) if sealed else ""),
                        sealed_to=np.str_(str(sealed[-1][0]) if sealed else ""),
                        n_sealed=np.int32(len(sealed)))
    # VERIFY THE MACHINE RAN, part two: ratios implausible if something broke.
    per_session = X.shape[0] / max(1, len(sess))
    if not (5 <= per_session <= 80):
        raise ValueError(f"{per_session:.1f} decision points per session is outside "
                         "the plausible range — the machine did not run as intended")
    rl_a, rs_a = np.asarray(RL), np.asarray(RS)
    for nm, r in (("long", rl_a), ("short", rs_a)):
        deep = float(np.mean(r < -1.05))
        if deep > 0.25:
            raise ValueError(
                f"{deep:.1%} of {nm} outcomes are below -1.05R on a 1R stop. Gaps "
                "explain a few percent; a quarter means the stop is not honoured")

    print(f"\n  {X.shape[0]} decision points x {X.shape[1]} features")
    print(f"  sanity: {per_session:.1f} decisions/session, "
          f"{np.mean(rl_a < -1.05):.1%}/{np.mean(rs_a < -1.05):.1%} beyond the stop")
    print(f"  mean R if you entered long everywhere : {np.mean(RL):+.4f}")
    print(f"  mean R if you entered short everywhere: {np.mean(RS):+.4f}")
    print(f"  -> {OUT.name}")
    print("\n  Those two means are the null every learned policy must beat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
