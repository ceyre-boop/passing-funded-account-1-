# Carry Buy Gate — spec 021 build plan

## Context

An external audit concluded we are not ready to buy a funded evaluation: the engineering
discipline is real, but the carry edge's live-relevant evidence is not reproducible from this
checkout, and the operational gate points at the wrong system. Two verification passes over the
repo confirmed (with line-level evidence):

- **Three contradictory sizing doctrines coexist**: COLIN_V1's "92.4% pass at 0.5%" is unsourced
  (no generating script; grep hits only the doc itself) and conditioned on Lucid, which
  force-closes daily and cannot host a 6-day-median-hold strategy at all (FIRM_FIT.md:5–9).
  COLIN_V2's 5% eval sizing assumed a 90-day trailing-DD clock the swing lane doesn't impose.
  FIRM_FIT's current 2%/static-DD/CTI recommendation is encoded nowhere in code.
- **Every executable firm model in the tree is trailing-DD futures** (Lucid/MFF/Tradeify) — the
  lane FIRM_FIT declares dead for carry. CTI 1-Step and Alpha Swing exist as one prose line.
- **Five campaign scripts disagree** on risk (0.25%–5%), horizon, and reset rules; three are
  broken outright by a hard-coded `/home/claude/...` BASE (`eval_lab.py:34`,
  `eval_lab_carry_fix.py:12`, `instant_pro_sim.py:24`).
- **The offline rig reproduces only ~300 of the 411 sealed trades.** Missing ^VIX3M/^TNX only
  scale position size (clipped [0.5,1.5] in `signal_engine.py:189–304`) and cannot suppress
  signals, so the gap is undiagnosed. `data/cache/cot/` is empty; `build_cb_decisions.py` is
  missing; `data/oos_trades_2025_2026.json` (60 OOS trades) has no committed writer.
- **The operational gate is ICT-lane legacy**: `deployment_checklist.py` is Lucid/ICT-shaped,
  reads 4 missing files, reports 0/5 green; `data/agent/prop_challenge_state.json` is a stale
  2026-07-27 ICT snapshot whose gate IDs no current code emits; `build_daily_verdict_page.py`
  reads that stale file and would render a misleading "4 of 6 green" page.

**Goal:** one reproducible, firm-exact evaluator ("buy gate") plus a paper log, so the purchase
decision rests on honest numbers. No strategy invention — that stays in ~/quant.

**User rulings (2026-08-10):** sizing doctrine is re-derived by the evaluator's sweep under the
real CTI/Alpha contracts (FIRM_FIT's 2% is the prior, not the answer). The n≥80 paper sprint
blocks the BUY verdict only; OOS numbers may circulate with a "paper sprint incomplete"
watermark.

## Build phases

### Phase 0 — Spec card `specs/021_CARRY_BUY_GATE.md` (blocks all else)

Add row to `specs/README.md` (next number is 021, status `[SPEC]`). Pre-register:

1. **Firm rule schema** + three contracts with citations to official rule pages:
   CTI 1-Step (8% target, 5% static DD, no daily limit, ≈$382); Alpha Swing (two-phase 10%→5%,
   10% static max DD, 5% daily); FTMO Swing as reference (10%→5%, 10% static, 5% daily,
   close-basis).
2. **Verdict rules — BUY requires ALL green:**
   - **G1 GOLDEN**: evaluator reproduces sealed 411-trade replay — n=411 exact, avg R +0.356
     ±0.005, WR 48.7% ±0.5pt, median hold 6 exact, weekend-crossing 72% ±1pt.
   - **G2 REPRO**: the 411→~300 offline gap attributed to named causes (or explicitly waived in
     writing via decision_logger).
   - **G3 EDGE**: OOS P(pass) CI lower bound > zero-edge control CI upper bound at the chosen
     risk (SANITY_AUDIT standing order).
   - **G4 FIT**: chosen contract mechanically permits every sealed trade (hold/weekend check run
     against the trade series, not asserted in prose).
   - **G5 PAPER**: n≥80 paper trades, live avg R within pre-registered band of backtest.
     Blocks BUY only; docs may quote numbers with an explicit watermark.
3. **Zero-edge control**: every P(pass) prints with its mean-centered control on the same output
   line — omission structurally impossible.
4. **CIs**: block bootstrap (block = 20 trading days, ≥2000 paths), 5–95% interval; point
   estimates banned from docs.
5. **Reproduce-before-OOS**: evaluator exits nonzero on any non-sealed series until G1 passes.
6. **Sizing**: re-derived by the sweep under real contracts; ratified before purchase.

### Phase 1 — Firm contract layer

- **Create `data/propfirm/firm_contracts.yaml`**: per firm — `phases:[{target_pct,
  min_trading_days, max_days|null}]`, `max_dd:{type: static|trailing, pct, basis, mark}`,
  `daily_dd:{...}|null`, `permissions:{weekend_hold, overnight_hold, news_hold}`,
  `costs:{fee_usd, refund_on_pass, swap_haircut_r_per_day}` (reuse the −0.004 R/day haircut from
  `eval_lab_carry_fix.py`), `account_size`.
- **Create `sovereign/propfirm/firm_contracts.py`**: `FirmContract` dataclass, `load_contract()`,
  and a `to_prop_cfg(phase)` adapter emitting the exact `cfg["prop"]` dict that
  `sovereign/risk/layers/prop.py::prop_ceiling` consumes — **reuse its static/trailing/daily
  floor math, don't reimplement**. Keep `prop_ceiling` byte-compatible; do NOT touch
  `risk_config.yaml` (switches to the chosen contract only at account-open, via `to_prop_cfg`).
- **Mutation tests** (`sovereign/propfirm/test_firm_contracts.py` + `specs/021_MUTATION_LOG.md`):
  static→trailing flip changes floor as predicted; dropping `daily_dd` (CTI) removes the daily
  floor; close↔intraday mark diverges a known scenario; Alpha phase-2 target is 5% not 10%.

### Phase 2 — Canonical campaign evaluator `scripts/carry_buy_gate.py` (the bulk)

Replaces the five inconsistent scripts. Skeleton: adopt `eval_lab_carry_fix.py`'s
daily-increment design (`inc` credited at exit, `worst` intraday clip for daily-floor checks;
concurrent pairs sum into the same day = the correlation/heat model) — with all paths via
`Path(__file__).resolve().parents[1]`, never a hard-coded BASE.

- `--series sealed|oos|<path>`; sealed loader uses R = pnl_pct/risk_pct.
- Walk contract phases sequentially; **no horizon timeout unless the contract has `max_days`**
  (kills the artificial 90/365-day resets); min-trading-days enforced; bust → rebuy with fee
  accounting; flag any day where summed open risk exceeds the daily budget.
- `--risk` sweep (default 1–3%), one policy per run.
- Outputs per (firm, risk): P(pass), P(blowup/attempt), median/p10/p90 days, E[evals burned],
  E[$ net of refund] — each with bootstrap CI **and the zero-edge control inline**. State to
  `data/agent/carry_buy_gate_state.json` (G1–G5 + numbers); human table to stdout.
- G1 golden gate runs first, every invocation; G4 fit check iterates sealed trades against the
  contract's permissions.
- **Retire, don't delete**: docstring banner "SUPERSEDED by scripts/carry_buy_gate.py (spec 021)"
  on `eval_lab.py`, `eval_lab_carry_fix.py`, `instant_pro_sim.py`, `colin_v2_campaign_sim.py`,
  and `oos_campaign_test.py`'s campaign section (its `get_trades` rig survives — Phase 3 uses it).
- **Mutation tests** (`scripts/test_carry_buy_gate.py`): sign-flipped R series fails golden;
  daily-floor off-by-one busts a constructed scenario; removing the zero-edge arm fails the
  output validator; trailing-on-CTI flips a known verdict; all-win series can't pass before
  min_trading_days; no-deadline contract never emits TIMEOUT.

### Phase 3 — Reproduction-gap diagnosis `scripts/diagnose_repro_gap.py` (parallel with 2)

Reuses the offline `yf.download` monkeypatch + rig from `oos_campaign_test.py` /
`oos_2025_2026_runner.py`:
1. Per-pair, per-year trade-count diff vs sealed CSV.
2. Cache coverage audit of `data/research/spot_cache/` + surface the runner's `_missing` set.
3. First-divergence report: earliest sealed trade the rig misses, with signal-engine state that
   date (COT gate, cb_decisions availability).
4. A/B toggles (COT gate forced open; cb_decisions absent) attributing count deltas per cause —
   attribution must sum to the total gap; no unexplained remainder may be labeled "diagnosed".

**Out of scope — ~/quant restore list, not rebuilt here**: ^VIX3M/^TNX parquets,
`data/cache/cot/*`, the `oos_trades_2025_2026.json` writer, `build_cb_decisions.py`. The script
emits that list; G2 goes green on attribution or a logged waiver.

### Phase 4 — Retire/repoint the wrong gate (after 2's state schema exists)

- `sovereign/propfirm/deployment_checklist.py`: top banner + first-line runtime print —
  "ICT-LANE LEGACY (Lucid futures) — not the carry gate; see spec 021". Keep the file.
- `scripts/build_daily_verdict_page.py:29`: repoint from the stale
  `data/agent/prop_challenge_state.json` to `data/agent/carry_buy_gate_state.json`; red-by-default
  (missing key, missing file, or state older than 7 days ⇒ NOT READY). This structurally kills
  the misleading "4 of 6 green" render.
- `sovereign/propfirm/rules_engine.py`: docstring note — futures-lane only, dead for carry.
- **Mutation tests**: stale ICT state ⇒ NOT READY; all-green state older than 7 days ⇒ NOT
  READY; deleted gate key ⇒ NOT READY.

### Phase 5 — Paper carry log `scripts/paper_carry_log.py` (after 1; G5 wiring after 2)

- `open` / `close` CLI → `sovereign/intelligence/decision_logger.py::log_forex_decision` +
  append `data/trade_logs/paper_carry_trades.jsonl`; R computed with the same swap/cost constants
  imported from `firm_contracts` (never re-declared). No new signal logic.
- `carry_buy_gate.py` reads the JSONL for G5 (n/80 counter, live avg R vs band); verdict page
  shows the sprint counter.
- **Tests**: open/close round-trip R matches hand-computed; n<80 ⇒ G5 red; out-of-band paper avg
  R ⇒ G5 red even at n≥80.

### Phase 6 — Doc reconciliation (last, cheap)

- `COLIN_V1.md`: superseded banner — 92.4% is unsourced and Lucid-conditioned.
- `COLIN_V2.md`: 5% tables + 90-day clock superseded; pointer to FIRM_FIT + 021.
- `FIRM_FIT.md`: pointer to the yaml + evaluator. `EVAL_LAB.md`: superseded pointer.
- `CLAUDE.md` (~lines 33–35, 61–64): daily check becomes `python3 scripts/carry_buy_gate.py
  --series sealed` + the repointed verdict page; single sizing doctrine noted as
  "re-derived under real contracts, pending ratification".
- Doc lint (manual grep): no P(pass) anywhere without an adjacent zero-edge figure or watermark;
  "92.4" only under a superseded banner.

## Sequence & effort

0 → 1 → 2 (bulk, ~2–3 sessions) → {3 ∥ 4 ∥ 5} → 6. Roughly a week of solo sessions.

## Key reuse seams

- `sovereign/risk/layers/prop.py::prop_ceiling` — static/trailing/daily floor math.
- `scripts/eval_lab_carry_fix.py` — inc/worst series, zero-edge arm, block bootstrap.
- `scripts/oos_campaign_test.py` — offline yf.download monkeypatch + `get_trades` rig.
- `sovereign/intelligence/decision_logger.py::log_forex_decision` — paper-log sink.

## Out of scope entirely

Sealed CSV regeneration; any `data/proof/*` modification; cache restoration (re-export from
~/quant, tracked by the Phase 3 restore list); any strategy variant research.

## Verification (end-to-end)

1. `python3 scripts/carry_buy_gate.py --series sealed --firm cti_1step --risk 0.02` → G1 green,
   stats match the pre-registered golden numbers, state file written.
2. Same command with `--series oos` before a golden receipt exists → nonzero exit.
3. Full mutation suites for phases 1/2/4/5 pass, and each deliberate fault row in
   `specs/021_MUTATION_LOG.md` demonstrably fails the suite when injected.
4. `python3 scripts/build_daily_verdict_page.py` with no carry state → page renders NOT READY.
5. `python3 scripts/diagnose_repro_gap.py` → attribution sums to the 411−rig gap; restore list
   printed.
