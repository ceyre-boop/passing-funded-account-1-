"""Fault-injection tests for the extended-feature builder.

Three invariants must be provably enforced, not just documented:
  1. the look-ahead assertion at the classify/grade boundary
     (specs/005_BACKTEST.md),
  2. the macro join is D-1, never D (FRED publication lag), and
  3. a missing value never gets coerced to 0 — absence stays absence.
Each has a test below that fails if the guarding code is removed or reverted.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "daytrade"))
sys.path.insert(0, str(ROOT / "scripts"))

import build_extended_features as bef  # noqa: E402
import decision_ledger                 # noqa: E402
import macro_state                     # noqa: E402

ET = ZoneInfo("America/New_York")


# --------------------------------------------------------------------- fixtures

def _synthetic_bars(tmp_path: Path, symbol: str, sessions: list[date]) -> Path:
    """A tiny RTH-only 5m cache: 09:30-15:55 ET, every session, real OHLCV
    shape (monotone-ish, no NaNs) so decision_ledger.snapshot's arithmetic
    (tr, gap, compression, ma20) runs cleanly."""
    rows = []
    price = 100.0
    for sess in sessions:
        start = datetime(sess.year, sess.month, sess.day, 9, 30, tzinfo=ET)
        for i in range(78):                       # 09:30 .. 15:55, 5m bars
            ts = start + timedelta(minutes=5 * i)
            price += 0.01
            rows.append({"ts": ts, "Open": price, "High": price + 0.05,
                         "Low": price - 0.05, "Close": price + 0.02,
                         "Volume": 1000 + i})
    df = pd.DataFrame(rows).set_index("ts")
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{symbol}_5m.parquet"
    df.to_parquet(path)
    return cache


def _synthetic_macro(tmp_path: Path, dates: list[date]) -> Path:
    """One observation per calendar date, per series, so every D-1 lookup
    resolves to an exact, checkable date (no stale-fallback ambiguity)."""
    macro_dir = tmp_path / "macro"
    macro_dir.mkdir(parents=True, exist_ok=True)
    for sid in macro_state.SERIES:
        obs = {d.isoformat(): 1.0 + i for i, d in enumerate(dates)}
        (macro_dir / f"{sid}.json").write_text(json.dumps(
            {"series_id": sid, "why": "test", "n": len(obs),
             "observations": obs}))
    return macro_dir


@pytest.fixture
def sessions():
    return [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]


@pytest.fixture
def wired(tmp_path, monkeypatch, sessions):
    """Full synthetic environment: extended bar cache + macro series files,
    covering the session dates and a run-up before them (so D-1 always
    resolves)."""
    all_dates = [sessions[0] - timedelta(days=d) for d in range(10, 0, -1)] + sessions
    cache = _synthetic_bars(tmp_path, "NVDA", sessions)
    macro_dir = _synthetic_macro(tmp_path, all_dates)
    monkeypatch.setattr(macro_state, "SERIES_DIR", macro_dir)
    return cache


# ------------------------------------------------------ look-ahead assertion

def test_lookahead_assertion_catches_bar_data_after_asof():
    row = {"symbol": "NVDA", "session": "2024-01-02", "et_time": "09:35",
           "as_of": "2024-01-02T09:35:00-05:00",
           "max_data_ts": "2024-01-02T09:40:00-05:00"}   # 5 min AFTER as_of
    with pytest.raises(bef.FeatureBuildError, match="LOOK-AHEAD"):
        bef._assert_no_lookahead(row, {}, date(2024, 1, 2))


def test_lookahead_assertion_catches_macro_join_on_D_not_D_minus_1():
    """A macro value dated the session date itself (D) must be rejected —
    only D-1 or earlier is knowable at the open."""
    row = {"symbol": "NVDA", "session": "2024-01-03", "et_time": "09:35",
           "as_of": "2024-01-03T09:35:00-05:00",
           "max_data_ts": "2024-01-03T09:35:00-05:00"}
    macro = {"VIXCLS": {"value": 14.0, "as_of": "2024-01-03",   # == D, invalid
                         "stale_days": 0, "pctile_2y": None, "change_20d": None}}
    with pytest.raises(bef.FeatureBuildError, match="LOOK-AHEAD"):
        bef._assert_no_lookahead(row, macro, date(2024, 1, 3))


def test_lookahead_assertion_passes_on_a_valid_row():
    row = {"symbol": "NVDA", "session": "2024-01-03", "et_time": "09:35",
           "as_of": "2024-01-03T09:35:00-05:00",
           "max_data_ts": "2024-01-03T09:35:00-05:00"}
    macro = {"VIXCLS": {"value": 14.0, "as_of": "2024-01-02",   # D-1, valid
                         "stale_days": 1, "pctile_2y": None, "change_20d": None}}
    bef._assert_no_lookahead(row, macro, date(2024, 1, 3))     # must not raise


def test_full_build_never_produces_a_lookahead_row(wired, sessions):
    """Integration check: every row the real pipeline emits satisfies the
    assertion — this fails if the D-1 offset is ever silently dropped inside
    macro_block() rather than only in the standalone unit tests above."""
    df, skipped, empty = bef.build("NVDA", wired)
    assert len(df) > 0
    for col in df.columns:
        if col.endswith("_as_of") and col.startswith("macro_"):
            as_of_dates = pd.to_datetime(df[col].dropna()).dt.date
            session_dates = pd.to_datetime(
                df.loc[df[col].notna(), "session_date"]).dt.date
            assert (as_of_dates < session_dates).all(), (
                f"{col}: a macro as_of reached the session date or later")


def test_D_minus_1_join_bug_is_caught_by_the_boundary_assertion(wired, sessions, monkeypatch):
    """FAULT INJECTION: patch macro_block to join on D instead of D-1 (the
    exact regression this task calls out) and confirm the boundary assertion
    stops the build rather than silently shipping a leaky column."""
    def _buggy_macro_block(session_date):
        out, missing = {}, []
        for sid in macro_state.SERIES:
            obs = macro_state._load(sid)
            s = macro_state.series_at(sid, session_date, obs)   # BUG: D, not D-1
            out[sid] = s
        return out, missing

    monkeypatch.setattr(bef, "macro_block", _buggy_macro_block)
    with pytest.raises(bef.FeatureBuildError, match="LOOK-AHEAD"):
        bef.build("NVDA", wired)


# ------------------------------------------------------------- absence != 0

def test_missing_macro_series_is_null_with_a_reason_not_zero(monkeypatch):
    monkeypatch.setattr(macro_state, "_load", lambda sid: {})   # nothing cached
    macro, missing = bef.macro_block(date(2024, 1, 5))
    assert all(v is None for v in macro.values())
    assert len(missing) == len(macro_state.SERIES)
    assert all("no cached observations" in m for m in missing)


def test_flatten_missing_macro_stays_none_not_zero():
    row = {"symbol": "NVDA", "as_of": "2024-01-03T09:35:00-05:00",
           "et_time": "09:35", "max_data_ts": "2024-01-03T09:35:00-05:00",
           "regimes": ["UNREADABLE"],
           "last": 100.0, "day_open": 100.0, "prior_close": 99.0,
           "gap_pct": 1.0, "or_high": None, "or_low": None,
           "or_complete": False, "or_state": None, "tr5": 0.1,
           "tr20_median": 0.1, "compression": None, "trend_pct_vs_ma20": 0.0,
           "volume_last": 1000}
    macro = {"VIXCLS": None}
    flat = bef._flatten(date(2024, 1, 3), "09:35", row, macro, ["VIXCLS: none"])
    assert flat["macro_VIXCLS_value"] is None
    assert flat["macro_VIXCLS_value"] != 0
    assert flat["macro_VIXCLS_stale_days"] is None


def test_missing_macro_never_coerced_to_zero_end_to_end(wired, sessions, monkeypatch):
    """FAULT INJECTION: if a future edit coerces an absent macro value to
    0.0, this must fail — a real 0.0 VIX reading is impossible, so any zero
    in that column proves the coercion happened."""
    monkeypatch.setattr(macro_state, "_load", lambda sid: {})    # all series absent
    df, skipped, empty = bef.build("NVDA", wired)
    assert len(df) > 0
    for sid in macro_state.SERIES:
        col = f"macro_{sid}_value"
        assert df[col].isna().all(), f"{col}: missing series was not left null"
        assert not (df[col].fillna(-1) == 0).any()
    assert df["macro_unavailable"].notna().all()


# --------------------------------------------------------------- basic shape

def test_build_produces_rows_and_writes_sidecar_metadata(wired, sessions, tmp_path):
    df, skipped, empty = bef.build("NVDA", wired)
    assert len(df) > 0
    assert set(df["session_date"].unique()) <= {str(s) for s in sessions}
    assert "headlines_last_hour" not in df.columns

    out = tmp_path / "extended_features.parquet"
    sidecar = tmp_path / "extended_features.json"
    meta = bef._write(df, skipped, empty, "NVDA", out, sidecar)
    assert out.exists() and sidecar.exists()
    written = json.loads(sidecar.read_text())
    assert written["rows"] == len(df)
    assert "headlines_last_hour" in written["excluded_columns"]


def test_empty_cache_refuses_rather_than_falling_back(tmp_path):
    empty_cache = tmp_path / "empty"
    empty_cache.mkdir()
    from bars import BarDataError
    with pytest.raises(BarDataError):
        bef.build("NVDA", empty_cache)
