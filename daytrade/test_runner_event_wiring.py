"""Gate 2 wiring — the runner emits spec-012 events for real.

The proofs, in the order they matter:
  1. the event stream and the session JSONL agree action-for-action
  2. append happens BEFORE apply (the spec's crash-window obligation)
  3. THE card-012 DoD through the REAL loop: run k cycles, let the process
     die, resume purely from the event log, and the continuation is
     indistinguishable from an unbroken run — including the C005 key set
     across the TP2 partial
  4. an open trade's log refuses a non-resume start; resume demands a log
  5. the session JSONL / backtest DoD diff channel is unchanged

Fault rows in specs/GATE2_WIRING_MUTATION_LOG.md (mutation_check_gate2_wiring.py).
"""
from __future__ import annotations

import json

import pytest

import runner
from trade_events import JsonlEventLog

PLAN = {"symbol": "NVDA", "direction": 1, "entry": 200.0, "qty": 100.0,
        "sl": 199.0, "tp1": 201.0, "tp2": 202.14, "trail_dist": 0.5,
        "goal_fraction": 0.5, "trail_mult": 1.5, "be_arm_frac": 0.25,
        "hold_past_tp2": True}

# Crosses be-arm (200.25), TP1, TP2 (202.14 -> partial at index 3), then trails.
REPLAY = [200.5, 201.2, 202.0, 202.5, 203.0, 203.4]


def _patch(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "LOGDIR", tmp_path)
    monkeypatch.setattr(runner, "EVENTS_DIR", tmp_path)
    monkeypatch.setattr(runner, "DIRECTIVES", tmp_path / "directives.json")
    monkeypatch.setattr(runner, "read_urgency", lambda require: (None, "test"))


def _run(tmp_path, monkeypatch, replay, *, resume=False) -> int:
    _patch(tmp_path, monkeypatch)
    return runner.run(dict(PLAN), interval=0, once=False, replay=list(replay),
                      max_stale=180, require_bias=False, resume=resume)


def _recs(tmp_path) -> list:
    log = next(tmp_path.glob("session_*.jsonl"))
    return [json.loads(l) for l in log.read_text().splitlines()]


def _events(tmp_path) -> list:
    path = next(tmp_path.glob("events_*.jsonl"))
    return JsonlEventLog(path).load()


def _comparable(events) -> list:
    """Events minus the wall-clock field — everything else must be identical."""
    return [{k: v for k, v in e.to_dict().items() if k != "occurred_at"}
            for e in events]


# ------------------------------------------------------------- agreement

def test_event_stream_agrees_with_session_log(tmp_path, monkeypatch):
    assert _run(tmp_path, monkeypatch, REPLAY) == 0
    recs, events = _recs(tmp_path), _events(tmp_path)

    assert events[0].event_type == "POSITION_OPENED"
    assert events[0].payload["plan"]["entry"] == PLAN["entry"]
    assert events[0].payload["plan"]["trail_mult"] == PLAN["trail_mult"]

    # every stop move, in order, with identical levels
    rec_moves = [a["sl"] for r in recs for a in r["actions"] if a["kind"] == "MOVE_SL"]
    ev_moves = [e.payload["sl"] for e in events if e.event_type == "STOP_ADVANCED"]
    assert rec_moves and rec_moves == ev_moves

    # exactly one partial, agreeing on fraction, submitted-then-confirmed
    rec_partials = [a["fraction"] for r in recs for a in r["actions"]
                    if a["kind"] == "TAKE_PARTIAL"]
    subs = [e for e in events if e.event_type == "PARTIAL_ORDER_SUBMITTED"]
    fills = [e for e in events if e.event_type == "PARTIAL_FILL_CONFIRMED"]
    assert rec_partials == [0.5] and len(subs) == len(fills) == 1
    assert subs[0].payload["idempotency_key"] == fills[0].payload["idempotency_key"]
    # the recorded key must be computed against the PRE-apply revision — one
    # prior STOP_ADVANCED (breakeven) => revision 1 at the partial. A translator
    # called pre-apply but left in post_apply mode records ...:-1 instead.
    assert fills[0].payload["idempotency_key"] == "TAKE_PARTIAL:0.5:1"

    # one FACTS_OBSERVED per cycle, prices in replay order
    facts = [e.payload["price"] for e in events if e.event_type == "FACTS_OBSERVED"]
    assert facts == REPLAY


# ------------------------------------------------- append-before-apply

def test_append_happens_before_apply(tmp_path, monkeypatch):
    """Crash INSIDE apply: the decision is already durable in the event log,
    and the session JSONL (written after apply) has nothing — proving the
    ordering decide -> append -> apply -> log_cycle."""
    _patch(tmp_path, monkeypatch)

    def bomb(state, actions, keys):
        raise RuntimeError("simulated crash inside apply")

    monkeypatch.setattr(runner, "apply_actions", bomb)
    with pytest.raises(RuntimeError, match="simulated crash"):
        runner.run(dict(PLAN), interval=0, once=False, replay=[200.5],
                   max_stale=180, require_bias=False)

    events = _events(tmp_path)
    assert [e.event_type for e in events][:2] == ["POSITION_OPENED", "FACTS_OBSERVED"]
    assert events[1].payload["price"] == 200.5           # the cycle IS in the log
    assert not list(tmp_path.glob("session_*.jsonl"))    # and apply/log never ran


def test_constitution_refusal_appends_nothing(tmp_path, monkeypatch):
    """The pure pre-validation gate: a batch the constitution refuses must
    leave NO trace in the event log — the log can never claim an action the
    engine's own law rejected."""
    _patch(tmp_path, monkeypatch)
    from stockfish_exit import Action

    real = runner.decide_exit

    def poison(state):
        real(state)
        return [Action("EXIT_ALL", reason="emergency"),
                Action("MOVE_SL", sl=200.0, reason="illegal after the end")]

    monkeypatch.setattr(runner, "decide_exit", poison)
    from stockfish_constitution import ConstitutionError
    with pytest.raises(ConstitutionError):
        runner.run(dict(PLAN), interval=0, once=False, replay=[200.5],
                   max_stale=180, require_bias=False)

    events = _events(tmp_path)
    assert [e.event_type for e in events] == ["POSITION_OPENED"]  # nothing more


# ------------------------------------- the card-012 DoD through the real loop

@pytest.mark.parametrize("k", [1, 2, 4, 5])   # incl. 4 = right after the TP2 partial
def test_destroy_and_resume_matches_unbroken(tmp_path, monkeypatch, k):
    """Run k cycles, let the process die (the run returns and every in-memory
    object is discarded), resume PURELY from the event log, finish the replay —
    and diff everything against one unbroken run. Zero diff, including the
    C005 key set surviving the kill at the partial."""
    unbroken = tmp_path / "a"
    broken = tmp_path / "b"
    unbroken.mkdir(), broken.mkdir()

    assert _run(unbroken, monkeypatch, REPLAY) == 0

    assert _run(broken, monkeypatch, REPLAY[:k]) == 0            # ...crash here

    # spy on the resumed run's key threading: after the TP2 partial (k > 3)
    # the reconstructed key set MUST carry the applied key — the C005 payoff
    seen_keys: list = []
    real_apply = runner.apply_actions

    def spy(state, actions, keys):
        if not seen_keys:
            seen_keys.append(set(keys))
        return real_apply(state, actions, keys)

    monkeypatch.setattr(runner, "apply_actions", spy)
    assert _run(broken, monkeypatch, REPLAY[k:], resume=True) == 0
    monkeypatch.setattr(runner, "apply_actions", real_apply)
    if k > 3:
        assert seen_keys and "TAKE_PARTIAL:0.5:1" in seen_keys[0], \
            "resume must reconstruct the applied key set WITH the state"

    # the event histories are identical, wall clock aside
    assert _comparable(_events(broken)) == _comparable(_events(unbroken))

    # both halves append to the same session log, so the broken run's full
    # action stream must equal the unbroken run's — cycle for cycle
    acts_a = [[(a["kind"], a["sl"], a["fraction"]) for a in r["actions"]]
              for r in _recs(unbroken)]
    recs_b = _recs(broken)
    acts_b = [[(a["kind"], a["sl"], a["fraction"]) for a in r["actions"]]
              for r in recs_b]
    assert acts_b == acts_a

    # and the final folded state agrees field for field
    final_a, final_b = _recs(unbroken)[-1], recs_b[-1]
    for f in ("sl", "qty", "hwm", "stage"):
        assert final_b[f] == final_a[f], f


# ------------------------------------------------------------ refusals

def test_open_trade_refuses_non_resume_start(tmp_path, monkeypatch):
    assert _run(tmp_path, monkeypatch, REPLAY[:2]) == 0
    with pytest.raises(RuntimeError, match="Refusing to silently fork"):
        _run(tmp_path, monkeypatch, REPLAY[2:])
    with pytest.raises(RuntimeError, match="no durable events"):
        _run(tmp_path / "empty", monkeypatch, REPLAY, resume=True)


def test_closed_trade_refuses_resume(tmp_path, monkeypatch):
    flat = {**PLAN, "flatten_at_et": None}
    exit_replay = [200.5, 201.2, 198.5]                  # stop hit -> closed
    assert _run(tmp_path, monkeypatch, exit_replay) == 0
    events = _events(tmp_path)
    assert events[-1].event_type == "POSITION_CLOSED"
    with pytest.raises(RuntimeError, match="CLOSED trade"):
        _run(tmp_path, monkeypatch, [200.0], resume=True)


def test_dry_run_carries_the_clock_c004(tmp_path, monkeypatch):
    """Review finding 1 (HIGH): the pre-validation dry-run must forward now_et
    exactly like apply_action does, or a backwards clock passes the dry-run,
    the events go durable, and apply refuses actions the log already claims."""
    _patch(tmp_path, monkeypatch)
    real = runner.decide_exit

    def clock_stepback(state):
        acts = real(state)
        state.last_now_et = "11:00"                  # facts recorded at 11:00...
        state.now_et = "10:59"                       # ...now the clock steps back
        return acts

    monkeypatch.setattr(runner, "decide_exit", clock_stepback)
    from stockfish_constitution import ConstitutionError
    with pytest.raises(ConstitutionError, match="C004"):
        runner.run(dict(PLAN), interval=0, once=False, replay=[200.5],
                   max_stale=180, require_bias=False)
    events = _events(tmp_path)
    assert [e.event_type for e in events] == ["POSITION_OPENED"], \
        "the refused cycle must leave NO durable events"


def test_per_action_dry_run_catches_what_the_batch_check_cannot(tmp_path, monkeypatch):
    """Review finding 2: a batch that passes C007 ordering but contains a
    per-action violation (a LOOSENING stop, C003) must be refused BEFORE any
    append — this is the test that makes the per-action dry-run loop
    load-bearing instead of dead weight."""
    _patch(tmp_path, monkeypatch)
    from stockfish_exit import Action
    real = runner.decide_exit

    def loosen(state):
        real(state)
        return [Action("MOVE_SL", sl=state.sl - 5.0, reason="loosen it")]

    monkeypatch.setattr(runner, "decide_exit", loosen)
    from stockfish_constitution import ConstitutionError
    with pytest.raises(ConstitutionError, match="C003"):
        runner.run(dict(PLAN), interval=0, once=False, replay=[200.5],
                   max_stale=180, require_bias=False)
    assert [e.event_type for e in _events(tmp_path)] == ["POSITION_OPENED"]


def test_resume_the_logs_plan_wins_over_a_doctored_cli_plan(tmp_path, monkeypatch, capsys):
    """Review finding 3: on resume, the trade's RECORDED plan governs — a
    doctored CLI plan is ignored (with a loud warning), and the continuation
    matches the unbroken run exactly."""
    unbroken = tmp_path / "a"
    broken = tmp_path / "b"
    unbroken.mkdir(), broken.mkdir()
    assert _run(unbroken, monkeypatch, REPLAY) == 0
    assert _run(broken, monkeypatch, REPLAY[:4]) == 0

    doctored = {**PLAN, "trail_mult": None, "be_arm_frac": 1.0}   # very different
    _patch(broken, monkeypatch)
    assert runner.run(doctored, interval=0, once=False, replay=REPLAY[4:],
                      max_stale=180, require_bias=False, resume=True) == 0
    assert "the LOG's plan wins" in capsys.readouterr().out
    assert _comparable(_events(broken)) == _comparable(_events(unbroken))
    final_a, final_b = _recs(unbroken)[-1], _recs(broken)[-1]
    for f in ("sl", "qty", "hwm", "stage"):
        assert final_b[f] == final_a[f], f
    # and the resumed session-log's provenance stamp is the LOG's plan, not the
    # doctored CLI plan — this is the observable that pins the plan override
    # itself (the continuation's behavior flows from the rebuilt STATE either
    # way, so behavior alone cannot catch a dropped override)
    resumed_stamp = [r["plan"] for r in _recs(broken)[4:] if "plan" in r]
    assert resumed_stamp and resumed_stamp[0]["trail_mult"] == PLAN["trail_mult"]
    assert resumed_stamp[0]["be_arm_frac"] == PLAN["be_arm_frac"]


def test_newline_terminated_garbage_final_line_is_corruption_not_torn(tmp_path):
    """Review finding 4: a newline-terminated final line was a COMPLETE write.
    If it doesn't parse, it is mid-log corruption — never truncated, never
    granted the tear's repair exemption."""
    from test_trade_events import opened
    from trade_events import EventError, TornTailError
    log = JsonlEventLog(tmp_path / "t.events.jsonl")
    log.append(opened())
    with log.path.open("a") as fh:
        fh.write("{not json but complete}\n")
    with pytest.raises(EventError) as e:
        log.load()
    assert not isinstance(e.value, TornTailError)
    with pytest.raises(EventError):
        log.load(tolerate_torn_tail=True)            # tolerance is for tears only
    assert log.truncate_torn_tail() == 0             # and truncation refuses too


def test_closed_trade_rotates_aside_on_fresh_start(tmp_path, monkeypatch):
    """A CLOSED trade's log never blocks a fresh session and is never
    overwritten — it rotates aside with its history intact."""
    assert _run(tmp_path, monkeypatch, [200.5, 201.2, 198.5]) == 0   # stop hit
    assert _events(tmp_path)[-1].event_type == "POSITION_CLOSED"
    assert _run(tmp_path, monkeypatch, REPLAY[:2]) == 0              # fresh start OK
    rotated = list(tmp_path.glob("events_*.closed-0.jsonl"))
    assert len(rotated) == 1                                         # history kept
    fresh = _events(tmp_path)
    assert fresh[0].event_type == "POSITION_OPENED"
    assert not any(e.event_type == "POSITION_CLOSED" for e in fresh)


def test_torn_tail_tolerated_on_resume_refused_otherwise(tmp_path, monkeypatch):
    """A torn final line IS the crash Gate 2 defends against: on --resume it is
    dropped loudly (the decision never became durable — same as crashing before
    the append); without --resume it refuses with the guiding error."""
    from trade_events import TornTailError
    assert _run(tmp_path, monkeypatch, REPLAY[:2]) == 0
    path = next(tmp_path.glob("events_*.jsonl"))
    n_durable = len(path.read_text().splitlines())
    with path.open("a") as fh:
        fh.write('{"event_id": "torn')                               # crash mid-append
    with pytest.raises(TornTailError):
        _run(tmp_path, monkeypatch, REPLAY[2:])                      # non-resume: refuse
    assert _run(tmp_path, monkeypatch, REPLAY[2:], resume=True) == 0
    events = _events(tmp_path)
    assert len(events) > n_durable                                   # continued past it
    assert all(e.event_id != "torn" for e in events)


def test_pending_submission_refuses_resume(tmp_path, monkeypatch):
    """A SUBMITTED-but-never-CONFIRMED reduction means the crash landed between
    the two appends. Reconciling that is card 013's unwired job — resume must
    refuse rather than guess whether the broker filled (and rather than
    re-emit the same key, which would corrupt the log)."""
    from datetime import datetime as dt, timezone as tz
    from trade_events import TradeEvent
    _patch(tmp_path, monkeypatch)
    now = dt.now(tz.utc).isoformat()
    date = dt.now(runner.ET).date()
    log = JsonlEventLog(tmp_path / f"events_{date}_NVDA.jsonl")
    log.append(TradeEvent(event_id="t-0", trade_id="t", sequence=0,
                          event_type="POSITION_OPENED", occurred_at=now,
                          source_version="test", payload={"plan": dict(PLAN)}))
    log.append(TradeEvent(event_id="t-1", trade_id="t", sequence=1,
                          event_type="PARTIAL_ORDER_SUBMITTED", occurred_at=now,
                          source_version="test",
                          payload={"fraction": 0.5,
                                   "idempotency_key": "TAKE_PARTIAL:0.5:0"}))
    with pytest.raises(RuntimeError, match="never CONFIRMED"):
        runner.run(dict(PLAN), interval=0, once=False, replay=[203.0],
                   max_stale=180, require_bias=False, resume=True)


def test_first_append_fsyncs_the_directory(tmp_path, monkeypatch):
    """Spec 012 wiring obligation: creating the log fsyncs the PARENT DIRECTORY
    too, or the brand-new file itself can vanish on power loss. Observed via an
    fsync spy: two fsyncs on the creating append, one thereafter."""
    import os as os_mod
    import trade_events as te
    calls: list = []
    real_fsync = os_mod.fsync
    monkeypatch.setattr(te.os, "fsync", lambda fd: (calls.append(fd),
                                                    real_fsync(fd))[1])
    from test_trade_events import opened, ev
    log = JsonlEventLog(tmp_path / "sub" / "t.events.jsonl")
    log.append(opened())
    assert len(calls) == 2                       # file + parent directory
    log.append(ev(1, "HWM_UPDATED", {"hwm": 201.0}))
    assert len(calls) == 3                       # file only


# ----------------------------------------------------- DoD channel parity

def test_backtest_dod_diff_channel_unchanged(tmp_path, monkeypatch):
    """Events are additive I/O: the session JSONL still round-trips through
    the backtest harness with a clean DoD diff."""
    assert _run(tmp_path, monkeypatch, REPLAY) == 0
    import backtest
    log = next(tmp_path.glob("session_*.jsonl"))
    identical, problems = backtest.diff_logs(log, dict(PLAN))
    assert identical, problems
