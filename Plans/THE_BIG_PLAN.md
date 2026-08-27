# THE BIG PLAN — what this repo is, what it actually does, and what stands between them

Written 2026-08-26 from a four-agent full-repo map (live-path trace, maturity
audit, failure-mode hunt, two-engine contract). Every claim below was verified
directly, not taken from a docstring.

---

## 1. The goal, restated

Pass a funded evaluation. Then trade that funded account. One trader, built from
two engines:

- **ALPHAZERO** — meaning. Reads news, calendar, context. Claude at runtime, not
  a trained model. Supplies the semantic read.
- **STOCKFISH** — mechanics. Evaluates current state, takes exits, enforces the
  constitution. Never infers meaning.

The files are not the intelligence. They are the **computation and simulation
substrate** — the thing that lets a strategy be tested against history instead of
against real money.

## 2. What is genuinely built, and it is not nothing

This is the part that survives contact with an audit.

**The two-engine boundary is real and enforced.** `ContextDirective.from_dict`
(`daytrade/context_directive.py:102-115`) structurally refuses any payload
carrying `quantity`, `stop_price`, `order_type`, or `filled_qty` — AlphaZero
*cannot express an order*. `expires_at` is checked at consumption inside
`evaluate()` (`:207-211`), not merely stamped at emission. All nine constitution
rules C001–C009 exist, funnel through the single `apply_action`
(`daytrade/stockfish_exit.py:564-596`), and carry mutation kills.

**The measurement discipline is the best thing in the repo.** Pre-registration
with hash-and-commit gates, `SEALS.json` content hashes, mutation logs, zero-edge
controls beside every number, date-based holdouts that survive population
changes, `measure.exclude_self`. It caught two of my own over-claims in a single
session — a +94R "prize" that was selection arithmetic, and a G5 recommendation
that was backwards.

**The carry edge has a sealed proof set.** 411 trades, 2015–2024, G1 reproduces
exactly, G2 attributed with zero residual, G4 zero permission violations.

**The data substrate now spans 2024–2026** with the first real look-ahead
assertion in the repo's history (`scripts/build_extended_features.py`).

## 3. What actually runs tomorrow morning

**Two LaunchAgents.** Of 57 loaded `com.alta.*`/`com.sovereign.*` jobs on this
machine, **56 point at a different checkout** (`~/quant`, branch `sovereign-v2`,
which contains no `daytrade/` directory at all). This repo has exactly two:

| job | schedule | what it does |
|---|---|---|
| `com.alta.alpha-operator` | every 5 min, 08:00–16:30 ET | refresh bars → write a plan → archive news → seal a **shadow** judgment |
| `com.alta.dashboard-publish` | 16:40 ET | compute exit quality → build `docs/data.json` → **git push** |

The daily terminal action is a `git push` of a dashboard.

**No order is placed anywhere on any automated path.** `daytrade/runner.py` and
`daytrade/broker.py` — the only code in this repo that can reach a broker — have
no plist, no cron, no shell caller. The operator's directive channel is dead
twice over: `operator_tick.sh:100` hardcodes `--shadow`, and
`alpha_operator.py:86` hardcodes `EMISSION_MODE = "log-only"` regardless of the
flag.

## 4. The four disconnects

Every one is the same shape: the component exists, is tested, and is **not wired
to the thing it was built to govern.**

### D1 — There is no execution path
`runner.py`/`broker.py` dead. `execution/funderpro_executor.py:282` imports
`sovereign.execution.ctrader_bridge`, which **does not exist** on disk
(`sovereign/execution/` holds one file) and is **unguarded** — it sits after the
credentials check, so it raises `ModuleNotFoundError` at the moment of first real
order rather than at startup.

### D2 — There is no risk enforcement
`CLAUDE.md` non-negotiable #4 says Kelly sizing is bounded and *"never bypass
either layer."* `rg kelly_engine|risk_engine` outside `sovereign/` returns
**nothing** — you cannot bypass a layer that was never on the path.
`portfolio_guard.py` computes the daily-loss lock and emergency flatten, is
labelled *"Gate 7 enforces last,"* and no Gate 7 exists — it never reaches
`broker.send()`. `survival.py` and `streak.py` are `raise NotImplementedError`
stubs whose own docstrings say "no blockers." `streak.py` is the two-red-days
cooloff; `friction_ladder.py` says the entire 93–98% ladder pass probability
depends on it, and `TRAINING_DAY_1.md:138` states plainly: *"cooloff is not
enforced by code — you enforce it."*

### D3 — AlphaZero structurally cannot learn
`forecast.promotion_decision()` — zero production callers.
`context_directive.AuthorityRegistry` — zero production callers.
`runner.py:300` hardcodes `granted_level=1` every cycle. `regret.grade()` is
never wired into `Resolution.policy_regret_r`, so it is permanently `None` and
the tail-regret gate can never evaluate — every promotion attempt fails
`REGRET_DATA_MISSING` for reasons unrelated to performance. `ledger.grade()` is
`print()`-only. The loop is **open**: graded, logged, consumed by nothing.

### D4 — The daytrade lane has no validated edge
Exit-policy selection failed its pre-registered verifier twice, and the second
failure is structural: `null_leak = E[max of K columns] − best_fixed` converges
upward (1.4496 → 1.5813 as n goes 39 → 402), so `NULL_GATE = 0.15` is
unreachable for a 6-family max at σ≈1.13. More data cannot fix it. The entry-side
question (`specs/037`) is pre-registered, powered, and pending.

## 5. The strategic call

**The funded account gets passed by the carry edge, not the daytrade cockpit.**

They are almost entirely separate code. Carry has a sealed 411-trade proof set
and a five-gate buy protocol. The cockpit is where the AlphaZero/Stockfish
architecture lives and it is research-grade — genuinely valuable, not yet an
edge. Treating them as one program is why the eval keeps not happening.

Two numbers gate the purchase, and both are now measured:

- **Sizing.** `max_safe_risk` on the realized sealed curve is **0.328%** for
  `cti_1step` against a planned 1.00%. Every risk in the pre-registered sweep
  (1.00%–3.00%) sits above the survival ceiling.
- **G5.** Sealed per-trade σ is **1.6922**, so the ±0.25R band needs **n≈283**
  (6.9 years at 41.4 trades/yr). n=80 resolves only ±0.471R — nearly double the
  band it polices. G5 is **under**-powered, and `specs/021:119`'s defence of the
  band ("σ/√n ≈ 0.13R") reconciles with no σ in the series. Drafted for
  ratification in `specs/036`, `[UNRATIFIED]`.

## 6. The plan, in dependency order

### Phase 0 — stop the bleeding (landmines, no live path yet)
- Spend ledger durability: `record_spend()` has no fsync while every other audit
  log in the repo does; the `messages.parse()` fallback is self-documented as
  "billed but NOT ledgered." *(in flight)*
- `_compute_atr`'s `0.001` fallback must raise, not return. Measured today it
  oversizes EURUSD 4.9x, GBPUSD 6.0x, AUDUSD 4.2x, **USDJPY 817x** — 0.3%
  intended risk becomes ~245% of equity on one vendor timeout. Not currently on a
  live path; a landmine for whoever wires it. *(in flight)*
- FRED failures label themselves `source: 'fred'` — make the fallback say
  `fallback_static` and call `flag_degraded`. *(in flight)*
- Preflight-check `ctrader_bridge` at startup instead of on the order path.

### Phase 1 — make risk real before making execution real
Order of operations matters: an execution path without enforced risk is worse
than no execution path.
- Build `survival.py` (spec 002, no blockers) — the pre-trade sentence.
- Build `streak.py` (spec 004, no blockers) — the cooloff, mechanically enforced,
  so it is not Colin at 2am.
- Wire `kelly_engine` + `layers/prop.py` onto the sizing path, or delete
  non-negotiable #4 from `CLAUDE.md`. One of the two must be true.
- Give `portfolio_guard` a real Gate 7 that can block an order.

### Phase 2 — a paper execution path, end to end
- Revive `runner.py`/`broker.py` on **paper only**, scheduled, with Phase 1's
  guards on the path and `decision_logger.update_outcome()` on every close.
- This is what fills `paper_carry_trades.jsonl` — currently 0 bytes — and it is
  the only thing that can ever close G5.

### Phase 3 — close AlphaZero's loop
- Wire `regret.grade()` → `Resolution.policy_regret_r` so the gate can evaluate.
- Chain `promotion_decision()` → `AuthorityRegistry.grant()` →
  `runner.ReceiverContext(granted_level=...)`.
- Only then does "AlphaZero trains itself" describe the running system rather
  than the design.

### Phase 4 — resolve the edge questions
- Run `specs/037` (pre-registered, powered). *(in flight)*
- Ratify or reject `specs/036`'s G5 options.
- Re-derive eval sizing at or below 0.328% and ratify through spec 021.

## 6b. The wiring detector (added 2026-08-26)

Fixing disconnects one at a time is whack-a-mole. `scripts/wiring_audit.py`
makes the CLASS detectable — static AST analysis, five shapes, allowlist where
every entry needs a written reason. A new disconnect fails the suite; an empty
reason fails a different test, so the allowlist cannot be used to silence a
finding.

```
ORPHANS              73   daytrade:32  scripts:17  sovereign:10  execution:7  backtester:7
IMPORTED_NOT_CALLED  25   sovereign:11 daytrade:10  execution:2  scripts:2
NO_SUPPLIER           5   daytrade:4   sovereign:1
BROKEN_IMPORT         2   sovereign:1  execution:1
DEAD_ARTIFACT        24   data:24
TOTAL               129
```

The 73 orphans and 24 dead artifacts are mostly research tools and report JSONs
that SHOULD have no importer — noise you expect in a research repo. **The 5
NO_SUPPLIER are the ones that look like protection and are not**, and once
examined they are not five equivalent bugs:

| field | what it actually is |
|---|---|
| `ConstitutionViolation.action_kind` | genuinely fixable now |
| `RiskState.mc_breach_prob` | gate silently inert — `is not None` guard, nothing computes it |
| `TradeState.thesis_sl` | **not an oversight** — documented inactive layer, blocked on spec 001 |
| `ExecEvent.intent` | blocked on D1 — zero production constructors of ExecEvent at all |
| `ExecEvent.fill_id` | blocked on D1 — same root cause |

**Correction worth recording:** `thesis_sl` was first reported here as a missed
fifth AlphaZero->Stockfish disconnect. It is not. `stockfish_exit.py` documents
it at the point of use — *"INACTIVE: needs an invalidation level from the
classifier; daytrade/regime.py is a stub"* — under a comment reading
`--- inactive layers, awaiting their inputs`. And AlphaZero could not supply it
even if regime.py existed: `ContextDirective` structurally refuses any
`stop_price`. That is the boundary working, not failing. When spec 001 lands the
level must be derived mechanically on the Stockfish side, never handed across
the envelope.

The detector is a floor, not a ceiling: static analysis cannot see runtime-only
wiring. `layers/kelly.py` looks clean statically and explodes at runtime through
its transitive import of the broken `kelly_engine`.

## 7. What will fail live, ranked

1. **Nothing, because nothing goes live** — the honest #1. No order path exists.
2. `ctrader_bridge` ModuleNotFoundError on first real order (D1).
3. Unenforced sizing — Kelly/prop layers off the path (D2).
4. Unenforced cooloff and daily-loss lock — the two rules the ladder math depends
   on are advisory (D2).
5. `_compute_atr` 817x oversizing, if `sovereign/forex` is ever wired (Phase 0).
6. Sizing at 1.00% when the realized curve breaches at 0.328% (§5).

## 8. The one-sentence version

This is a research instrument of unusual quality that has never placed an order;
the distance to a funded account is not intelligence or edge discovery, it is
four wires that were built, tested, and never connected.
