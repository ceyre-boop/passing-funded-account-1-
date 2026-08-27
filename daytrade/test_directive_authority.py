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
                               DEFAULT_GRANTED_LEVEL, DirectiveError,
                               ReceiverContext, Scope, UNPROMOTED_CAP, evaluate)

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


def _run_replay(tmp_path, monkeypatch, directives_content, *,
                authority_rows=None):
    import runner
    monkeypatch.setattr(runner, "LOGDIR", tmp_path)
    monkeypatch.setattr(runner, "EVENTS_DIR", tmp_path)   # Gate 2: events too
    monkeypatch.setattr(runner, "read_urgency", lambda require: (None, "test"))
    monkeypatch.setattr(runner, "DIRECTIVES", tmp_path / "directives.json")
    monkeypatch.setattr(runner, "AUTHORITY_REGISTRY",
                        tmp_path / "authority_registry.jsonl")
    if directives_content is not None:
        (tmp_path / "directives.json").write_text(json.dumps(directives_content))
    if authority_rows is not None:
        (tmp_path / "authority_registry.jsonl").write_text(
            "\n".join(json.dumps(r) for r in authority_rows) + "\n")
    captured = []
    real = runner.decide_exit

    def spy(state):
        captured.append(state.urgent)
        return real(state)

    monkeypatch.setattr(runner, "decide_exit", spy)
    plan = {"symbol": "NVDA", "direction": 1, "entry": 200.0, "qty": 100,
            "sl": 199.0, "tp1": 201.0, "tp2": 202.14, "trail_dist": 0.5,
            "trail_mult": None, "be_arm_frac": 1.0, "hold_past_tp2": True,
            # required since runner.run keys a replay's trade_id/session off
            # the plan, never off wall clock — see runner.py's session_date.
            "_session": "2026-08-12"}
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

# --------------------------------------- D3 link 4: AuthorityRegistry wiring
# (THE_BIG_PLAN.md — the registry is now the SOURCE of the runner's granted
# level, not a bare literal `1`. Behaviour-preserving today: nothing in
# production ever calls `.grant()`, so an absent or empty registry resolves
# to exactly DEFAULT_GRANTED_LEVEL, same as the literal it replaces.)

def test_granted_level_with_no_registry_file_is_the_default(tmp_path, monkeypatch):
    import runner
    monkeypatch.setattr(runner, "AUTHORITY_REGISTRY",
                        tmp_path / "authority_registry.jsonl")
    level, note = runner._granted_level("alpha-operator-v1")
    assert level == DEFAULT_GRANTED_LEVEL == 1
    assert note == ""


def test_granted_level_malformed_registry_fails_safe_to_default(tmp_path, monkeypatch):
    import runner
    reg = tmp_path / "authority_registry.jsonl"
    reg.write_text("{not json\n")
    monkeypatch.setattr(runner, "AUTHORITY_REGISTRY", reg)
    level, note = runner._granted_level("alpha-operator-v1")
    assert level == DEFAULT_GRANTED_LEVEL
    assert "UNREADABLE" in note


def test_production_resolves_to_exactly_default_level_today(tmp_path, monkeypatch):
    """The specific assertion the coordinator asked for: with the registry
    wired in as the source, production still resolves to exactly 1 today —
    the wiring cannot silently become a promotion."""
    import runner
    assert runner.OPERATOR_MODEL_VERSION == "alpha-operator-v1"
    urgents, _ = _run_replay(tmp_path, monkeypatch,
                             [wall_clock_directive(interrupt="TIGHTEN")],
                             authority_rows=[])
    assert urgents == ["tighten", "tighten"]        # unchanged from the
                                                      # pre-registry-wiring test above
    level, _ = runner._granted_level(runner.OPERATOR_MODEL_VERSION)
    assert level == 1


def test_a_real_grant_reaches_evaluate_via_the_registry(tmp_path, monkeypatch):
    """Proves the wire is live, not cosmetic: a hand-written, validly-shaped
    GRANT row (level 2 — 'recommend', within UNPROMOTED_CAP, no promotion_ref
    needed) raises what evaluate() sees. This does NOT reach the exit engine
    — runner.py hardcodes `policy_candidate=None` into resolve_channels()
    regardless of authority (a second, independent gate this change does not
    touch) — only evaluate()'s own Decision.policy_candidate, logged in the
    directive note, is affected."""
    import runner
    d = wall_clock_directive(interrupt="TIGHTEN", authority=2)
    d["recommendation"] = "RIDE"
    grant_row = {"model_version": "alpha-operator-v1", "level": 2,
                "action": "GRANT", "granted_by": "test-fixture",
                "reason": "unit test", "ts": datetime.now(timezone.utc).isoformat(),
                "promotion_ref": None}
    urgents, recs = _run_replay(tmp_path, monkeypatch, [d],
                                authority_rows=[grant_row])
    assert urgents == ["tighten", "tighten"]
    level, _ = runner._granted_level(runner.OPERATOR_MODEL_VERSION)
    assert level == 2


def test_authority_registry_from_rows_replays_validation(tmp_path):
    """`from_rows` must not be a trusting deserializer — a row that `grant()`
    would refuse live (anonymous, or above UNPROMOTED_CAP with no
    promotion_ref) must still raise when replayed from disk."""
    with pytest.raises(AuthorityError):
        AuthorityRegistry.from_rows([
            {"model_version": "m", "level": 3, "action": "GRANT",
             "granted_by": "x", "reason": "y",
             "ts": NOW.isoformat(), "promotion_ref": None}])
    reg = AuthorityRegistry.from_rows([
        {"model_version": "m", "level": 2, "action": "GRANT",
         "granted_by": "x", "reason": "y",
         "ts": NOW.isoformat(), "promotion_ref": None}])
    assert reg.granted_level("m") == 2
    assert reg.granted_level("other-model") == DEFAULT_GRANTED_LEVEL


def test_authority_registry_round_trips_through_rows(tmp_path):
    reg = AuthorityRegistry()
    reg.grant("m", 2, by="x", reason="y", ts=NOW.isoformat())
    rows = reg.to_rows()
    reg2 = AuthorityRegistry.from_rows(rows)
    assert reg2.granted_level("m") == 2
    assert reg2.audit() == reg.audit()
