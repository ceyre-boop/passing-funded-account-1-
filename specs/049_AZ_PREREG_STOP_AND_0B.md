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

---

# GATE 3a RESULT — 2026-08-30. **VERDICT: STOP.**

Table: `artifacts/az_gate3a_table.parquet`, sha256 `bc4a2f9a4436c36a…`, reproduced
bit-identically on a second run. 628,080 graded rows = 104,680 legal candidates ×
3 graders × 2 fills. **N_days = 5,234 symbol-days / 2,620 calendar days.**
Adjudicated on the **pessimistic** arm, locked before the table existed.

## Distribution — absolute R, no deltas

| grader | fill | mean R | sd | p10 | p50 | p90 | frac>0 |
|---|---|---|---|---|---|---|---|
| G1 frozen | base | −0.0676 | 1.293 | −1.116 | −1.008 | +1.843 | 25.4% |
| G1 frozen | **pess** | **−0.3540** | 1.241 | −1.290 | −1.054 | +1.605 | 20.2% |
| G2 hold-to-close | base | −0.0773 | 4.435 | −4.889 | −0.073 | +4.729 | 49.2% |
| G2 hold-to-close | **pess** | **−0.3639** | 4.442 | −5.192 | −0.350 | +4.454 | 46.0% |
| G3 fixed-R | base | −0.0653 | 1.275 | −1.116 | −1.008 | +2.017 | 25.4% |
| G3 fixed-R | **pess** | **−0.3525** | 1.225 | −1.290 | −1.054 | +1.880 | 20.2% |

## PRIMARY — FAILS

| | N_days | mean R | total R | threshold |
|---|---|---|---|---|
| SPY | 2,619 | **−0.37795** | −19,796.9 | 0.01590 |
| QQQ | 2,615 | **−0.33010** | −17,264.5 | 0.01592 |

Pair rule returns HOLDS — **both arms agree, and they agree the sign is
negative.** The primary requires a positive mean, so it fails.

## SECONDARY — FAILS

| | cells scanned | valued | best cell mean R | null p95 | p |
|---|---|---|---|---|---|
| SPY | 1,542 | 354 | +0.0956 | +0.4310 | 0.9687 |
| QQQ | 1,666 | 346 | +0.3557 | +0.5028 | 0.2975 |

**The null's 95th percentile is higher than the observed best cell on both
symbols.** Chance routinely produces a better "best cell" than the data does.
That is the arithmetic certainty the STOP was locked in advance to defeat, and it
behaved exactly as predicted.

## Sub-periods — the negative result is stable, not a regime artifact

| | N_days | mean R | threshold | share | |
|---|---|---|---|---|---|
| P1 2016-17 | 499 | −0.62908 | 0.03414 | 33.8% | clears |
| P2 2018-19 | 498 | −0.39678 | 0.03418 | 21.3% | clears |
| P3 2020-21 | 499 | −0.28707 | 0.03414 | 15.4% | clears |
| P4 2022-26 | 1,124 | −0.24263 | 0.02275 | 29.4% | clears |

Sign 4/4, magnitude 4/4, max period share 34%. No period carries it.

## Multi-grader stability — no exploitation, and one leg was uninformative

- G1 vs G3 **+0.9957** — near-identical. **That leg of the check is
  uninformative** and is reported as such rather than counted as a pass.
- G1 vs G2 **+0.2926**, G2 vs G3 **+0.2853** — G2 is genuinely independent, so the
  check has real content through it.
- Top 1% by G1 sit at the **90.0%** percentile of G2 and **92.8%** of G3. The best
  entries are best under all three. **No grader exploitation detected.**

## What the STOP does and does not establish

**Does:** under the declared geometry, entering SPY/QQQ on a 30-minute grid in
either direction has no positive-expectancy region — not on average, and not in
the best of ~1,600 correlated cells after a correction that counts every cell
scanned. Stable across four regimes and three exit policies.

**Does not:** this is measured at **`k_stop = 1.0`**. A 1×ATR stop is tight — the
median candidate loses a full R and only 20–25% finish positive under the
stop-based graders. A different `k_stop` is a different study, as declared in the
plan before the run, and this result does not speak to it.

**Also worth recording:** G1 base mean **−0.0676** against G2 hold-to-close base
mean **−0.0773** — nearly identical expectancy from a policy that trails and
scales versus one that makes no decisions at all, while the distribution shape
differs completely (sd 1.29 vs 4.44; 25.4% positive vs 49.2%). **The exit policy
redistributes; it does not create.** Phase 1's finding, reproduced independently
on 104,680 entries the exit lane never saw.

## Gate 3a closes here

Gate 3b is not built and no policy is fitted. Per the design doc's own build
order: *"If the graded table shows no positive candidates anywhere, the policy
layer is moot and you have saved yourself the build."* **This finding is the
output.** Next step requires Colin's ruling.
