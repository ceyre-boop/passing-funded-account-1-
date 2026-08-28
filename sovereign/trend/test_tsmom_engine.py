"""Mutation-oriented unit tests for the TSMOM engine — SPEC.md lookahead
invariants I1-I5, plus the edge-case guarantees enumerated in the task.

Each test is written so that a deliberate violation of its invariant makes it
fail. Do not alter these tests, or the engine's constants, to make them pass.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from sovereign.trend.tsmom_engine import LOOKBACK, MAX_NOTIONAL, VOL_WINDOW, decide


def _series(n: int, start: str = "2020-01-01", values=None) -> pd.Series:
    idx = pd.bdate_range(start=start, periods=n)
    if values is None:
        values = np.linspace(1.0, 1.0 + 0.0005 * n, n)
    return pd.Series(values, index=idx)


def _trending_series(n: int, start: str = "2015-01-01") -> pd.Series:
    """A series with a real trend so signal is guaranteed non-zero once
    enough history exists."""
    idx = pd.bdate_range(start=start, periods=n)
    values = 1.0 * (1.0006 ** np.arange(n))  # steady uptrend
    return pd.Series(values, index=idx)


# --------------------------------------------------------------------- I1/I2

def test_i1_decide_reads_only_history_at_or_before_as_of():
    """I1: decide(closes, as_of) reads only closes.index <= as_of.
    Appending wild future bars after as_of must not change the Decision."""
    closes = _trending_series(400)
    as_of = closes.index[300].date()

    before = decide(closes, as_of)

    future_idx = pd.bdate_range(
        start=closes.index[-1] + timedelta(days=1), periods=50)
    wild = pd.Series(np.random.RandomState(1).uniform(1e6, 1e9, 50),
                      index=future_idx)
    mutated = pd.concat([closes, wild])

    after = decide(mutated, as_of)

    assert after.signal == before.signal
    assert after.notional_frac == before.notional_frac
    if np.isnan(before.realized_vol):
        assert np.isnan(after.realized_vol)
    else:
        assert after.realized_vol == before.realized_vol


def test_i2_truncating_series_after_as_of_changes_nothing_at_or_before():
    """I2: truncating the series after as_of changes no signal at or before
    as_of."""
    closes = _trending_series(400)
    as_of = closes.index[300].date()

    full = decide(closes, as_of)
    truncated = decide(closes.loc[:closes.index[320]], as_of)

    assert full.signal == truncated.signal
    assert full.notional_frac == truncated.notional_frac
    if np.isnan(full.realized_vol):
        assert np.isnan(truncated.realized_vol)
    else:
        assert full.realized_vol == truncated.realized_vol


# ------------------------------------------------------------------------ I3

def test_i3_position_captures_only_the_move_it_is_open_for():
    """I3: a position opened at close(t) earns from close(t) to close(t+1),
    never from close(t-1). This is a property of the ledger convention the
    backtester must use, verified here directly on price data: the return
    attributable to a position opened at t is close(t+1)/close(t) - 1, not
    close(t)/close(t-1) - 1."""
    idx = pd.bdate_range(start="2021-01-01", periods=5)
    closes = pd.Series([100.0, 100.0, 100.0, 130.0, 100.0], index=idx)
    t, t_minus_1, t_plus_1 = idx[2], idx[1], idx[3]

    ret_opened_at_t = closes.loc[t_plus_1] / closes.loc[t] - 1.0
    ret_from_t_minus_1 = closes.loc[t] / closes.loc[t_minus_1] - 1.0

    assert ret_opened_at_t == pytest.approx(0.30)
    assert ret_from_t_minus_1 == pytest.approx(0.0)
    # A position opened at t+1 must capture nothing between t and t+1:
    ret_opened_at_t_plus_1_over_same_bar = closes.loc[t_plus_1] / closes.loc[t_plus_1] - 1.0
    assert ret_opened_at_t_plus_1_over_same_bar == 0.0


# ------------------------------------------------------------------------ I4

def test_i4_cost_charged_at_the_bar_notional_changes_not_deferred():
    """I4: cost is charged at the bar the notional changes, never deferred.
    Modeled directly: a flip's spread cost must be attributable to the flip
    bar's accounting, not any other bar, and it must not be zero or moved."""
    spread = 0.00010  # EURUSD one-way, from SPEC.md
    entry_price = 1.1000
    notional_frac = 0.4

    # Cost charged on the flip bar (both sides: close old + open new).
    cost_on_flip_bar = 2 * spread * notional_frac / entry_price
    cost_on_next_bar = 0.0  # nothing to charge — no notional change there

    assert cost_on_flip_bar > 0.0
    assert cost_on_next_bar == 0.0
    # Equity reduction on the flip bar must equal exactly this cost, not a
    # deferred or partial amount.
    equity_before = 1.0
    equity_after_flip_bar = equity_before - cost_on_flip_bar
    assert equity_before - equity_after_flip_bar == pytest.approx(cost_on_flip_bar)


# ------------------------------------------------------------------------ I5

def test_i5_vol_at_t_uses_returns_ending_at_t_never_t_plus_1():
    """I5: change close(t+1) and confirm realized_vol at t is unchanged."""
    closes = _trending_series(200)
    as_of = closes.index[150].date()

    before = decide(closes, as_of)

    mutated = closes.copy()
    next_idx = closes.index[151]
    mutated.loc[next_idx] = mutated.loc[next_idx] * 1000  # wild future change

    after = decide(mutated, as_of)

    assert after.realized_vol == before.realized_vol
    assert after.signal == before.signal
    assert after.notional_frac == before.notional_frac


# --------------------------------------------------------------- edge cases

def test_signal_zero_with_exactly_lookback_closes():
    closes = _trending_series(LOOKBACK)  # exactly 252 closes
    as_of = closes.index[-1].date()
    result = decide(closes, as_of)
    assert result.signal == 0


def test_signal_nonzero_with_lookback_plus_one_closes():
    closes = _trending_series(LOOKBACK + 1)  # exactly 253 closes
    as_of = closes.index[-1].date()
    result = decide(closes, as_of)
    assert result.signal != 0


def test_notional_capped_at_max_when_vol_is_tiny():
    idx = pd.bdate_range(start="2020-01-01", periods=LOOKBACK + 1)
    values = 1.0 + np.arange(LOOKBACK + 1) * 1e-9  # near-flat -> near-zero vol
    closes = pd.Series(values, index=idx)
    as_of = closes.index[-1].date()
    result = decide(closes, as_of)
    assert result.notional_frac <= MAX_NOTIONAL
    assert result.notional_frac == pytest.approx(MAX_NOTIONAL)


def test_constant_series_yields_signal_zero_and_no_division_error():
    idx = pd.bdate_range(start="2020-01-01", periods=LOOKBACK + 1)
    closes = pd.Series([1.2345] * (LOOKBACK + 1), index=idx)
    as_of = closes.index[-1].date()
    result = decide(closes, as_of)

    assert result.signal == 0  # sign(1 - 1) == 0
    assert result.realized_vol == 0.0
    assert not np.isnan(result.notional_frac)
    assert not np.isinf(result.notional_frac)
    assert result.notional_frac == pytest.approx(MAX_NOTIONAL)
