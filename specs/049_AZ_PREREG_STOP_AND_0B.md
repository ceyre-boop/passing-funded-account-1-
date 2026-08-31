# 049 — AZ entry layer: §0b enforcement and the Gate 3a STOP  `[SPEC]` `[PRE-REGISTERED]`

Written 2026-08-30, **before any graded table exists** and before Gate 2 code.
Locks the design doc's §0b plus three decisions Colin ratified tonight. Nothing
here may be revised after a table has been seen; if a rule is wrong the move is
to record that and open a new pre-registration.

## 1. Why this is locked now

`fine` (Gate 1's selected granularity) has **933 cells** and every day touches
~80 of them. Cells are heavily correlated and the honest sample stays **2,620
days** however finely it is sliced. Scanning correlated cells for a positive
region *will* find one — arithmetic, not risk — and it fires exactly when the
answer is most wanted. A STOP criterion written after the table has no teeth.

## 2. The Gate 3a STOP — ratified

**STOP fires only if BOTH fail. Either surviving ⇒ proceed to Gate 4 calibration.**

**Primary — pooled mean R.** All legal candidates, all days, **no cell
selection**. One number per symbol. Unconditional and ungameable.

**Secondary — max cell mean against a max-statistic null.** The correction counts
**every cell scanned**, never the ones that hit.

Rationale for the conjunction rather than the primary alone: a negative pooled
mean says the *average* candidate loses. It does not establish that no
*selectable* region exists — and selection is exactly what the policy layer does.
The pooled number alone could retire a layer that would have worked.

### 2.1 The null — circular day-shift

Verified before declaring: every sampled session carries all grid timestamps
(400/400 for both symbols) and SPY ∩ QQQ is **2,614 shared days**, so candidate
slots are aligned across days. That makes the permutation exact:

> Rotate the day index of the **outcome** vector against the **cell-assignment**
> vector by `s = 1 … N−1`. Recompute all cell means. Take the max. That
> distribution is "the best cell by chance".

Preserves within-day structure and cross-cell correlation, neither of which a
Bonferroni over 933 correlated cells respects. Days with masked (illegal) slots
contribute only their legal slots; the mask is applied before permutation, never
after.

No precedent exists to reuse — `daytrade/mechanisms.py::permutation_p` has the
correct day-blocked mechanic but a fixed contrast, not a max-over-cells
reduction. It is a donor, not a dependency.

## 3. Two declared hypothesis families, one state space

Including signed dimensions (`trend`, `gap`, `vwap`) means the state space *can*
express a directional policy, so §0's magnitude-only target stops being enforced
by construction. It is declared instead. The families differ in the **response
variable**, not the state space:

| family | response | claim |
|---|---|---|
| **M** (primary, §0's target) | realized `\|R\|` / dispersion | magnitude is conditionable |
| **D** (secondary, declared here) | signed R | expectancy is conditionable |

Each carries its **own** max-statistic correction over its own scanned cells. The
pair rule (§4) and all four sub-periods (§5) apply to **both** — secondary claims
are not exempt.

The STOP's primary statistic is signed mean R, i.e. Family-D flavoured, because
"positive expectancy" is inherently signed. **A surviving Family-M cell is sizing
and timing information; it does not by itself imply positive expectancy and
cannot alone prevent a STOP.**

## 4. The pair rule — SPY and QQQ are ONE grading unit

Same beta, different weights. Treating them as independent confirmations turns
one coin flip into a fabricated replication.

**A result must hold on both.** Sign agrees on both **and** both clear the
threshold independently. **One clearing and one failing is NULL**, never "held on
SPY". Implemented as a single `adjudicate()` returning one verdict, fault-injected.

## 5. Sub-periods — fixed here, in advance

| period | span | character |
|---|---|---|
| P1 | 2016–2017 | pre-2018 low-vol |
| P2 | 2018–2019 | post-vol-shock normalisation |
| P3 | 2020–2021 | COVID dislocation |
| P4 | 2022–2026 | rate-hike cycle and after |

Requirements: **sign agrees in 4/4**; **magnitude clears in ≥3**; **no single
period contributes more than half the total effect** (a result carried by P3 is a
COVID artifact and is reported as one). `N_days` reported **per period** — a
period that "holds" on eleven days did not hold.

Per-period threshold = `daytrade.mechanisms.mde(σ_day, N_days_p)`, the repo's own
formula, honest about differing power per period. `σ_day` is computed once on the
full window so the bar does not move with a period's own noise.

`daytrade/splits.py` is strictly two-way (tune/sealed); this N-way splitter is new.

## 6. Granularity fallback ladder — declared before it is needed

`fine` passed at **0.9164** — passing, not comfortable. The generalisation lane
degrading exactly with day count says `fine` needs roughly the days we have.

**Declared: `fine` → `medium`, at most one step**, taken only if Gate 3a's primary
lands inside the null band. The step is recorded with its reason and **the
correction budget counts cells scanned at BOTH granularities** — a retreat buys
no fresh budget. `coarse` is not in the ladder.

## 7. `N_days` is mechanical, not remembered

Every emission carrying a candidate-row count must carry `N_days` in the same
output, with rows labelled **`not the sample size`**. Enforced by `az/report.py`'s
frozen `Tally` plus a test that AST-scans `az/` and fails on any formatted
candidate count in a statement lacking `n_days`. Precedent: `az/state.py::Occupancy`
already ships every count beside its denominator.

This has been the unit error twice — 54 snapshots per trade in Phase 1, 44
candidates per day here. It is closed by construction rather than by vigilance.

## 8. Deferred: the 73-session cross-asset union

Its role is named now so it cannot be repurposed: the **out-of-universe
generalisation check** for a result that has already cleared Gate 4 on SPY+QQQ.
One shot, with unseal discipline — reason, rule version, frozen commit, recorded
before the run.

**It is not a second sample to test on if SPY+QQQ comes back null.** If SPY+QQQ
fails, the study fails and the union stays untouched for the next
pre-registration.

## 9. Invariants

- **I80** any emission with a candidate count and no `N_days` fails a test.
- **I81** `adjudicate()` returns NULL when the pair disagrees; never a partial.
- **I82** the max-statistic correction counts every cell scanned, across every
  granularity used.
- **I83** the STOP requires both primary and secondary to fail.
