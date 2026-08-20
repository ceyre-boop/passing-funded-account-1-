#!/usr/bin/env python3
"""Spec 032 invariants (I41-I46) + the shared chain primitive."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chain
import fx_state as fx


def test_i46_real_fx_chain_intact():
    """THE guard: the live FX ledger's chain verifies on every suite run."""
    assert chain.verify(fx.LEDGER, quiet=True) == 0, \
        "fx_state chain broken — history was rewritten"


def test_i45_vocabulary_is_empty_until_a_label_earns_entry():
    """The equity ontology asserted 9 labels and 6 were decoration. No FX
    label may exist in code without an audit row behind it."""
    rows = chain.rows(fx.LEDGER)
    if not rows:
        pytest.skip("no fx rows yet")
    assert not {g for r in rows for g in r.get("regimes", [])}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(fx, "CARRY", tmp_path)
    monkeypatch.setattr(fx, "LEDGER", tmp_path / "fx_state.jsonl")
    monkeypatch.setattr(fx, "RATES", tmp_path / "rates")
    monkeypatch.setattr(fx, "BARS", tmp_path / "bars")
    (tmp_path / "rates").mkdir()
    (tmp_path / "bars").mkdir()
    return tmp_path


def _rates(sandbox, ccy, obs):
    (sandbox / "rates" / f"{ccy}.json").write_text(
        json.dumps({"series_id": "X", "currency": ccy, "observations": obs}))


def _bars(sandbox, pair="EURUSD=X", n=80):
    import pandas as pd
    idx = pd.bdate_range("2026-01-01", periods=n)
    v = [1.10 + i * 0.001 for i in range(n)]
    pd.DataFrame({"Open": v, "High": [x * 1.002 for x in v],
                  "Low": [x * 0.998 for x in v], "Close": v},
                 index=idx).to_parquet(
        sandbox / "bars" / f"{pair.replace('=', '_')}.parquet")


# --------------------------------------------------------------------- I41

def test_i41_row_contains_nothing_newer_than_its_session(sandbox, monkeypatch):
    _bars(sandbox)
    _rates(sandbox, "EUR", {"2026-01-01": 2.0})
    _rates(sandbox, "USD", {"2026-01-01": 4.0})
    monkeypatch.setattr(fx, "swap_per_day", lambda: 0.004)
    fx.build("EURUSD=X")
    for r in chain.rows(fx.LEDGER):
        assert r["max_data_ts"][:10] <= r["session_date"]


# --------------------------------------------------------------------- I42

def test_i42_stale_rate_leg_is_flagged_not_forward_filled(sandbox, monkeypatch):
    """A monthly series lagging 60 days must REPORT 60 days, never present
    itself as current (the JPY/AUD case)."""
    _bars(sandbox)
    _rates(sandbox, "EUR", {"2025-11-01": 2.0})       # deliberately stale
    _rates(sandbox, "USD", {"2026-01-01": 4.0})
    monkeypatch.setattr(fx, "swap_per_day", lambda: 0.004)
    fx.build("EURUSD=X")
    rows = chain.rows(fx.LEDGER)
    assert rows[0]["rate_diff"] == pytest.approx(-2.0)
    assert rows[0]["rate_diff_stale_days"] >= 60


def test_rate_at_never_reads_the_future(sandbox):
    _rates(sandbox, "EUR", {"2026-01-01": 2.0, "2026-06-01": 9.9})
    v, stale = fx._rate_at("EUR", date(2026, 3, 1))
    assert v == 2.0 and stale == 59          # the June print does not exist yet


# --------------------------------------------------------------------- I43

def test_i43_missing_rate_is_null_not_zero(sandbox, monkeypatch):
    _bars(sandbox)
    _rates(sandbox, "USD", {"2026-01-01": 4.0})       # EUR leg absent entirely
    monkeypatch.setattr(fx, "swap_per_day", lambda: 0.004)
    fx.build("EURUSD=X")
    r = chain.rows(fx.LEDGER)[0]
    assert r["rate_base"] is None and r["rate_diff"] is None


def test_i43_unavailable_event_fields_are_null(sandbox, monkeypatch):
    _bars(sandbox)
    _rates(sandbox, "EUR", {"2026-01-01": 2.0})
    _rates(sandbox, "USD", {"2026-01-01": 4.0})
    monkeypatch.setattr(fx, "swap_per_day", lambda: 0.004)
    fx.build("EURUSD=X")
    r = chain.rows(fx.LEDGER)[0]
    assert r["cb_calendar_days_to_next"] is None
    assert r["positioning_extreme"] is None


# --------------------------------------------------------------------- I44

def test_i44_swap_comes_from_the_contract_and_refuses_without_it(monkeypatch):
    assert fx.swap_per_day() == 0.004            # the real contract value
    import sovereign.propfirm.firm_contracts as fc
    monkeypatch.setattr(fc, "load_contract", lambda *a, **k: object())
    with pytest.raises(fx.FxStateError, match="swap_haircut_r_per_day"):
        fx.swap_per_day()


def test_weekend_cost_uses_three_days_of_swap(sandbox, monkeypatch):
    _bars(sandbox)
    _rates(sandbox, "EUR", {"2026-01-01": 2.0})
    _rates(sandbox, "USD", {"2026-01-01": 4.0})
    monkeypatch.setattr(fx, "swap_per_day", lambda: 0.004)
    fx.build("EURUSD=X")
    we = [r for r in chain.rows(fx.LEDGER) if r["weekend_next_session"]]
    assert we, "business-day bars must produce weekend crossings"
    assert all(r["weekend_cost_r"] == pytest.approx(0.012) for r in we)


# ------------------------------------------------------------- idempotence

def test_build_is_idempotent(sandbox, monkeypatch):
    _bars(sandbox)
    _rates(sandbox, "EUR", {"2026-01-01": 2.0})
    _rates(sandbox, "USD", {"2026-01-01": 4.0})
    monkeypatch.setattr(fx, "swap_per_day", lambda: 0.004)
    fx.build("EURUSD=X")
    n = len(chain.rows(fx.LEDGER))
    fx.build("EURUSD=X")
    assert len(chain.rows(fx.LEDGER)) == n


def test_build_refuses_without_bars(sandbox, monkeypatch):
    monkeypatch.setattr(fx, "swap_per_day", lambda: 0.004)
    with pytest.raises(fx.FxStateError, match="no bars"):
        fx.build("EURUSD=X")


# ----------------------------------------------------- the shared primitive

def test_chain_detects_tamper_and_deletion(tmp_path):
    p = tmp_path / "c.jsonl"
    for i in range(3):
        chain.append(p, {"a": i})
    assert chain.verify(p, quiet=True) == 0
    rows = chain.rows(p)
    rows[0]["a"] = 99
    p.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    assert chain.verify(p, quiet=True) == 1
    rows = [r for r in chain.rows(p)]
    del rows[1]
    p.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    assert chain.verify(p, quiet=True) == 1


def test_chain_corrupt_line_raises(tmp_path):
    p = tmp_path / "c.jsonl"
    chain.append(p, {"a": 1})
    with p.open("a") as fh:
        fh.write("{not json\n")
    with pytest.raises(chain.ChainError, match="not JSON"):
        chain.rows(p)
