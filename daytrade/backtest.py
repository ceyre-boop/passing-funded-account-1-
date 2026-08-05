#!/usr/bin/env python3
"""BACKTEST — spec 005. The bench, and the Definition of Done for the runner.

JOB 1 (built): replay a recorded live session through the SAME engine and prove
the harness and the runner produce identical action logs. If that diff is not
empty, something in the runner is deciding on its own authority and must be moved
into `decide_exit` (ruling 1). This is the test that proves the architecture.

JOB 2 (blocked on spec 001): label history with regime calls and grade them.
`label_history` below is a live stub — it raises with the reason. See its
docstring; the work is NOT forgotten, it is waiting on `regime.py`.

The ceiling measurement (spec 008) lives in `ceiling.py` and already carries the
counterfactual machinery — `ceiling.simulate` replays one entry under one exit
config, which is exactly what `counterfactual` needs. It is imported, not
reimplemented.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stockfish_exit import TradeState, decide_exit, apply_action  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LOGDIR = ROOT / "data" / "daytrade"

# The fields the DoD compares. Quote source, latency and broker responses
# legitimately differ between a live run and a replay, so they are excluded —
# comparing them would produce a diff that means nothing and train us to ignore it.
COMPARED = ("price", "actions")


class ReplayMismatch(RuntimeError):
    """The harness and the runner disagreed. Never downgraded to a warning."""


def _state_from_record(rec: dict, plan: dict) -> TradeState:
    """Rebuild the engine's starting state — via the RUNNER'S OWN constructor.

    This used to build a TradeState by hand, and it silently drifted: it never
    threaded trail_mult / be_arm_frac / hold_past_tp2 / exit_policy, so the
    harness reconstructed engine DEFAULTS no matter what the plan said. On
    plan.example.json that is invisible, because the example uses the defaults.
    On data/daytrade/policy_today.json — the config actually configured to trade
    — the harness trailed at full width and armed breakeven late while the live
    runner did neither, and the DoD diff failed on four of eight cycles.

    A second way to build the state is a second implementation, which is the one
    thing this architecture forbids. There is now one: runner.state_from_plan.
    """
    from runner import state_from_plan
    return state_from_plan(plan)


def replay_session(session_jsonl: Path, plan: dict) -> list[dict]:
    """Job 1. Feed the recorded price stream back through the engine.

    Reads ONLY price and urgency from each recorded cycle — the same two inputs
    the runner had — and re-derives the actions. Anything the runner added on its
    own authority shows up immediately as a diff.
    """
    rows = [json.loads(l) for l in session_jsonl.open() if l.strip()]
    if not rows:
        raise ReplayMismatch(f"{session_jsonl} is empty — nothing to replay")

    # The session's own recorded plan always wins. A plan passed on the command
    # line is a guess about what the runner used; the log knows.
    embedded = rows[0].get("plan")
    if embedded:
        # Already RESOLVED — the runner stamped it after load_plan had computed
        # tp1/tp2/trail_dist and normalised direction. Re-running load_plan on it
        # would fail its own "exactly one of tp2/tp2_r/day_goal_usd" check, since
        # a resolved plan legitimately carries both the ratio and the price.
        plan = embedded
    else:
        print("  !! this session log predates plan-stamping, so the plan passed on\n"
              "     the command line is being TRUSTED, not verified. If it is not the\n"
              "     plan the runner actually ran, this diff is meaningless.")

    st = _state_from_record(rows[0], plan)
    out = []
    for rec in rows:
        st.price = float(rec["price"])
        st.urgent = rec.get("urgent")
        st.now_et = None            # replay has no honest clock; runner logged its own
        actions = decide_exit(st)
        for a in actions:
            apply_action(st, a)
        out.append({
            "step": rec.get("step"), "price": st.price,
            "actions": [{"kind": a.kind, "sl": a.sl, "fraction": a.fraction,
                         "reason": a.reason} for a in actions],
        })
        if any(a.kind == "EXIT_ALL" for a in actions):
            break
    return out


def diff_logs(live_jsonl: Path, plan: dict) -> tuple[bool, list[str]]:
    """The DoD. Returns (identical, human-readable differences)."""
    live = [json.loads(l) for l in live_jsonl.open() if l.strip()]
    replayed = replay_session(live_jsonl, plan)

    problems = []
    if len(live) != len(replayed):
        problems.append(f"cycle count: live {len(live)} vs replay {len(replayed)}")
    for i, (lv, rp) in enumerate(zip(live, replayed)):
        for f in COMPARED:
            if json.dumps(lv.get(f), sort_keys=True) != json.dumps(rp.get(f), sort_keys=True):
                problems.append(f"cycle {i} field {f!r}:\n"
                                f"    live   {json.dumps(lv.get(f))}\n"
                                f"    replay {json.dumps(rp.get(f))}")
    return (not problems), problems


def label_history(symbol: str, days: int) -> None:
    """Job 2 — NOT BUILT. Blocked on spec 001 (`daytrade/regime.py`).

    WHAT IS MISSING, so this is not forgotten:
      - `regime.classify(bars_5m, bars_1m, ctx) -> RegimeRead` does not exist yet.
      - `scorecard.grade(read, forward_bars) -> dict` (spec 004) does not exist yet.

    WHEN BOTH EXIST, this function does exactly this and nothing more:
      for each 5m block in tune_sessions(): classify() on bars <= N, grade()
      on bars N+1..N+K, append one row to data/regime_labels.csv and
      data/regime_scorecard.csv. Assert at the boundary that the classify window
      and the grade window do not overlap — spec 005 requires it and the reclaim
      window (N_RECLAIM=3 bars) and the grade window (K=3 blocks) are the same
      length, so overlap is a live hazard, not a theoretical one.

    It runs on the TUNE split only. See splits.py for why that is not optional.
    """
    raise NotImplementedError(
        "label_history needs regime.classify (spec 001) and scorecard.grade (spec 004). "
        "Neither exists yet. Build 001 next — the ceiling measurement in ceiling.py is "
        "the bench it gets tuned on. See this function's docstring for the exact "
        "contract it will implement.")


def counterfactual(symbol: str = "NVDA", policies=("STATIC", "TRAIL_WIDE", "TRAIL_TIGHT")) -> dict:
    """Same entries, each exit policy. Delegates to ceiling.py — one implementation."""
    from bars import load_sessions
    from ceiling import find_entry, simulate, narrow_space
    from splits import tune_sessions

    space = {k: v for k, v in narrow_space().items() if k in policies}
    entries = [(s, e) for s in tune_sessions(load_sessions(symbol, "5m"))
               if (e := find_entry(s))]
    return {name: {"total_R": sum(simulate(s, e, cfg) for s, e in entries),
                   "n": len(entries)}
            for name, cfg in space.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="spec 005 — the bench and the DoD diff")
    ap.add_argument("--replay-session", metavar="JSONL",
                    help="a runner session log to replay and diff")
    ap.add_argument("--plan", default=str(LOGDIR / "plan.example.json"))
    ap.add_argument("--counterfactual", action="store_true")
    a = ap.parse_args(argv)

    if a.counterfactual:
        for name, r in counterfactual().items():
            print(f"  {name:12s} {r['total_R']:+7.2f} R over {r['n']} entries")
        return 0

    if not a.replay_session:
        ap.error("give --replay-session or --counterfactual")

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from runner import load_plan
    plan = load_plan(Path(a.plan))

    ok, problems = diff_logs(Path(a.replay_session), plan)
    bar = "=" * 68
    print(bar)
    print(f"  DoD DIFF — {Path(a.replay_session).name}")
    print(f"  comparing: {', '.join(COMPARED)}")
    print(bar)
    if ok:
        print("  EMPTY. The harness and the runner produce identical action logs.")
        print("  The engine is the single source of exit decisions.")
        print(bar)
        return 0
    print(f"  {len(problems)} DIFFERENCE(S) — the runner is deciding something the")
    print("  engine did not. Move that rule into decide_exit (ruling 1).")
    for p in problems[:20]:
        print(f"    {p}")
    print(bar)
    return 1


if __name__ == "__main__":
    sys.exit(main())
