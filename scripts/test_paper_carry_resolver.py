"""Tests for the paper carry resolver — closes open paper trades against the
tape, reusing sovereign/execution/forex_exit_manager.py's broker-free exit
core. The one invariant proven here without a network call: a corrupted
ledger record makes the resolver FAIL LOUD, never silently skip."""
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import paper_carry_log as pcl  # noqa: E402
import paper_carry_resolver as pcr  # noqa: E402


def _good_rec(**over):
    base = dict(id="t1", status="open", pair="EURUSD=X", direction="LONG",
               entry=1.0850, stop=1.0790, entry_date="2026-08-01", risk_pct=0.01,
               qty=100_000, R=None, paper=True)
    base.update(over)
    return base


def test_valid_record_passes_validation():
    pcr.validate_open_record(_good_rec())  # must not raise


@pytest.mark.parametrize("bad, why", [
    ({k: v for k, v in _good_rec().items() if k != "stop"}, "missing field"),
    (_good_rec(direction="BUY"), "bad direction"),
    (_good_rec(entry=1.085, stop=1.085), "zero risk"),
    (_good_rec(entry="not-a-number"), "non-numeric entry"),
    (_good_rec(entry_date="not-a-date"), "unparseable date"),
    (_good_rec(entry_date=None), "null date"),
])
def test_corrupted_record_fails_loud_not_skipped(bad, why):
    """The acceptance criterion: one deliberately corrupted record makes the
    resolver RAISE, not quietly disappear from the run."""
    with pytest.raises(pcr.ResolverError):
        pcr.validate_open_record(bad)


def test_resolve_raises_before_any_market_call_on_corrupt_ledger(tmp_path, monkeypatch):
    """A corrupt record anywhere in the ledger must abort the WHOLE resolve()
    call before touching the network for ANY record — never silently drop
    just the bad one and proceed with the rest."""
    log = tmp_path / "paper.jsonl"
    monkeypatch.setattr(pcl, "LOG_PATH", log)
    recs = [_good_rec(id="ok1"), _good_rec(id="bad1", stop=1.0850)]  # entry==stop
    pcl._write(recs)

    def _boom(*a, **kw):
        raise AssertionError("market data must not be fetched when the ledger is corrupt")
    monkeypatch.setattr(pcr.carry_scan, "_make_backtester", _boom)

    with pytest.raises(pcr.ResolverError):
        pcr.resolve(date(2026, 8, 26), state_path=tmp_path / "state.json")


def test_oanda_style_mapping():
    assert pcr._to_oanda_style("EURUSD=X") == "EUR_USD"
    assert pcr._to_oanda_style("USDJPY=X") == "USD_JPY"


def test_unmappable_pair_refuses_rather_than_guessing():
    with pytest.raises(pcr.ResolverError):
        pcr._to_oanda_style("EU=X")


def test_resolve_skips_record_it_cannot_price_without_fabricating(tmp_path, monkeypatch):
    """A record that IS valid but whose market data cannot be fetched today is
    SKIPPED with a reason — not corrupted, not closed with an invented price."""
    log = tmp_path / "paper.jsonl"
    monkeypatch.setattr(pcl, "LOG_PATH", log)
    pcl._write([_good_rec()])

    class _FakeBt:
        pass

    monkeypatch.setattr(pcr.carry_scan, "_make_backtester", lambda as_of: _FakeBt())
    monkeypatch.setattr(pcr.carry_scan, "pair_bar",
                        lambda bt, pair: (_ for _ in ()).throw(RuntimeError("network down")))

    out = pcr.resolve(date(2026, 8, 26), state_path=tmp_path / "state.json")
    assert out.closed == []
    assert len(out.skipped) == 1
    assert "network down" in out.skipped[0]["reason"]
    # the ledger record must be untouched — still open, no fabricated exit
    remaining = pcl._read()
    assert remaining[0]["status"] == "open"
