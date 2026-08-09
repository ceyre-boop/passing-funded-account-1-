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

from stockfish_constitution import ConstitutionError, idempotency_key
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


# ------------------------------------------------------------- regression

def test_apply_action_defaults_unchanged():
    """apply_action with no new arguments behaves exactly as before — existing
    callers and the recorded replays are unaffected."""
    s = make_state()
    apply_action(s, Action("MOVE_SL", sl=200.0, reason="tighten"))
    assert s.sl == 200.0 and s.state_revision == 1
    apply_action(s, Action("HOLD", reason="nothing"))
    assert s.state_revision == 1                  # HOLD never advances revision (C009)
