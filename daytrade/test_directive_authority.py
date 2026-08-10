"""Card 016 — abstention, the authority registry, and the production wiring
that finally makes 018 WIRED (the runner's directive channel).

DoD from the card: an NVDA directive cannot affect an unrelated symbol; a SPY
market event is not automatically a symbol emergency; expiry is enforced at
use time; a level-2 model cannot issue a level-3 interrupt; unpromoted grants
above the cap are refused; the runner is byte-identical with no directives
file and tightens with a valid TIGHTEN directive.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from context_directive import (ABSTENTION_REASONS, Abstention, AuthorityError,
                               AuthorityRegistry, ContextDirective,
                               DirectiveError, ReceiverContext, Scope,
                               UNPROMOTED_CAP, evaluate)

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=timezone.utc)


def directive(*, interrupt: str | None = "TIGHTEN", authority: int = 1,
              scope: Scope | None = None, did: str = "d1",
              expires: datetime | None = None) -> ContextDirective:
    return ContextDirective(
        directive_id=did, scope=scope or Scope(symbols=["NVDA"]),
        issued_at=(NOW - timedelta(minutes=5)).isoformat(),
        expires_at=(expires or NOW + timedelta(minutes=25)).isoformat(),
        model_version="az-test", authority_level=authority, interrupt=interrupt)


# ---------------------------------------------------------------- abstention

def test_abstention_is_a_valid_named_output():
    a = Abstention(model_version="az-test", reason="CONFLICTING_EVIDENCE",
                   detail="two confirmed stories point opposite ways",
                   issued_at=NOW.isoformat())
    assert a.to_dict()["reason"] == "CONFLICTING_EVIDENCE"
    assert ABSTENTION_REASONS == {"NO_OPINION", "LOW_CONFIDENCE",
                                  "CONFLICTING_EVIDENCE", "STALE_CONTEXT",
                                  "DATA_INCOMPLETE"}


def test_abstention_rejects_unknown_reason_and_anonymity():
    with pytest.raises(DirectiveError):
        Abstention(model_version="az-test", reason="FELT_LIKE_IT",
                   detail="x", issued_at=NOW.isoformat())
    with pytest.raises(DirectiveError):
        Abstention(model_version="", reason="NO_OPINION", detail="x",
                   issued_at=NOW.isoformat())
    with pytest.raises(DirectiveError):                  # naive timestamp
        Abstention(model_version="az-test", reason="NO_OPINION", detail="x",
                   issued_at="2026-08-07T14:30:00")


# ------------------------------------------------------------------ registry

def test_unpromoted_grants_cap_at_recommend():
    reg = AuthorityRegistry()
    reg.grant("az-1", 2, by="colin", reason="scored well in shadow",
              ts=NOW.isoformat())
    assert reg.granted_level("az-1") == 2
    with pytest.raises(AuthorityError):
        reg.grant("az-1", 3, by="colin", reason="looks great lately",
                  ts=NOW.isoformat())                    # no promotion_ref
    reg.grant("az-1", 3, by="colin", reason="cleared the 017 gate",
              ts=NOW.isoformat(), promotion_ref="promo-001")
    assert reg.granted_level("az-1") == 3
    assert UNPROMOTED_CAP == 2


def test_rollback_never_escalates_a_revoked_model():
    """Adversarial-review finding 4: a rollback that unconditionally writes the
    default level would GRANT authority to a fully-revoked model. It may only
    reduce — and anonymous authority changes are refused."""
    reg = AuthorityRegistry()
    reg.grant("az-1", 0, by="colin", reason="full revoke after incident",
              ts=NOW.isoformat())
    reg.rollback("az-1", by="colin", reason="routine sweep", ts=NOW.isoformat())
    assert reg.granted_level("az-1") == 0                # still revoked
    with pytest.raises(AuthorityError):
        reg.rollback("az-1", by="", reason="x", ts=NOW.isoformat())
    with pytest.raises(AuthorityError):
        reg.grant("az-2", 1, by="", reason="x", ts=NOW.isoformat())


def test_rollback_is_explicit_and_audited():
    reg = AuthorityRegistry()
    reg.grant("az-1", 3, by="colin", reason="promoted", ts=NOW.isoformat(),
              promotion_ref="promo-001")
    reg.rollback("az-1", by="colin", reason="false emergency on 08-07",
                 ts=NOW.isoformat())
    assert reg.granted_level("az-1") == 1                # back to default
    trail = reg.audit()
    assert [g.action for g in trail] == ["GRANT", "ROLLBACK"]
    assert trail[1].reason == "false emergency on 08-07"
    assert reg.granted_level("az-unknown") == 1          # default, derived


# --------------------------------------------------------- the DoD scenarios

def test_nvda_directive_cannot_affect_unrelated_symbol():
    dec = evaluate([directive(interrupt="TIGHTEN")],
                   ReceiverContext(symbol="TSLA"), now=NOW)
    assert dec.urgent is None
    assert dec.rejected[0].reason == "OUT_OF_SCOPE"


def test_spy_market_event_is_not_automatically_a_symbol_emergency():
    market = directive(interrupt="EMERGENCY", authority=4,
                       scope=Scope(market=True))
    # not index-linked: the market's panic is not this symbol's problem
    dec = evaluate([market], ReceiverContext(symbol="XYZ", granted_level=4),
                   now=NOW)
    assert dec.urgent is None
    # index-linked AND authority granted: now it is
    dec = evaluate([market], ReceiverContext(symbol="NVDA", granted_level=4,
                                             index_linked=True), now=NOW)
    assert dec.urgent == "exit"


def test_expiry_enforced_at_use_time():
    d = directive(interrupt="TIGHTEN", expires=NOW + timedelta(minutes=1))
    ctx = ReceiverContext(symbol="NVDA")
    assert evaluate([d], ctx, now=NOW).urgent == "tighten"
    later = NOW + timedelta(minutes=2)
    dec = evaluate([d], ctx, now=later)                  # same directive, later use
    assert dec.urgent is None and dec.rejected[0].reason == "STALE"


def test_level2_model_cannot_issue_level3_interrupt():
    d = directive(interrupt="INVALIDATE", authority=2)   # INVALIDATE needs 3
    dec = evaluate([d], ReceiverContext(symbol="NVDA", granted_level=2), now=NOW)
    assert dec.urgent is None
    assert dec.rejected[0].reason == "OVER_AUTHORIZED"


# ------------------------------------------------- production wiring (runner)

def wall_clock_directive(*, interrupt: str, authority: int = 1) -> dict:
    """The runner evaluates at REAL wall clock (it is the live path), so wiring
    fixtures must be fresh relative to now — a frozen-clock fixture is
    genuinely stale there, which is itself the expiry rule working."""
    now = datetime.now(timezone.utc)
    return ContextDirective(
        directive_id="dw1", scope=Scope(symbols=["NVDA"]),
        issued_at=(now - timedelta(minutes=5)).isoformat(),
        expires_at=(now + timedelta(minutes=25)).isoformat(),
        model_version="az-test", authority_level=authority,
        interrupt=interrupt).to_dict()


def _run_replay(tmp_path, monkeypatch, directives_content):
    import runner
    monkeypatch.setattr(runner, "LOGDIR", tmp_path)
    monkeypatch.setattr(runner, "read_urgency", lambda require: (None, "test"))
    monkeypatch.setattr(runner, "DIRECTIVES", tmp_path / "directives.json")
    if directives_content is not None:
        (tmp_path / "directives.json").write_text(json.dumps(directives_content))
    captured = []
    real = runner.decide_exit

    def spy(state):
        captured.append(state.urgent)
        return real(state)

    monkeypatch.setattr(runner, "decide_exit", spy)
    plan = {"symbol": "NVDA", "direction": 1, "entry": 200.0, "qty": 100,
            "sl": 199.0, "tp1": 201.0, "tp2": 202.14, "trail_dist": 0.5,
            "trail_mult": None, "be_arm_frac": 1.0, "hold_past_tp2": True}
    rc = runner.run(plan, interval=0, once=False, replay=[200.2, 200.4],
                    max_stale=180, require_bias=False)
    assert rc == 0
    log = next(tmp_path.glob("session_*.jsonl"))
    recs = [json.loads(l) for l in log.read_text().splitlines()]
    return captured, recs


def test_runner_without_directives_file_is_unchanged(tmp_path, monkeypatch):
    urgents, recs = _run_replay(tmp_path, monkeypatch, None)
    assert urgents == [None, None]
    assert [r["urgent"] for r in recs] == [None, None]


def test_runner_tightens_on_valid_directive_and_logs_merged_urgency(
        tmp_path, monkeypatch):
    d = wall_clock_directive(interrupt="TIGHTEN")
    urgents, recs = _run_replay(tmp_path, monkeypatch, [d])
    assert urgents == ["tighten", "tighten"]             # steered the engine
    assert [r["urgent"] for r in recs] == ["tighten", "tighten"]  # replay-faithful


def test_runner_refuses_emergency_from_unpromoted_authority(tmp_path, monkeypatch):
    """The runner evaluates at the DEFAULT granted level (1): an EMERGENCY
    directive is refused as over-authorized — flattening is authority a model
    earns through 017, not by asserting it in a JSON file."""
    d = wall_clock_directive(interrupt="EMERGENCY", authority=4)
    urgents, _ = _run_replay(tmp_path, monkeypatch, [d])
    assert urgents == [None, None]


def test_runner_malformed_directives_steer_nothing(tmp_path, monkeypatch):
    urgents, recs = _run_replay(tmp_path, monkeypatch,
                                [{"directive_id": "d1", "surprise": True}])
    assert urgents == [None, None]
    assert [r["urgent"] for r in recs] == [None, None]
