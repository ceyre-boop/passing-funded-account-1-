# Re-run the oracle audit on the extended NVDA population

## Context

On 2026-08-25 the `DataSource` seam (commit `d3088c6`) lifted the yfinance ~60-day
intraday cap. NVDA 5m history went from 74 sessions to **664** (2024-01-02 →
2026-08-25, 126,919 bars, Alpaca SIP, in `data/daytrade/bars_extended/`).

Running `ceiling.policy_ceiling` on that population produced a headline that got
quoted as if it were evidence: best fixed policy −5.99R vs oracle +94.08R, a
"prize" of +0.2332 R/trade. **It is not evidence.** `policy_ceiling`'s own
docstring says so — the oracle is a per-day max over configs and beats any fixed
policy by construction. An oracle computed after the fact is leaked judgment.

The correct experiment — can a *non-omniscient, walk-forward* chooser realize any
of that prize out of sample — **already exists and already ran**:
`daytrade/oracle_audit.py`, 2026-08-17, n=336. Result on disk:

| quantity | value | gate | outcome |
|---|---|---|---|
| `null_leak_R` | 1.7369 | < 0.15 | **FAIL (11x)** |
| `tree_oof_R` | 0.123 | — | — |
| `best_fixed` (NOON_FLAT) | 0.1508 | — | — |
| `realizable_leak_R` | **−0.0278** | > 0 | chooser **loses** to best fixed |
| `oracle_agreement_oof` | 0.4643 | — | — |
| verdict | `NOTHING_QUOTABLE` | | |

Colin's own pre-registered prediction was `"0.15-0.30 realizable; >0.5 rich;
<0.1 drawer"`. It came in at −0.028. That is the drawer.

**So this plan is not "build a policy chooser."** It is: the one input that
genuinely changed is sample size, so re-run the already-pre-registered audit,
gates untouched, on the extended population — and let a gate written before this
data existed speak.

### Why the extended population is a real test, not a formality

The prior run's 336 entries were only **39 distinct sessions** — 8.62 entries per
day across 16 symbols. `oracle_audit`'s null permutation and its `GroupKFold` are
both day-blocked, so the effective independent sample was ~39, not 336. Worse,
those 16 symbols are heavily correlated; `daytrade/mechanisms.py` already carries
`K_EFF = 8.7` for exactly this (SPY/ES=F ρ=0.714, QQQ/NQ=F 0.902, IWM/RTY=F
0.790 — "one market counted twice") and `oracle_audit` never applied it.

NVDA-only extended gives **628 tune sessions = 628 independent days**, ~16x the
effective sample, one symbol, no cross-sectional double-counting.

Scaling `null_leak` as `1/√n`: `1.7369 / √(628/39) ≈ **0.43**` — a 4x improvement,
but still ~3x over the 0.15 gate.

### Pre-registered prediction (written before the run, per repo convention)

- `null_leak_R` lands **0.30–0.60**; gate still **FAILS**.
- `realizable_leak_R` stays **negative or under +0.05**.
- Verdict: another `NOTHING_QUOTABLE` / `DRAWER`.
- **If that is the result, it is the finding**, and it is stronger than the first
  one: it says the prize is structural selection noise, not thin data.
- A `null_gate_pass: true` would be the surprise. Only then does
  `realizable_leak_R` become quotable at all.

## The split needs no edit — verify, don't touch

`daytrade/splits.py` pins `TUNE_END = date(2026, 7, 6)` under a
`# NEVER EDIT THIS LINE` comment. Because it is a **date** and not an index, it
extends over the new population correctly with zero changes:

- tune: **628** sessions, 2024-01-02 .. 2026-07-06
- sealed: **36** sessions, 2026-07-07 .. 2026-08-25 — untouched, not read

This is precisely the failure mode `splits.py`'s docstring was written to prevent
("a count-based split silently reshuffles the holdout overnight"). Confirm the
counts; change nothing.

## Approach

### 1. `daytrade/oracle_audit.py` — parameterize input, freeze everything else

Add **only** CLI arguments. Do not touch `NULL_GATE = 0.15`, `FAMILIES` (6),
`FEATURES` (9), `N_PERM = 200`, tree depth 3 / `min_samples_leaf=15`, or
`GroupKFold(5)`. Editing a gate after seeing it fail is the laundering
`specs/008` and `ceiling._verdict()` exist to refuse.

- `--symbols NVDA` (default: current basket, so the existing behaviour is the
  default path)
- `--cache data/daytrade/bars_extended` (default: `None` → current `bars.CACHE`)
- `--out data/daytrade/oracle_audit_nvda_extended.json` (default: current `OUT`)
- `--label` written into the output JSON so the two runs are distinguishable

Cache redirect: reuse the save/restore-in-`finally` pattern already demonstrated
at `scripts/prove_datasource.py:67-75`.

**Blocking issue this fixes:** `OUT` is currently hardcoded to
`data/daytrade/oracle_audit.json` (line 52). A re-run would silently overwrite
the 2026-08-17 result — destroying the prior evidence. The new run must write a
new path.

### 2. Record that `cls_*` goes constant

On a single symbol, `cls_SINGLE_NAME` = 1 and `cls_CASH_INDEX` / `cls_FUTURES` =
0 for every row. A tree never splits on a constant, so this is harmless — but it
means the effective feature set is 6, not 9. **Do not remove them** (that edits a
pre-registered feature set). Write `constant_features: [...]` into the output
JSON so the run is self-describing.

### 3. Seal the prior result

Add `data/daytrade/oracle_audit.json` to `SEALS.json` via `daytrade/seals.py`
with `unseal_condition: "never — superseded runs get a new path"`, so the
2026-08-17 `NOTHING_QUOTABLE` cannot be quietly overwritten by a future run.
This mirrors `SF_FROZEN_001.json`'s treatment.

### 4. Run it, and report the reference set together

Never quote the chooser alone. The output must always print, on one screen:
anti-oracle → random picker → best fixed → **chooser (OOF)** → oracle, plus
`null_leak_R` beside its gate. `carry_buy_gate`'s P5 does this structurally
("the output formatter takes both or raises"); match that discipline.

## Files

| File | Change |
|---|---|
| `daytrade/oracle_audit.py` | add `--symbols` / `--cache` / `--out` / `--label`; record `constant_features`; **gates untouched** |
| `daytrade/test_oracle_audit_args.py` | new — see verification |
| `SEALS.json` | seal the 2026-08-17 result |
| `data/daytrade/oracle_audit_nvda_extended.json` | new output |

**Reuse, do not reimplement:** `daytrade/splits.tune_sessions`,
`daytrade/bars.load_sessions(..., allow_fetch=False)`,
`daytrade/ceiling.find_entry` / `simulate`, `oracle_audit.entry_features`
(already clean: `pre = s.df[s.df.index < e.ts]`),
`daytrade/exit_evaluator.day_grouped_oof`, `daytrade/measure.exclude_self`.

## Verification

**1. No behaviour change on the default path — the load-bearing check.**
```bash
cp data/daytrade/oracle_audit.json /tmp/oracle_audit_before.json
python3 -m daytrade.oracle_audit          # no args = old behaviour
diff /tmp/oracle_audit_before.json data/daytrade/oracle_audit.json
# must be EMPTY except generated_at
```

**2. Full suite stays green.** Baseline is `523 passed, 1 skipped`.
```bash
python3 -m pytest daytrade/ scripts/ -q
```

**3. Split counts, asserted in the new test:** 628 tune / 36 sealed on the
extended cache; `TUNE_END` still `2026-07-06`; `sealed_sessions()` never called.

**4. Fault injection — the gate must still bite.** Per CLAUDE.md, an invariant
with no test that fails on violation is not verified:
- set `NULL_GATE` to `99.0` → the new test asserting `null_gate_pass` is honestly
  reported must fail
- point `--cache` at a directory with no parquet → must raise, not fall back to
  the live cache silently
- make the cache redirect skip its `finally` → a test asserting `bars.CACHE` is
  restored after the run must fail

**5. The run itself.**
```bash
python3 -m daytrade.oracle_audit \
  --symbols NVDA \
  --cache data/daytrade/bars_extended \
  --out data/daytrade/oracle_audit_nvda_extended.json \
  --label nvda_extended_2024_2026
```
Read `null_leak_R` against 0.15 **first**. If it fails, `realizable_leak_R` is
not quotable and the run ends there — that is the protocol, not a disappointment.

## Out of scope

- Editing any gate, family, or feature in `oracle_audit.py`.
- Reading the 36 sealed sessions.
- Multi-symbol extension (needs Alpaca pulls for 11 more equities; futures cannot
  be extended at all, and mixing depths is its own contamination).
- The carry lane's sizing question (`max_safe_risk` 0.328% vs COLIN_V1's 1.00%) —
  tracked separately, unaffected by this.
