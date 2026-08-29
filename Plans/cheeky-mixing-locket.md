# Phase 1 — the deterministic execution core, to a decisive gate

## Context

The architecture splits a rule-bound execution core (Stockfish) from an adaptive
evaluation layer (AlphaZero). A learning layer cannot be built on an execution
engine whose behaviour is unmeasured, so Phase 1 finishes the core and puts a
statistical gate in front of it.

The engine is **software-verified and statistically unexposed** — two claims that
have been sharing one word. `daytrade/stockfish_exit.py` has mutation logs, a
constitution funnel, and a clean checkpoint (SF-FROZEN-002, `verify` → 0). Its
lifetime exercised record is **36 events across 2 NVDA sessions**. Phase 1
converts the second number.

This morning's rollback to 2026-08-24 discarded 89 commits, preserved at tag
`pre-rollback-2026-08-29`. **161 files exist in the tag but not in the tree**, so
for two of the four components the first question is recover-vs-rebuild.

## The agreement, recorded before the result exists

**Gate 4 returning "incumbent holds" is Phase 1 complete, not Phase 1 failed.**

With one condition, which is a guard rather than an escape hatch: it counts only
if the gate *could* have said otherwise. Last night's carry SPRT returned
`ACCEPT_H0` while 47 of 97 units had ΔR ≡ 0 — thin cells meant the table never
deviated, and those silent units supplied 96% of the H0 bound. A hollow null and
a real null print the same word. **Gate 4 is complete when the SPRT reaches a
bound AND the deviation rate shows the candidate acted.** Near-zero deviation
means "no test was run", which sends us back to coverage, not to a conclusion.

## Population — ratified

**Build on SPY, apply to the basket.** The occupancy audit already warned the
basket is too thin: at episode level nothing reached OK, `minimal` was MARGINAL
at 25.4%, and the threshold is ~117 trading days against the basket's 39.

| lane | source | sessions | entries | role |
|---|---|---|---|---|
| build | `data/daytrade/bars_premarket/SPY_5m.parquet` | 2,618 pre-`TUNE_END` | 2,115 | fit, coverage, SPRT |
| apply | `data/daytrade/bars/` (16 symbols) | 39 days | 336 | applied, reported, **never re-fitted** |

Nothing after `TUNE_END` is read; `sealed_sessions()` is never called. Known risk
on the record: SPY and NVDA select *opposite* best-fixed policies
(`artifacts/CEILING_10Y_RECORD.md`), so transfer is measured, not assumed.

## Current state — what exploration established

**Already on disk, written before plan mode and unverified:** `state_space.py`
(rename applied), `transitions.py` (199 lines), `test_transitions.py` (163). The
builder stopped correctly before running anything. Two steps outstanding: the
pytest run, and the report run. It also correctly refused to loosen a check —
see the `TUNE_END` item below.

**Recoverable from the tag, with tests, rather than rewritten:**

| what | tag path | lines | tests |
|---|---|---|---|
| the enumerable move list | `daytrade/exit_taxonomy.py` | 381 | 224 |
| the SPRT | `sovereign/forex/sprt.py` | 144 | 127 |

`exit_taxonomy.py` holds 21 mechanisms across 5 families (10 ACTIVE, 8 DARK,
3 OUT_OF_SCOPE), each non-ACTIVE one carrying a required reason, plus 3
`FalsifiedClaim` records. **It also carries the recorded ladder-vs-no-management
ruling**, which is otherwise only in the tag.

## Component 1 — state representation

- **Convention fix** — already applied by the builder: `State.session` collided
  with `bars.Session` and carried FX levels; now `time_block` with
  `ceiling.time_block` levels.
- **Dead dimensions**: `carry_r` and `weekend_exposure` are constant intraday, so
  the nominal 5,760-cell grid is an effective 480. Report `reachable/effective`.
- **`audit()`'s counting unit stays as-is** — it counts snapshots where the gate
  should count episodes, which is why the full grid reads 4.4% thin by snapshot
  and 57.5% by trade. That one-line fix is Colin's; an agent silently making it
  would erase the finding. Comment the line only.
- **`state_space.py` has no tests anywhere** — not in tree, not in tag. The whole
  tablebase keys off it. Needs a test file.

**DoD:** sweep run on real SPY sessions; granularity at MARGINAL or better;
**the choice written down before anything is fit on it.** On 2,115 episodes a
finer granularity than `minimal` may clear — that is why it is re-run.

## Component 2 — move generation and pruning

Mechanical and stage legality are real, funnelled and tested: `ALLOWED_ACTIONS`
per `Stage` makes illegal-for-stage actions unrepresentable (`_gate`,
`stockfish_exit.py:552`), and C001–C009 are enforced through a single funnel
(`apply_action` → `enforce`, `:586`). Three gaps, all confirmed by audit:

1. **Economic legality is ABSENT.** `grep` for `COST_PER_SHARE|spread|slippage|
   toll|round_trip` in `stockfish_exit.py` and `stockfish_constitution.py`
   returns **zero hits**. Cost is accounted in every scoring module and never
   used to *refuse*. Nothing declines a `TAKE_PARTIAL` whose capture is below its
   own round-trip toll. This is the mechanical form of "assume the counterparty
   is optimal" and it is the single largest gap.
2. **Regime-conditional legality is ABSENT.** No move is ever illegal because of
   market state. `decide_exit` imports nothing from `regime_vector.py` or
   `thesis.py`.
3. **Refusals crash rather than log.** `runner.py:745` calls `enforce()` with no
   try/except inside the run loop, so a violation raises uncaught. Fail-loud is
   correct, but there is no "refused" event in `trade_events.py` — auditability
   today is "crash with a message", not "logged refusal with a reason".

Plus a coverage hole: **`specs/011_MUTATION_LOG.md` is ABSENT.** The constitution
card has no fault-injection log; only C004/C005/C007 *threading* is covered by
`WIRING_MUTATION_LOG`. C001/C002/C003/C006/C008/C009 have no driver proving a
deliberate violation fails.

**All seven stop layers adjudicated — dark-because-undecided becomes
dark-because-decided:**

| layer | today | disposition |
|---|---|---|
| catastrophic · breakeven · profit_lock | ACTIVE | unchanged |
| trail | ACTIVE | unchanged; MECH-001 doubts it and `tmNone` won on SPY/QQQ — recorded, not acted on |
| **volatility** | hard-coded off | build as **placement**: `entry − k·ATR`, set once at entry. NOT `hwm − k·ATR`, which is a trailing stop and the instrument the evidence is most negative about. `k` required, no default |
| **thesis** | dark — `regime.py` raises by design | unblock via **regime-as-magnitude** — a dispersion conditioner, not a direction classifier |
| **time_decay** | hard-coded off | ruled in or out **in writing**. Current claim: "time pressure is a cliff (`flatten_at_et`), not a schedule." That either stands or falls on the record |

**Note on pruning:** this is *legality* pruning — moves refused with a stated
reason. No adversarial search is built; nature deals these cards, so the search
analogue is expectimax, not alpha-beta.

## Component 3 — static evaluation (the tablebase)

`tablebase.py` (untracked, 207 lines) does backward induction over realized paths
with leave-one-episode-out and a `min_paths` floor returning `NO_VALUE`. Settled,
so it is not re-litigated:

- `ceiling.simulate(session, e, cfg, observer=None, urgency_schedule=None) -> float`
  returns a bare R and **cannot start mid-path**.
- `r_if_tightened` comes honestly from `urgency_schedule`, which sets `st.urgent`
  per bar; `stockfish_exit.py:218` halves the trail under `tighten`. A real
  re-simulation through the engine's own channel.
- **Reference config is the shipped policy** (`frozen_policy.POLICIES`, pinned in
  SF-FROZEN-002), per asset class — not a hand-picked config, because "best fixed"
  is a max over 396 and explicitly not a result. Consequence to report and not
  smooth over: `SINGLE_NAME` ships `trail_mult=None`, so Tighten is structurally
  inert and `r_if_tightened` is `None` there. A third of classes ship a
  two-action space.
- **Resolve the `TUNE_END` boundary**: `transitions.py` asserts `day < TUNE_END`
  while `splits.tune_sessions()` is inclusive (`<=`). Pick one and make both
  agree; do not loosen the assertion to dodge a legitimate raise.
- `tablebase.py`'s docstring claims "components 1, 2 and 4 are built" — false in
  this tree post-rollback. Correct it.

**DoD:** built; `coverage()` reported; `frac_obs_valued` above the component-1
threshold; and **one leak test that deliberately fails** — build the table, call
`evaluate()` with an episode that helped build the cell, assert `LeakError`
fires. An assertion that has never fired is not verified.

## Component 4 — the SPRT gate

Recover `sovereign/forex/sprt.py` + tests from the tag and re-home lane-neutral
(it is not carry-specific). Two defects from last night fixed before the run:

- **σ from the paired-difference distribution, not the level.** Last night σ was
  the SD of per-unit incumbent R, inflating δ to roughly twice the whole edge.
  δ is a declared plausible-improvement argument.
- **Deviation rate is a first-class output**, and a block with near-zero
  deviation contributes nothing to the LLR rather than voting H0 for free.

Opponent is **SF-FROZEN-002**. Component 2's volatility layer changes
`engine_sha256`, so it requires minting **SF_FROZEN_003** — I49 refuses
re-pinning — proving behaviour-inertness against the pre-change engine.

**DoD:** run once, end to end, a real candidate against SF-FROZEN-002, to a
decisive result: `ACCEPT_H1`, `ACCEPT_H0`, or an `INCONCLUSIVE` that reports why.

## Order

1. Verify what is already on disk — run `test_transitions.py`, resolve the
   `TUNE_END` boundary, add `state_space` tests.
2. Component 1 re-run on SPY; granularity recorded. *(blocks 3)*
3. Component 3 built; coverage reported.
4. Component 4 recovered and wired; **gate run with the shipped policy as its own
   candidate** — proves the harness is not hollow before any engine edit.
5. Component 2's adjudications; recover `exit_taxonomy.py`; economic-legality
   prune; volatility layer; mint SF_FROZEN_003.
6. Gate re-run with the volatility layer as candidate. **This is the real Gate 4.**
7. Apply to the 336-entry basket; report coverage and transfer; never re-fit.

Steps 1–4 touch no engine code and need no checkpoint. Step 5 is the first byte
change to `stockfish_exit.py`, where the ceremony applies.

## Housekeeping — blocks nothing, but decide deliberately rather than by drift

- **The trade-evidence freeze is unenforceable**: `.githooks/` does not exist and
  `core.hooksPath` points at nothing. Both hooks and `check_trade_freeze.py` are
  tag-only.
- `com.alta.paper-carry-daily` is loaded but its target script is gone.
- A subagent flagged my mid-task correction as a possible injection before
  adopting it, having verified the cited files independently. That was right, and
  it is confirmed as mine.

## Verification

```bash
python3 -m pytest test_transitions.py -q                 # incl. the deliberate leak failure
python3 state_space.py artifacts/exit_snapshots_spy.parquet
python3 -m pytest daytrade/ scripts/ sovereign/ -q
python3 -m daytrade.frozen_policy verify                 # 0 until step 5, then SF_FROZEN_003
python3 scripts/ceiling_10y.py SPY                       # population unchanged: 2,115 entries
```

Every new invariant fault-injected before it is called verified. Coverage,
deviation rate and the SPRT decision are reported together — never the decision
alone.

## Out of scope

AlphaZero's re-spec (Phase 2); unsealing anything after `TUNE_END`; the entry
rule; live orders. `C5_gap:high` stays a lead needing its own pre-registration.
