# Close One Loop — the splash loop to a real R

## Context

This repo has ~35 spec cards, ~90 files, and **zero exercised loops**. Nearly every
component is implemented and unit-tested to a high standard. Almost none of it has
ever touched live data end to end.

Three read-only recon passes and one design pass established the current state:

- `alpha_operator.py` runs live under launchd and writes real evidence/forecast rows,
  but **every forecast grades as `unscoreable_reason: "no plan with entry/sl on file
  — R is undefined"`**. The learning loop has been eating nulls.
- `stockfish_exit.decide_exit` is wired into `runner.py` and covered by 87 genuine
  fault-injection tests, but **no `data/daytrade/session_*.jsonl` has ever existed** —
  despite `ARCHITECTURE.md:102-107` marking the runner DONE on 2026-08-03 and
  `data/shot_ledger.csv` row 1 recording +$300 that same morning.
- `data/daytrade/operator/launchd.log` shows the plan writer crashing on **98
  consecutive ticks**, invisible because `operator_tick.sh:57` omits its return code
  from the tick's exit status.
- The carry baseline cannot price carry on 3 of 4 pairs: AU CPI dead 597 days, UK CPI
  dead 538 days, JP CPI a **fabricated flat line of 3.2 across 1,978 rows**.
- The buy gate reads **NOT READY**, G5 stands at **0/80**, and the state file is 9 days
  stale.

**The intended outcome of this plan is a single number**: one real, numeric `r_realized`
written to a real session log from a real tape. Not a feature. Not a card. One honest
measurement, which the system currently cannot produce at all.

This is Musk's five-step applied deliberately. Step 1 (question the requirement) already
happened: the requirement was never "build the 36th card," it was "get one loop to
contact." Step 2 (delete) is the attic pass. Step 3 (simplify) is the four-file repair.
Steps 4 and 5 — accelerate and automate — are explicitly **not in this plan**.

### Governing constraint

`ARCHITECTURE.md:38-39` — *"data/daytrade/bias.json ... THIS FILE IS THE ONLY INTERFACE
to Stockfish. Freeze it."* Per project `CLAUDE.md`, ARCHITECTURE.md supersedes on
conflict. The `directives.json` channel (specs 015-018, 023) is a second interface and
is therefore **off the critical path by the repo's own definitive spec**. It was also
retired on measured evidence in `specs/026_COMPETENCE_REPORT.md:77-79`. It is not
touched by this plan.

### Gate before any code is written

Molly does not write code outside `~/molly`. Implementation requires Colin to say
**"Molly, engineering mode"**. Until then this plan is a plan.

---

## PHASE 1 — ON THE LOOP (four files; this is the whole deliverable)

Nothing in Phase 2 is required to produce a real R. If time runs out, Phase 1 alone is
a complete, valuable session.

### Step 1 — Make the loop importable under the interpreter it actually runs on

**Root cause.** `daytrade/stockfish_exit.py` lacks `from __future__ import annotations`.
Every sibling module on the loop has it (`ceiling.py:31`, `broker.py:29`,
`forecast.py:18`). `~/Library/LaunchAgents/com.alta.alpha-operator.plist` has no
`EnvironmentVariables`/`PATH` key, so launchd resolves `python3` → `/usr/bin/python3`
(**3.9.6**), where the PEP 604 annotation at `stockfish_exit.py:332`
(`plan: dict | None`) raises `TypeError` at import. This cascades to `ceiling`,
`write_baseline_plan`, **and `runner.py`**.

**Changes:**

1. Add `from __future__ import annotations` to `daytrade/stockfish_exit.py`.
   Also add for convention parity: `runner.py`, `scorecard.py`, `streak.py`,
   `survival.py`.
   *Rejected alternative:* changing `:332` to `Optional[dict]` treats one line and
   leaves the class of bug live.
2. Add `EnvironmentVariables` → `PATH` = `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`
   to `com.alta.alpha-operator.plist`. **Copy the block verbatim from
   `~/Library/LaunchAgents/com.alta.forex_exit_manager.plist`**, which already does this
   correctly — the alpha-operator plist is the outlier.
3. `daytrade/operator_tick.sh` — make the swallowed failure observable. Keep the plan
   writer non-fatal (a plan failure must not stall the resolver) but capture `PLAN_RC`,
   fold it into the exit status at `:57`, and add a consecutive-failure counter that
   prints a `!! PERSISTENT` banner and exits non-zero after 3.
   *"Non-fatal" must mean "does not abort this tick", never "is invisible for four days."*
4. Add a preflight assertion: `/usr/bin/python3 -c "import stockfish_exit, runner, ceiling"`
   must exit 0. This is the check that would have caught it on day one.

**Verification:**
- `/usr/bin/python3 -c "import runner"` exits 0.
- Next RTH tick produces `data/daytrade/plan.json` with `_writer == "mechanical-or-break-v1"`
  and `_session == <today>` (`write_baseline_plan.py:35,67-68`).
- The next `records.jsonl` row carries **no** `unscoreable_reason`.
- `grep -c Traceback data/daytrade/operator/launchd.log` stops increasing.

### Step 2 — Stop forecasts from silently accumulating as unscoreable

**Diagnosis (not a bars bug).** `NVDA_5m.parquet` is RTH-only by design. The 4 stuck
forecasts were issued outside market hours via **manual runs that bypass the session
guard** at `operator_tick.sh:12-15`. Three have zero bars in their window; the fourth
was issued at 15:53 ET, so its +60m horizon ends past the close.
`alpha_operator.py:1039` requires `len(window) >= 2`.

**Changes:**

1. **Gate at emission, not at resolution.** In `alpha_operator.py` `run_once`, before
   `_append_jsonl(FC_LOG, ...)` (~`:878`), refuse to emit a forecast whose
   `[as_of, as_of + horizon_min]` window does not lie inside a tradable RTH session.
   Reuse the existing `bars.load_sessions` (already used by `write_baseline_plan.py:57`).
   - zero session minutes in window → seal the record with `forecast: null` + reason.
     A judgment outside RTH is still worth sealing; a *scoreable claim* is not.
   - window crosses the close → clamp `horizon_min` to session end and stamp
     `horizon_clamped_from: 60` so the grader can see it was not a free 60m claim.
2. Add a terminal `kind: "unresolvable"` for the 4 existing stuck rows, plus
   `n_unresolvable` on `ScoreReport`, excluded from **both** `pairs` and `n_open`.
   **Route this outside `ForecastLedger.resolve()` entirely** — never resolve with an
   inferred outcome, which would put a fabricated `outcome_scenario` into the Brier sum.

**Verification:**
- Resolver prints `4 unresolvable (window has no tradable session)` instead of `4 still open`.
- **Regression test: `grade()` returns bit-identical `brier` and `calibration_error` on
  the existing corpus before and after.** `forecast.py:177-222` builds every denominator
  from `len(pairs)`, so stuck rows never touched the math — this change must prove it.

### Step 3 — First real session log and first real R ★

**The trap:** `ledger_mode_for(live=True, ...)` at `runner.py:446-457` returns `None`
by design. A live run with `--ledger-mode auto` writes a session log but **no R**. The
mode must be passed explicitly.

**Run A — geometry proof, offline, zero risk:**
```
python3 runner.py --plan data/daytrade/plan.json \
    --replay data/daytrade/replay_expected/<file>.json --ledger-mode sim
```

**Run B — first live tape, no broker, real R:**
```
python3 runner.py --plan data/daytrade/plan.json \
    --broker off --ledger-mode paper --interval 15
```

`--broker off` (`runner.py:851`) means `intents_from_actions` is never reached. The
broker sits downstream of the decision (`runner.py:750-753`), so **the full session log
and the full R are produced without touching a broker at all.** There is no reason for
first contact to involve one.

**Label the result honestly.** Both fills route through `_sim_fill` in every mode:
entry = the *plan's* price (`runner.py:608`), exit = the *live cycle* price
(`runner.py:796`). The first R is therefore a **real-tape exit on plan geometry**, not
a fill-to-fill R. The `basis` string already says `sim:plan-entry;costs=none-declared`.
Whatever artifact declares victory must say the same.

**Live-money vectors — all confirmed closed for this run:**

| vector | status |
|---|---|
| Alpaca host pin (`broker.py:44,110-116`) | refuses any host ≠ `paper-api.alpaca.markets` |
| entry orders (`broker.py:264`) | broker can only reduce/flatten, never enter |
| `--yes` (`runner.py:825` → `broker.py:351`) | **bypasses the arming prompt — never pass it on a first run** |
| `--ledger-mode live` (`runner.py:834`) | a `live` row from a paper session is a data-integrity hazard; consider refusing unless `broker.mode == "armed"` |
| `OANDA_LIVE` in `.env` | not on this loop, untouched |

**Verification — the loop is closed when all four exist:**
1. `data/daytrade/plan.json` with `_writer: "mechanical-or-break-v1"`.
2. `data/daytrade/session_<ET-date>.jsonl`, ≥1 line, line 0 carrying `rec["plan"]`.
3. Final line's `actions` contains `{"kind": "EXIT_ALL"}`; stdout printed
   `# ledger[paper] closed trade_id=NVDA-<date> <outcome> R=<±n.nnn>`.
4. The `paper` execution-ledger row's `r_realized` is a **float, not null**.

---

## PHASE 2 — OFF THE LOOP (cleanup; only after Phase 1 closes)

### Step 4 — Kelly's silent infinity

`risk_engine._ceiling()` (`sovereign/risk/risk_engine.py:38-43`) catches `ImportError`
and returns `math.inf`. The Kelly layer file **exists and is correct**
(`layers/kelly.py`); it fails because `kelly_engine.py:26-29` imports phantom
`layer2.risk_engine`, `layer2.dynamic_rr_engine`, `config.loader` at module level.
(`contracts.types` **does** exist — only `layer2.*` and `config.*` are phantom.)

This makes `CLAUDE.md` non-negotiable #4 false in code: the layer it names as
load-bearing silently returns infinity.

- **4a** — move the three phantom imports inside `SovereignRiskEngine`'s methods so the
  pure functions `fractional_kelly`/`hoeffding_win_rate` become importable.
- **4b** — split the identity case in `_ceiling` (and `_modulator` at `:31-37`, same bug
  with `1.0`): `find_spec(...) is None` → `inf` ("not built yet"); spec found but
  `import_module` raises → **re-raise**, honoring the invariant stated at `:8`.
- **Land 4a before 4b** so the loud state never exists. `risk_engine.decide()` has zero
  callers repo-wide, so nothing breaks either way.
- **Do not attic `risk_engine.py` or `layers/kelly.py`** — doctrinally load-bearing.
- Test: finite ceiling on synthetic stats; present-but-raising module propagates;
  genuinely absent module returns `inf`.

### Step 5 — The attic pass

Move to `_attic/` with a one-line note each. All verified zero live callers.

| target | note |
|---|---|
| `sovereign/execution/forex_exit_manager.py` | No `oanda_bridge` anywhere in this repo and no `tests/test_forex_exit_manager.py`. **The live `com.alta.forex_exit_manager` job runs the `~/quant` copy — moving this file stops nothing.** |
| `sovereign/risk/kelly_engine.py::SovereignRiskEngine` (`:146-382`) | **Class only, not the file.** Sole consumer of the phantom imports; the two pure functions stay behind. |
| `colin.md` | 9 bytes, self-referential. |
| `NIGHTCAP.md`, `V3_RESEARCH.md` | Zero inbound references repo-wide. |
| `render.yaml` + `requirements-dashboard.txt` | **HOLD — verify first.** `startCommand` names a nonexistent `scripts/live_signals_server.py`, but `com.alta.render_keepalive` is **running (pid 959)**. Confirm the deployed service is dead before moving. |

**Required in the same commit:** amend `CLAUDE.md` non-negotiable #2, which names
`forex_exit_manager.py` by path and would otherwise point at nothing. Do not let the
amendment lag the move by even one commit.

### Step 6 — Nominal carry cut (requires ratification first)

**Write `specs/036_CARRY_NOMINAL_ONLY_REREGISTRATION.md [UNRATIFIED]` BEFORE touching a
line of forex code.** Model it exactly on `specs/031_CARRY_GRADING_REREGISTRATION.md:1-8`
— an agent never self-ratifies a gate change.

It must contain: the CPI death evidence; an explicit statement that this **departs from
the sealed v015 real-rate methodology**; a `MECHANISMS.json` entry with a
**pre-registered predicted effect and band** for nominal-vs-real, written before
measuring; and a new gate **G1'** pinned to the nominal engine.

**The critical finding: G1 cannot go red.** It replays a frozen sealed CSV
(`carry_buy_gate.py:43,97-100`; `SEALS.json` `unseal_condition: "never"`). After the cut
it will keep certifying the sealed series perfectly while certifying **nothing about the
engine placing the next trade**. A green gate that no longer covers live code is worse
than a red one. **Do not ship the cut until G1' exists.**

Implementation, once ratified:
- Keep reading `data/cache/macro/{CC}_rates.parquet`; simply **drop the CPI leg**. Use
  the fresh `data/carry/rates/*.json` (currently **zero readers**) only to validate the
  parquets aren't stale — they're currency-keyed while the macro layer is country-keyed,
  so wiring them properly is a separate adapter, not this change.
- `nominal_rate_diff = base_rate - quote_rate` at the 4 computation sites:
  `signal_engine.py:527`, `fair_value.py:116`, `macro_engine.py:146-153`,
  `data_fetcher.py:209` (plus `cycle_detector.py:66-74`).
- **Rename the field.** `real_rate_differential` holding a nominal number is exactly the
  silent lie this repo is built against. Rename everywhere, including
  `combat_vetoes.py:54-73` parameter names and its `VetoHit` strings at `:68,73`.
- **Re-register C-001's `deadband: 0.2` and C-005's `weak_rate: 0.5`.** These were fitted
  in real-rate units. Carrying them to nominal is an un-preregistered parameter
  transplant — the sharpest hidden cost of this decision.
- Never mutate or re-cut `backtest_trades_v015_2015_2024.csv`. Add alongside.

---

## Deliberately frozen — the scope boundary

| frozen | why |
|---|---|
| `daytrade/regime.py` | **Do not attic.** Its `NotImplementedError` is the design working — `stockfish_exit.py:236,250,314` cite it by path as the honest reason `thesis`/`volatility`/`SALVAGE` are dark. Spec 008's decision rule is not evaluable (divides by Z; Z≈0); picking a threshold now is the laundering that spec exists to prevent. |
| `thesis` + `volatility` stop layers | Downstream of `regime.py`; `volatility` additionally needs an ATR no caller supplies. |
| `SALVAGE` / `SCRATCH_FAST` | `NOT_AUTO_EMITTABLE` by design. |
| `EMISSION_MODE` / `directives.json` / specs 015-018, 023 | Retired on measured evidence (`specs/026:77-79`). Off the loop per `ARCHITECTURE.md:38-39`. Re-arming to prove the loop works would launder the repo's own discipline. |
| `com.alta.alphazero.plist` | **Deliberately still not installed.** `MANUAL_STEPS.md:68-72`: the v1 keyword table would flatten a live position on any headline containing "probe" or "halt". A 4-day-stale `urgency: "none"` is strictly safer than a live placeholder on the only channel reaching the exit engine. |
| Sealed CSV + `SEALS.json` + G1 constants | `unseal_condition: "never"`. G1' is additive. |
| `broker.py` armed mode | Stays unused and untested. A real gap, not on this loop. |
| G5 paper sprint, the whole carry lane | Frozen except Step 6. |

---

## Open decision, deferred on purpose

**Who takes the entry?** `ARCHITECTURE.md:12-14` assigns the discretionary entry read to
Colin and gives AlphaZero only the bias. The pool thesis moves the entry to AlphaZero —
detect the disruption, enter in the 2-3 second dead window, let Stockfish own the exit.
Those are two different systems.

This decision is **not required for Phase 1** and is deliberately deferred until a real R
exists. Deciding it before there is a single honest measurement would be choosing an
architecture with no evidence — the exact failure this plan is built to end.

---

## End-to-end verification

The session succeeded if and only if:

1. `/usr/bin/python3 -c "import stockfish_exit, runner, ceiling"` exits 0.
2. `data/daytrade/plan.json` exists, written by the launchd tick, not by hand.
3. `data/daytrade/session_<ET-date>.jsonl` exists with a final `EXIT_ALL`.
4. An execution-ledger row carries a **float** `r_realized`.
5. `grade()`'s Brier and calibration error are **bit-identical** to their pre-change values.
6. No new file in `data/proof/` or `SEALS.json`; no sealed artifact mutated.

Failing 1-4 means the loop is still open. Failing 5 means measurement was corrupted and
the change must be reverted regardless of what else passed.
