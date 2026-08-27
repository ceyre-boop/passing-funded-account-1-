"""Mutation-oriented unit tests for CarryEngine ATR handling.

DEFECT (2026-08-26): ``_compute_atr`` used to return a hardcoded ``0.001``
"safe fallback" whenever price data was missing, too short, or NaN. Against
live data that fallback oversized every carry pair by 4x-800x (measured:
EURUSD 4.9x, GBPUSD 6.0x, AUDUSD 4.2x, USDJPY 817x against real ATR). The
fix: ``_compute_atr`` raises ``CarryATRUnavailable`` instead of returning a
number, and a pair whose ATR cannot be computed must produce NO signal.

Each test states the fault it exists to catch.
"""
import math

import pandas as pd
import pytest

from sovereign.forex.carry_engine import (
    CARRY_PAIRS,
    CarryEngine,
    CarryATRUnavailable,
    CarryPairConfig,
)


def _ohlc(rows: int, *, high=1.10, low=1.09, close=1.095) -> pd.DataFrame:
    """Build a well-formed OHLC frame with `rows` bars."""
    idx = pd.date_range('2026-01-01', periods=rows, freq='B')
    return pd.DataFrame(
        {'High': [high] * rows, 'Low': [low] * rows, 'Close': [close] * rows},
        index=idx,
    )


class TestComputeATRRaisesInsteadOfFallback:
    """Fault: _compute_atr silently returning 0.001 instead of raising."""

    def test_none_prices_raises(self):
        with pytest.raises(CarryATRUnavailable):
            CarryEngine._compute_atr(None)

    def test_empty_prices_raises(self):
        with pytest.raises(CarryATRUnavailable):
            CarryEngine._compute_atr(_ohlc(0))

    def test_too_short_prices_raises(self):
        # ATR_PERIOD is 14; 5 bars is well under the 15-bar minimum.
        with pytest.raises(CarryATRUnavailable):
            CarryEngine._compute_atr(_ohlc(5))

    def test_nan_atr_raises(self):
        rows = 20
        idx = pd.date_range('2026-01-01', periods=rows, freq='B')
        df = pd.DataFrame(
            {'High': [float('nan')] * rows, 'Low': [float('nan')] * rows,
             'Close': [float('nan')] * rows},
            index=idx,
        )
        with pytest.raises(CarryATRUnavailable):
            CarryEngine._compute_atr(df)

    def test_no_result_is_the_literal_magic_number(self):
        """Fault: someone reverts the fix but leaves the raise unreachable —
        assert 0.001 is never returned even if the raise is swallowed by a
        broad except somewhere upstream in a future edit."""
        with pytest.raises(CarryATRUnavailable) as exc_info:
            CarryEngine._compute_atr(None)
        # The exception must not itself carry the old magic number as a
        # "fallback" attribute reintroducing the defect via another path.
        assert not hasattr(exc_info.value, 'fallback_atr')

    def test_well_formed_prices_return_real_atr(self):
        """Sanity: the happy path still works and returns a plausible number,
        not the old magic constant."""
        df = _ohlc(20, high=1.10, low=1.09, close=1.095)
        atr = CarryEngine._compute_atr(df)
        assert math.isclose(atr, 0.01, rel_tol=1e-6)
        assert atr != 0.001


class TestNoSignalWithoutATR:
    """Fault: a pair whose ATR could not be computed still gets a sized
    signal (the original defect's blast radius — mis-sized units on live
    orders)."""

    def _cfg(self) -> CarryPairConfig:
        return CarryPairConfig(
            ticker='USDJPY=X',
            base_currency='USD',
            quote_currency='JPY',
            high_yield_side='BASE',
            description='test pair',
        )

    def test_evaluate_pair_raises_when_atr_unavailable(self, monkeypatch, tmp_path):
        engine = CarryEngine(
            account_equity=100_000.0,
            rate_overrides={'USD': 5.25, 'JPY': 0.10},
        )
        monkeypatch.setattr(CarryEngine, '_fetch_prices', staticmethod(lambda ticker: None))
        with pytest.raises(CarryATRUnavailable):
            engine._evaluate_pair(self._cfg())

    def test_scan_emits_no_signal_for_unpriceable_pair(self, monkeypatch):
        """The real regression: scan() must never emit a CarrySignal (LONG,
        SHORT, or FLAT) for a pair whose ATR is unavailable — it must be
        dropped from the results entirely, not sized off a guess."""
        engine = CarryEngine(
            account_equity=100_000.0,
            rate_overrides={'AUD': 4.35, 'CHF': 1.50, 'NZD': 5.50, 'JPY': 0.10},
        )
        monkeypatch.setattr(CarryEngine, '_fetch_prices', staticmethod(lambda ticker: None))
        signals = engine.scan()
        assert signals == []

    def test_scan_still_emits_signal_when_atr_is_computable(self, monkeypatch):
        """Control: scan() must still produce signals when ATR IS available,
        proving the empty result above is caused by the ATR fault, not by
        scan() being broken outright."""
        engine = CarryEngine(
            account_equity=100_000.0,
            rate_overrides={'AUD': 4.35, 'CHF': 1.50, 'NZD': 5.50, 'JPY': 0.10},
        )
        good_prices = _ohlc(20, high=1.10, low=1.09, close=1.095)
        monkeypatch.setattr(CarryEngine, '_fetch_prices', staticmethod(lambda ticker: good_prices))
        signals = engine.scan()
        assert len(signals) == len(CARRY_PAIRS)
        assert all(sig.atr != 0.001 for sig in signals)

    def test_flag_degraded_still_called_on_atr_unavailable(self, monkeypatch, tmp_path):
        """The observability call must survive the fix — ATR unavailability
        must still be fail-loud, not just silently absent from results."""
        import sovereign.forex.carry_engine as ce_mod

        calls = []
        monkeypatch.setattr(
            'sovereign.forex.degraded_sentinel.flag_degraded',
            lambda pair, reason, source='yfinance': calls.append((pair, reason, source)),
        )
        engine = CarryEngine(
            account_equity=100_000.0,
            rate_overrides={'USD': 5.25, 'JPY': 0.10},
        )
        monkeypatch.setattr(ce_mod.CarryEngine, '_fetch_prices', staticmethod(lambda ticker: None))
        with pytest.raises(CarryATRUnavailable):
            engine._evaluate_pair(self._cfg())
        assert len(calls) == 1
        pair, reason, source = calls[0]
        assert pair == 'USDJPY=X'
        assert 'ATR' in reason
        assert source == 'atr'
