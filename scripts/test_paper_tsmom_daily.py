"""Tests for the paper TSMOM daily loop — sovereign/trend/SPEC.md.

`sovereign/trend/tsmom_engine.py` is owned by another agent and may not
exist yet when this file runs. If it's missing, a minimal stub is installed
into sys.modules HERE ONLY (never under sovereign/trend/) so
scripts/paper_tsmom_daily.py's `from sovereign.trend.tsmom_engine import
...` succeeds at import time; every test then monkeypatches `ptd.decide`
directly, exactly the way test_carry_scan.py patches carry_scan's own
module-level names.
"""
from __future__ import annotations

import dataclasses
import math
import sys
import types
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "daytrade"))

if "sovereign.trend.tsmom_engine" not in sys.modules:
    try:
        import sovereign.trend.tsmom_engine  # noqa: F401
    except ModuleNotFoundError:
        @dataclasses.dataclass(frozen=True)
        class _Decision:
            signal: int
            notional_frac: float
            realized_vol: float

        def _stub_decide(closes, as_of):
            return _Decision(signal=0, notional_frac=0.0, realized_vol=0.0)

        stub = types.ModuleType("sovereign.trend.tsmom_engine")
        stub.LOOKBACK = 252
        stub.VOL_WINDOW = 60
        stub.VOL_TARGET = 0.05
        stub.MAX_NOTIONAL = 1.0
        stub.Decision = _Decision
        stub.decide = _stub_decide
        sys.modules["sovereign.trend.tsmom_engine"] = stub

import paper_tsmom_daily as ptd  # noqa: E402
import fx_state as fx  # noqa: E402
from sovereign.trend.tsmom_engine import Decision  # noqa: E402
from sovereign.propfirm.firm_contracts import load_contract  # noqa: E402

HAIRCUT = load_contract("cti_1step").costs.swap_haircut_r_per_day
PAIR = "EURUSD=X"


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    log = tmp_path / "paper_tsmom_trades.jsonl"
    monkeypatch.setattr(ptd, "LOG_PATH", log)
    monkeypatch.setattr(ptd, "REPLAY_MODE", False)
    return log


@pytest.fixture
def bars(tmp_path, monkeypatch):
    """A closes series long enough to be irrelevant to the stub decide() —
    only _price_at() reads it directly in these tests."""
    monkeypatch.setattr(fx, "BARS", tmp_path)
    monkeypatch.setattr(fx, "fetch_bars", lambda **kw: 0)
    idx = pd.date_range("2026-08-01", periods=20, freq="D")
    df = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0,
                       "Close": [1.10 + 0.001 * i for i in range(20)]}, index=idx)
    df.index.name = "Date"
    path = tmp_path / f"{PAIR.replace('=', '_')}.parquet"
    df.to_parquet(path)
    monkeypatch.setattr(ptd, "_load_closes", lambda pair: df["Close"])
    return df


def _fixed_decide(signal=1, notional_frac=0.4, realized_vol=0.10):
    def _d(closes, as_of):
        return Decision(signal=signal, notional_frac=notional_frac,
                        realized_vol=realized_vol)
    return _d


def _no_decision_logger(monkeypatch):
    calls = []
    import sovereign.intelligence.decision_logger as dl
    monkeypatch.setattr(dl, "log_forex_decision",
                        lambda **kw: calls.append(("open", kw)))
    monkeypatch.setattr(dl, "update_outcome",
                        lambda **kw: calls.append(("close", kw)) or True)
    return calls


def test_opens_on_first_nonzero_signal(ledger, bars, monkeypatch):
    calls = _no_decision_logger(monkeypatch)
    monkeypatch.setattr(ptd, "decide", _fixed_decide(signal=1))
    as_of = date(2026, 8, 4)  # Tuesday, not a resize day
    results = ptd.run_tick(as_of, pairs=[PAIR])
    assert results[0]["action"] == "OPEN"
    recs = ptd._read(ledger)
    assert len(recs) == 1
    rec = recs[0]
    assert rec["status"] == "open"
    assert rec["direction"] == "LONG"
    assert rec["stop"] is None
    assert rec["strategy"] == "tsmom"
    assert rec["risk_pct"] == pytest.approx(ptd.RISK_PCT)
    assert rec["notional_frac"] == pytest.approx(0.4)
    assert any(c[0] == "open" for c in calls)


def test_holds_on_same_sign(ledger, bars, monkeypatch):
    calls = _no_decision_logger(monkeypatch)
    monkeypatch.setattr(ptd, "decide", _fixed_decide(signal=1, notional_frac=0.4))
    ptd.run_tick(date(2026, 8, 4), pairs=[PAIR])
    n_after_open = len(ptd._read(ledger))
    calls.clear()
    results = ptd.run_tick(date(2026, 8, 5), pairs=[PAIR])
    assert results[0]["action"] == "HOLD"
    assert len(ptd._read(ledger)) == n_after_open
    assert calls == []


def test_flip_closes_then_opens(ledger, bars, monkeypatch):
    _no_decision_logger(monkeypatch)
    monkeypatch.setattr(ptd, "decide", _fixed_decide(signal=1, notional_frac=0.4))
    ptd.run_tick(date(2026, 8, 4), pairs=[PAIR])
    monkeypatch.setattr(ptd, "decide", _fixed_decide(signal=-1, notional_frac=0.3))
    results = ptd.run_tick(date(2026, 8, 5), pairs=[PAIR])
    assert results[0]["action"] == "FLIP"
    recs = ptd._read(ledger)
    closed = [r for r in recs if r["status"] == "closed"]
    opened = [r for r in recs if r["status"] == "open"]
    assert len(closed) == 1 and len(opened) == 1
    assert closed[0]["exit_reason"] == "signal_flip"
    assert opened[0]["direction"] == "SHORT"


def test_flat_signal_closes(ledger, bars, monkeypatch):
    _no_decision_logger(monkeypatch)
    monkeypatch.setattr(ptd, "decide", _fixed_decide(signal=1, notional_frac=0.4))
    ptd.run_tick(date(2026, 8, 4), pairs=[PAIR])
    monkeypatch.setattr(ptd, "decide", _fixed_decide(signal=0, notional_frac=0.0))
    results = ptd.run_tick(date(2026, 8, 5), pairs=[PAIR])
    assert results[0]["action"] == "CLOSE"
    recs = ptd._read(ledger)
    closed = [r for r in recs if r["status"] == "closed"]
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "flat"
    assert not any(r["status"] == "open" for r in recs)


def test_resize_only_on_resize_day_and_past_band(ledger, bars, monkeypatch):
    _no_decision_logger(monkeypatch)
    monkeypatch.setattr(ptd, "decide", _fixed_decide(signal=1, notional_frac=0.40))
    ptd.run_tick(date(2026, 8, 4), pairs=[PAIR])  # Tuesday, opens at 0.40

    # Not a resize day (Wednesday) and big delta: must HOLD.
    monkeypatch.setattr(ptd, "decide", _fixed_decide(signal=1, notional_frac=0.80))
    results = ptd.run_tick(date(2026, 8, 5), pairs=[PAIR])
    assert results[0]["action"] == "HOLD"
    assert len([r for r in ptd._read(ledger) if r["status"] == "open"]) == 1

    # Resize day (Monday) but delta inside the 0.10 band (held is still 0.40
    # -- the prior HOLD never changed it): must HOLD.
    monkeypatch.setattr(ptd, "decide", _fixed_decide(signal=1, notional_frac=0.45))
    results = ptd.run_tick(date(2026, 8, 10), pairs=[PAIR])  # Monday
    assert results[0]["action"] == "HOLD"

    # Resize day AND past the band: must resize (close + open).
    monkeypatch.setattr(ptd, "decide", _fixed_decide(signal=1, notional_frac=0.20))
    results = ptd.run_tick(date(2026, 8, 17), pairs=[PAIR])  # Monday
    assert results[0]["action"] == "FLIP"
    recs = ptd._read(ledger)
    closed = [r for r in recs if r["status"] == "closed"]
    assert closed[-1]["exit_reason"] == "resize"
    open_rec = [r for r in recs if r["status"] == "open"][0]
    assert open_rec["notional_frac"] == pytest.approx(0.20)


def test_idempotent_on_second_run_same_day(ledger, bars, monkeypatch):
    calls = _no_decision_logger(monkeypatch)
    monkeypatch.setattr(ptd, "decide", _fixed_decide(signal=1, notional_frac=0.4))
    ptd.run_tick(date(2026, 8, 4), pairs=[PAIR])
    n1 = len(ptd._read(ledger))
    calls.clear()
    results = ptd.run_tick(date(2026, 8, 4), pairs=[PAIR])
    assert results[0]["action"] == "HOLD"
    assert len(ptd._read(ledger)) == n1
    assert calls == []


def test_r_matches_spec_formula_hand_computed(ledger, bars, monkeypatch):
    _no_decision_logger(monkeypatch)
    monkeypatch.setattr(ptd, "decide", _fixed_decide(signal=1, notional_frac=0.4))
    ptd.run_tick(date(2026, 8, 4), pairs=[PAIR])  # entry = closes[Aug 4] = 1.103
    monkeypatch.setattr(ptd, "decide", _fixed_decide(signal=0, notional_frac=0.0))
    ptd.run_tick(date(2026, 8, 5), pairs=[PAIR])  # exit = closes[Aug 5] = 1.104
    rec = [r for r in ptd._read(ledger) if r["status"] == "closed"][0]

    entry, exit_ = 1.103, 1.104
    spread = ptd.SPREADS[PAIR]
    risk_pct = 0.05 / math.sqrt(252)
    raw = (exit_ - entry) / entry
    cost = spread / entry + spread / exit_
    expected = (raw - cost) / risk_pct - HAIRCUT * 1
    assert rec["R"] == pytest.approx(round(expected, 6), abs=1e-6)


def test_replay_mode_writes_replay_flag_and_skips_decision_logger(
        ledger, bars, monkeypatch):
    monkeypatch.setattr(ptd, "REPLAY_MODE", True)
    calls = _no_decision_logger(monkeypatch)
    monkeypatch.setattr(ptd, "decide", _fixed_decide(signal=1, notional_frac=0.4))
    ptd.run_tick(date(2026, 8, 4), pairs=[PAIR])
    recs = ptd._read(ledger)
    assert recs[0]["replay"] is True
    assert calls == []


def test_missing_bar_is_loud_not_silent(ledger, bars, monkeypatch, capsys):
    """A pair whose decide()/price lookup fails must be reported loudly and
    skipped, never silently treated as flat/zero."""
    _no_decision_logger(monkeypatch)

    def _boom(closes, as_of):
        raise RuntimeError("insufficient history")

    monkeypatch.setattr(ptd, "decide", _boom)
    monkeypatch.setattr(fx, "fetch_bars", lambda **kw: None)
    results = ptd.run_tick(date(2026, 8, 4), pairs=[PAIR])
    assert results[0]["action"] == "ERROR"
    out = capsys.readouterr().out
    assert PAIR in out
    assert ptd._read(ledger) == []
