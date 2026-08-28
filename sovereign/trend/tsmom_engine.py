"""TSMOM engine — the one and only implementation of the rule in
sovereign/trend/SPEC.md. Do not re-implement any part of this rule anywhere
else (backtester and paper loop both import decide()).

Source: Moskowitz, Ooi, Pedersen (2012). Constants are pre-registered and
fixed — see SPEC.md. Do not tune them here or anywhere else.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

# Pre-registered constants (SPEC.md "Constants"). Fixed. Never tuned.
LOOKBACK = 252       # trading days
VOL_WINDOW = 60      # trading days
VOL_TARGET = 0.05    # annualized, per instrument
MAX_NOTIONAL = 1.00  # no leverage beyond 1x notional per instrument


@dataclass(frozen=True)
class Decision:
    signal: int          # -1, 0, 1
    notional_frac: float  # 0.0 .. MAX_NOTIONAL
    realized_vol: float  # annualized; float('nan') if unavailable


def decide(closes: pd.Series, as_of: date) -> Decision:
    """SPEC.md "Rule". `closes` is a Series indexed by date (ascending),
    price levels for one instrument.

    Invariant I1 (decide reads only closes.index <= as_of) is enforced by
    making the as_of slice the FIRST action below — structurally impossible
    to read a future bar afterward.
    """
    as_of_ts = pd.Timestamp(as_of)
    hist = closes.loc[:as_of_ts]  # I1 — must be first action

    # --- signal_i(t) = sign(P(t)/P(t-252) - 1), exactly zero if < 253 closes.
    if len(hist) < LOOKBACK + 1:
        signal = 0
    else:
        p_t = float(hist.iloc[-1])
        p_lag = float(hist.iloc[-1 - LOOKBACK])
        signal = int(np.sign(p_t / p_lag - 1.0))

    # --- realized_vol_i(t) = stdev(daily log returns over the last 60
    # closes) * sqrt(252). Judgment call: "last 60 closes" is read literally
    # as a window of 60 price levels, which yields 59 consecutive log
    # returns inside that window (the more conservative reading — a smaller
    # return sample yields a noisier, generally larger vol estimate than
    # padding the window to 61 closes for a full 60 returns). Sample stdev
    # (ddof=1) is used for the same reason: it is never smaller than the
    # population stdev, so notional sizing never comes out more aggressive
    # than the literal reading supports.
    if len(hist) < VOL_WINDOW:
        realized_vol = float("nan")
    else:
        window = hist.iloc[-VOL_WINDOW:]
        log_returns = np.log(window / window.shift(1)).dropna()
        realized_vol = float(log_returns.std(ddof=1) * np.sqrt(252))

    # --- notional_frac_i(t) = min(VOL_TARGET / realized_vol, MAX_NOTIONAL).
    # Judgment call: realized_vol of exactly 0.0 (a constant price series)
    # would divide-by-zero into inf; that is capped at MAX_NOTIONAL rather
    # than left as inf/NaN. Vol truly unavailable (insufficient history)
    # yields notional_frac 0.0 — conservative, since a signal computed on
    # <253 closes is already forced to 0 in every real case this can arise.
    if np.isnan(realized_vol):
        notional_frac = 0.0
    elif realized_vol <= 0.0:
        notional_frac = MAX_NOTIONAL
    else:
        notional_frac = min(VOL_TARGET / realized_vol, MAX_NOTIONAL)

    return Decision(signal=signal, notional_frac=notional_frac,
                     realized_vol=realized_vol)
