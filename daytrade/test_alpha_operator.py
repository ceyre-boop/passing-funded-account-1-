#!/usr/bin/env python3
"""Spec 023 invariant tests for alpha_operator.py.

Every invariant named in the spec card gets a test here. The Claude call is
monkeypatched — these tests prove the machinery around the judgment, not the
judgment. No network, no API key, no cost.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import alpha_operator as ao
import news_claude
import bars as bars_mod
from context_directive import ContextDirective, ReceiverContext, evaluate
from forecast import ForecastError

UTC = timezone.utc
NOW = datetime(2026, 8, 14, 15, 0, tzinfo=UTC)


def _read(verdict="TIGHTEN", **kw):
    base = dict(
        bull="strong AI capex tailwind", base="drifts with the index",
        bear="valuation air pocket on any guide-down",
        invalidators=["closes below 170 on volume"],
        prob_bull_continuation=0.30, prob_bear_continuation=0.20,
        prob_range_consolidation=0.30, prob_failed_breakout=0.10,
        prob_risk_event=0.10,
        direction="up", horizon_min=60, verdict=verdict,
        abstain_reason="LOW_CONFIDENCE" if verdict == "ABSTAIN" else None,
        confidence=0.55, expires_min=45,
        evidence=[ao.EvidenceItem(headline="NVDA supplier reports record orders",
                                  evidence_type="PRODUCT", direction="bullish",
                                  severity=0.4, scope="symbol")])
    base.update(kw)
    return ao.OperatorRead(**base)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect every operator path into tmp; stub triggers, packet, and the
    priced API call. Returns a dict the tests mutate to steer the stub."""
    opdir = tmp_path / "operator"
    monkeypatch.setattr(ao, "OUT", tmp_path)
    monkeypatch.setattr(ao, "OPDIR", opdir)
    monkeypatch.setattr(ao, "RECORDS", opdir / "records.jsonl")
    monkeypatch.setattr(ao, "EV_LOG", opdir / "evidence.jsonl")
    monkeypatch.setattr(ao, "FC_LOG", opdir / "forecasts.jsonl")
    monkeypatch.setattr(ao, "STATE", opdir / "state.json")
    monkeypatch.setattr(ao, "DIRECTIVES", tmp_path / "directives.json")
    monkeypatch.setattr(ao, "PLAN", tmp_path / "plan.json")

    ctl = {"read": _read(), "calls": 0, "call_order": []}

    def fake_call(system, user, schema, *, model, cap, effort, kind):
        ctl["calls"] += 1
        ctl["call_order"].append("api")
        return ctl["read"], 0.001

    monkeypatch.setattr(news_claude, "_call", fake_call)
    monkeypatch.setattr(ao, "build_packet",
                        lambda s: ("PACKET", [], {"bar_age_min": 3.0}))
    monkeypatch.setattr(ao, "check_triggers",
                        lambda s, st, refresh: [("bar", "test fixture")])
    monkeypatch.setattr(ao, "_mark_state", lambda s, st: st)
    ctl["tmp"] = tmp_path
    return ctl


def _run(ctl, **kw):
    return ao.run_once("NVDA", cap=5.0, model="claude-sonnet-5",
                       refresh=False, **kw)


# ---------------------------------------------------------------- I1 + I2

def test_i1_directive_never_exceeds_tighten_authority_1(sandbox):
    for verdict in ("TIGHTEN", "EXIT"):
        sandbox["read"] = _read(verdict=verdict)
        _run(sandbox)
    payloads = json.loads((sandbox["tmp"] / "directives.json").read_text())
    assert payloads, "directives were expected"
    for p in payloads:
        assert p["interrupt"] == "TIGHTEN"
        assert p["authority_level"] == 1


def test_i1_runner_side_default_context_accepts_the_emitted_directive(sandbox):
    """The full path: what the operator writes, the runner's default
    ReceiverContext (granted_level=1) actually accepts as 'tighten'."""
    _run(sandbox)
    payloads = json.loads((sandbox["tmp"] / "directives.json").read_text())
    ds = [ContextDirective.from_dict(p) for p in payloads]
    dec = evaluate(ds, ReceiverContext(symbol="NVDA"),
                   now=ao._aware(payloads[0]["issued_at"]) + timedelta(minutes=1))
    assert dec.urgent == "tighten"
    assert not dec.rejected


def test_i2_directive_round_trips_unchanged(sandbox):
    _run(sandbox)
    p = json.loads((sandbox["tmp"] / "directives.json").read_text())[0]
    assert ContextDirective.from_dict(p).to_dict() == p


# --------------------------------------------------------------------- I3

def test_i3_sealed_record_exists_before_directive_is_observable(sandbox, monkeypatch):
    """At the moment the directive file is written, the sealed record must
    already be on disk. Proven by observing state from inside the write."""
    seen = {}
    real_write = ao._write_directive

    def spying_write(d):
        seen["record_on_disk"] = ao.RECORDS.exists() and bool(
            [r for r in ao._read_jsonl(ao.RECORDS)
             if r["directive"] and r["directive"]["directive_id"] == d.directive_id])
        return real_write(d)

    monkeypatch.setattr(ao, "_write_directive", spying_write)
    _run(sandbox)
    assert seen["record_on_disk"] is True


# --------------------------------------------------------------------- I5

def test_i5_no_trigger_means_zero_api_calls(sandbox, monkeypatch):
    monkeypatch.setattr(ao, "check_triggers", lambda s, st, refresh: [])
    assert _run(sandbox) == 0
    assert sandbox["calls"] == 0
    assert not ao.RECORDS.exists()


# --------------------------------------------------------------------- I6

def test_i6_abstain_emits_no_directive_and_carries_valid_reason(sandbox):
    sandbox["read"] = _read(verdict="ABSTAIN")
    _run(sandbox)
    assert not (sandbox["tmp"] / "directives.json").exists()
    rec = ao._read_jsonl(ao.RECORDS)[0]
    assert rec["directive"] is None
    assert rec["abstention"]["reason"] == "LOW_CONFIDENCE"


def test_i6_abstain_without_reason_raises(sandbox):
    sandbox["read"] = _read(verdict="ABSTAIN", abstain_reason=None)
    with pytest.raises(ao.OperatorError, match="abstain_reason"):
        _run(sandbox)


def test_allow_baseline_emits_no_directive_but_still_forecasts(sandbox):
    sandbox["read"] = _read(verdict="ALLOW_BASELINE")
    _run(sandbox)
    assert not (sandbox["tmp"] / "directives.json").exists()
    assert any(r["kind"] == "forecast" for r in ao._read_jsonl(ao.FC_LOG))


# --------------------------------------------------------------------- I9

def test_i9_exit_sealed_verbatim_directive_capped_with_suppression_noted(sandbox):
    sandbox["read"] = _read(verdict="EXIT")
    _run(sandbox)
    rec = ao._read_jsonl(ao.RECORDS)[0]
    assert rec["verdict"] == "EXIT"
    assert rec["suppressed_to"] == "TIGHTEN"
    assert rec["directive"]["interrupt"] == "TIGHTEN"
    fc = [r for r in ao._read_jsonl(ao.FC_LOG) if r["kind"] == "forecast"][0]
    assert fc["interrupt"] == "TIGHTEN"       # forecast interrupt is the capped one


# --------------------------------------------------------------------- I7

def test_i7_ledger_replay_reconstructs_identically(sandbox):
    for verdict in ("TIGHTEN", "ALLOW_BASELINE", "EXIT"):
        sandbox["read"] = _read(verdict=verdict)
        _run(sandbox)
    a, b = ao.load_ledger(), ao.load_ledger()
    fa = {fid: f.to_dict() for fid, f in a._forecasts.items()}
    fb = {fid: f.to_dict() for fid, f in b._forecasts.items()}
    assert fa == fb and len(fa) == 3


# -------------------------------------------------------------------- I10

def test_i10_corrupt_persistence_line_raises_on_load(sandbox):
    _run(sandbox)
    with ao.FC_LOG.open("a") as fh:
        fh.write("{not json\n")
    with pytest.raises(ao.OperatorError, match="corrupt audit line"):
        ao.load_ledger()


# ------------------------------------------------------- prob validation

def test_probs_outside_tolerance_refused(sandbox):
    sandbox["read"] = _read(prob_bull_continuation=0.9, prob_bear_continuation=0.9)
    with pytest.raises(ao.OperatorError, match="renorm tolerance"):
        _run(sandbox)


def test_probs_within_tolerance_renormalised_to_valid_forecast(sandbox):
    sandbox["read"] = _read(prob_bull_continuation=0.32)   # sums to 1.02
    _run(sandbox)
    fc = [r for r in ao._read_jsonl(ao.FC_LOG) if r["kind"] == "forecast"][0]
    assert abs(sum(fc["scenario_probs"].values()) - 1.0) < 1e-9


# ---------------------------------------------------------- resolver / I4

def _write_bars(tmp_path, monkeypatch, closes, start=NOW, spike=None):
    """Synthetic 5m parquet cache the resolver reads."""
    import pandas as pd
    idx = pd.date_range(start - timedelta(minutes=5 * 30), periods=30 + len(closes),
                        freq="5min", tz="UTC")
    base = [100.0] * 30 + list(closes)
    df = pd.DataFrame({"Open": base, "High": [c * 1.001 for c in base],
                       "Low": [c * 0.999 for c in base], "Close": base,
                       "Volume": [1000] * len(base)}, index=idx)
    if spike is not None:
        i = 30 + spike
        df.iloc[i, df.columns.get_loc("High")] = base[i] * 1.05
        df.iloc[i, df.columns.get_loc("Low")] = base[i] * 0.95
    cache = tmp_path / "bars"
    cache.mkdir(exist_ok=True)
    monkeypatch.setattr(bars_mod, "CACHE", cache)
    df.to_parquet(cache / "NVDA_5m.parquet")


def _seed_forecast(sandbox, as_of=NOW, horizon=60):
    sandbox["read"] = _read(horizon_min=horizon)
    import alpha_operator as ao_
    orig = ao_._utcnow
    ao_._utcnow = lambda: as_of
    try:
        _run(sandbox)
    finally:
        ao_._utcnow = orig
    return [r for r in ao_._read_jsonl(ao_.FC_LOG) if r["kind"] == "forecast"][0]["forecast_id"]


def test_i4_resolution_before_horizon_is_refused_forecast_stays_open(sandbox, monkeypatch):
    _write_bars(sandbox["tmp"], monkeypatch, [100.0] * 13)
    fid = _seed_forecast(sandbox, horizon=60)
    ao.resolve_open(now=NOW + timedelta(minutes=30))       # too early
    assert not any(r["kind"] == "resolution" for r in ao._read_jsonl(ao.FC_LOG))
    # and the 017 ledger itself refuses a hand-built early resolution
    from forecast import Resolution
    with pytest.raises(ForecastError, match="before the .*horizon"):
        ao.load_ledger().resolve(Resolution(
            forecast_id=fid, resolved_at=(NOW + timedelta(minutes=30)).isoformat(),
            outcome_scenario="range_consolidation", outcome_direction="flat",
            shock_occurred=False, was_stale=False))


def test_resolver_up_move_resolves_bull_continuation_hit(sandbox, monkeypatch):
    _write_bars(sandbox["tmp"], monkeypatch, [100 + i * 0.1 for i in range(13)])
    _seed_forecast(sandbox, horizon=60)
    ao.resolve_open(now=NOW + timedelta(minutes=61))
    res = [r for r in ao._read_jsonl(ao.FC_LOG) if r["kind"] == "resolution"][0]
    assert res["outcome_direction"] == "up"
    assert res["outcome_scenario"] == "bull_continuation"
    assert res["shock_occurred"] is False


def test_resolver_flat_tape_resolves_range_consolidation(sandbox, monkeypatch):
    _write_bars(sandbox["tmp"], monkeypatch, [100.0] * 13)
    _seed_forecast(sandbox, horizon=60)
    ao.resolve_open(now=NOW + timedelta(minutes=61))
    res = [r for r in ao._read_jsonl(ao.FC_LOG) if r["kind"] == "resolution"][0]
    assert res["outcome_scenario"] == "range_consolidation"


def test_resolver_spike_marks_shock_risk_event(sandbox, monkeypatch):
    _write_bars(sandbox["tmp"], monkeypatch, [100.0] * 13, spike=5)
    _seed_forecast(sandbox, horizon=60)
    ao.resolve_open(now=NOW + timedelta(minutes=61))
    res = [r for r in ao._read_jsonl(ao.FC_LOG) if r["kind"] == "resolution"][0]
    assert res["shock_occurred"] is True
    assert res["outcome_scenario"] == "risk_event"


def test_resolver_missing_bars_leaves_forecast_open(sandbox, monkeypatch):
    monkeypatch.setattr(bars_mod, "CACHE", sandbox["tmp"] / "nonexistent")
    _seed_forecast(sandbox, horizon=60)
    ao.resolve_open(now=NOW + timedelta(minutes=61))
    assert not any(r["kind"] == "resolution" for r in ao._read_jsonl(ao.FC_LOG))


def test_resolver_double_resolution_impossible(sandbox, monkeypatch):
    _write_bars(sandbox["tmp"], monkeypatch, [100.0] * 13)
    _seed_forecast(sandbox, horizon=60)
    ao.resolve_open(now=NOW + timedelta(minutes=61))
    ao.resolve_open(now=NOW + timedelta(minutes=62))       # second pass: no-op
    assert sum(1 for r in ao._read_jsonl(ao.FC_LOG) if r["kind"] == "resolution") == 1


# ------------------------------------------------------------- gradeable

def test_grade_runs_on_resolved_forecasts(sandbox, monkeypatch, capsys):
    _write_bars(sandbox["tmp"], monkeypatch, [100 + i * 0.1 for i in range(13)])
    _seed_forecast(sandbox, horizon=60)
    ao.resolve_open(now=NOW + timedelta(minutes=61))
    assert ao.grade("claude-sonnet-5") == 0
    outp = capsys.readouterr().out
    assert "brier" in outp and "directional_accuracy" in outp
