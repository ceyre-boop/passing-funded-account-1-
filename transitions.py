"""
transitions.py — turns replayed sessions into the raw input tablebase.py needs.

Sits between the replay engine (`daytrade/ceiling.simulate`) and backward
induction (`tablebase.Tablebase.build`): one `Transition` per bar a position
was actually open, carrying the discretized state, what exiting right there
would have been worth, and what the episode eventually resolved to.

Two invariants are enforced here rather than trusted upstream, because a
tablebase built on either violation is silently wrong, not loudly wrong:

  - episode identity: `f"{symbol}:{day}"` must be unique across the whole
    session list, and the count of distinct episodes must equal the count of
    entries actually processed. A collision means two different trades would
    be mistaken for the same leave-one-out unit.
  - phase monotonicity: `tablebase.py`'s backward induction relies on the
    phase index being strictly increasing within an episode (that's what
    makes the state graph a DAG). Asserted per episode, not assumed.

TUNE LANE ONLY. This module builds from `splits.tune_sessions(...)` and
refuses any session in the SEALED lane (`s.day > splits.TUNE_END`). The
boundary day itself is tune data: `splits.tune_sessions()` is `<= TUNE_END`
and `sealed_sessions()` is `> TUNE_END`, so
that inclusive edge is the one case this module will not build transitions
from. It never touches `splits.sealed_sessions`.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "daytrade"))

from ceiling import find_entry, simulate, COST_PER_SHARE, time_block   # noqa: E402
from splits import TUNE_END                                            # noqa: E402
from stockfish_exit import Stage                                       # noqa: E402

from state_space import discretize                                     # noqa: E402
from tablebase import Action, Transition                               # noqa: E402


# TIGHTEN is not weakly-supported when trail_mult is None, it is an ILLEGAL
# move: stockfish_exit.trail_distance() (stockfish_exit.py:218) has no width
# to scale, so the execution layer physically cannot perform it. Quotable
# module-level constant, in the spirit of stop_candidates()'s dark-layer
# reasons (stockfish_exit.py).
TIGHTEN_ILLEGAL_NO_TRAIL = (
    "TIGHTEN requires a trail to tighten: cfg['trail_mult'] is None, so "
    "stockfish_exit.py:218 has nothing to scale"
)

ALWAYS_LEGAL = frozenset({Action.HOLD, Action.EXIT})
ALL_ACTIONS = frozenset({Action.HOLD, Action.TIGHTEN, Action.EXIT})


def legal_moves_for(cfg: dict) -> frozenset[Action]:
    """HOLD and EXIT are always legal. TIGHTEN is legal iff cfg has a trail
    to tighten — computed from the config, not from whether r_if_tightened
    happened to be computable for a given path."""
    return ALL_ACTIONS if cfg["trail_mult"] is not None else ALWAYS_LEGAL


def assert_legal_moves_consistent(transitions) -> None:
    """r_if_tightened is None **iff** TIGHTEN is absent from legal_moves.

    tablebase.py's q_tighten is built only from transitions carrying a
    non-None r_if_tightened. If a future config ever made TIGHTEN legal
    without populating r_if_tightened (or vice versa), the tablebase would
    silently starve a legal move of data, or silently offer a value for a
    move the executor cannot perform. Checked here rather than assumed.
    """
    for t in transitions:
        tighten_legal = Action.TIGHTEN in t.legal_moves
        r_present = t.r_if_tightened is not None
        if tighten_legal != r_present:
            raise ValueError(
                f"legal_moves / r_if_tightened disagreement in episode "
                f"{t.episode} phase {t.phase}: TIGHTEN legal={tighten_legal} "
                f"but r_if_tightened is "
                f"{'a float' if r_present else 'None'}"
            )


def assert_phases_monotone(transitions) -> None:
    """Phase must strictly increase within each episode.

    Two different things protect ordering and they see different failures.
    C004 ("no stale facts") refuses a backwards clock inside the engine, so
    reordered BARS never reach this module. This guard covers what C004 cannot
    see: a Transition list reordered AFTER extraction, on its way to
    `Tablebase.build`, where a non-monotone phase would silently corrupt the
    backward induction rather than raise.
    """
    seen: dict[str, int] = {}
    for t in transitions:
        prev = seen.get(t.episode)
        if prev is not None and not (t.phase > prev):
            raise ValueError(
                f"phase not strictly increasing within episode {t.episode}: "
                f"bars out of chronological order ({prev} -> {t.phase})"
            )
        seen[t.episode] = t.phase


def transitions_from_sessions(
    sessions, cfg: dict, *, edges: dict, min_paths: int
) -> tuple[list["Transition"], dict]:
    """Replay every session's entry through `cfg`, emit one Transition per
    open bar. `min_paths` is accepted for signature symmetry with
    `Tablebase(min_paths=...)` / `state_space.audit(min_paths=...)` and is
    recorded in the report; occupancy filtering itself happens downstream in
    `Tablebase.build`, not here — this module's job is to produce the raw
    paths, not to decide which cells are thick enough to trust.
    """
    for s in sessions:
        if s.day > TUNE_END:
            raise ValueError(
                f"session {s.symbol}:{s.day} is AFTER splits.TUNE_END "
                f"({TUNE_END}) — that is the sealed lane and this module "
                "refuses to build transitions from it"
            )

    transitions: list[Transition] = []
    seen_episodes: set[str] = set()
    n_entries = 0
    n_r_if_tightened_none = 0
    tighten_active = cfg["trail_mult"] is not None
    legal_moves = legal_moves_for(cfg)

    for s in sessions:
        e = find_entry(s)
        if e is None:
            continue
        n_entries += 1

        episode = f"{s.symbol}:{s.day}"
        if episode in seen_episodes:
            raise ValueError(
                f"episode {episode!r} was produced by more than one session "
                "— duplicate (symbol, day) in the input list"
            )
        seen_episodes.add(episode)

        bar_rows: list[dict] = []
        tr5: list[float] = []

        def collect(ts, bar, st, realized, held, _rows=bar_rows, _tr5=tr5):
            # Mirrors exit_evaluator.build_dataset's obs() guard: once the
            # engine has closed or fully reduced the position there is
            # nothing left to discretize a state for.
            if st.stage in (Stage.CLOSING, Stage.CLOSED) or held <= 0:
                return
            px = float(bar["Open"])
            tr = (float(bar["High"]) - float(bar["Low"])) / e.risk
            _tr5.append(tr)
            atr_now = sum(_tr5[-5:]) / min(len(_tr5), 5)
            # exit_evaluator.build_dataset's exit_now, term for term.
            realized_r = (realized + held * (px - e.entry) * e.direction
                          - COST_PER_SHARE) / e.risk
            unrealized_r = (px - e.entry) * e.direction / e.risk
            _rows.append({
                "ts": ts,
                "bars_held": len(_rows),
                "atr_now": atr_now,
                "unrealized_r": unrealized_r,
                "realized_r": realized_r,
            })

        terminal_r = simulate(s, e, cfg, observer=collect)

        if not bar_rows:
            # Entry fired on the last tradeable bar of the session: nothing
            # after it to observe. No transitions for this episode.
            continue

        atr0 = bar_rows[0]["atr_now"]
        states = [
            discretize(
                unrealized_r=r["unrealized_r"],
                bars_held=r["bars_held"],
                atr_ratio=r["atr_now"] / atr0,
                carry_r=0.0,
                time_block=time_block(r["ts"].strftime("%H:%M")),
                weekend_exposure=False,
                **edges,
            )
            for r in bar_rows
        ]

        # phase == bar position, which is trivially increasing as a list
        # index; what backward induction actually depends on is that those
        # positions track real chronological order. Check the timestamps
        # behind them, not the index that would pass no matter what.
        for i in range(1, len(bar_rows)):
            if not (bar_rows[i]["ts"] > bar_rows[i - 1]["ts"]):
                raise ValueError(
                    f"phase not strictly increasing within episode {episode} "
                    f"at index {i}: bars out of chronological order "
                    f"({bar_rows[i - 1]['ts']} -> {bar_rows[i]['ts']})"
                )

        for i, r in enumerate(bar_rows):
            next_state = states[i + 1] if i + 1 < len(states) else None

            if not tighten_active:
                r_if_tightened = None
                n_r_if_tightened_none += 1
            else:
                k = r["ts"]
                r_if_tightened = simulate(
                    s, e, cfg,
                    urgency_schedule=lambda ts, _k=k: "tighten" if ts >= _k else None,
                )

            transitions.append(Transition(
                episode=episode,
                phase=i,
                state=states[i],
                realized_r=r["realized_r"],
                next_state=next_state,
                r_if_tightened=r_if_tightened,
                terminal_r=terminal_r,
                legal_moves=legal_moves,
            ))

    n_episodes = len(seen_episodes)
    if n_episodes != n_entries:
        raise ValueError(
            f"episode count mismatch: {n_episodes} distinct episodes vs "
            f"{n_entries} entries processed"
        )

    assert_legal_moves_consistent(transitions)

    n_terminal = sum(1 for t in transitions if t.next_state is None)
    distinct_states = len({t.state for t in transitions})

    reason = (
        "cfg['trail_mult'] is None -> Tighten is structurally inert "
        "(stockfish_exit.trail_distance() returns None regardless of "
        "st.urgent); every transition from this cfg has r_if_tightened=None "
        "by construction, never a number equal to terminal_r"
        if not tighten_active else
        "cfg['trail_mult'] is not None -> Tighten is expressible; every "
        "transition got its own re-simulated r_if_tightened, so this should "
        "be 0"
    )

    report = {
        "n_sessions": len(sessions),
        "n_entries": n_entries,
        "n_transitions": len(transitions),
        "n_episodes": n_episodes,
        "transitions_per_episode": (
            len(transitions) / n_episodes if n_episodes else 0.0
        ),
        "n_r_if_tightened_none": n_r_if_tightened_none,
        "r_if_tightened_none_reason": reason,
        "n_terminal": n_terminal,
        "n_distinct_states": distinct_states,
        "min_paths": min_paths,
        "legal_moves": sorted(a.value for a in legal_moves),
        "tighten_illegal_reason": None if tighten_active else TIGHTEN_ILLEGAL_NO_TRAIL,
    }
    return transitions, report
