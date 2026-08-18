#!/usr/bin/env python3
"""Spec 023 tests for the four-book harness. Synthetic bars, synthetic
operator records, real Stockfish engine — no network, no ledger writes."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import four_books as fb

UTC = timezone.utc
T0 = datetime(2026, 8, 14, 14, 0, tzinfo=UTC)          # 10:00 ET


def _df(closes, start=T0):
    import pandas as pd
    idx = pd.date_range(start, periods=len(closes), freq="5min", tz="UTC")
    return pd.DataFrame({"Open": closes, "High": [c * 1.001 for c in closes],
                         "Low": [c * 0.999 for c in closes], "Close": closes,
                         "Volume": [1000] * len(closes)}, index=idx)


def _plan(**kw):
    base = dict(symbol="NVDA", direction=1, entry=100.0, qty=100.0, sl=99.0,
                tp1=101.0, tp2=102.0, trail_dist=0.5, exit_policy="DEFAULT")
    base.update(kw)
    # mirror load_plan's derived geometry without touching the filesystem
    base["risk_per_share"] = abs(base["entry"] - base["sl"])
    return base


def _record(verdict, ts=T0, expires_min=120, symbol="NVDA", rid="op-test-1"):
    return {"record_id": rid, "symbol": symbol, "verdict": verdict,
            "ts": ts.isoformat(),
            "expires_at": (ts + timedelta(minutes=expires_min)).isoformat()}


WIN = [99.5, 100.0, 100.6, 101.2, 101.8, 102.4, 102.0, 101.9, 101.8, 101.7]
LOSS = [99.5, 100.0, 99.6, 99.2, 98.9, 98.8]


def test_i8_all_books_consume_identical_bar_data():
    out = fb.run_session("NVDA", _df(WIN), _plan(), [])
    fps = {b["bars_fingerprint"] for b in out["books"].values()}
    assert len(fps) == 1


def test_baseline_takes_the_trade_and_scores_a_ladder():
    out = fb.run_book("baseline", _df(WIN), _plan(), [], "NVDA")
    assert out["entered"] is True
    assert out["r"] > 0
    assert len(out["legs"]) >= 2               # TP2 partial + final leg


def test_losing_session_scores_negative_via_022_math():
    out = fb.run_book("baseline", _df(LOSS), _plan(), [], "NVDA")
    assert out["entered"] is True
    assert out["r"] <= -0.9                    # stopped near -1R


def test_no_records_means_veto_equals_baseline():
    base = fb.run_book("baseline", _df(WIN), _plan(), [], "NVDA")
    veto = fb.run_book("veto", _df(WIN), _plan(), [], "NVDA")
    assert base["r"] == veto["r"]
    assert base["legs"] == veto["legs"]


def test_veto_skips_entry_when_tighten_record_active():
    out = fb.run_book("veto", _df(WIN), _plan(), [_record("TIGHTEN")], "NVDA")
    assert out["entered"] is False
    assert "vetoed" in out["why"]


def test_veto_ignores_expired_record():
    stale = _record("TIGHTEN", ts=T0 - timedelta(hours=3), expires_min=30)
    out = fb.run_book("veto", _df(WIN), _plan(), [stale], "NVDA")
    assert out["entered"] is True


def test_veto_ignores_record_sealed_after_entry():
    late = _record("EXIT", ts=T0 + timedelta(minutes=30))
    out = fb.run_book("veto", _df(WIN), _plan(), [late], "NVDA")
    assert out["entered"] is True              # the seal is the arrow of time


def test_veto_ignores_other_symbol():
    out = fb.run_book("veto", _df(WIN), _plan(),
                      [_record("EXIT", symbol="AMD")], "NVDA")
    assert out["entered"] is True


def test_select_requires_allow_baseline():
    none = fb.run_book("select", _df(WIN), _plan(), [], "NVDA")
    assert none["entered"] is False
    ok = fb.run_book("select", _df(WIN), _plan(),
                     [_record("ALLOW_BASELINE")], "NVDA")
    assert ok["entered"] is True


def test_full_is_veto_in_v1():
    for recs in ([], [_record("TIGHTEN")]):
        full = fb.run_book("full", _df(WIN), _plan(), recs, "NVDA")
        veto = fb.run_book("veto", _df(WIN), _plan(), recs, "NVDA")
        assert (full["entered"], full["r"]) == (veto["entered"], veto["r"])


def test_mid_trade_record_tightens_but_never_exits(monkeypatch):
    """A record sealed AFTER entry influences the held position (tighten) but
    cannot flatten it — the unpromoted cap, engine-side. Proven by observing
    every urgency value the engine actually received."""
    seen = []
    real = fb.decide_exit

    def spy(state):
        seen.append(state.urgent)
        return real(state)

    monkeypatch.setattr(fb, "decide_exit", spy)
    mid = _record("EXIT", ts=T0 + timedelta(minutes=10))
    veto = fb.run_book("veto", _df(WIN), _plan(), [mid], "NVDA")
    assert veto["entered"] is True
    assert veto["urgency_bars"] > 0
    assert "tighten" in seen
    assert "exit" not in seen                  # the cap, observed at the engine


def test_entry_never_triggered_is_a_no_trade():
    out = fb.run_book("baseline", _df([98.0, 98.2, 98.4]), _plan(), [], "NVDA")
    assert out["entered"] is False
    assert out["r"] == 0.0


def test_open_position_is_flattened_at_session_close_and_reconciles():
    # Rises past entry then drifts — never stops, never reaches TP2
    drift = [99.5, 100.1, 100.3, 100.2, 100.3, 100.25]
    out = fb.run_book("baseline", _df(drift), _plan(), [], "NVDA")
    assert out["entered"] is True
    assert out["legs"][-1]["reason"].startswith("session close flat")
    # realized_r_multileg would have raised if the legs did not reconcile
    assert isinstance(out["r"], float)


def test_unknown_book_raises():
    with pytest.raises(fb.HarnessError, match="unknown book"):
        fb.run_book("shadow", _df(WIN), _plan(), [], "NVDA")


# ---------------------------------------- random-veto control arm (review)

def test_random_arm_rate_matched_and_deterministic():
    """With a 100% realized veto rate the coin always loses -> always vetoed;
    with 0% (no records) it equals baseline; and reruns reproduce exactly."""
    all_veto = [_record("TIGHTEN", rid=f"op-{i}") for i in range(5)]
    a = fb.run_book("random_veto", _df(WIN), _plan(), all_veto, "NVDA")
    b = fb.run_book("random_veto", _df(WIN), _plan(), all_veto, "NVDA")
    assert a == b                              # deterministic
    assert a["entered"] is False and "random veto" in a["why"]
    none = fb.run_book("random_veto", _df(WIN), _plan(), [], "NVDA")
    base = fb.run_book("baseline", _df(WIN), _plan(), [], "NVDA")
    assert none["entered"] is True and none["r"] == base["r"]


def test_random_arm_ignores_verdict_content():
    """The coin must not depend on WHICH records exist, only the rate: same
    rate, different record ids -> identical outcome (zero information)."""
    r1 = [_record("TIGHTEN", rid="op-x"), _record("ALLOW_BASELINE", rid="op-y")]
    r2 = [_record("EXIT", rid="op-z"), _record("ALLOW_BASELINE", rid="op-w")]
    a = fb.run_book("random_veto", _df(WIN), _plan(), r1, "NVDA")
    b = fb.run_book("random_veto", _df(WIN), _plan(), r2, "NVDA")
    assert a["entered"] == b["entered"]


def test_session_artifact_carries_edge_vs_random():
    out = fb.run_session("NVDA", _df(WIN), _plan(), [])
    assert "edge_vs_random_r" in out
    assert out["books"]["random_veto"]["role"].startswith("CONTROL")
