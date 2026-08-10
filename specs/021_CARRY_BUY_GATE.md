# 021 — CARRY BUY GATE `[SPEC]`

**Component:** `data/propfirm/firm_contracts.yaml` · `sovereign/propfirm/firm_contracts.py` ·
`scripts/carry_buy_gate.py` · `scripts/diagnose_repro_gap.py` · `scripts/paper_carry_log.py` ·
repointed `scripts/build_daily_verdict_page.py`
**Status:** `[SPEC]` — safe to build from.
**Plan of record:** `Plans/soft-churning-pelican.md` (approved 2026-08-10).
**Rulings honored:** SANITY_AUDIT standing order (no edge-derived probability without a
zero-edge baseline); CLAUDE.md non-negotiable 1 (no new hypothesis generation — this card
evaluates the one proven edge, it does not search); "Tune split only" rule 5; fail-loud rule 2.

## Why this card exists

The purchase decision for a funded evaluation currently rests on numbers that are unsourced
(COLIN_V1's 92.4% has no generating script), mis-conditioned (Lucid cannot host a multi-day
carry hold at all), or mutually contradictory (five campaign scripts, three sizing doctrines,
0.25%–5% risk, incompatible reset rules, three scripts dead on a hard-coded `/home/claude`
path). This card replaces all of it with ONE evaluator, ONE firm-contract schema, and ONE
machine-computed verdict. Nothing in this card invents strategy; it prices the existing sealed
edge against real firm contracts, honestly.

## Pre-registration (locked before any results are read)

Everything in this section is fixed now. Changing any value after evaluator output exists
requires a logged decision (`decision_logger`) stating what changed and why — same discipline
as spec 008's re-registration clause.

### P1. Trade series

- **Sealed series:** `data/proof/backtest_trades_v015_2015_2024.csv`, read-only.
  Per-trade `R = pnl_pct / risk_pct` using that row's own `risk_pct` (the file contains two
  distinct values: 0.0075 and 0.007969 — a constant divisor is WRONG and must fail tests).
- **OOS series:** `data/oos_trades_2025_2026.json` (60 trades, 2025-01-03 → 2026-06-25).
  Known caveat, stated in every OOS output: this artifact has no committed writer and comes
  from the degraded offline rig (G2 covers this).
- Sealed data is never modified. The evaluator opens it read-only.

### P2. Firm contract schema

One YAML file, `data/propfirm/firm_contracts.yaml`. Per firm:

```yaml
<firm_key>:
  display_name: str
  account_size: float          # USD
  phases:                      # evaluated strictly in order
    - target_pct: float        # equity gain to clear the phase
      min_trading_days: int
      max_days: int | null     # null = NO deadline. Deadlines are never invented.
  max_dd: {type: static|trailing, pct: float, basis: balance|equity, mark: close|intraday}
  daily_dd: {pct: float, basis: balance|equity, mark: close|intraday} | null   # null = none
  permissions: {weekend_hold: bool, overnight_hold: bool, news_hold: bool}
  costs:
    fee_usd: float
    refund_on_pass: float      # fraction of fee returned on funding (0.0 if none)
    swap_haircut_r_per_day: float   # -0.004 R/day, carried over from EVAL_LAB family D
  rules_asof: str              # date the official rules page was read
  source_url: str
```

Initial contracts (parameters from official rules pages, to be re-verified against the live
pages on the day of purchase — `rules_asof` records staleness):

| key | firm | phases | max DD | daily DD | fee |
|---|---|---|---|---|---|
| `cti_1step` | City Traders Imperium 1-Step | 8%, no deadline | 5% **static** | **none** | ≈$382 |
| `alpha_swing` | Alpha Capital "Alpha Swing" | 10% then 5%, no deadline | 10% **static** | 5% | ≈$490 |
| `ftmo_swing` | FTMO Swing (reference) | 10% then 5%, no deadline | 10% **static** | 5% (close basis) | ≈$501 |

The futures lane (Lucid/MFF/Tradeify trailing-DD presets in `rules_engine.py`) is dead for
carry per FIRM_FIT.md and is NOT represented here.

### P3. Sizing doctrine

Re-derived, not assumed (user ruling 2026-08-10): the evaluator sweeps risk 1.0%–3.0% in
0.5% steps under each real contract. FIRM_FIT's 2% is a prior. The doctrine is ratified from
the sweep's output BEFORE any purchase, and the ratification is a logged decision. Until then
no doc may present a single risk level as "the plan".

### P4. Evaluator mechanics

- Daily-increment engine (the `eval_lab_carry_fix.py` design): each trade credits its R at
  exit date; each open trade contributes a worst-case intraday adverse mark for daily/max-DD
  floor checks; concurrent trades sum into the same day (this is the correlation/heat model).
- Heat flag: any day where summed open risk (Σ risk-fraction of concurrent trades) exceeds the
  daily-DD budget (when the contract has one) is counted and reported.
- Phases walk strictly in order; `min_trading_days` enforced per phase; `max_days: null` means
  the walk never times out — TIMEOUT is not a legal outcome for a no-deadline contract.
- Bust → rebuy same policy, fee accounted, attempt counter incremented. No attempt clock
  resets other than what the contract's own `max_days` imposes.
- Swap/weekend cost: `swap_haircut_r_per_day × hold_days` subtracted from each trade's R.
- All paths via `Path(__file__).resolve().parents[1]`. A literal home-directory path anywhere
  in the new code is a defect.

### P5. Statistics

- Campaign start sweep: every eligible start date in the series (step 3 trading days), plus a
  block bootstrap: block = 20 trading days, ≥2000 resampled paths.
- Reported per (firm, risk): P(pass→funded), P(bust per attempt), median/p10/p90 calendar
  days, E[evals burned], E[$ net of refund] — each as **5–95% interval**, never a bare point.
- **Zero-edge control:** the identical campaign on the mean-centered series (per-trade R minus
  sample mean R, same dates/holds/order). Every output line carries real AND control side by
  side; the output formatter takes both or raises. Omission is structurally impossible.
- Verdict comparisons use interval bounds, not point estimates (G3 below).

### P6. The five gates (BUY requires ALL green)

| gate | test | red until |
|---|---|---|
| **G1 GOLDEN** | evaluator's sealed-series replay reproduces, measured from the CSV itself: n=411 exact · avg R +0.3556 ±0.005 · WR 48.66% ±0.5pt · median hold_days 5 exact · weekend-crossing 72.26% ±1pt (computed raw: no swap haircut, per-row risk_pct) | the run in hand shows it |
| **G2 REPRO** | the offline rig's 411→~300 trade gap is attributed to named causes summing to the total, OR waived by a logged decision naming the residual | `diagnose_repro_gap.py` report or waiver |
| **G3 EDGE** | at the ratified risk: OOS P(pass) interval LOWER bound > zero-edge control interval UPPER bound | the comparison holds |
| **G4 FIT** | chosen contract mechanically permits every sealed trade (weekend/overnight holds checked trade-by-trade against `permissions`); violations are listed, not summarized | zero violations |
| **G5 PAPER** | ≥80 closed paper trades via `paper_carry_log.py`, and paper mean R within ±0.25R of the sealed +0.3556 | the log shows it |

G5 blocks the BUY verdict only (user ruling 2026-08-10): OOS numbers may circulate in docs
before the sprint completes, but every such quote carries the literal watermark
**"paper sprint incomplete (n=X/80)"**. The G5 band (±0.25R) is pre-registered here; it is
wide because n=80 has σ/√n ≈ 0.13R on this series — tightening it later is allowed only
before the sprint starts.

### P7. Reproduce-before-OOS

The evaluator refuses (nonzero exit, reason printed) to score any non-sealed series unless G1
passed in the same invocation. No cached "it passed last week" receipts — G1 is cheap; run it
every time.

### P8. Verdict output

`data/agent/carry_buy_gate_state.json`: timestamp, evaluator git rev, per-gate
{status: GREEN|RED, evidence}, and the P5 tables. The daily verdict page renders THIS file
red-by-default: missing file, missing gate key, or timestamp older than 7 days ⇒ NOT READY.
The stale ICT-lane `prop_challenge_state.json` is never read again.

## Out of scope

Sealed CSV regeneration · any `data/proof/*` write · restoring ^VIX3M/^TNX/COT/cb_decisions
caches or the OOS JSON writer (re-export from ~/quant; `diagnose_repro_gap.py` prints the
restore list) · any strategy variant, filter, or parameter search on the edge itself.

## Verification obligations (per repo maturity ladder)

Mutation rows in `specs/021_MUTATION_LOG.md`; the seat that built each module does not certify
it. Minimum fault set:

- contracts: static→trailing flip moves a known floor; deleting `daily_dd` removes the daily
  floor; close↔intraday mark diverges a constructed scenario; alpha phase-2 target ≠ 10%.
- evaluator: sign-flipped R series fails G1; constant-divisor R loader fails G1 (the
  0.007969 rows); daily-floor off-by-one busts a constructed scenario; removing the control
  arm raises in the formatter; trailing-on-CTI flips a known verdict; all-win series cannot
  clear a phase before `min_trading_days`; no-deadline contract never emits TIMEOUT; OOS run
  without G1 exits nonzero.
- verdict page: stale ICT state ⇒ NOT READY; all-green state 8 days old ⇒ NOT READY; missing
  gate key ⇒ NOT READY.
- paper log: round-trip R matches hand computation; n=79 ⇒ G5 red; out-of-band mean ⇒ G5 red
  at n≥80.
