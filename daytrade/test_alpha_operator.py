#!/usr/bin/env python3
"""Spec 023 invariant tests for alpha_operator.py.

Every invariant named in the spec card gets a test here. The Claude call is
monkeypatched — these tests prove the machinery around the judgment, not the
judgment. No network, no API key, no cost.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
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
                                  severity=0.4, scope="symbol")],
        expected_r_low=None if verdict == "ABSTAIN" else -0.5,
        expected_r_high=None if verdict == "ABSTAIN" else 1.5,
        invalidation_predicates=[] if verdict == "ABSTAIN" else
            [ao.InvalidationPredicate(kind="close_below", value=99.5)],
        recommended_policy=None,
        recommendation_reason="fixture default — no defensible policy preference")
    base.update(kw)
    if base.get("recommended_policy") is not None:
        base["recommendation_reason"] = None
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
    monkeypatch.setattr(ao, "SCORE_LOG", opdir / "score_history.jsonl")
    monkeypatch.setattr(ao, "STATE", opdir / "state.json")
    monkeypatch.setattr(ao, "DIRECTIVES", tmp_path / "directives.json")
    monkeypatch.setattr(ao, "PLAN", tmp_path / "plan.json")

    # Tests exercise the LIVE emission path deliberately — production default
    # is "log-only" since the spec-026 channel retirement.
    monkeypatch.setattr(ao, "EMISSION_MODE", "live")

    # Pin the operator's clock near NOW (a Friday, 11:00 ET — inside the RTH
    # tradability gate's window) so every test's forecasts are, by default,
    # scoreable claims regardless of the wall-clock time the suite actually
    # runs at. Tests that care about a specific as_of already monkeypatch
    # ao._utcnow themselves (e.g. _seed_forecast) — those overrides still win.
    # Ticks forward a few ms per call (never enough to leave the RTH window
    # in any of these fixtures) so record_id/forecast_id stay distinct across
    # repeated run_once() calls in one test, same as real time always did.
    clock = {"calls": 0}

    def _fake_utcnow():
        clock["calls"] += 1
        return NOW + timedelta(milliseconds=clock["calls"])

    monkeypatch.setattr(ao, "_utcnow", _fake_utcnow)

    ctl = {"read": _read(), "calls": 0, "call_order": []}

    def fake_call(system, user, schema, *, model, cap, effort, kind):
        ctl["calls"] += 1
        ctl["call_order"].append("api")
        return ctl["read"], 0.001

    monkeypatch.setattr(news_claude, "_call", fake_call)
    monkeypatch.setattr(ao, "build_packet",
                        lambda s, as_of=None: ("PACKET", [], {"bar_age_min": 3.0}))
    monkeypatch.setattr(ao, "check_triggers",
                        lambda s, st, refresh: ([("bar", "test fixture")],
                                                {"bar_ts": "fixture"}))
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
    monkeypatch.setattr(ao, "check_triggers", lambda s, st, refresh: ([], {}))
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

def test_i7_replay_matches_forecasts_captured_at_write_time(sandbox, monkeypatch):
    """NOT replay-vs-replay (that was tautological — Cato finding 1): capture
    each Forecast object the moment run_once records it, then assert the
    from-disk replay reproduces those exact dicts."""
    written = {}
    real_record = ao.ForecastLedger.record

    def spy(self, f):
        # setdefault: the FIRST sighting of each id is the genuine in-memory
        # write; later sightings are run_once's own replays and must not
        # overwrite the captured original (that would re-tautologise the test).
        written.setdefault(f.forecast_id, f.to_dict())
        return real_record(self, f)

    ao.ForecastLedger.record = spy
    try:
        for verdict in ("TIGHTEN", "ALLOW_BASELINE", "EXIT"):
            sandbox["read"] = _read(verdict=verdict)
            _run(sandbox)
    finally:
        ao.ForecastLedger.record = real_record     # replay must NOT feed the spy
    replayed = {fid: f.to_dict() for fid, f in ao.load_ledger()._forecasts.items()}
    assert len(written) == 3
    assert replayed == written


# -------------------------------------------------------------------- I10

def test_i10_corrupt_persistence_line_raises_on_load(sandbox):
    _run(sandbox)
    with ao.FC_LOG.open("a") as fh:
        fh.write("{not json\n")
    with pytest.raises(ao.OperatorError, match="corrupt audit line"):
        ao.load_ledger()


# ------------------------------------------- spec 024 discipline-layer tests

def test_i11_non_abstain_requires_expected_r_band(sandbox):
    sandbox["read"] = _read(verdict="TIGHTEN", expected_r_low=None,
                            expected_r_high=None)
    with pytest.raises(ao.OperatorError, match="pre-registration is required"):
        _run(sandbox)
    assert not ao.RECORDS.exists()             # refused before any write


def test_i11_inverted_band_refused(sandbox):
    sandbox["read"] = _read(verdict="TIGHTEN", expected_r_low=2.0,
                            expected_r_high=-1.0)
    with pytest.raises(ao.OperatorError, match="inverted"):
        _run(sandbox)


def test_i12_prereg_block_sealed_with_record(sandbox):
    _run(sandbox)
    rec = ao._read_jsonl(ao.RECORDS)[0]
    assert rec["pre_registration"]["expected_r_low"] == -0.5
    assert rec["pre_registration"]["expected_r_high"] == 1.5
    assert rec["pre_registration"]["invalidation_predicates"][0]["kind"] == "close_below"


def test_abstain_carries_no_prereg(sandbox):
    sandbox["read"] = _read(verdict="ABSTAIN")
    _run(sandbox)
    assert ao._read_jsonl(ao.RECORDS)[0]["pre_registration"] is None


def test_i13_resolver_scores_prereg_deterministically(sandbox, monkeypatch):
    _write_bars(sandbox["tmp"], monkeypatch, [100 + i * 0.1 for i in range(13)])
    (sandbox["tmp"] / "plan.json").write_text(json.dumps(
        {"symbol": "NVDA", "direction": "long", "entry": 100.0, "sl": 99.0,
         "qty": 100, "tp1": 101.0, "tp2": 102.0, "trail_dist": 0.5}))
    _seed_forecast(sandbox, horizon=60)
    ao.resolve_open(now=NOW + timedelta(minutes=61))
    scores = [r for r in ao._read_jsonl(ao.FC_LOG) if r["kind"] == "prereg_score"]
    assert len(scores) == 1
    s = scores[0]
    # up-drift of +1.2 over risk 1.0 → realized ~+1.2R, inside [-0.5, 1.5]
    assert s["r_in_band"] is True
    assert 1.0 < s["realized_r"] < 1.4
    # close_below 99.5 never fired on a rising tape
    assert s["predicates"][0]["fired"] is False
    # ledger replay tolerates the extra row kind
    ao.load_ledger()


def test_i13b_out_of_band_outcome_scores_false(sandbox, monkeypatch):
    """A rising tape against a pre-registered band of [-1.5, -0.5] must score
    r_in_band=False — the miss is recorded, not forgiven."""
    _write_bars(sandbox["tmp"], monkeypatch, [100 + i * 0.1 for i in range(13)])
    (sandbox["tmp"] / "plan.json").write_text(json.dumps(
        {"symbol": "NVDA", "direction": "long", "entry": 100.0, "sl": 99.0,
         "qty": 100, "tp1": 101.0, "tp2": 102.0, "trail_dist": 0.5}))
    sandbox["read"] = _read(expected_r_low=-1.5, expected_r_high=-0.5)
    orig = ao._utcnow
    ao._utcnow = lambda: NOW               # forecast as_of pinned to NOW
    try:
        _run(sandbox)
    finally:
        ao._utcnow = orig
    ao.resolve_open(now=NOW + timedelta(minutes=61))
    s = [r for r in ao._read_jsonl(ao.FC_LOG) if r["kind"] == "prereg_score"][0]
    assert s["r_in_band"] is False


def test_i15_session_cap_refuses_fourth_directive(sandbox):
    for i in range(4):
        sandbox["read"] = _read(verdict="TIGHTEN")
        _run(sandbox)
    recs = ao._read_jsonl(ao.RECORDS)
    emitted = [r for r in recs if r["directive"]]
    refused = [r for r in recs if r["emission_refused"]]
    assert len(emitted) == 3
    assert len(refused) == 1
    assert refused[0]["emission_refused"]["gate"] == "session_cap"
    assert refused[0]["verdict"] == "TIGHTEN"  # judgment sealed verbatim
    assert len(json.loads((sandbox["tmp"] / "directives.json").read_text())) == 3


def test_i16_codrift_pause_two_symbols_same_group(sandbox, monkeypatch):
    ao.OPDIR.mkdir(parents=True, exist_ok=True)
    (sandbox["tmp"] / "operator" / "correlated_groups.json").write_text(
        json.dumps({"SEMIS": ["NVDA", "AMD"]}))
    monkeypatch.setattr(ao, "GROUPS_FILE",
                        sandbox["tmp"] / "operator" / "correlated_groups.json")
    # a freshly-emitted AMD TIGHTEN directive inside the window
    ao._append_jsonl(ao.RECORDS, {
        "record_id": "op-amd", "symbol": "AMD", "verdict": "TIGHTEN",
        "ts": ao._iso(ao._utcnow() - timedelta(minutes=5)),
        "expires_at": ao._iso(ao._utcnow() + timedelta(minutes=40)),
        "directive": {"directive_id": "d-amd"}, "shadow": False})
    sandbox["read"] = _read(verdict="TIGHTEN")
    _run(sandbox)
    rec = [r for r in ao._read_jsonl(ao.RECORDS) if r["symbol"] == "NVDA"][0]
    assert rec["emission_refused"]["gate"] == "codrift_pause"
    assert rec["directive"] is None
    assert not (sandbox["tmp"] / "directives.json").exists()


def test_i16b_codrift_inert_without_groups_file(sandbox):
    sandbox["read"] = _read(verdict="TIGHTEN")
    _run(sandbox)
    rec = ao._read_jsonl(ao.RECORDS)[0]
    assert rec["emission_refused"] is None
    assert rec["directive"] is not None


def test_i17_guard_violation_never_blocks_emission(sandbox, monkeypatch):
    ao.OPDIR.mkdir(parents=True, exist_ok=True)
    limits_path = sandbox["tmp"] / "operator" / "limits.json"
    limits_path.write_text(json.dumps({
        "limits": {"max_total_open_risk_r": 1.0, "max_per_symbol_exposure_r": 0.5,
                   "max_correlated_exposure_r": 1.0, "max_unprotected_count": 0,
                   "daily_loss_lock_r": 2.0, "emergency_flatten_r": 3.0},
        "positions": [{"symbol": "NVDA", "open_risk_r": 2.0, "protected": False}],
        "realized_today_r": 0.0}))
    monkeypatch.setattr(ao, "LIMITS_FILE", limits_path)
    sandbox["read"] = _read(verdict="TIGHTEN")
    _run(sandbox)
    rec = ao._read_jsonl(ao.RECORDS)[0]
    assert rec["portfolio_advisory"]["violations"]          # G001/G002/G004 fired
    assert rec["directive"] is not None                     # ...and blocked nothing


def test_i22_shadow_writes_zero_directives(sandbox):
    sandbox["read"] = _read(verdict="TIGHTEN")
    ao.run_once("NVDA", cap=5.0, model="claude-sonnet-5", refresh=False,
                shadow=True)
    rec = ao._read_jsonl(ao.RECORDS)[0]
    assert rec["shadow"] is True
    assert rec["directive"] is not None        # sealed for the soak record...
    assert not (sandbox["tmp"] / "directives.json").exists()  # ...never emitted


def test_shadow_directives_do_not_count_toward_session_cap(sandbox):
    for _ in range(3):
        sandbox["read"] = _read(verdict="TIGHTEN")
        ao.run_once("NVDA", cap=5.0, model="claude-sonnet-5", refresh=False,
                    shadow=True)
    assert ao._session_directive_count("NVDA", ao._utcnow()) == 0
    sandbox["read"] = _read(verdict="TIGHTEN")
    _run(sandbox)                              # real emission still allowed
    assert len(json.loads((sandbox["tmp"] / "directives.json").read_text())) == 1


def test_i14_packet_point_in_time(tmp_path, monkeypatch):
    """Reconstruct the packet at T: nothing newer than T enters, and two
    builds at the same T are byte-identical. Uses the REAL build_packet —
    deliberately not the sandbox fixture, which stubs it."""
    import pandas as pd
    monkeypatch.setattr(ao, "OUT", tmp_path)
    monkeypatch.setattr(ao, "PLAN", tmp_path / "plan.json")
    sandbox = {"tmp": tmp_path}
    T = NOW
    idx = pd.date_range(T - timedelta(hours=2), T + timedelta(hours=1),
                        freq="5min", tz="UTC")
    vals = [100.0 + i * 0.01 for i in range(len(idx))]
    df = pd.DataFrame({"Open": vals, "High": vals, "Low": vals, "Close": vals,
                       "Volume": [1] * len(idx)}, index=idx)
    cache = sandbox["tmp"] / "bars"; cache.mkdir(exist_ok=True)
    monkeypatch.setattr(bars_mod, "CACHE", cache)
    df.to_parquet(cache / "NVDA_5m.parquet")
    # an event after T must be excluded
    (sandbox["tmp"] / "events_2026-08-14_NVDA.jsonl").write_text(
        json.dumps({"occurred_at": ao._iso(T + timedelta(minutes=10)),
                    "event_type": "LATE"}) + "\n" +
        json.dumps({"occurred_at": ao._iso(T - timedelta(minutes=10)),
                    "event_type": "EARLY"}) + "\n")
    p1, _, meta1 = ao.build_packet("NVDA", as_of=T)
    p2, _, meta2 = ao.build_packet("NVDA", as_of=T)
    assert p1 == p2                            # byte-identical reconstruction
    assert ao._aware(meta1["max_data_ts"]) <= T
    assert "LATE" not in p1 and "EARLY" in p1
    # the newest bar shown is at-or-before T
    assert meta1["bar_age_min"] >= 0


# ---------------------------------------- round-3 (durability review) tests

def test_stale_bars_mechanically_seal_abstain_without_api_call(sandbox, monkeypatch):
    """Stale data must not reach the model at all — code-authored
    ABSTAIN(STALE_CONTEXT), zero cost, no directive (review fix 2)."""
    for age in (None, 16.0, 17705.7):
        monkeypatch.setattr(ao, "build_packet",
                            lambda s, a=age, as_of=None: ("PACKET", [], {"bar_age_min": a}))
        assert _run(sandbox) == 0
    assert sandbox["calls"] == 0               # the model was never consulted
    recs = ao._read_jsonl(ao.RECORDS)
    assert len(recs) == 3
    for r in recs:
        assert r["verdict"] == "ABSTAIN"
        assert r["abstention"]["reason"] == "STALE_CONTEXT"
        assert r["directive"] is None and r["forecast"] is None
        assert r["cost_usd"] == 0.0
    assert not (sandbox["tmp"] / "directives.json").exists()


def test_fresh_bars_pass_the_stale_gate(sandbox):
    _run(sandbox)                              # fixture bar_age_min=3.0
    assert sandbox["calls"] == 1


# --------------------------------------------------------- trade_id join key

def test_sealed_record_carries_the_derivable_trade_id(sandbox):
    """The join the observed-predicted roadmap names as missing: records.jsonl
    rows must not seal `trade_id: None` — they carry the SAME
    `{symbol}-{ET session date}` key runner.py mints for the execution
    ledger, stamped at seal time from the record's own decision clock."""
    _run(sandbox)
    rec = ao._read_jsonl(ao.RECORDS)[0]
    assert rec["trade_id"] == ao._session_trade_id("NVDA", NOW)
    assert rec["trade_id"] == "NVDA-2026-08-14"


def test_stale_gate_abstention_also_carries_trade_id(sandbox, monkeypatch):
    """The mechanical stale-gate ABSTAIN path seals its own RECORDS row
    without ever calling the model — it must not skip the join key either."""
    monkeypatch.setattr(ao, "build_packet",
                        lambda s, as_of=None: ("PACKET", [], {"bar_age_min": None}))
    assert _run(sandbox) == 0
    rec = ao._read_jsonl(ao.RECORDS)[0]
    assert rec["verdict"] == "ABSTAIN"
    assert rec["trade_id"] == "NVDA-2026-08-14"


def test_trade_id_never_backfilled_on_historical_rows(sandbox):
    """A record made before trade_id existed has no trade_id — that is a fact
    about that row, never silently repaired by a later reader (CLAUDE.md
    rule 3: never fabricate a historical id)."""
    ao.RECORDS.parent.mkdir(parents=True, exist_ok=True)
    ao._append_jsonl(ao.RECORDS, {
        "record_id": "op-legacy-NVDA", "ts": "2026-08-01T12:00:00+00:00",
        "trigger": "bar", "symbol": "NVDA",
        "model_version": "alphazero/legacy", "prompt_version": "v0",
        "forecast_id": None, "trade_id": None, "evidence_ids": [],
        "both_sides": None, "invalidators": [], "verdict": "ABSTAIN",
        "confidence": 0.0, "expires_at": "2026-08-01T12:00:00+00:00",
        "suppressed_to": None, "directive": None, "abstention": None,
        "forecast": None, "evidence": [], "pre_registration": None,
        "emission_refused": None, "portfolio_advisory": None, "shadow": False,
        "packet_as_of": None, "packet_max_data_ts": None, "bar_age_min": None,
        "cost_usd": 0.0})
    rec = ao._read_jsonl(ao.RECORDS)[0]
    assert rec["trade_id"] is None


def test_crash_between_seal_and_derived_rows_is_recovered(sandbox):
    """The sealed record is the transaction: wipe the derived logs, and
    reconcile rebuilds them byte-honest from the seal (review fix 1)."""
    for verdict in ("TIGHTEN", "EXIT"):
        sandbox["read"] = _read(verdict=verdict)
        _run(sandbox)
    before = {fid: f.to_dict() for fid, f in ao.load_ledger()._forecasts.items()}
    ao.FC_LOG.unlink()                         # the crash: seal landed, derived lost
    ao.EV_LOG.unlink()
    assert ao._reconcile_derived() > 0
    after = {fid: f.to_dict() for fid, f in ao.load_ledger()._forecasts.items()}
    assert after == before
    ev = [r for r in ao._read_jsonl(ao.EV_LOG) if r["kind"] == "evidence"]
    assert len(ev) == 2


def test_reconcile_is_idempotent(sandbox):
    _run(sandbox)
    assert ao._reconcile_derived() == 0        # nothing missing, nothing duplicated
    assert len([r for r in ao._read_jsonl(ao.FC_LOG) if r["kind"] == "forecast"]) == 1


def test_position_trigger_scoped_to_symbol(sandbox, tmp_path):
    """Another symbol's session events must not fire our position trigger."""
    (sandbox["tmp"] / "events_2026-08-17_AMD.jsonl").write_text('{"e":1}\n{"e":2}\n')
    assert ao._event_line_count("NVDA") == 0
    (sandbox["tmp"] / "events_2026-08-17_NVDA.jsonl").write_text('{"e":1}\n')
    assert ao._event_line_count("NVDA") == 1
    assert [p.name for p in ao._event_files("NVDA")] == ["events_2026-08-17_NVDA.jsonl"]


# ------------------------------------------- Cato-round hardening tests

def test_hostile_preexisting_directive_is_refused_not_repersisted(sandbox):
    """An EMERGENCY/authority-4 payload already sitting in directives.json must
    block the operator's write, never be laundered through it."""
    hostile = {"directive_id": "evil", "scope": {"market": [], "sector": [],
               "symbols": ["NVDA"], "trade_id": None},
               "issued_at": "2026-08-14T13:00:00+00:00",
               "expires_at": "2026-08-14T20:00:00+00:00",
               "model_version": "rogue", "authority_level": 4,
               "schema_version": "1", "regime": {}, "thesis_state": None,
               "recommendation": None, "interrupt": "EMERGENCY",
               "confidence": 1.0, "evidence_ids": []}
    (sandbox["tmp"] / "directives.json").write_text(json.dumps([hostile]))
    with pytest.raises(ao.OperatorError, match="unpromoted cap"):
        _run(sandbox)
    kept = json.loads((sandbox["tmp"] / "directives.json").read_text())
    assert kept == [hostile]                   # file untouched, nothing laundered


def test_refused_judgment_leaves_no_orphaned_evidence(sandbox):
    """A forecast refused after the call must not leave evidence rows behind."""
    sandbox["read"] = _read(prob_bull_continuation=0.9, prob_bear_continuation=0.9)
    with pytest.raises(ao.OperatorError):
        _run(sandbox)
    assert not ao.EV_LOG.exists()
    assert not ao.FC_LOG.exists()
    assert not ao.RECORDS.exists()


def test_state_advances_even_when_judgment_is_refused(sandbox):
    """The call was priced; a persistently malformed response must cost one
    call, not one per invocation — the snapshot lands despite the raise."""
    sandbox["read"] = _read(verdict="ABSTAIN", abstain_reason=None)
    with pytest.raises(ao.OperatorError):
        _run(sandbox)
    assert json.loads(ao.STATE.read_text())["bar_ts"] == "fixture"


def test_out_of_range_horizon_is_refused_by_schema_not_clamped():
    with pytest.raises(Exception):             # pydantic ValidationError
        _read(horizon_min=100000)
    with pytest.raises(Exception):
        _read(horizon_min=0)
    with pytest.raises(Exception):
        _read(expires_min=0)


def test_directive_ttl_and_record_expiry_are_the_same_instant(sandbox):
    sandbox["read"] = _read(verdict="TIGHTEN", expires_min=45)
    _run(sandbox)
    rec = ao._read_jsonl(ao.RECORDS)[0]
    assert rec["directive"]["expires_at"] == rec["expires_at"]


def test_market_scoped_evidence_names_no_symbol(sandbox):
    sandbox["read"] = _read(evidence=[ao.EvidenceItem(
        headline="Fed surprises with emergency cut", evidence_type="MACRO",
        direction="mixed", severity=0.9, scope="market")])
    _run(sandbox)
    ev = [r for r in ao._read_jsonl(ao.EV_LOG) if r["kind"] == "evidence"][0]
    assert ev["scope"] == "market"
    assert ev["symbols"] == []


def test_resolver_refuses_forecast_with_no_sealed_record(sandbox, monkeypatch):
    _write_bars(sandbox["tmp"], monkeypatch, [100.0] * 13)
    _seed_forecast(sandbox, horizon=60)
    ao.RECORDS.unlink()                        # break the audit chain
    with pytest.raises(ao.OperatorError, match="no sealed record"):
        ao.resolve_open(now=NOW + timedelta(minutes=61))


def test_resolver_null_bar_age_counts_as_stale(sandbox, monkeypatch):
    """The mechanical stale gate now prevents a live run from ever producing a
    forecast with null bar_age — but the resolver's None→stale branch stays as
    defense for legacy/hand-edited records, so it is pinned by rewriting a
    sealed record's bar_age_min after the fact."""
    _write_bars(sandbox["tmp"], monkeypatch, [100.0] * 13)
    _seed_forecast(sandbox, horizon=60)
    rows = ao._read_jsonl(ao.RECORDS)
    rows[0]["bar_age_min"] = None
    ao.RECORDS.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    ao.resolve_open(now=NOW + timedelta(minutes=61))
    res = [r for r in ao._read_jsonl(ao.FC_LOG) if r["kind"] == "resolution"][0]
    assert res["was_stale"] is True


def test_resolver_insufficient_prior_bars_stays_open(sandbox, monkeypatch):
    # Only 10 bars before as_of — the 20-bar shock baseline is undefined
    import pandas as pd
    idx = pd.date_range(NOW - timedelta(minutes=50), periods=23, freq="5min", tz="UTC")
    vals = [100.0] * 23
    df = pd.DataFrame({"Open": vals, "High": vals, "Low": vals, "Close": vals,
                       "Volume": [1] * 23}, index=idx)
    cache = sandbox["tmp"] / "bars"; cache.mkdir(exist_ok=True)
    monkeypatch.setattr(bars_mod, "CACHE", cache)
    df.to_parquet(cache / "NVDA_5m.parquet")
    _seed_forecast(sandbox, horizon=60)
    ao.resolve_open(now=NOW + timedelta(minutes=61))
    assert not any(r["kind"] == "resolution" for r in ao._read_jsonl(ao.FC_LOG))


@pytest.mark.parametrize("target", ["RECORDS", "EV_LOG", "FC_LOG"])
def test_i10_corrupt_line_raises_across_all_jsonl_files(sandbox, target):
    _run(sandbox)
    path = getattr(ao, target)
    with path.open("a") as fh:
        fh.write("{not json\n")
    with pytest.raises(ao.OperatorError, match="corrupt audit line"):
        ao._read_jsonl(path)


def test_corrupt_state_json_raises(sandbox):
    ao.STATE.parent.mkdir(parents=True, exist_ok=True)
    ao.STATE.write_text("{broken")
    with pytest.raises(ao.OperatorError, match="not JSON"):
        ao._load_state()


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


# ------------------------------------------------- RTH tradability gate (024)

def _at(as_of, sandbox, **kw):
    """Run once with the operator's clock pinned to `as_of` (aware UTC)."""
    orig = ao._utcnow
    ao._utcnow = lambda: as_of
    try:
        return _run(sandbox, **kw)
    finally:
        ao._utcnow = orig


def test_rth_gate_refuses_forecast_with_no_tradable_session(sandbox):
    """23:00 ET Friday — market shut, same shape as the real stuck rows
    (fc-op-20260816-030041 etc). The judgment is still sealed; the claim
    is not: forecast is null with a machine-readable reason, and nothing
    lands in FC_LOG for it."""
    as_of = datetime(2026, 8, 15, 3, 0, tzinfo=UTC)     # 23:00 ET Fri
    _at(as_of, sandbox)
    rec = ao._read_jsonl(ao.RECORDS)[0]
    assert rec["forecast"] is None
    assert rec["forecast_id"] is None
    assert rec["forecast_refused"] == {
        "reason": "window has no tradable session",
        "as_of": ao._iso(as_of), "horizon_min": 60,
        "detail": f"[{ao._iso(as_of)} +60m] never touches a tradable RTH session"}
    # the judgment itself (verdict, both_sides, directive) is still sealed —
    # only the scoreable claim is refused
    assert rec["verdict"] == "TIGHTEN"
    assert not any(r["kind"] == "forecast" for r in ao._read_jsonl(ao.FC_LOG))


def test_rth_gate_clamps_horizon_that_crosses_the_close(sandbox):
    """15:53 ET Friday — 2 minutes of session left before the 15:55 close.
    Not refused (some tradable minutes exist); clamped instead, with the
    original claim preserved in horizon_clamped_from so the grader can see
    it was never a free 60-minute claim past the close."""
    as_of = datetime(2026, 8, 14, 19, 53, tzinfo=UTC)   # 15:53 ET Fri
    _at(as_of, sandbox)
    rec = ao._read_jsonl(ao.RECORDS)[0]
    assert rec["forecast"] is not None
    assert rec["forecast"]["horizon_min"] == 2
    assert rec["forecast"]["horizon_clamped_from"] == 60
    fc = [r for r in ao._read_jsonl(ao.FC_LOG) if r["kind"] == "forecast"][0]
    assert fc["horizon_min"] == 2 and fc["horizon_clamped_from"] == 60


def test_rth_gate_leaves_in_session_forecast_unclamped(sandbox):
    """A forecast issued mid-RTH (the sandbox default clock, 11:00 ET) is
    unaffected: full horizon, no clamp, no refusal."""
    _run(sandbox)
    rec = ao._read_jsonl(ao.RECORDS)[0]
    assert rec["forecast_refused"] is None
    assert rec["forecast"]["horizon_min"] == 60
    assert rec["forecast"]["horizon_clamped_from"] is None


def _seed_stuck_forecast(sandbox, fid: str, as_of_iso: str, horizon: int = 60,
                         model: str = "claude-sonnet-5") -> None:
    """Directly inject a raw forecast row, bypassing the emission gate — this
    is exactly the shape of the 4 real rows that predate this fix (minted by
    a manual run that bypassed operator_tick.sh's own session guard)."""
    ao.OPDIR.mkdir(parents=True, exist_ok=True)
    ao._append_jsonl(ao.FC_LOG, {
        "kind": "forecast", "forecast_id": fid,
        "model_version": f"alpha-operator-v1/{model}", "prompt_version": "op-1",
        "as_of": as_of_iso, "symbol": "NVDA", "horizon_min": horizon,
        "scenario_probs": {"bull_continuation": 0.7, "range_consolidation": 0.3},
        "direction": "up", "recommendation": None, "interrupt": None,
        "confidence": 0.5, "evidence_ids": []})


def test_resolver_marks_overnight_stuck_forecast_unresolvable(sandbox, monkeypatch):
    """The real fc-op-20260816-030041 case: issued 23:00 ET Fri, zero bars
    ever possible. Must become `unresolvable`, never `resolution` with a
    guessed outcome."""
    monkeypatch.setattr(bars_mod, "CACHE", sandbox["tmp"] / "nonexistent")
    as_of = "2026-08-16T03:00:41.537973+00:00"
    _seed_stuck_forecast(sandbox, "fc-stuck-overnight", as_of)
    ao.resolve_open(now=ao._aware(as_of) + timedelta(minutes=120))
    rows = ao._read_jsonl(ao.FC_LOG)
    unresolvable = [r for r in rows if r["kind"] == "unresolvable"]
    assert len(unresolvable) == 1
    assert unresolvable[0]["forecast_id"] == "fc-stuck-overnight"
    assert unresolvable[0]["reason"] == "window has no tradable session"
    assert not any(r["kind"] == "resolution" for r in rows)
    led = ao.load_ledger()                     # tolerates the new row kind
    assert led.is_unresolvable("fc-stuck-overnight")


def test_resolver_marks_close_crossing_stuck_forecast_unresolvable(sandbox, monkeypatch):
    """The real fc-op-20260817-195335 case: issued 15:53 ET Mon, +60m horizon
    ends past the close — at most 1 bar can ever exist in the window."""
    monkeypatch.setattr(bars_mod, "CACHE", sandbox["tmp"] / "nonexistent")
    as_of = "2026-08-17T19:53:35.400001+00:00"
    _seed_stuck_forecast(sandbox, "fc-stuck-close", as_of)
    ao.resolve_open(now=ao._aware(as_of) + timedelta(minutes=120))
    unresolvable = [r for r in ao._read_jsonl(ao.FC_LOG) if r["kind"] == "unresolvable"]
    assert len(unresolvable) == 1
    assert unresolvable[0]["forecast_id"] == "fc-stuck-close"


def test_resolver_reports_unresolvable_separately_from_still_open(sandbox, monkeypatch, capsys):
    monkeypatch.setattr(bars_mod, "CACHE", sandbox["tmp"] / "nonexistent")
    _seed_stuck_forecast(sandbox, "fc-stuck-1", "2026-08-16T03:00:41.537973+00:00")
    _seed_stuck_forecast(sandbox, "fc-stuck-2", "2026-08-16T04:40:34.709506+00:00")
    ao.resolve_open(now=datetime(2026, 8, 18, 12, 0, tzinfo=UTC))
    out = capsys.readouterr().out
    assert "2 unresolvable (window has no tradable session)" in out
    assert "0 still open" in out


# ------------------------------------------------------------- gradeable

def test_grade_runs_on_resolved_forecasts(sandbox, monkeypatch, capsys):
    _write_bars(sandbox["tmp"], monkeypatch, [100 + i * 0.1 for i in range(13)])
    _seed_forecast(sandbox, horizon=60)
    ao.resolve_open(now=NOW + timedelta(minutes=61))
    assert ao.grade("claude-sonnet-5") == 0
    outp = capsys.readouterr().out
    assert "brier" in outp and "directional_accuracy" in outp


# ------------------------------------------------------- regret wiring (D3)
#
# `forecast.Resolution.policy_regret_r` has a consumer (promotion_decision)
# and, until this wiring, no supplier — THE_BIG_PLAN.md's D3. `_regret_for`
# grades the forecast's recommended policy against the shadow tournament
# using the plan on file and the actual resolution-window bars.

_REGRET_PLAN = {"symbol": "NVDA", "direction": 1, "entry": 200.0, "qty": 100.0,
                "sl": 199.0, "tp1": 201.0, "tp2": 202.14, "trail_dist": 0.5,
                "goal_fraction": 0.5}

# Same fixture as test_shadow_regret.py's CYCLES: a genuine peak-and-fade so
# DEFEND/HARVEST/RIDE diverge and the recommended policy (RIDE) leaves real,
# non-zero R on the table relative to the best counterfactual (HARVEST).
_REGRET_CYCLES_CLOSING = [(200.4, "09:35"), (201.2, "09:40"), (202.5, "09:45"),
                         (203.8, "09:50"), (203.1, "09:55"), (202.4, "10:00"),
                         (201.9, "10:05")]

# Mild path: never reaches tp1 or sl, so every policy in the tournament stays
# OPEN through the window's end — the "regret is unmeasurable" fixture.
_REGRET_CYCLES_OPEN = [(200.1, "09:35"), (200.2, "09:40"), (200.0, "09:45")]


def _window_df(cycles, date="2026-08-14"):
    import pandas as pd
    idx = pd.DatetimeIndex([pd.Timestamp(f"{date} {t}", tz="America/New_York")
                            .tz_convert("UTC") for _, t in cycles])
    return pd.DataFrame({"Close": [p for p, _ in cycles]}, index=idx)


def _regret_forecast(recommendation, *, fid="fc-regret-1"):
    from forecast import Forecast
    return Forecast(forecast_id=fid, model_version="alpha-operator-v1/claude",
                    prompt_version="op-1", as_of=NOW.isoformat(), symbol="NVDA",
                    horizon_min=60, scenario_probs={"bull_continuation": 0.6,
                                                    "range_consolidation": 0.4},
                    direction="up", recommendation=recommendation,
                    interrupt=None, confidence=0.6)


def test_regret_none_when_no_recommendation_never_zero(sandbox, monkeypatch):
    """Fault injection target: a forecast that named no policy has NOTHING to
    grade. The wiring must leave policy_regret_r None with a stated reason —
    silently defaulting to 0.0 would be indistinguishable from 'graded, no
    regret found' (CLAUDE.md rule 3)."""
    monkeypatch.setattr(ao, "PLAN", sandbox["tmp"] / "plan.json")
    (sandbox["tmp"] / "plan.json").write_text(json.dumps(_REGRET_PLAN))
    f = _regret_forecast(None)
    window = _window_df(_REGRET_CYCLES_CLOSING)
    val, reason = ao._regret_for(f, window)
    assert val is None
    assert val != 0.0
    assert "recommendation" in reason


def test_regret_none_when_still_open_never_zero(sandbox, monkeypatch):
    """Fault injection target: regret.grade() itself refuses an open trade.
    The wiring must respect that refusal — never substitute 0.0 for a
    position that never closed within the resolution window."""
    monkeypatch.setattr(ao, "PLAN", sandbox["tmp"] / "plan.json")
    (sandbox["tmp"] / "plan.json").write_text(json.dumps(_REGRET_PLAN))
    f = _regret_forecast("DEFEND")
    window = _window_df(_REGRET_CYCLES_OPEN)
    val, reason = ao._regret_for(f, window)
    assert val is None
    assert val != 0.0
    assert "did not close" in reason or "open" in reason.lower()


def test_regret_computed_from_real_closed_outcome(sandbox, monkeypatch):
    """Happy path: recommended policy actually closes within the window ->
    a real, non-zero regret number, matching an independent hand computation
    via regret.grade() + shadow.run_shadow() over the identical fixture."""
    monkeypatch.setattr(ao, "PLAN", sandbox["tmp"] / "plan.json")
    (sandbox["tmp"] / "plan.json").write_text(json.dumps(_REGRET_PLAN))
    f = _regret_forecast("RIDE")
    window = _window_df(_REGRET_CYCLES_CLOSING)
    val, reason = ao._regret_for(f, window)
    assert reason == ""
    assert val is not None
    assert val == pytest.approx(-0.04999999999999716, abs=1e-6)
    assert val != 0.0


def test_resolve_open_never_drops_resolution_when_regret_unmeasurable(sandbox, monkeypatch):
    """Fault injection target: an unmeasurable regret must not cost the
    resolution itself. The forecast still resolves; only policy_regret_r
    (and its recorded reason) reflect the missing input."""
    _write_bars(sandbox["tmp"], monkeypatch, [100 + i * 0.1 for i in range(13)])
    fid = _seed_forecast(sandbox, horizon=60)          # production forecasts
    ao.resolve_open(now=NOW + timedelta(minutes=61))   # never set recommendation
    rows = ao._read_jsonl(ao.FC_LOG)
    res = [r for r in rows if r["kind"] == "resolution"]
    assert len(res) == 1 and res[0]["forecast_id"] == fid
    assert res[0]["policy_regret_r"] is None
    regret_rows = [r for r in rows if r["kind"] == "regret"]
    assert len(regret_rows) == 1
    assert regret_rows[0]["policy_regret_r"] is None
    assert regret_rows[0]["reason"]                     # WHY is recorded, not silent
    # and load_ledger() round-trips it without choking on the new row kind
    led = ao.load_ledger()
    assert led.forecast(fid).forecast_id == fid


def test_promotion_gate_reachable_with_real_regret_data():
    """The end of D3: once real graded outcomes supply policy_regret_r, the
    promotion gate's REGRET_DATA_MISSING is no longer structurally guaranteed
    — it depends on the model's actual track record, not on a wiring gap."""
    from dataclasses import replace
    from forecast import (Forecast, ForecastLedger, PromotionThresholds,
                          Resolution, promotion_decision)
    window = _window_df(_REGRET_CYCLES_CLOSING)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        plan_path = pathlib.Path(td) / "plan.json"
        plan_path.write_text(json.dumps(_REGRET_PLAN))
        import alpha_operator as ao_
        orig_plan = ao_.PLAN
        ao_.PLAN = plan_path
        try:
            led = ForecastLedger()
            n = 60
            for i in range(n):
                fid = f"fc-regret-{i}"
                f = Forecast(forecast_id=fid, model_version="alpha-operator-v1/claude",
                            prompt_version="op-1", as_of=NOW.isoformat(), symbol="NVDA",
                            horizon_min=60, scenario_probs={"bull_continuation": 0.6,
                                                            "range_consolidation": 0.4},
                            direction="up", recommendation="RIDE", interrupt=None,
                            confidence=0.6)
                led.record(f)
                val, _ = ao_._regret_for(f, window)
                led.resolve(Resolution(
                    forecast_id=fid, resolved_at=(NOW + timedelta(minutes=60)).isoformat(),
                    outcome_scenario="bull_continuation", outcome_direction="up",
                    shock_occurred=False, was_stale=False, policy_regret_r=val))
            rep = led.grade("alpha-operator-v1/claude", oos=True)
            assert rep.worst_policy_regret is not None
            inc = replace(rep, brier=rep.brier + 0.1)   # a strictly worse incumbent
            d = promotion_decision(inc, rep, PromotionThresholds(),
                                   per_regime_incumbent={"trend": 0.1},
                                   per_regime_challenger={"trend": 0.1},
                                   ref="p")
            assert "REGRET_DATA_MISSING" not in d.failed_gates
        finally:
            ao_.PLAN = orig_plan


# ------------------------------------------- D3 link 1: recommendation
# (THE_BIG_PLAN.md — a populated Forecast.recommendation, never an authority
# change: recorded and graded only, never copied onto the ContextDirective
# this same read produces.)

def test_recommended_policy_flows_into_forecast_recommendation(sandbox):
    """A model that names a policy has it recorded on the sealed forecast."""
    sandbox["read"] = _read(recommended_policy="RIDE")
    _run(sandbox)
    rows = ao._read_jsonl(ao.FC_LOG)
    fc = [r for r in rows if r["kind"] == "forecast"][0]
    assert fc["recommendation"] == "RIDE"
    assert fc["recommendation_reason"] is None


def test_declined_recommendation_requires_a_reason(sandbox):
    """A decline (recommended_policy=None) with no recommendation_reason must
    refuse the whole judgment, not silently seal an unauditable None."""
    sandbox["read"] = _read(recommended_policy=None, recommendation_reason=None)
    with pytest.raises(ao.OperatorError, match="recommendation_reason"):
        _run(sandbox)


def test_declined_recommendation_records_its_reason(sandbox):
    sandbox["read"] = _read(recommended_policy=None,
                            recommendation_reason="no defensible edge either way")
    _run(sandbox)
    rows = ao._read_jsonl(ao.FC_LOG)
    fc = [r for r in rows if r["kind"] == "forecast"][0]
    assert fc["recommendation"] is None
    assert fc["recommendation_reason"] == "no defensible edge either way"


def test_unknown_recommended_policy_refused_not_passed_through(sandbox):
    """Defence in depth: even a read that bypasses pydantic's own Literal
    enforcement (model_copy(update=...) skips validation, matching what the
    non-strict-mode JSON-Schema fallback path in news_claude._call() could
    let through) must still be refused at the call site, never sealed."""
    sandbox["read"] = _read().model_copy(
        update={"recommended_policy": "MOON", "recommendation_reason": None})
    with pytest.raises(ao.OperatorError, match="MOON"):
        _run(sandbox)


def test_recommendation_never_reaches_the_directive(sandbox):
    """The authority boundary: a granted_level-1 receiver never sees a policy
    candidate from this call site, however confident the recommendation.
    ContextDirective.recommendation is None on every directive this path
    emits, and evaluate() at the production default (level 1) surfaces no
    policy candidate either."""
    sandbox["read"] = _read(verdict="TIGHTEN", recommended_policy="RIDE")
    _run(sandbox)
    payloads = json.loads((sandbox["tmp"] / "directives.json").read_text())
    assert payloads
    for p in payloads:
        assert p["recommendation"] is None
    ds = [ContextDirective.from_dict(p) for p in payloads]
    dec = evaluate(ds, ReceiverContext(symbol="NVDA"))
    assert dec.policy_candidate is None


# ------------------------------------------- D3 link 3: promotion_status()
# (THE_BIG_PLAN.md — forecast.promotion_decision() made REACHABLE and
# AUDITABLE, without ever inventing which model_version is the incumbent.)

def test_promotion_status_without_incumbent_is_insufficient_data(sandbox):
    """The default, and every automated caller's, shape: no --incumbent means
    an honest refusal, never a guessed pairing."""
    row = ao.promotion_status(challenger_model="claude-sonnet-5")
    assert row["verdict"] == "INSUFFICIENT_DATA"
    assert "incumbent" in row["why"]
    logged = [r for r in ao._read_jsonl(ao.FC_LOG) if r["kind"] == "promotion_attempt"]
    assert len(logged) == 1
    assert logged[0]["verdict"] == "INSUFFICIENT_DATA"


def test_promotion_status_missing_ledger_data_is_insufficient_data(sandbox):
    """An explicit incumbent with nothing resolved for either model is still
    an honest refusal, not a crash — ForecastError is caught and recorded."""
    row = ao.promotion_status(challenger_model="claude-sonnet-5",
                              incumbent_model="claude-opus-5")
    assert row["verdict"] == "INSUFFICIENT_DATA"
    assert row["incumbent_model_version"] == "alpha-operator-v1/claude-opus-5"


def _seed_graded_model(sandbox, model: str, n: int, *, hit_rate: float):
    """Write n resolved forecast/resolution pairs straight to FC_LOG for one
    model_version — enough for ledger.grade() to produce a real ScoreReport,
    bypassing the emission gate the way _seed_stuck_forecast already does."""
    version = f"alpha-operator-v1/{model}"
    for i in range(n):
        as_of = (NOW + timedelta(minutes=i)).isoformat()
        hit = i < round(n * hit_rate)
        outcome = "bull_continuation" if hit else "bear_continuation"
        f = ao.Forecast(
            forecast_id=f"fc-{model}-{i}", model_version=version,
            prompt_version="op-1", as_of=as_of, symbol="NVDA", horizon_min=60,
            scenario_probs={"bull_continuation": 0.6, "range_consolidation": 0.4},
            direction="up", recommendation=None, interrupt=None, confidence=0.6,
            recommendation_reason="test fixture")
        ao._append_jsonl(ao.FC_LOG, {"kind": "forecast", **f.to_dict()})
        r = ao.Resolution(
            forecast_id=f.forecast_id,
            resolved_at=(NOW + timedelta(minutes=i, hours=2)).isoformat(),
            outcome_scenario=outcome, outcome_direction="up" if hit else "down",
            shock_occurred=False, was_stale=False, policy_regret_r=None)
        ao._append_jsonl(ao.FC_LOG, {"kind": "resolution", **r.__dict__})


def test_promotion_status_real_pair_reaches_promotion_decision(sandbox):
    """The wire is genuinely live: with two real graded model_versions on
    disk, promotion_status() calls promotion_decision() for real. Regime data
    is never fabricated here, so REGIME_DATA_MISSING must always appear —
    proving an empty regime breakdown is a FAILED gate, not a bypassed one."""
    _seed_graded_model(sandbox, "claude-sonnet-5", 5, hit_rate=0.8)   # challenger
    _seed_graded_model(sandbox, "claude-opus-5", 5, hit_rate=0.2)     # incumbent
    row = ao.promotion_status(challenger_model="claude-sonnet-5",
                              incumbent_model="claude-opus-5")
    assert row["verdict"] == "REJECTED"
    assert "REGIME_DATA_MISSING" in row["failed_gates"]
    assert "MIN_DECISIONS" in row["failed_gates"]     # n=5 < default min_decisions=50
    logged = [r for r in ao._read_jsonl(ao.FC_LOG) if r["kind"] == "promotion_attempt"]
    assert len(logged) == 1 and logged[0]["verdict"] == "REJECTED"


def test_promotion_status_never_calls_grant(sandbox, monkeypatch):
    """No path through promotion_status() may call AuthorityRegistry.grant()
    — a verdict is recorded, authority is never touched."""
    from context_directive import AuthorityRegistry
    called = []
    monkeypatch.setattr(AuthorityRegistry, "grant",
                        lambda self, *a, **kw: called.append((a, kw)) or (_ for _ in ()).throw(
                            AssertionError("grant() must never be called")))
    _seed_graded_model(sandbox, "claude-sonnet-5", 60, hit_rate=0.95)
    _seed_graded_model(sandbox, "claude-opus-5", 60, hit_rate=0.05)
    ao.promotion_status(challenger_model="claude-sonnet-5",
                        incumbent_model="claude-opus-5")
    assert called == []


# ------------------------------------------ A: grade() persists a track record

def test_grade_persists_score_report_append_only(sandbox):
    """ledger.grade()'s ScoreReport must be written to SCORE_LOG, durably —
    calibration over time is readable rather than recomputed-and-lost."""
    _seed_graded_model(sandbox, "claude-sonnet-5", 5, hit_rate=0.8)
    assert not ao.SCORE_LOG.exists()
    rc = ao.grade("claude-sonnet-5")
    assert rc == 0
    rows = ao._read_jsonl(ao.SCORE_LOG)
    assert len(rows) == 1
    assert rows[0]["model_version"] == "alpha-operator-v1/claude-sonnet-5"
    assert rows[0]["n_resolved"] == 5
    assert "at" in rows[0]

    # A second grading run APPENDS, never overwrites — the history compounds.
    ao.grade("claude-sonnet-5")
    rows = ao._read_jsonl(ao.SCORE_LOG)
    assert len(rows) == 2


# --------------------------------------------- B: event-anchored track record

CPI_EVENT = {"date": "2026-08-14", "source": "fred", "name": "Consumer Price Index",
            "importance": 1.0}


def test_next_scheduled_event_skips_low_importance_and_finds_first_qualifying(
        sandbox, monkeypatch):
    calendar = {
        "2026-08-14": [{"date": "2026-08-14", "source": "fred",
                        "name": "Weekly Claims", "importance": 0.3}],
        "2026-08-17": [{"date": "2026-08-17", "source": "fred",
                        "name": "Producer Price Index", "importance": 0.6}],
    }
    monkeypatch.setattr(ao.macro_calendar, "scheduled_events",
                        lambda d, **kw: calendar.get(d.isoformat(), []))
    ev = ao._next_scheduled_event(NOW, lookahead_days=14, min_importance=0.5)
    assert ev is not None
    assert ev["name"] == "Producer Price Index"
    assert ev["date"] == "2026-08-17"


def test_next_scheduled_event_none_beyond_lookahead(sandbox, monkeypatch):
    monkeypatch.setattr(ao.macro_calendar, "scheduled_events",
                        lambda d, **kw: [{"date": d.isoformat(), "source": "fred",
                                          "name": "FOMC", "importance": 1.0}]
                        if d.isoformat() == "2026-09-30" else [])
    ev = ao._next_scheduled_event(NOW, lookahead_days=5, min_importance=0.5)
    assert ev is None


def test_anchor_window_horizon_ends_exactly_at_session_close(sandbox):
    anchor_now = datetime(2026, 8, 14, 19, 50, tzinfo=UTC)     # 15:50 ET, Fri
    window = ao._anchor_window(date(2026, 8, 14), now=anchor_now)
    assert window["horizon_min"] == 5
    assert window["resolves_at"].astimezone(ao.ET).strftime("%H:%M") == "15:55"


def test_anchor_window_refuses_after_own_session_close(sandbox):
    past_close = datetime(2026, 8, 14, 20, 0, tzinfo=UTC)      # 16:00 ET
    with pytest.raises(ao.OperatorError, match="at-or-after"):
        ao._anchor_window(date(2026, 8, 14), now=past_close)


def test_anchor_window_refuses_weekend_event_date(sandbox):
    with pytest.raises(ao.OperatorError, match="not a trading day"):
        ao._anchor_window(date(2026, 8, 15), now=NOW)          # Saturday


def test_seal_anchored_forecast_no_qualifying_event_returns_none(sandbox, monkeypatch):
    monkeypatch.setattr(ao.macro_calendar, "scheduled_events", lambda d, **kw: [])
    row = ao.seal_anchored_forecast("NVDA", _read(), model="claude-sonnet-5",
                                    now=NOW)
    assert row is None
    assert not ao.RECORDS.exists()


def test_seal_anchored_forecast_seals_record_never_writes_directive(sandbox):
    anchor_now = datetime(2026, 8, 14, 19, 50, tzinfo=UTC)
    row = ao.seal_anchored_forecast(
        "NVDA", _read(verdict="TIGHTEN"), model="claude-sonnet-5",
        now=anchor_now, event=CPI_EVENT)
    assert row is not None
    assert row["anchor_event"] == "Consumer Price Index@2026-08-14"
    assert row["directive"] is None
    assert row["suppressed_to"] is None
    assert row["shadow"] is True
    assert not ao.DIRECTIVES.exists()          # never, even on a TIGHTEN verdict

    fc_rows = [r for r in ao._read_jsonl(ao.FC_LOG) if r["kind"] == "forecast"]
    assert len(fc_rows) == 1
    assert fc_rows[0]["anchor_event"] == "Consumer Price Index@2026-08-14"
    assert fc_rows[0]["horizon_min"] == 5                       # to the session close


def test_seal_anchored_forecast_resolves_through_existing_resolver(sandbox, monkeypatch):
    """No separate resolver exists — resolve_open() picks up an anchored
    Forecast exactly like any other, because it lives in the same ledger."""
    anchor_now = datetime(2026, 8, 14, 19, 50, tzinfo=UTC)
    _write_bars(sandbox["tmp"], monkeypatch, [100.0, 101.0], start=anchor_now)
    row = ao.seal_anchored_forecast(
        "NVDA", _read(verdict="ALLOW_BASELINE", direction="up"),
        model="claude-sonnet-5", now=anchor_now, event=CPI_EVENT)
    assert row is not None
    ao.resolve_open(now=anchor_now + timedelta(minutes=6))
    res = [r for r in ao._read_jsonl(ao.FC_LOG) if r["kind"] == "resolution"]
    assert len(res) == 1
    assert res[0]["outcome_direction"] == "up"


def test_grade_anchored_no_data_names_events_to_power(sandbox):
    result = ao.grade_anchored("claude-sonnet-5", event_name="Consumer Price Index")
    assert result["n_resolved"] == 0
    assert result["hit_rate"] is None
    assert result["events_to_power"] == round(ao.events_to_power())
    assert "note" in result


def test_grade_anchored_filters_by_event_and_counts_hits(sandbox, monkeypatch):
    anchor_now = datetime(2026, 8, 14, 19, 50, tzinfo=UTC)
    _write_bars(sandbox["tmp"], monkeypatch, [100.0, 101.0], start=anchor_now)
    ao.seal_anchored_forecast("NVDA", _read(verdict="ALLOW_BASELINE", direction="up"),
                              model="claude-sonnet-5", now=anchor_now, event=CPI_EVENT)
    ppi_event = {**CPI_EVENT, "name": "Producer Price Index"}
    ao.seal_anchored_forecast("NVDA", _read(verdict="ALLOW_BASELINE", direction="down"),
                              model="claude-sonnet-5", now=anchor_now, event=ppi_event)
    ao.resolve_open(now=anchor_now + timedelta(minutes=6))

    cpi_only = ao.grade_anchored("claude-sonnet-5", event_name="Consumer Price Index")
    assert cpi_only["n_resolved"] == 1
    assert cpi_only["hits"] == 1                # direction "up" matched the up-move
    assert cpi_only["hit_rate"] == 1.0

    pooled = ao.grade_anchored("claude-sonnet-5")
    assert pooled["n_resolved"] == 2
    assert pooled["hits"] == 1                  # the PPI-anchored "down" call missed
