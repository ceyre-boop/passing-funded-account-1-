"""
tablebase.py — Phase 1, component 3. The static evaluation function.

Backward induction over realized paths. No Monte Carlo, no rollout sampling:
the value of every (state, action) pair is computed exactly from what actually
happened, working backwards from session close.

This is the piece that does not exist yet. Components 1, 2 and 4 are built.

Why induction terminates: session_phase is monotone and every path ends at the
close, so the state graph is a DAG. Values at the last phase are terminal by
definition and propagate backwards with no fixed-point iteration.

Two guardrails, both non-optional:
  - leave-one-episode-out: a path never contributes to the value it is scored
    against. Asserted, not assumed.
  - min_paths: cells below the occupancy floor return NO_VALUE rather than a
    number. A thin cell must be visibly absent, not quietly wrong.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Iterator

import numpy as np
import pandas as pd

from state_space import State, discretize


class Action(str, Enum):
    HOLD = "hold"
    TIGHTEN = "tighten"
    EXIT = "exit"


NO_VALUE = float("nan")


@dataclass(frozen=True, slots=True)
class Transition:
    """One observed step of one episode."""
    episode: str
    phase: int              # monotone session phase index; induction runs on this
    state: State
    realized_r: float       # unrealized R at this bar — value of exiting now
    next_state: State | None    # None => terminal (session close or stop-out)
    r_if_tightened: float | None  # realized R had the tightened stop been active
    terminal_r: float       # R the episode actually ended at


class LeakError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Value construction
# ---------------------------------------------------------------------------

@dataclass
class Cell:
    q_hold: float = NO_VALUE
    q_tighten: float = NO_VALUE
    q_exit: float = NO_VALUE
    n_paths: int = 0
    episodes: frozenset[str] = frozenset()

    def best(self) -> tuple[Action, float]:
        qs = {Action.HOLD: self.q_hold,
              Action.TIGHTEN: self.q_tighten,
              Action.EXIT: self.q_exit}
        live = {a: v for a, v in qs.items() if not np.isnan(v)}
        if not live:
            return Action.EXIT, NO_VALUE      # unknown state => flatten, never guess
        a = max(live, key=live.get)
        return a, live[a]


class Tablebase:
    def __init__(self, *, min_paths: int = 30):
        self.min_paths = min_paths
        self.cells: dict[State, Cell] = {}
        self._built = False

    # -- build -------------------------------------------------------------

    def build(self, transitions: list[Transition]) -> "Tablebase":
        by_phase: dict[int, list[Transition]] = defaultdict(list)
        for t in transitions:
            by_phase[t.phase].append(t)

        # Backwards over the monotone phase index. DAG, single sweep.
        for phase in sorted(by_phase, reverse=True):
            bucket: dict[State, list[Transition]] = defaultdict(list)
            for t in by_phase[phase]:
                bucket[t.state].append(t)

            for state, ts in bucket.items():
                if len(ts) < self.min_paths:
                    self.cells[state] = Cell(
                        n_paths=len(ts),
                        episodes=frozenset(t.episode for t in ts),
                    )
                    continue

                q_exit = float(np.mean([t.realized_r for t in ts]))

                hold_vals = []
                for t in ts:
                    if t.next_state is None:
                        hold_vals.append(t.terminal_r)
                    else:
                        succ = self.cells.get(t.next_state)
                        if succ is None:
                            continue          # unreached successor: contributes nothing
                        _, v = succ.best()
                        hold_vals.append(t.terminal_r if np.isnan(v) else v)
                q_hold = float(np.mean(hold_vals)) if hold_vals else NO_VALUE

                tight = [t.r_if_tightened for t in ts if t.r_if_tightened is not None]
                q_tighten = float(np.mean(tight)) if len(tight) >= self.min_paths else NO_VALUE

                self.cells[state] = Cell(
                    q_hold=q_hold,
                    q_tighten=q_tighten,
                    q_exit=q_exit,
                    n_paths=len(ts),
                    episodes=frozenset(t.episode for t in ts),
                )

        self._built = True
        return self

    # -- query -------------------------------------------------------------

    def evaluate(self, state: State, *, scoring_episode: str | None = None) -> tuple[Action, float]:
        """
        scoring_episode: when evaluating a path, pass its episode id. If that
        episode helped build this cell, the lookup is contaminated and raises.
        Leave-one-out is enforced here rather than trusted upstream.
        """
        if not self._built:
            raise RuntimeError("build() before evaluate()")
        cell = self.cells.get(state)
        if cell is None:
            return Action.EXIT, NO_VALUE
        if scoring_episode is not None and scoring_episode in cell.episodes:
            raise LeakError(
                f"episode {scoring_episode} is inside the cell it is being scored "
                f"against (state={state}, n={cell.n_paths}). Rebuild with that "
                "episode held out."
            )
        return cell.best()

    # -- reporting ---------------------------------------------------------

    def coverage(self) -> dict:
        total = len(self.cells)
        valued = sum(1 for c in self.cells.values() if not np.isnan(c.best()[1]))
        obs = sum(c.n_paths for c in self.cells.values())
        obs_valued = sum(c.n_paths for c in self.cells.values()
                         if not np.isnan(c.best()[1]))
        return {
            "cells": total,
            "cells_valued": valued,
            "frac_cells_valued": valued / total if total else 0.0,
            "observations": obs,
            "frac_obs_valued": obs_valued / obs if obs else 0.0,
            "min_paths": self.min_paths,
        }

    def policy_frame(self) -> pd.DataFrame:
        rows = []
        for s, c in self.cells.items():
            a, v = c.best()
            rows.append({
                "state": str(s), "action": a.value, "value": v,
                "q_hold": c.q_hold, "q_tighten": c.q_tighten, "q_exit": c.q_exit,
                "n_paths": c.n_paths,
            })
        return pd.DataFrame(rows).sort_values("n_paths", ascending=False)


# ---------------------------------------------------------------------------
# Fold-safe construction: build K tablebases, each blind to its own scoring fold
# ---------------------------------------------------------------------------

def build_purged_folds(
    transitions: list[Transition],
    k: int = 5,
    *,
    min_paths: int = 30,
    seed: int = 0,
) -> Iterator[tuple[Tablebase, list[Transition]]]:
    """Yields (tablebase_built_without_fold, held_out_transitions)."""
    episodes = sorted({t.episode for t in transitions})
    rng = np.random.default_rng(seed)
    rng.shuffle(episodes)
    folds = np.array_split(episodes, k)

    for f in folds:
        held = set(f.tolist())
        train = [t for t in transitions if t.episode not in held]
        test = [t for t in transitions if t.episode in held]
        yield Tablebase(min_paths=min_paths).build(train), test