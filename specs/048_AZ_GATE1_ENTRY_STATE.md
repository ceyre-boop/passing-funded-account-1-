# 048 — AlphaZero entry layer, GATE 1: entry state representation  `[SPEC]` `[PRE-REGISTERED]`

Written 2026-08-30 **before any Gate 1 code existed**. Implements Gate 1 of the
AlphaZero Entry Layer design doc, with the four amendments applied. The doc's
§0 fixed-decision table is binding and is not relitigated here.

## 1. Universe — declared, with the rationale the amendment requires

The amendment says: build cross-asset so it generalises, run Gate 3a first on the
deep series, because the kill diagnostic needs days. Two of its premises are
factually off against the caches, so the choice is made on measured numbers:

| candidate universe | symbols | distinct complete sessions | span |
|---|---|---|---|
| `bars/` cross-asset | 19 | **73** (union; ragged — NVDA 73, SPY 66, ES=F 48) | 2026-05 → 2026-08 |
| `bars_extended/` deepest **single name** | 1 (NVDA) | **664** | 2024-01 → 2026-08 |
| `bars_premarket/` SPY + QQQ | 2 | **2,656 / 2,652** | 2016-01 → 2026-08 |

- The cross-asset union is **73**, not 84. The 84 figure is `decision_ledger.jsonl`
  row coverage, which is not the same as gradeable complete sessions.
- The 2,678-session series is **not single-name** — it is SPY/QQQ, index ETFs.
  The deepest genuine single name is NVDA at 664.

**DECLARED: the Gate 1 and Gate 3a lane is SPY + QQQ, ~2,650 shared sessions.**
It is cross-asset (two assets, so universe-wide selection is exercised rather
than degenerate) **and** deep (36× the session count of the `bars/` union), so it
satisfies both halves of the amendment instead of trading one against the other.
`bars/`'s 19 symbols are the **generalisation lane**: the machinery must run on
them unchanged, and Gate 1 reports occupancy there too, but no threshold is set
against 73 days because 73 days cannot support one.

**Day count is the sample size** (doc risk #2). Every occupancy number below is
reported in distinct **days**, never rows.

## 2. Candidate moments — the time grid, declared

Candidates are enumerated on a **fixed 30-minute grid within RTH**: 09:30, 10:00,
10:30, 11:00, 11:30, 12:00, 12:30, 13:00, 13:30, 14:00, 14:30 ET. 15:00 onward is
**excluded as illegal**, not as zero-reward — a candidate needs ≥ 60 minutes of
remaining intraday path for any grader to reach a genuine close (doc Gate 2).

Per day: **11 timestamps × 2 symbols × 2 directions = 44 candidates**, coarse
enough to enumerate exhaustively (amendment 2). Over ~2,650 days that is
**≈116,600 candidates**, so Gate 3a's counterfactual table is complete and Gate 3b
is a lookup. No behaviour policy exists, so importance weighting never applies.
Stated here so Gate 3b is not built as an estimator.

## 3. The state vector — six dimensions, all as-of computable

| dim | definition | why |
|---|---|---|
| `realized_vol` | ATR14 over bars ≤ t, ÷ close | the magnitude conditioner the doc requires |
| `vol_expansion` | log(ATR over trailing 12 bars ÷ ATR over trailing 78) | second magnitude conditioner |
| `trend_state` | slope per ATR over trailing 24 bars | signed market state |
| `gap_state` | (session open ÷ prior close − 1) ÷ ATR14 | see §3.1 |
| `time_block` | `ceiling.time_block(t)` | intraday phase; `bars.Session` owns the name "session" |
| `rel_to_vwap` | (close − session VWAP) ÷ ATR14 | position within the day |

Formulas are reused from `daytrade/regime_vector.py` (`_atr`, `_slope_per_atr`,
`_vwap`) — one implementation, per repo rule 1.

**LLM-derived features are excluded from this lane entirely** (doc Gate 1). All
history predates the model cutoff, so any such feature is hindsight-contaminated.
`sovereign/forex/feature_registry.py`'s `as_of_computable` discipline is the
precedent; a feature marked `False` may not enter.

### 3.1 `gap_state` is included, and the tension is recorded not hidden

The doc's §0 fixes the **prediction target** as magnitude. `gap_state` is a
*signed* feature, so its inclusion needs saying out loud.

Today's adversarial pass (`artifacts/CORRECTIONS_2026-08-30.md`) reversed the
finding the magnitude framing rested on. `C5_gap:high` is a **sign** effect:
the cell is 100% gap-ups, and on true `|gap|` it collapses from 68% of configs
profitable to 9%. So the strongest single piece of evidence for "magnitude, not
direction" no longer supports it.

Gate 1 defines the **state representation**, not the prediction target. Excluding
the one conditioner with a measured effect because a §0 decision points elsewhere
would be choosing the feature set to protect a hypothesis. **`gap_state` is
included as a state dimension; the §0 prediction-target decision is untouched and
remains Colin's to revisit at Gate 3.** Both magnitude conditioners are included
as the doc requires.

## 4. Occupancy — the pre-declared threshold

A cell is **VALUED** iff it contains ≥ **30 distinct days**. Days, not rows:
44 candidates share a day and are one bet, exactly as 54 snapshots shared a trade
in Phase 1 and made a 4.4%-thin grid read as 57.5%-thin once counted correctly.

Reported at each granularity: cells, cells valued, `frac_days_valued`, and
`frac_candidates_in_valued_cells`. **Gate 1 closes at
`frac_candidates_in_valued_cells` ≥ 0.85**, mirroring Phase 1's bar (the
complement of `state_space`'s own 0.15 OK threshold). 0.65 is MARGINAL.
**The finest granularity clearing 0.85 is selected.** The rule reads occupancy
only — never R, never any outcome.

Granularities swept: `coarse`, `medium`, `fine`, declared in code as edge tuples
before the run.

## 5. The lookahead guard

Mechanical, not conventional (doc Gate 1). Every feature is computed from a frame
truncated at the candidate timestamp; the guard **re-computes** each feature
against a frame that has been corrupted *after* t and asserts the value is
unchanged. A feature that moves has seen the future. `LookaheadError` raises —
never warns, never degrades.

**Fault injection is part of the gate, not a follow-up:** the guard is disabled
and the corresponding test must fail; a guard that has never fired is not a guard.

## 6. Out of scope for Gate 1

Candidate generation and legality masking (Gate 2), grading (Gate 3a), the
pessimistic fill model (amendment 4, lands in Gate 2 or 3a), multi-grader
stability (amendment 3, Gate 3a), any policy or learning (Gate 3b/4). The sealed
lane stays sealed. `C5_gap:high` is a separate pre-registered test.

## 7. Invariants

- **I76** every state feature is unchanged when data after `t` is corrupted; a
  deliberate violation raises `LookaheadError` and fails a named test.
- **I77** occupancy counts distinct **days** per cell, never rows.
- **I78** granularity is chosen by the §4 rule on occupancy alone; no outcome
  statistic may enter the selection.
- **I79** no LLM-derived or `as_of_computable=False` feature enters this lane.

---

# GATE 1 RESULT — 2026-08-30

## Grid correction, found by running it: 09:30 is ILLEGAL, not zero-support

`09:30` has exactly **one bar** at or before it, and the state vector requires ≥3
(ATR is undefined on one bar). It failed on 100% of sessions. That is a
**legality fact**, not thin support — the same distinction as TIGHTEN under
`trail_mult=None` on the Stockfish side.

**§2's grid is corrected from 11 timestamps to 10** (10:00 … 14:30), so
**40 candidates/day**, not 44 — 2 symbols × 10 times × 2 directions. Over the
declared lane that is ≈104,800 candidates, still exhaustively enumerable, so
amendment 2 stands: Gate 3b is a lookup.

`09:30` moves to Gate 2's legality mask with the reason recorded, and the
illegal fraction is reported per day there.

## Occupancy — declared lane, SPY + QQQ

5,234 symbol-days · **2,620 distinct calendar days** · 52,340 candidate moments
· `min_days=30` · threshold 0.85 pre-declared.

| granularity | cells | valued | `frac_candidates_in_valued_cells` | verdict |
|---|---|---|---|---|
| coarse | 90 | 50 | 0.9951 | OK |
| medium | 328 | 147 | 0.9757 | OK |
| **fine** | **933** | **260** | **0.9164** | **OK — SELECTED** |

All three clear the bar. The §4 rule takes the **finest** clearing it: **`fine`**.
The rule read occupancy only; no outcome statistic existed at selection time,
because Gate 3a has not been built.

This is a materially better position than Phase 1's exit lane, where nothing
reached OK at episode level and `minimal` was MARGINAL at best. 2,620 days
against 39 is the difference.

## Occupancy — generalisation lane, `bars/` 12 symbols

387 symbol-days · **40 distinct calendar days** · 3,870 candidate moments.

| granularity | cells | valued | frac | verdict |
|---|---|---|---|---|
| coarse | 88 | 24 | 0.8711 | OK |
| medium | 297 | 22 | 0.5119 | FAIL |
| fine | 667 | 11 | 0.2364 | FAIL |

As §1 said in advance, no threshold is set here — 40 days cannot support one, and
the machinery is required to *run* unchanged, not to *pass*. It runs. The lane
degrades exactly as day count predicts, which is itself a check on the audit.

## Lookahead guard — fault-injection result

The guard computes each feature, then corrupts every bar strictly after `t` by a
**per-bar factor shared across O/H/L/C** (independent per-column corruption
inverts High/Low, yields a negative ATR, and raises `StateError` before the
comparison can run — a real bug found and fixed while building this) and
recomputes. Any movement raises `LookaheadError`.

A feature reading only `truncate_at(df, t)` **cannot** leak — the truncation makes
it structurally impossible. So the thing that can break is the truncation, and
that is what the guard tests. `truncate_at` is therefore a named, patchable seam.

- Break the truncation (return the full session): **all five features move** —
  `vol` 0.00267→0.00304, `expansion` −0.135→−0.039, `trend` −3.77→−235.15,
  `gap` 2.185→1.922, `vwap` −3.48→−96.37 — and `LookaheadError` raises. ✓
- Disable the guard's comparison (`if moved:` → `if False:`): **exactly the two
  guard tests fail**, nothing else. Restored, 7/7 green. ✓

## Gate 1 status: CLOSED

Occupancy passes at the pre-declared threshold, granularity selected by the
declared rule, lookahead guard fires correctly under fault injection.
