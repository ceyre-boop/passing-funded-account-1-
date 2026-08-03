# 003 — STOCKFISH v2  `daytrade/stockfish_exit.py` (EXTEND, never fork)   `[SPEC]`
The engine already exists and is correct. v2 adds exactly one input field and
one behavior switch. Resist every temptation to add more — this file is the one
thing in the system that must stay small enough to hold in your head.

## What changes
```python
@dataclass
class TradeState:
    ...                                   # everything existing, unchanged
    exit_policy: str = "STATIC"           # from RegimeRead.exit_policy
```
That is the entire interface change.

## Policy behavior table
```python
POLICY_PARAMS = {
    #                 trail_mult   be_arm_frac   notes
    "STATIC":        (None,        1.00),   # no trailing at all after TP2; levels stay put
    "TRAIL_WIDE":    (1.50,        1.00),   # let winners run on continuation days
    "TRAIL_TIGHT":   (0.50,        0.50),   # manipulation: arm breakeven at HALF the TP1
                                            #   distance, trail hard
    "SCRATCH_FAST":  (0.25,        0.25),   # [SKETCH] not emitted by regime v1
}
```
- `trail_mult=None` → after TP2, `decide_exit` emits no trail MOVE_SLs at all.
  Consolidation days: stop churning, leave the levels where the plan put them.
- `be_arm_frac` scales the TP1 breakeven trigger: on TRAIL_TIGHT, breakeven arms
  at `entry + 0.5*(tp1-entry)` instead of at tp1. Manipulation days steal stops;
  get to riskless sooner.

## Precedence — write it as a comment above the function and never violate it
```
1. urgent == "exit"        -> EXIT_ALL          (ALPHAZERO news shock)
2. hard stop hit           -> EXIT_ALL
3. time flatten            -> EXIT_ALL          (ruling 1: lives HERE, not the runner)
4. TP1 / TP2 ladder        -> MOVE_SL / TAKE_PARTIAL
5. exit_policy trailing    -> MOVE_SL           (the only thing v2 changes)
```
`urgent == "tighten"` still halves whatever trail the policy selected — it
multiplies with the policy, it does not replace it. `TRAIL_WIDE` + `tighten`
= 0.75×, not 0.5×.

## Definition of done
The replay test in `__main__` grows to four runs over the SAME price path, one
per policy, printing four distinct logs. Concretely, on the existing path:
- STATIC produces no MOVE_SL after the TP2 cycle.
- TRAIL_WIDE's stop lags further behind the high than the current default.
- TRAIL_TIGHT arms breakeven one cycle earlier than the others.
These four logs get committed as `data/daytrade/replay_expected/*.log` and the
backtest harness (spec 005) diffs against them. That is the regression test.

## Hard constraints (unchanged, restated because they matter most here)
- ONE implementation. The harness, the runner, and the replay all import this.
- Pure. No I/O, no clock, no network. Everything arrives on `TradeState`.
- Fail loud: unknown `exit_policy` string raises. No silent fallback to STATIC —
  a typo'd policy must stop the session, not quietly change the risk profile.
- Rule changes commit BEFORE the next session, never after a loss (APEX #5).

## `[SKETCH]` — SCRATCH_FAST, and why it isn't in v1
The idea: a confirmed sweep against an open position → get out at breakeven
immediately, don't wait for the trail. It is probably correct and probably
valuable. It is also the most dangerous rule in the plan, because "confirmed
sweep against us" is exactly the judgment call that, mis-specified, turns into
"panic out of every wick" — which would systematically convert winners into
scratches and look fine in P&L terms while quietly destroying the edge.

Prerequisites before this gets designed: regime v1 matching Colin's eye, ≥200
scored MANIPULATION blocks with measured accuracy, and a counterfactual replay
showing what SCRATCH_FAST would have done versus TRAIL_TIGHT on those exact days.
Then it gets its own spec. Not before.
