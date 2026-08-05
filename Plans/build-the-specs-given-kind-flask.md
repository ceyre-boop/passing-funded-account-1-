# Plan — Stockfish v3: lifecycle stages, layered stops, intent policies

## Context

The reframing is a sharpening of ruling 1, not a departure from it: AlphaZero
communicates *meaning*, Stockfish controls *mechanics*, and that boundary holds
as both grow. What changes is Stockfish's internal shape — from one flat ladder
of conditions into a position-lifecycle state machine, a layered protection
system, and a named-intent policy vocabulary.

**State of the evidence, recorded so the plan is read in context.** Training day 1
did not happen: zero orders on the paper account on Aug 4, no session log, no
ledger row 2. The system has one ledger row (Aug 3, taken by hand, not through
this system), zero live runner sessions, and 24 backtest entries with 3 winners.
The Aug 3 ceiling found the binding constraint is the **entry** — on 21 of 24
days no exit configuration, perfect and with hindsight across 396 configs, could
make money. Spec 003 says this file "must stay small enough to hold in your head."

I raised that this argues for a smaller slice first. Colin's call is the full
vision now. Proceeding with it — the job of this plan is to make a change this
size survivable, which means the invariants below are not optional.

**What the ceiling says about the policy values** (use data, not intuition, for
the parameter tables). Of the oracle's 24 picks: **22 used no trailing at all**,
**22 armed breakeven early at 0.25R**, and **all 24 carried a flatten time**.
Trail width — the axis the current five presets differ on — was reached for in
2 of 24. `EVENT` maps to `flatten_at_et`, the one lever every pick used.

---

## The conflict this plan has to resolve first

Byte-identical replay against the 2026-08-03 baseline **cannot survive this
refactor**, and pretending otherwise would produce a worse design.

At TP2 today the engine emits two stop moves: `MOVE_SL 204.90` ("stop rides to
TP1") then `MOVE_SL 205.00` (trail). Under most-protective-wins, both are
candidates at that instant and the trail already dominates, so the layered system
emits **one** `MOVE_SL` to 205.00. Final stop identical, action sequence not.

Resolution: **the baseline is deliberately re-cut, once.** That property was a
refactor-safety check, not the Definition of Done. The DoD is
`runner log == harness log`, and that must stay empty throughout. Both the old
and new expected logs get committed side by side under
`data/daytrade/replay_expected/` so the change is visible and reviewable rather
than silent.

The stronger replacement test, which does not depend on emission granularity:
**every one of the ceiling's 24 per-config R values must be unchanged.** The
refactor changes *when stop moves are announced*, never *where the stop ends up* —
so if any R moves, the refactor changed behaviour and is wrong.

---

## 1. Lifecycle state machine — `daytrade/stockfish_exit.py`

```python
class Stage(str, Enum):
    ENTERED   = "ENTERED"     # hard stop + emergency exit only
    PROTECTED = "PROTECTED"   # breakeven armed; first reduction allowed
    SCALED    = "SCALED"      # profit ladder + volatility stop allowed
    RUNNER    = "RUNNER"      # trailing only
    CLOSING   = "CLOSING"     # no new adjustments; reconcile remaining orders
    CLOSED    = "CLOSED"
```

`stage` becomes authoritative on `TradeState`. `tp1_done` / `tp2_done` stay as
**read-only properties** derived from it — `runner.py` writes both into every
session-log record, and changing that schema would break the diffability the
whole architecture rests on.

Transitions are **monotonic by construction**: a single `advance(state, target)`
refuses any move to a lower ordinal. That is the guarantee the proposal is after
("could never fall back from RUNNER to an earlier risk state"). Worth being
honest that this guarantee already holds today — `tp1_done`/`tp2_done` are never
unset — so the value here is structural enforcement, not a bug fix.

`ALLOWED_ACTIONS: dict[Stage, frozenset[str]]` gates emission. `decide_exit`
filters its action list through the current stage's allowlist and **raises** on
violation rather than dropping silently, per APEX #2. On the existing code path
the gate must be a no-op — if it changes which actions fire, the mapping is wrong.

---

## 2. Layered protection system — same file

```python
@dataclass(frozen=True)
class StopCandidate:
    name: str          # catastrophic | thesis | time_decay | breakeven |
                       # volatility | profit_lock | trail
    level: float
    active: bool
    reason: str

def stop_candidates(state) -> list[StopCandidate]
def effective_stop(state) -> StopCandidate   # most protective ACTIVE candidate
```

"Most protective" = max level for a long, min for a short. This makes the
never-loosen invariant **structural**. It currently holds — I fuzzed 4,000 random
paths × 60 bars across all five policies and both directions, zero violations —
but it is enforced by branch *ordering* plus a single `cur_ok` check at
`stockfish_exit.py:164`. Nothing prevents a future branch from emitting a looser
`MOVE_SL`; `apply_action` assigns whatever it is handed.

The payoff the proposal names is real and should be delivered: the emitted
`reason` becomes structural rather than a hand-written string —
*"profit_lock became more protective than volatility, effective stop 204.90 → 205.00."*

Layers at v3, honestly labelled:

| layer | v3 status |
|---|---|
| catastrophic | **active** — the original plan stop, never removed |
| breakeven | **active** from PROTECTED — `entry` |
| profit_lock | **active** from SCALED — `tp1` |
| trail | **active** from SCALED when `trail_mult` is not None |
| volatility | **inactive** — needs ATR on `TradeState`; no caller supplies it |
| thesis | **inactive** — needs an invalidation level from AlphaZero; `regime.py` is a stub |
| time_decay | **inactive** — the flatten rule stays an `EXIT_ALL`, not a stop level |

Inactive layers are defined, constructed, and return `active=False` with the
reason they are dark. They are not commented-out code and not silently absent.

---

## 3. Intent policies — same file, replacing `POLICY_PARAMS`

`DEFEND` · `HARVEST` · `RIDE` · `EVENT` · `SALVAGE`, each expanding to the same
authoritative params on `TradeState` (`policy_params()` keeps its current shape,
so `ceiling.py`'s grid sweep is untouched).

Two design rules that matter more than the names:

- **`EVENT` requires `flatten_at_et`.** A policy whose entire meaning is "be out
  before the catalyst" must fail loud when no catalyst time is supplied, not
  quietly behave like DEFEND. `policy_params()` grows a required-field check.
- **`SALVAGE` is definable but never auto-emitted.** It means "the original
  thesis has weakened," and nothing in the system can currently assert that —
  that is AlphaZero's job and the classifier does not exist. Same treatment
  `SCRATCH_FAST` already carries: hand-selectable, labelled `[SKETCH]`, and
  documented as blocked on `regime.py`.

The legacy names (`STATIC`/`TRAIL_WIDE`/`TRAIL_TIGHT`/`SCRATCH_FAST`/`DEFAULT`)
stay as **aliases** for one release. `ceiling.py:narrow_space()`,
`data/daytrade/policy_today.json` and `TRAINING_DAY_1.md` all reference them, and
breaking the runbook the night before a session it might finally be used is a bad
trade.

---

## Files

- `daytrade/stockfish_exit.py` — all three changes; the only file with new logic
- `daytrade/runner.py` — log `stage` and `effective_stop.name` per cycle; handle
  `EVENT`'s required-param error at plan load
- `daytrade/ceiling.py` — `narrow_space()` onto the new names; grid sweep unchanged
- `data/daytrade/replay_expected/{v2_baseline,v3}.log` — both committed
- `specs/003_STOCKFISH_V2.md` — record what was built and that v3 supersedes it
- `daytrade/regime.py` (stub) — its docstring names the old `exit_policy`
  vocabulary; update so the next builder isn't handed a stale contract

---

## Verification

1. **Ceiling R values unchanged.** Re-run `ceiling.py`; all 24 per-config returns
   must match `data/daytrade/ceiling_report.json` exactly. This is the primary
   correctness test — it is insensitive to emission granularity and sensitive to
   any real behaviour change.
2. **DoD diff empty.** `backtest.py --replay-session` on a runner-produced log.
   Non-empty means the runner is deciding something the engine is not.
3. **Fuzz the invariants** — the harness from this session, extended: stop never
   loosens (now asserted inside `effective_stop`), stage never regresses, no
   action fires outside its stage allowlist. 4,000 paths minimum, both directions,
   every policy.
4. **Re-baselined replay reviewed, not just regenerated.** Diff
   `v2_baseline.log` against `v3.log` by hand and confirm every difference is a
   collapsed intermediate `MOVE_SL` and nothing else.
5. **`EVENT` without `flatten_at_et` raises**; `SALVAGE` is selectable by hand and
   emitted by nothing.
6. **Runbook still runs.** `newplan.py` → `runner.py --once` against
   `policy_today.json` unchanged, since that file is what a live session loads.

---

## Left explicitly undone, with reasons in the code

`volatility` and `thesis` stop layers (need ATR and an invalidation level),
`SALVAGE` auto-emission (needs `regime.py`), and the `time_decay` layer as a
tightening schedule rather than a flat exit. Each ships as a constructed,
inactive layer carrying its blocker — visible in the layer list at runtime, not
buried in markdown.
