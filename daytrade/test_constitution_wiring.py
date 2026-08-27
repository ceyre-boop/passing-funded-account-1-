"""Constitution wiring — C004/C005/C007 must be reachable on the LIVE path.

Spec 011 built the rules; the handoff (CLAUDE_LONG_TERM_HANDOFF.md:143-148)
flagged that the production `enforce()` call inside `apply_action` passed
neither `now_et=` nor `actions=`, and no production caller passed
`applied_keys` — so C004 (stale facts), C005 (duplicate reduction) and C007
(emergency precedence) could fire only from the module's own self-test
fixtures. These tests exercise the rules through `apply_action` /
`apply_actions`, the funnel every production caller (runner, backtest,
ceiling) routes through.
"""
from __future__ import annotations

import pytest

from stockfish_constitution import ConstitutionError, idempotency_key, _self_test
from stockfish_exit import Action, Stage, TradeState, apply_action, apply_actions


def make_state(**kw) -> TradeState:
    base = dict(direction=1, entry=200.0, qty=100, price=201.0,
                sl=199.0, tp1=201.0, tp2=202.14, trail_dist=0.5)
    base.update(kw)
    return TradeState(**base)


def rules_of(err: ConstitutionError) -> set[str]:
    return {v.rule_id for v in err.violations}


# ------------------------------------------------------------- C004: clock

def test_c004_backwards_clock_refused_through_apply_action():
    """A state whose supplied clock ran backwards must be refused at the live
    funnel — apply_action forwards state.now_et into enforce()."""
    s = make_state(now_et="10:00", last_now_et="11:00")
    with pytest.raises(ConstitutionError) as e:
        apply_action(s, Action("HOLD", reason="clock check"))
    assert "C004" in rules_of(e.value)


def test_c004_forward_and_equal_clock_is_legal():
    s = make_state(now_et="11:00", last_now_et="11:00")
    apply_action(s, Action("HOLD", reason="same minute"))
    assert s.last_now_et == "11:00"
    s2 = make_state(now_et="11:05", last_now_et="11:00")
    apply_action(s2, Action("HOLD", reason="clock advanced"))
    assert s2.last_now_et == "11:05"


def test_c004_midnight_crossing_refused_by_design():
    """020 architect ruling: the session clock is same-day HH:MM. 23:59 -> 00:00
    is a session-boundary violation — an overnight position — and must raise
    C004 with a message that says it is deliberate, not a wraparound bug."""
    s = make_state(now_et="00:00", last_now_et="23:59")
    with pytest.raises(ConstitutionError) as e:
        apply_action(s, Action("HOLD", reason="held past midnight"))
    assert "C004" in rules_of(e.value)
    assert "same-day" in str(e.value) and "overnight" in str(e.value)


def test_c004_clockless_replay_is_unaffected():
    """Replay supplies no clock (now_et=None) — the rule stays unarmed, never a
    guessed time. This is what keeps historical replays byte-identical."""
    s = make_state(now_et=None, last_now_et="11:00")
    apply_action(s, Action("HOLD", reason="replay"))


# ------------------------------------------- C005: duplicate reduction keys

def test_c005_replayed_reduction_refused_through_apply_actions():
    """The crash-retry scenario the rule exists for: a reduction was applied,
    the caller's key set survived (it is caller-owned state), the process
    reloaded the PRE-apply state and re-derived the same reduction. The replay
    must be refused."""
    part = Action("TAKE_PARTIAL", fraction=0.5, reason="bank the goal")
    keys: set[str] = set()

    s1 = make_state(stage=Stage.SCALED)
    apply_actions(s1, [part], keys)
    assert s1.qty == pytest.approx(50.0)
    assert keys == {idempotency_key(make_state(stage=Stage.SCALED), part)}

    s2 = make_state(stage=Stage.SCALED)          # reloaded pre-crash state, same revision
    with pytest.raises(ConstitutionError) as e:
        apply_actions(s2, [part], keys)
    assert "C005" in rules_of(e.value)
    assert s2.qty == 100                          # refused before any mutation


def test_c005_new_reduction_at_new_revision_is_legal():
    """A genuinely new reduction is computed against the advanced revision, so
    its key differs and it passes."""
    keys: set[str] = set()
    s = make_state(stage=Stage.SCALED)
    apply_actions(s, [Action("TAKE_PARTIAL", fraction=0.5, reason="first")], keys)
    apply_actions(s, [Action("TAKE_PARTIAL", fraction=0.5, reason="second, later rev")], keys)
    assert s.qty == pytest.approx(25.0)
    assert len(keys) == 2


# --------------------------------------------- C007: emergency precedence

def test_c007_action_after_exit_all_refused_through_apply_actions():
    s = make_state()
    batch = [Action("EXIT_ALL", reason="emergency"),
             Action("MOVE_SL", sl=200.0, reason="nonsense after the end")]
    with pytest.raises(ConstitutionError) as e:
        apply_actions(s, batch, set())
    assert "C007" in rules_of(e.value)
    assert s.stage is not Stage.CLOSED            # refused before the first apply


def test_c007_exit_all_last_is_legal():
    s = make_state()
    apply_actions(s, [Action("MOVE_SL", sl=200.0, reason="tighten first"),
                      Action("EXIT_ALL", reason="then done")], set())
    assert s.stage is Stage.CLOSED
    assert s.sl == 200.0


# ------------------------------------------- caller threading (WIRED proof)

def test_backtest_replay_threads_one_session_lifetime_key_set(tmp_path, monkeypatch):
    """backtest.replay_session must fold state through apply_actions with ONE
    session-lifetime key set — not a fresh set per row (which would silently
    disable C005's mechanics) and not the bare per-action loop it used before
    the wiring fix. Added after adversarial review showed the caller-side
    threading had no test: breaking it left the whole suite green."""
    import backtest

    plan = {"symbol": "TEST", "direction": 1, "entry": 200.0, "qty": 100,
            "sl": 199.0, "tp1": 201.0, "tp2": 202.14, "trail_dist": 0.5,
            "trail_mult": None, "be_arm_frac": 1.0, "hold_past_tp2": True}
    rows = [{"step": i, "price": p, "urgent": None, **({"plan": plan} if i == 0 else {})}
            for i, p in enumerate([200.2, 200.6, 201.2, 201.5])]
    log = tmp_path / "session.jsonl"
    log.write_text("\n".join(__import__("json").dumps(r) for r in rows) + "\n")

    seen: list = []
    real = backtest.apply_actions

    def capture(state, actions, applied_keys=None):
        seen.append(applied_keys)
        return real(state, actions, applied_keys)

    monkeypatch.setattr(backtest, "apply_actions", capture)
    out = backtest.replay_session(log, plan)

    assert len(out) == len(rows)                  # every row folded through the funnel
    assert len(seen) == len(rows)
    assert all(isinstance(k, set) for k in seen)
    assert all(k is seen[0] for k in seen), \
        "applied_keys must be ONE session-lifetime set, not a fresh set per row"


def test_runner_replay_threads_one_session_lifetime_key_set(tmp_path, monkeypatch):
    """The runner's live loop must fold every cycle through apply_actions with
    ONE session-lifetime key set. Exercised via the runner's own --replay path
    (no quotes, no broker); log output redirected to tmp_path so no real
    session log is touched. Required by the 020 promotion ruling — this was a
    declared residual until now."""
    import runner

    seen: list = []
    real = runner.apply_actions

    def capture(state, actions, applied_keys=None):
        seen.append(applied_keys)
        return real(state, actions, applied_keys)

    monkeypatch.setattr(runner, "apply_actions", capture)
    monkeypatch.setattr(runner, "read_urgency", lambda require: (None, "test"))
    monkeypatch.setattr(runner, "LOGDIR", tmp_path)
    monkeypatch.setattr(runner, "EVENTS_DIR", tmp_path)   # Gate 2: events too

    plan = {"symbol": "TEST", "direction": 1, "entry": 200.0, "qty": 100,
            "sl": 199.0, "tp1": 201.0, "tp2": 202.14, "trail_dist": 0.5,
            "trail_mult": None, "be_arm_frac": 1.0, "hold_past_tp2": True,
            # required since runner.run keys a replay's trade_id/session off
            # the plan, never off wall clock — see runner.py's session_date.
            "_session": "2026-08-12"}
    rc = runner.run(plan, interval=0, once=False, replay=[200.2, 200.6, 201.2],
                    max_stale=180, require_bias=False)

    assert rc == 0
    assert len(seen) == 3                          # one fold per replayed cycle
    assert all(isinstance(k, set) for k in seen)
    assert all(k is seen[0] for k in seen), \
        "runner must thread ONE session-lifetime key set, not a fresh set per cycle"
    assert any(tmp_path.iterdir()), "session log must land in the redirected LOGDIR"


def test_ceiling_simulate_threads_keys_and_batch(monkeypatch):
    """ceiling.simulate's hand-rolled fold (the documented apply_actions
    exception) must still thread the full constitution context: every
    apply_action call carries the whole decide_exit batch, and once a
    TAKE_PARTIAL is applied its idempotency key appears in later calls'
    applied_keys. Required by the 020 promotion ruling."""
    import pandas as pd
    import ceiling as ceiling_mod
    from bars import Session

    closes = [201.0, 202.5, 203.0, 203.2]
    idx = pd.date_range("2026-08-07 09:40", periods=len(closes), freq="5min")
    df = pd.DataFrame({"Open": [c - 0.1 for c in closes],
                       "High": [c + 0.4 for c in closes],
                       "Low": [c - 0.4 for c in closes],
                       "Close": closes, "Volume": [1000.0] * len(closes)}, index=idx)
    session = Session(symbol="TEST", day="2026-08-07", df=df)
    entry = ceiling_mod.Entry(day="2026-08-07", ts="2026-08-07T09:35",
                              time_block="OPEN_DRIVE", direction=1, entry=200.0,
                              stop=199.0, risk=1.0, tp1=201.0, tp2=202.14,
                              trail_dist=0.5)
    cfg = {"partial_frac": 0.5, "trail_mult": 1.5, "be_arm_frac": 0.25,
           "hold_past_tp2": True, "flatten_et": None}

    calls: list = []
    real = ceiling_mod.apply_action

    def capture(state, action, *, applied_keys=frozenset(), batch=None):
        calls.append((action.kind, applied_keys, batch))
        return real(state, action, applied_keys=applied_keys, batch=batch)

    monkeypatch.setattr(ceiling_mod, "apply_action", capture)
    ceiling_mod.simulate(session, entry, cfg)

    assert calls, "simulate applied nothing — fixture failed to reach the engine"
    assert all(batch is not None for _, _, batch in calls), \
        "every apply must see the whole ordered batch (C007 context)"
    partial_at = next((i for i, (kind, _, _) in enumerate(calls)
                       if kind == "TAKE_PARTIAL"), None)
    assert partial_at is not None, "fixture must reach TP2 and take the partial"
    later_keys = [keys for _, keys, _ in calls[partial_at + 1:]]
    # len == 1 is a DESIGN PIN, not just a threading check: the current
    # lifecycle takes exactly one partial (TP2), so exactly one key exists.
    # If a future card legitimately adds a second partial, update this
    # deliberately — do not weaken it to make a new feature pass.
    assert later_keys and all(len(k) == 1 for k in later_keys), \
        "the applied partial's idempotency key must reach every later call (C005 context)"


# ------------------------------------------------------------- regression

# ------------------------------------- action_kind wiring (NO_SUPPLIER fix)

def test_action_kind_populated_for_every_action_scoped_violation():
    """Every violation raised while an Action is in scope must carry that
    action's kind on ConstitutionViolation.action_kind — the audit trail
    field spec 011 added but no production call site supplied. Fault
    injection: strip `action_kind=k` from any call site in
    stockfish_constitution.py and this test fails."""
    cases = [
        # (setup kwargs, action, expected rule, expected action_kind)
        (dict(), Action("MOVE_SL", sl=198.0, reason="loosen"), "C003", "MOVE_SL"),
        (dict(), Action("MOVE_SL", sl=None, reason="no price"), "C009", "MOVE_SL"),
        (dict(), Action("MOVE_SL", sl=float("nan"), reason="nan"), "C009", "MOVE_SL"),
        (dict(), Action("TAKE_PARTIAL", fraction=1.5, reason="oob"), "C006", "TAKE_PARTIAL"),
        (dict(), Action("SELL_EVERYTHING", reason="bogus kind"), "C009", "SELL_EVERYTHING"),
        (dict(stage=Stage.CLOSED), Action("MOVE_SL", sl=200.0, reason="on closed"),
         "C008", "MOVE_SL"),
    ]
    for setup, action, expected_rule, expected_kind in cases:
        s = make_state(**setup)
        with pytest.raises(ConstitutionError) as e:
            apply_action(s, action)
        matched = [v for v in e.value.violations if v.rule_id == expected_rule]
        assert matched, f"{expected_rule} did not fire for {action.kind}"
        assert all(v.action_kind == expected_kind for v in matched), (
            f"{expected_rule} violation for {action.kind} has action_kind="
            f"{[v.action_kind for v in matched]!r}, expected {expected_kind!r} "
            "— the action's kind is not reaching the violation record")


def test_action_kind_populated_for_batch_and_duplicate_violations():
    """C007 (batch ordering) and C005 (duplicate reduction) violations must
    also carry the offending action's kind — checked through apply_actions,
    the same funnel the runner/backtest/ceiling all route through."""
    s = make_state()
    batch = [Action("EXIT_ALL", reason="emergency"),
             Action("MOVE_SL", sl=200.0, reason="after the end")]
    with pytest.raises(ConstitutionError) as e:
        apply_actions(s, batch, set())
    c007 = [v for v in e.value.violations if v.rule_id == "C007"]
    assert c007 and all(v.action_kind == "EXIT_ALL" for v in c007), (
        "C007 violation missing/incorrect action_kind")

    part = Action("TAKE_PARTIAL", fraction=0.5, reason="bank the goal")
    keys: set[str] = set()
    s1 = make_state(stage=Stage.SCALED)
    apply_actions(s1, [part], keys)
    s2 = make_state(stage=Stage.SCALED)
    with pytest.raises(ConstitutionError) as e2:
        apply_actions(s2, [part], keys)
    c005 = [v for v in e2.value.violations if v.rule_id == "C005"]
    assert c005 and all(v.action_kind == "TAKE_PARTIAL" for v in c005), (
        "C005 violation missing/incorrect action_kind")


def test_action_kind_none_for_state_and_clock_violations():
    """State-level (C002/C009) and clock (C004) violations have no Action in
    scope at all — action_kind must genuinely stay None there, not be forced
    to a wrong value. Distinguishes 'no supplier' (the real bug) from
    'nothing to supply' (correct by design)."""
    s = make_state(qty=-1)
    with pytest.raises(ConstitutionError) as e:
        apply_action(s, Action("HOLD", reason="state itself is broken"))
    c002 = [v for v in e.value.violations if v.rule_id == "C002"]
    assert c002 and all(v.action_kind is None for v in c002)

    s2 = make_state(now_et="10:00", last_now_et="11:00")
    with pytest.raises(ConstitutionError) as e2:
        apply_action(s2, Action("HOLD", reason="clock check"))
    c004 = [v for v in e2.value.violations if v.rule_id == "C004"]
    assert c004 and all(v.action_kind is None for v in c004)


def test_self_test_all_rules_still_pass():
    """Guards against firing-condition drift: this fix must not change WHEN
    C001-C009 fire, only what the violation record carries. Runs the
    module's own per-rule fixtures + 2000+-path property test + C007
    permutation test."""
    assert _self_test() == 0


def test_apply_action_defaults_unchanged():
    """apply_action with no new arguments behaves exactly as before — existing
    callers and the recorded replays are unaffected."""
    s = make_state()
    apply_action(s, Action("MOVE_SL", sl=200.0, reason="tighten"))
    assert s.sl == 200.0 and s.state_revision == 1
    apply_action(s, Action("HOLD", reason="nothing"))
    assert s.state_revision == 1                  # HOLD never advances revision (C009)
