"""Spec 039 / invariant I56 — rate and CPI inputs must be read at their
publication-date vintage, and a value that was not knowable must never become a
number.

Every test here is written so that deliberately violating the invariant makes it
FAIL, per CLAUDE.md's definition of VERIFIED. The named violation is spelled out
above each test.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "cache" / "macro_vintage_raw"

# Resolved THROUGH the switch, never by hand: if the two modes are ever wired to
# the same tree, the data tests below read the same file twice and fail.
from sovereign.forex import rate_vintage as _rv  # noqa: E402

NOMINAL_DIR = _rv.macro_cache_dir(_rv.NOMINAL)
PUB_DIR = _rv.macro_cache_dir(_rv.PUBLICATION)

# The monthly OECD/Fed series behind three of the four sealed pairs' legs.
MONTHLY = {"US": "FEDFUNDS", "JP": "IRSTCI01JPM156N", "AU": "IR3TIB01AUM156N"}

needs_caches = pytest.mark.skipif(
    not (PUB_DIR.exists() and NOMINAL_DIR.exists() and RAW_DIR.exists()),
    reason="run scripts/build_rate_vintages.py first (needs FRED_API_KEY)",
)


def _reload(monkeypatch, mode: str):
    monkeypatch.setenv("CARRY_RATE_VINTAGE", mode)
    from sovereign.forex import rate_vintage
    importlib.reload(rate_vintage)
    return rate_vintage


# ── the switch itself ──────────────────────────────────────────────────── #

def test_default_mode_is_sealed_so_existing_artifacts_are_untouched(monkeypatch):
    monkeypatch.delenv("CARRY_RATE_VINTAGE", raising=False)
    from sovereign.forex import rate_vintage
    assert rate_vintage.vintage_mode() == "sealed"
    assert rate_vintage.macro_cache_dir().name == "macro"


def test_unknown_mode_raises_rather_than_guessing(monkeypatch):
    monkeypatch.setenv("CARRY_RATE_VINTAGE", "whatever")
    from sovereign.forex import rate_vintage
    with pytest.raises(ValueError):
        rate_vintage.vintage_mode()


def test_missing_vintage_cache_raises_and_never_falls_back(monkeypatch, tmp_path):
    """Violation: replacing require_cache's raise with a return of the sealed
    path would let a publication-mode run silently report look-ahead numbers."""
    from sovereign.forex import rate_vintage
    with pytest.raises(rate_vintage.VintageUnavailable):
        rate_vintage.require_cache(tmp_path / "nope.parquet")


# ── the vintage data itself ────────────────────────────────────────────── #

@needs_caches
@pytest.mark.parametrize("country,series_id", sorted(MONTHLY.items()))
def test_publication_series_contains_no_value_published_after_its_date(country, series_id):
    """I56, stated as an assertion over the data.

    For every business date d in the publication cache, the value at d must be
    one that ALFRED says had already been released on or before d.

    Violation: pointing PUB_DIR at the nominal tree (or rebuilding the pub tree
    with observation dates instead of realtime dates) fails this immediately —
    a monthly series' month-M value is not released until M+1.
    """
    pub = pd.read_parquet(PUB_DIR / f"{country}_rates.parquet").squeeze().dropna()
    raw = pd.read_parquet(RAW_DIR / f"{series_id}.parquet")
    # earliest date each distinct value was ever available
    first_avail = raw.groupby("value")["realtime_start"].min()

    sample = pub.loc["2015-01-01":"2024-12-31"].iloc[::17]  # ~every 3.5 weeks
    assert len(sample) > 100
    for date, value in sample.items():
        avail = first_avail.get(value)
        assert avail is not None, f"{country} {value} not in ALFRED raw table"
        assert avail <= date, (
            f"{country} on {date.date()} carries {value}, first published "
            f"{avail.date()} — {(avail - date).days} days of look-ahead"
        )


@needs_caches
@pytest.mark.parametrize("country", sorted(MONTHLY))
def test_nominal_and_publication_actually_differ_on_monthly_series(country):
    """Violation: wiring both modes to the same directory — the A/B would then
    report a zero delta and look like good news."""
    nom = pd.read_parquet(NOMINAL_DIR / f"{country}_rates.parquet").squeeze()
    pub = pd.read_parquet(PUB_DIR / f"{country}_rates.parquet").squeeze()
    span = slice("2015-01-01", "2024-12-31")
    differ = (nom[span] - pub[span]).abs() > 1e-9
    assert differ.mean() > 0.5, (
        f"{country}: only {differ.mean():.1%} of dates differ between nominal and "
        "publication vintage — the two trees are not distinct"
    )


@needs_caches
def test_daily_series_lag_constants_match_measured_alfred_behaviour():
    """The two daily legs use a fixed lag instead of ALFRED realtime, because
    ALFRED backfills their pre-2021 stamps. This pins the constants to the
    measured post-2021 behaviour so a silent change is caught.

    Violation: raising ECBDFR's constant to a monthly-sized lag fails here.
    """
    from scripts.build_rate_vintages import DAILY_SERIES_LAG_DAYS
    assert DAILY_SERIES_LAG_DAYS == {"ECBDFR": 0, "IUDSOIA": 2}
    for series_id, lag in DAILY_SERIES_LAG_DAYS.items():
        raw = pd.read_parquet(RAW_DIR / f"{series_id}.parquet")
        first = raw.groupby("date")["realtime_start"].min()
        post = first[first > first.min()]           # drop the ALFRED-add cohort
        measured = (post - post.index).dt.days.median()
        assert abs(measured - lag) <= 1, (
            f"{series_id}: measured median publication lag {measured}d, "
            f"constant says {lag}d"
        )


# ── the engine's refusal to invent a number ────────────────────────────── #

def _engine(mode, monkeypatch):
    monkeypatch.setenv("CARRY_RATE_VINTAGE", mode)
    from sovereign.forex import rate_vintage, signal_engine
    importlib.reload(rate_vintage)
    importlib.reload(signal_engine)
    eng = signal_engine.ForexSignalEngine(fetcher=None, cb_trigger=None)
    return signal_engine, eng


def _args(base_rate):
    """AU/US legs with equal nominal rates (so the IRP term is exactly zero) and
    a wide CPI gap (so the carry term alone clears the 0.15 threshold), on a
    steadily rising price so the momentum filter agrees. Passing NaN as the AU
    rate is the 'not published yet' case."""
    idx = pd.bdate_range("2015-01-01", "2016-12-31")
    close = pd.Series(np.linspace(1.0, 1.4, len(idx)), index=idx)
    return dict(
        close=close, date=pd.Timestamp("2016-06-01"),
        base_country="AU", quote_country="US",
        base_rates=pd.Series(base_rate, index=idx, dtype=float),
        quote_rates=pd.Series(1.0, index=idx, dtype=float),
        base_cpi_h=pd.Series(-6.0, index=idx, dtype=float),
        quote_cpi_h=pd.Series(1.0, index=idx, dtype=float),
    )


def test_unpublished_value_is_excluded_and_counted_not_filled(monkeypatch):
    """Violation: deleting the NaN guard in _macro_signal_for_date restores the
    FALLBACK_RATES constant, which produces a real signal here — and this test
    fails on both the returned sign and the empty exclusion list."""
    se, eng = _engine("publication", monkeypatch)
    se._LAST_RUN_EXCLUSIONS.clear()
    args = _args(np.nan)          # nothing published yet for the AU leg
    assert eng._macro_signal_for_date(**args) == 0
    assert len(eng.vintage_exclusions) == 1
    assert eng.vintage_exclusions[0]["date"] == "2016-06-01"
    assert "AU_rate" in eng.vintage_exclusions[0]["missing"]
    assert len(se._LAST_RUN_EXCLUSIONS) == 1


def test_a_published_value_still_scores_normally(monkeypatch):
    """Guard against the opposite failure: an exclusion rule so broad it zeroes
    every signal would make any vintage look 'safe'."""
    se, eng = _engine("publication", monkeypatch)
    se._LAST_RUN_EXCLUSIONS.clear()
    assert eng._macro_signal_for_date(**_args(1.0)) == 1
    assert eng.vintage_exclusions == []


def test_sealed_mode_keeps_the_historical_fallback_behaviour(monkeypatch):
    """Sealed mode must be untouched: a NaN rate there still falls through to
    the historical code path and never records a spec-039 exclusion, so every
    pre-existing artifact stays reproducible."""
    se, eng = _engine("sealed", monkeypatch)
    se._LAST_RUN_EXCLUSIONS.clear()
    eng._macro_signal_for_date(**_args(np.nan))
    assert eng.vintage_exclusions == []
    assert se._LAST_RUN_EXCLUSIONS == []


# ── the strategy must not have moved ───────────────────────────────────── #

def test_signal_config_defaults_are_the_sealed_run_values():
    """Step 0 of spec 039, pinned. The sealed v015 run used ForexBacktester with
    signal_weights=None, i.e. these defaults, verified against frozen commit
    897534ac in ceyre-boop/quant.

    Violation: tuning any of these to improve the vintage result fails here.
    """
    from sovereign.forex.signal_engine import SignalConfig, SIGNAL_THRESHOLD, HOLD_DAYS
    c = SignalConfig()
    assert (c.irp_weight, c.rate_weight) == (0.50, 0.50)
    assert c.use_momentum_filter is True
    assert c.signal_threshold == SIGNAL_THRESHOLD == 0.15
    assert c.hold_days == HOLD_DAYS == 60
