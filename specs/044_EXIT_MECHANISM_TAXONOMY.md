# 044 — EXIT MECHANISM TAXONOMY (Stockfish's opening book)  `[SPEC]`

**Status:** `[SPEC]` — safe to build from. Authored by the architect seat
2026-08-28. Per `specs/CLAUDE_LONG_TERM_HANDOFF.md` Roles, **the seat that wrote
this spec does not implement it.**

---

## Why this exists

A chess engine is strong because of two things working together: a **move
generator** that enumerates every legal move for the position, and an
**evaluation** that prunes to the handful a strong player would actually
consider — while the discarded moves remain enumerable rather than invisible.

`daytrade/stockfish_exit.py` already has the right instinct. `stop_candidates()`
constructs **every** layer, active or not, and each dark one carries the reason
it is dark (`:221-226`):

> "Inactive layers are CONSTRUCTED and returned carrying the reason they are
> dark — they are not commented-out code and not silently absent, so
> `daytrade/stockfish_exit.py --layers` shows the whole protection picture
> including what is missing and why."

What is missing is a **completeness claim** and an **enforcement**. The move list
lives only in code, nothing says whether it is the whole list, and nothing stops
a future seat from re-proposing a mechanism this repo has already killed under a
new name.

Spec 008 pre-registered the argument for building exactly this (`008:22-27`):

> "channel is too narrow — the knowledge is valuable, Stockfish has no way to
> express it → **build a different action space, not a different classifier**."
> … "A perfect classifier handed three trail settings is a grandmaster allowed
> three moves."

This spec is that action space, written down and made checkable.

## What this spec is NOT

It does **not** add anything to the selectable menu. `oracle_audit` measured
`null_leak = E[max of K families] − best_fixed` at **1.58** against a `0.15`
gate, and it converges *upward* with n (1.4496 → 1.5813 as n went 39 → 402)
because it is a property of the marginal distribution, not an estimation error.
Growing K makes selection strictly harder.

> **The library may grow. The menu may not.**

A mechanism enters the *selectable* set only by **replacing** something, measured
against the pinned opponent (`specs/033`), never by being appended.

---

## RULING — the ladder governs Stockfish (Colin, 2026-08-28)

Two exit doctrines contradicted each other on disk. This resolves it.

**Governing:** the TP1/TP2/TP3 ladder.
`ARCHITECTURE.md:46-56` — TP1 hit → stop to breakeven; TP2 hit → bank the
day-goal partial, stop rides to TP1; beyond → dynamic trail off the favourable
extreme; hard stop and time-flatten always live.
`I_AM_A_GOOD_TRADER.md:26-30` — same ladder in doctrine form.

**Not the Stockfish contract:** `MONDAY_OPEN.md:12-14`, `THE_SHOT.md:29-30`,
`NIGHTCAP.md:5` and `V3_RESEARCH.md:43` prescribe *no management at all* — fixed
OCO, no breakeven, no trail, no partials. Those are scoped to their own campaign
lane. They are not wrong; they are not this engine's contract, and a future
reader must not treat them as a competing specification for `decide_exit`.

---

## THE PARAMETER FIREWALL — the load-bearing rule of this spec

A mechanism has two separable parts, and they have completely different
epistemic status.

| part | what it is | where it may come from |
|---|---|---|
| **Definition** | what the mechanism *is* — "a volatility stop sits a multiple of average true range below the favourable extreme" | The literature. Vocabulary. Safe to write from training data or a textbook. |
| **Parameter** | the multiple, the lookback, the minute, the fraction | An **empirical claim** about a specific market and era. |

**Every parameter in this taxonomy MUST arrive as a required argument with no
default.** A plausible-looking default is indistinguishable in code from a
measured one, and unlike a fabricated data series nobody would ever catch it.

Precedent already set in this repo: `survival.py`'s daily-goal figure and
`portfolio_guard`'s G001–G004 exposure caps were left as **required arguments**
rather than guessed, precisely because an agent guessing there is the failure
mode the discipline exists to prevent.

**Enforcement:** a mechanism's `definition` string must contain no standalone
numeric literal. Operationally: no digit that is not preceded by a letter.
`TP1`, `C003`, `ATR14` pass (the digit is part of a name); `0.5`, `2x`,
`3 ATR`, `11:00` fail.

---

## THE REGISTRY

Implemented as `daytrade/exit_taxonomy.py`. Data, not prose, so it can be
checked.

```python
@dataclass(frozen=True)
class Mechanism:
    name: str
    family: str          # STOP_PLACEMENT | SCALING | TIME | INVALIDATION | TARGET
    definition: str      # what it IS. No standalone numeric literal.
    requires: tuple[str, ...]      # TradeState field names it needs
    status: str          # ACTIVE | DARK | FALSIFIED | OUT_OF_SCOPE
    reason: str          # required non-empty unless ACTIVE
    evidence: str | None # MECHANISMS.json id, spec file, or mutation-log ref
    constitution: tuple[str, ...]  # which C00x constrain it
```

`MECHANISMS: tuple[Mechanism, ...]`.

**The module must not import `stockfish_exit`** at module scope in any way that
changes that file, and must not modify it. `frozen_policy.engine_sha256()` is a
content hash of `stockfish_exit.py`; Phase A leaves it untouched.

### Status meanings

- **ACTIVE** — implemented and reachable on a live path today.
- **DARK** — recognized, deliberately not built, with a **named prerequisite**.
  This is the chess-engine property: the move is enumerable and the reason it
  was discarded is stated.
- **FALSIFIED** — was tested here and failed. `evidence` must resolve.
- **OUT_OF_SCOPE** — belongs to another lane (carry) or is structurally refused
  by the constitution or the two-engine boundary.

---

## THE MOVE LIST

Seeded from what exists — the seven `stop_candidates()` layers, the `INTENT`
policies, the `wide_space()` levers, and the three `[SKETCH]` blocked layers at
`stockfish_exit.py:632-645`. Anything marked DARK below must carry its
prerequisite verbatim from the source that blocked it.

### STOP_PLACEMENT

| name | status | requires | note |
|---|---|---|---|
| `catastrophic` | ACTIVE | `catastrophic_sl` | the original plan stop; never removed, never loosened |
| `breakeven` | ACTIVE | `entry`, `tp1_done`, `be_arm_frac` | rule #1: the day cannot go red |
| `profit_lock` | ACTIVE | `tp1`, `tp2_done` | goal banked, stop rides to TP1 |
| `trail` | ACTIVE | `hwm`, `trail_dist`, `trail_mult` | rides the favourable extreme |
| `volatility` | DARK | `atr`, `hwm` | **needs ATR on TradeState and a defensible multiple; no caller supplies it.** See Phase B of the plan. |
| `thesis` | DARK | `thesis_sl` | needs an invalidation level; `regime.py` raises at import (spec 001). Must be derived **mechanically on the Stockfish side** — `ContextDirective` structurally refuses any `stop_price`. |
| `time_decay` | DARK | `now_et`, `flatten_at_et` | today time pressure is a **cliff**, not a schedule. Prerequisites: the 3-of-24 entry problem addressed, plus a counterfactual showing schedule beats cliff on days that reached TP1. |
| `structural` | DARK | — | placing the stop beyond the most recent swing point. No swing detector exists; `regime.py` is the natural home. |

### SCALING

| name | status | note |
|---|---|---|
| `ladder_partial` | ACTIVE | the TP1/TP2 reduction ladder; `goal_fraction` / `partial_frac` |
| `scale_in` | OUT_OF_SCOPE | **C001 forbids any qty increase.** Structurally unavailable, not merely unused. |
| `structural_scale` | DARK | scaling at prior structure rather than an R multiple; same blocker as `structural` |

### TIME

| name | status | note |
|---|---|---|
| `session_flatten` | ACTIVE | `flatten_at_et` cliff. Ruling 1: this lives in `decide_exit`, never in the runner. |
| `event_flatten` | ACTIVE | the `EVENT` intent; `REQUIRES["EVENT"] = ("flatten_at_et",)` |
| `max_hold` | OUT_OF_SCOPE | a carry-lane concept. **I53: `CarryState` and `TradeState` share no terms; neither is a superset.** |

### INVALIDATION

| name | status | note |
|---|---|---|
| `urgency_exit` | ACTIVE | AlphaZero's `urgent == 'exit'`; outranks everything (C007) |
| `urgency_tighten` | ACTIVE | halves the trail. **Value claim FALSIFIED — see MECH-003.** The channel exists and is wired; what was killed is the claim that it adds R. |
| `thesis_break` | DARK | same blocker as the `thesis` stop |
| `correlation_break` | OUT_OF_SCOPE | no supplier; would need a cross-asset feed the engine does not have |

### TARGET

| name | status | note |
|---|---|---|
| `fixed_r` | ACTIVE | TP1/TP2 as R multiples |
| `atr_target` | DARK | same ATR blocker as the `volatility` stop |
| `measured_move` | DARK | needs structure detection |

---

## THE FALSIFIED REGISTER

**These must not be re-proposed under a new name.** Re-opening one requires new
evidence, not new vocabulary.

| id | claim | outcome |
|---|---|---|
| `MECH-001` | a wider trailing stop captures more of the winning tail, so trailing beats static wherever trends persist intraday | `029_MUTATION_LOG.md:21-35` — **"Trailing genuinely HURTS"**; the `trail_mult` config it wrote has been removed. The ceiling's oracle used no trailing on 22 of 24 picks. |
| `MECH-002` | protection earlier than TP1 plus a hard midday deadline beats riding the full session | **killed** |
| `MECH-003` | AlphaZero adds value by timing interrupts — a well-timed tighten or exit at shock onset protects R | **killed.** Perfect-hindsight tighten gave ~0 uplift in every cell, perfect exit ~0 in 11 of 12. Channel retired in code (`alpha_operator.EMISSION_MODE = 'log-only'`). |
| `MECH-004` | the right exit configuration is knowable from entry-time price features, so a per-day config choice beats one fixed policy | **killed.** Spec 025 returned `NO_SUPERSEDE`; `oracle_audit` returned `NOTHING_QUOTABLE` twice. |

---

## BOUNDARIES ANY MECHANISM MUST RESPECT

1. **Ruling 1** (`000_RULINGS_AND_ORDER.md:8-29`) — every rule that can close,
   reduce, or move a stop lives in `decide_exit`. The runner and the harness are
   I/O and decide nothing. A mechanism implementable in the runner is a
   mechanism specified wrong.
2. **The constitution C001–C009** (`specs/011`). Notably C001 (no qty increase),
   C003 (no stop loosening), C007 (emergency precedence).
3. **Monotone tightening.** `effective_stop()` takes the max over active
   candidates and that is where the never-loosen invariant lives. **Every new
   layer must be constant or monotonically tightening**, or the max stops being
   safe.
4. **Stage legality.** `ALLOWED_ACTIONS` per `Stage`; a mechanism must name the
   stages it can act in. `RUNNER` cannot take a new partial; `CLOSING` adjusts
   nothing.
5. **The two-engine boundary** (Ruling 1 / `specs/000`). AlphaZero communicates
   meaning; Stockfish controls mechanics. A mechanism requiring AlphaZero to
   supply a *price level* is mis-specified — `ContextDirective` structurally
   refuses `quantity`, `stop_price`, `order_type`, `filled_qty`.
6. **I53** — carry and intraday vocabularies stay disjoint.

---

## INVARIANTS

- **I58** — every `ACTIVE` stop-family mechanism in the registry appears by name
  in `stop_candidates()`, and every name in `stop_candidates()` appears in the
  registry. Neither direction may drift.
- **I59** — every non-`ACTIVE` mechanism has a non-empty `reason`.
- **I60** — every `FALSIFIED` mechanism's `evidence` resolves to a real
  `MECHANISMS.json` id or an existing file on disk.
- **I61** — no `definition` contains a standalone numeric literal (a digit not
  preceded by a letter). The parameter firewall, enforced.
- **I62** — every name in `requires` is a real key of
  `TradeState.__dataclass_fields__`.

Per `CLAUDE.md`, each of I58–I62 is verified **only** when a deliberate
violation makes the suite fail. Fault-inject all five.

---

## DEFINITION OF DONE

1. `daytrade/exit_taxonomy.py` exists with the registry populated from the
   sources named above — **not from imagination**.
2. `daytrade/test_exit_taxonomy.py` covers I58–I62, each fault-injected.
3. `frozen_policy.verify() == 0` still — Phase A does not touch the engine.
4. `scripts/wiring_audit.py` reports `0 new/unexplained`.
5. Full suite at or above its baseline.

## NOTE FOR WHOEVER READS `specs/README.md`

Its status table stops around 029 and is **stale by roughly fourteen specs**
(030–044 are largely absent). Do not treat it as the index of record.

## HONEST FRAMING

Three measured results bound what this work can be worth, and none is overturned
here: the entry is the binding constraint (on 21 of 24 days **no** exit policy,
perfect with hindsight across 396 configurations, could make money); trailing
hurts; per-day exit-config selection is dead.

The purpose of this taxonomy is that the engine be **correct, complete, and
honest about what it cannot do** before a better entry exists. It is not
expected to raise returns on the current entry population, and any later reading
of it that claims otherwise is overselling it.
