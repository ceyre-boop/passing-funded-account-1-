"""Tests for the trade/skip pre-registration (spec 037).

Fault-injection discipline, same shape as test_oracle_audit_args.py: these
tests exist so that quietly loosening a pre-registered gate, control, or join
rule FAILS THE SUITE rather than passing. `specs/037_TRADE_SKIP_PREREGISTRATION.md`
is committed BEFORE any model was fitted — the acceptance bar is that
deviating from its contract is caught here, not noticed later.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from daytrade import trade_skip as ts
from daytrade.splits import TUNE_END

EXTENDED = Path(__file__).resolve().parents[1] / "data" / "daytrade" / "bars_extended"


# ------------------------------------------------------- pre-registered constants

def test_gates_are_unchanged():
    """Fails if any pre-registered threshold moves. Not adjustable after
    seeing a result — that is laundering, not tuning."""
    assert ts.RNG_SEED == 26
    assert ts.POLICY_COLUMN == "EARLY_BANK"
    assert ts.EXPECTED_TUNE_ENTRIES == 402
    assert ts.T1_MIN_N == 129
    assert ts.T2_MIN_TRADE_RATE == 0.40
    assert ts.T3_DRAWS == 1000
    assert (ts.T3_LO_PCT, ts.T3_HI_PCT) == (5, 95)
    assert ts.T4_MARGIN == 0.05
    assert ts.T5_MARGIN == 0.05
    assert ts.ALWAYS_TRADE_BASELINE == -0.0061


# --------------------------------------------------- T2 short-circuit discipline

def _synthetic_dataset(n=200, trade_frac=0.5, seed=0):
    """A tiny synthetic day-grouped dataset — enough to drive run_gates without
    touching the real bar cache or feature table."""
    rng = np.random.default_rng(seed)
    X = rng.standard_normal((n, 5))
    # y correlated with a feature so predicted_trade isn't degenerate by luck
    y = X[:, 0] * 0.3 + rng.standard_normal(n) * 0.1
    if trade_frac < 0.4:
        y = y - 5.0                      # push almost everything below the
                                          # threshold -> near-zero trade rate
    days = np.array([f"2024-01-{i % 28 + 1:02d}" for i in range(n)])
    meta = [{"day": d} for d in days]
    return X, y, days, meta


def test_t2_short_circuits_t3_through_t5():
    """DEGENERATE_SKIPPER must omit T3/T4/T5 and the three controls entirely
    — not merely fail them."""
    X, y, days, meta = _synthetic_dataset(trade_frac=0.0)
    result = ts.run_gates(X, y, days, meta, ["f0", "f1", "f2", "f3", "f4"])
    assert result["verdict"] == "DEGENERATE_SKIPPER"
    for key in ("T3_BEATS_RANDOM", "T4_BEATS_NOISE", "T5_BEATS_ALWAYS_TRADE",
               "rate_matched_random", "shuffled_feature"):
        assert key not in result, f"{key} computed/quoted despite DEGENERATE_SKIPPER"


def test_t2_threshold_is_read_not_bypassed():
    """The verdict must key off T2_MIN_TRADE_RATE, not a hardcoded literal
    that could drift silently."""
    src = Path(ts.__file__).read_text()
    assert "trade_rate >= T2_MIN_TRADE_RATE" in src
    assert '"DEGENERATE_SKIPPER"' in src
    assert "if not t2_pass" in src
    assert "return result" in src


def test_t2_pass_computes_all_downstream_gates():
    X, y, days, meta = _synthetic_dataset(trade_frac=0.5)
    result = ts.run_gates(X, y, days, meta, ["f0", "f1", "f2", "f3", "f4"])
    if result["T2_NON_DEGENERATE"]["pass"]:
        for key in ("T3_BEATS_RANDOM", "T4_BEATS_NOISE", "T5_BEATS_ALWAYS_TRADE"):
            assert key in result


# ------------------------------------------------------ rate-matched random

def test_rate_matched_random_skips_exactly_k_every_draw():
    """The control must skip the SAME NUMBER of days the model skipped, on
    EVERY draw — not merely in expectation. With a CONSTANT-valued series,
    realizing v*(n-m)/n where m is the number skipped that draw, a properly
    rate-matched control (m == k_skip, every draw) collapses the 5-95%
    interval to a single point. An unmatched control (e.g. Bernoulli trials
    at the model's rate, m varying draw to draw) would NOT collapse it."""
    n, k_skip, v = 100, 37, 4.2
    y = np.full(n, v)
    mean, lo, hi = ts._rate_matched_random(y, k_skip, draws=200, seed=26)
    expected = v * (n - k_skip) / n
    assert mean == pytest.approx(expected)
    assert lo == pytest.approx(expected)
    assert hi == pytest.approx(expected), (
        "rate-matched random did not skip a constant k across draws — the "
        "interval failed to collapse under a constant-valued series")


def test_rate_matched_random_realizes_zero_on_skipped_days():
    y = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    mean, lo, hi = ts._rate_matched_random(y, k_skip=5, draws=10, seed=26)
    # skipping ALL days must realize exactly 0.0 every draw
    assert mean == 0.0 and lo == 0.0 and hi == 0.0

    mean0, lo0, hi0 = ts._rate_matched_random(y, k_skip=0, draws=10, seed=26)
    # skipping NO days must realize exactly the always-trade mean every draw
    assert mean0 == lo0 == hi0 == pytest.approx(y.mean())


# ------------------------------------------------------------- formatter

def test_formatter_requires_all_four_references():
    ok = {"mean": 0.0, "trade_rate": 1.0}
    with pytest.raises(ts.ReportingError):
        ts.format_reference_line(None, ok, ok, ok)
    with pytest.raises(ts.ReportingError):
        ts.format_reference_line(ok, None, ok, ok)
    with pytest.raises(ts.ReportingError):
        ts.format_reference_line(ok, ok, None, ok)
    with pytest.raises(ts.ReportingError):
        ts.format_reference_line(ok, ok, ok, None)


def test_formatter_prints_model_and_all_three_controls():
    ok = {"mean": 0.1, "trade_rate": 0.5}
    out = ts.format_reference_line(ok, ok, ok, ok)
    for label in ("always-trade", "rate-matched random", "shuffled-feature",
                  "MODEL (OOF)"):
        assert label in out


def test_main_prints_the_model_through_the_formatter_not_alone():
    """`main()` must never print the model's numbers directly — it has to go
    through `format_reference_line`, so a future edit that inlines a
    model-only print statement is caught here, not merely because the
    formatter function itself still works in isolation."""
    src = Path(ts.__file__).read_text()
    assert "print(format_reference_line(" in src


# ------------------------------------------------------------- population

def test_no_sealed_session_enters_the_population(monkeypatch):
    """A synthetic session dated AFTER TUNE_END must raise, never silently
    enter the tune population."""
    from datetime import timedelta

    class FakeSession:
        def __init__(self, day):
            self.day = day

    class FakeCache:
        """Stands in for `ts.CACHE`: a Path-like object whose `.glob()` says
        the cache is non-empty, satisfying the empty-cache guard only."""
        def glob(self, pattern):
            return iter(["NVDA_5m.parquet"])

    bad_day = TUNE_END + timedelta(days=1)
    monkeypatch.setattr(ts, "tune_sessions", lambda sessions: sessions)
    monkeypatch.setattr(ts, "load_sessions", lambda *a, **k: [FakeSession(bad_day)])
    monkeypatch.setattr(ts, "CACHE", FakeCache())

    with pytest.raises(ts.PopulationError, match="TUNE_END"):
        ts._collect_entries()


def test_expected_entry_count_is_enforced(monkeypatch):
    """A population that returns the wrong n must raise rather than silently
    running the pre-registration on a different sample."""
    monkeypatch.setattr(ts, "_collect_entries", lambda: [
        {"day": "2024-01-01", "entry_ts": "2024-01-01T10:05:00", "entry_hhmm": "10:05", "R": 0.1}
    ])
    with pytest.raises(ts.PopulationError, match="402"):
        ts.load_dataset()


# ------------------------------------------------------ no-look-ahead join

def test_join_rejects_a_decision_point_after_the_entry_bar():
    """The join must select the LATEST decision point at-or-before the entry
    bar. If only a later decision point exists, that is a coverage gap, not
    something to silently accept as the nearest available row."""
    feat = pd.DataFrame({
        "session_date": ["2024-01-02"],
        "decision_point": ["10:15"],           # strictly AFTER the entry
        "f0": [1.0],
    })
    entries = [{"day": "2024-01-02", "entry_ts": "2024-01-02T10:05:00",
               "entry_hhmm": "10:05", "R": 0.1}]
    with pytest.raises(ts.PopulationError, match="no feature row at or before"):
        ts._join_features(entries, feat, ["f0"])


def test_join_picks_the_latest_eligible_decision_point():
    feat = pd.DataFrame({
        "session_date": ["2024-01-02"] * 3,
        "decision_point": ["09:35", "09:45", "10:00"],
        "f0": [1.0, 2.0, 3.0],
    })
    entries = [{"day": "2024-01-02", "entry_ts": "2024-01-02T10:05:00",
               "entry_hhmm": "10:05", "R": 0.1}]
    X, y, days, meta = ts._join_features(entries, feat, ["f0"])
    assert X[0, 0] == 3.0                       # the 10:00 row, not an earlier one
    assert meta[0]["decision_point"] == "10:00"


# -------------------------------------------------------- real-population smoke

def test_real_population_matches_the_frozen_expectation():
    """The one integration check: real cache, real feature table, exact n."""
    X, y, days, meta, cols = ts.load_dataset()
    assert len(y) == ts.EXPECTED_TUNE_ENTRIES
    assert len(set(days.tolist())) == ts.EXPECTED_TUNE_ENTRIES  # 1 entry/day
    assert len(cols) == 59
    assert all(str(d) <= str(TUNE_END) for d in days)


def test_run_gates_end_to_end_writes_a_self_describing_result():
    X, y, days, meta, cols = ts.load_dataset()
    result = ts.run_gates(X, y, days, meta, cols)
    assert result["verdict"] in ("DEGENERATE_SKIPPER", "FAILED", "ALL_GATES_PASS")
    assert result["feature_columns"] == cols
    assert result["n"] == ts.EXPECTED_TUNE_ENTRIES


def test_t3_control_is_actually_rate_matched_to_the_model():
    """T3's entire validity rests on the control skipping the SAME NUMBER of
    days the model skipped — that is what isolates SELECTION from skip rate.

    `test_rate_matched_random_skips_exactly_k_every_draw` verifies the helper
    honours whatever k it is handed. Nothing verified that the CALLER hands it
    the model's real skip count. Setting `k_skip = 0` at the call site — which
    turns the "rate-matched" control into plain always-trade and makes T3 a
    comparison against the wrong thing entirely — passed the whole suite.

    That is the decorative-test shape specs/023 and specs/030 mutation logs
    already record three times (M9, M34, M37): an assertion that holds for a
    reason unrelated to the invariant it claims to protect.
    """
    X, y, days, meta, cols = ts.load_dataset()
    result = ts.run_gates(X, y, days, meta, cols)
    if not result["T2_NON_DEGENERATE"]["pass"]:
        pytest.skip("T2 short-circuited; T3 was correctly not computed")
    assert result["rate_matched_random"]["trade_rate"] == pytest.approx(
        result["T2_NON_DEGENERATE"]["trade_rate"]), (
        "the T3 control is NOT rate-matched to the model — T3 is then measuring "
        "skip rate, not selection, and its verdict means nothing")
