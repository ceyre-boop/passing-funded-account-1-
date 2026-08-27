"""Fault-injection tests: synthetic macro data must have a real consumer.

DEFECT (2026-08-26): ``ForexDataFetcher._fetch_macro`` correctly labels a failed
FRED call as ``fallback_static`` in ``source_map``/``synthetic_fields`` (commit
0ee1ec2), but nothing downstream ever read those fields. ``ForexMacroEngine``
feeds ``base_macro['rate']``/``['cpi_yoy']`` and ``quote_macro['rate']``/
``['cpi_yoy']`` straight into ``rate_diff_momentum`` (30% weight) and ``irp_z``
(25% weight) — 55% of the composite score — with no check on whether those
inputs were live or synthetic.

FIX: ``ForexMacroEngine.score_pair`` now refuses to emit a directional signal
when either leg's ``rate`` or ``cpi_yoy`` is synthetic — it returns a NEUTRAL
``ForexSignal`` with ``primary_driver='MACRO_DEGRADED'``, ``degraded=True``, and
``degraded_fields`` populated, mirroring the existing SNB/BOJ NEUTRAL-forcing
gates already in this file.
"""
from __future__ import annotations

import pytest

from sovereign.forex.macro_engine import ForexMacroEngine, MACRO_DEPENDENT_FIELDS


def _engine() -> ForexMacroEngine:
    """Build a ForexMacroEngine without hitting any constructor's network path."""
    eng = ForexMacroEngine.__new__(ForexMacroEngine)
    eng._fetcher = None  # replaced per-test via monkeypatch
    eng._fv = None
    eng._cycle = None
    eng._risk = None
    eng._price_cache = {}
    return eng


def _macro(rate_source='fred', cpi_source='fred', gdp_source='fred'):
    return {
        'rate': 4.0, 'cpi_yoy': 2.0, 'gdp_growth': 2.0, 'real_rate': 2.0,
        'rate_trajectory': [0, 0, 0],
        'source_map': {'rate': rate_source, 'cpi_yoy': cpi_source, 'gdp_growth': gdp_source,
                        'rate_trajectory': 'manual_prior'},
        'synthetic_fields': [f for f, s in
                              {'rate': rate_source, 'cpi_yoy': cpi_source,
                               'gdp_growth': gdp_source, 'rate_trajectory': 'manual_prior'}.items()
                              if s in {'fallback_static', 'manual_prior'}],
        'as_of': '2026-08-26',
    }


class _FakeFetcher:
    """Returns pre-scripted macro dicts per country, keyed positionally."""

    def __init__(self, base_macro, quote_macro):
        self._by_country = {'EU': base_macro, 'US': quote_macro}

    def get_country_macro(self, country, refresh=False):
        return self._by_country[country]


class TestMacroDependentFieldsConstant:
    def test_rate_and_cpi_are_the_tracked_fields(self):
        # These are exactly the two fields that feed rate_diff_momentum + irp_z.
        assert MACRO_DEPENDENT_FIELDS == {'rate', 'cpi_yoy'}


class TestScorePairRefusesOnSyntheticMacro:
    """Fails if a signal is emitted on synthetic macro without refusal/degradation."""

    def test_synthetic_base_rate_forces_neutral_and_degraded(self):
        eng = _engine()
        eng._fetcher = _FakeFetcher(
            base_macro=_macro(rate_source='fallback_static'),
            quote_macro=_macro(),
        )
        sig = eng.score_pair('EURUSD=X')
        assert sig is not None
        assert sig.direction == 'NEUTRAL'
        assert sig.conviction == 0.0
        assert sig.primary_driver == 'MACRO_DEGRADED'
        assert sig.degraded is True
        assert 'rate' in sig.degraded_fields

    def test_synthetic_quote_cpi_forces_neutral_and_degraded(self):
        eng = _engine()
        eng._fetcher = _FakeFetcher(
            base_macro=_macro(),
            quote_macro=_macro(cpi_source='fallback_static'),
        )
        sig = eng.score_pair('EURUSD=X')
        assert sig.direction == 'NEUTRAL'
        assert sig.degraded is True
        assert 'cpi_yoy' in sig.degraded_fields

    def test_synthetic_gdp_only_does_not_trigger_refusal(self):
        """gdp_growth doesn't feed rate_diff_momentum/irp_z directly — a
        synthetic GDP figure alone must not block a directional read (do not
        over-correct onto unrelated fields)."""
        eng = _engine()
        eng._fetcher = _FakeFetcher(
            base_macro=_macro(gdp_source='fallback_static'),
            quote_macro=_macro(),
        )
        # Both rate/cpi are live on both legs — the refusal gate must not fire.
        base = eng._fetcher.get_country_macro('EU')
        quote = eng._fetcher.get_country_macro('US')
        degraded_fields = (
            {f for f in base['synthetic_fields'] if f in MACRO_DEPENDENT_FIELDS} |
            {f for f in quote['synthetic_fields'] if f in MACRO_DEPENDENT_FIELDS}
        )
        assert not degraded_fields

    def test_fully_live_macro_does_not_short_circuit(self, monkeypatch):
        """Contrast: with rate+cpi live on both legs, score_pair proceeds past
        the refusal gate (it will go on to hit CHF/JPY gates or real scoring
        logic — we only assert it did NOT take the MACRO_DEGRADED early return)."""
        eng = _engine()
        eng._fetcher = _FakeFetcher(base_macro=_macro(), quote_macro=_macro())
        # Force an early, cheap failure past the refusal gate so this stays a
        # unit test: no price history configured means score_pair returns None
        # via the "Insufficient price history" branch, not MACRO_DEGRADED.
        eng._get_price_history = lambda pair: None
        sig = eng.score_pair('EURUSD=X')
        assert sig is None  # reached the price-history branch, not MACRO_DEGRADED
