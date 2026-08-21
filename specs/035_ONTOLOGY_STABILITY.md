# Spec 035 — Ontology stability under sample growth

Status: **[SPEC]** — safe to build from.
Governs: `daytrade/ontology_audit.py`. Extends spec 033 (measurement hygiene).

## The observation that forced it

The science loop re-ran the ontology audit after the decision ledger was
backfilled from 438 to 674 labeled sessions. Same nine labels, same code,
same outcome definition. The two trend labels **swapped verdicts**:

| label      | n=438              | n=674              |
|------------|--------------------|--------------------|
| TREND_UP   | p=0.0072 CARVES    | p=0.3248 DECORATION|
| TREND_DOWN | p=0.1223 DECORATION| p=0.0006 CARVES    |

Neither move is Monte Carlo error — MC se is 0.0002–0.0023, and the p-values
moved by 45x and 200x. A label that carves a real joint gets *sharper* as
data arrives. Two labels trading places is what noise looks like when the
sample is small enough that which noise wins is arbitrary.

The audit already guarded the WITHIN-run coin flip (`at_boundary`, spec 033's
MC-error note). It had no guard for the ACROSS-sample coin flip, so it kept
printing a confident verdict on both sides of the swap.

## Invariant I-035-1 — a verdict must survive subsampling

For every testable label, the permutation test is re-run on `K_SUB`
independent half-samples drawn **by calendar day** (day-grouping doctrine:
same-day cross-symbol entries are nearly one bet). `subsample_carve_rate` is
the fraction of evaluable half-samples in which the label separates at
p<0.05.

The rate is scored against a null, not a chosen floor. A label with NO real
effect still clears p<0.05 in ~5% of half-samples by construction, so
`NOISE_CARVE_RATE = 0.05` IS the null and `stability_p = P(X >= carved |
Binomial(n_eval, 0.05))`.

A label may be reported CARVES only if `stability_p < STABILITY_ALPHA`. A
label that clears p<0.05 on the full sample but not this bar is reported
**UNSTABLE**, never CARVES and never DECORATION — the honest statement is
that the verdict depends on which half you look at.

A majority floor was written first and discarded: it has no justification,
and at K_SUB=8 it would have thrown away a label reproducing at 3/8, which is
8x the noise rate (vs-noise p=0.006). The discarded rule is kept as mutation
M43 so the choice stays enforced rather than remembered.

**Known limitation, stated rather than hidden:** half-samples are drawn from
one record and overlap, so they are positively correlated and the binomial
tail UNDERSTATES the true p. `STABILITY_ALPHA` is tightened to 0.01 to
partly compensate. This is a bound, not an exact test, and the report says so
in `stability_note`.

`n_survive_bonferroni` counts only labels whose verdict is CARVES. An
UNSTABLE label cannot enter the surviving set no matter how small its
full-sample p is.

## Invariant I-035-2 — insufficient subsample power is named, not defaulted

If fewer than half the half-samples are evaluable (a label too rare to have
>=10 marked and >=10 unmarked inside a half), `subsample_carve_rate` is
`null` and the verdict is **UNSTABLE_UNTESTED**. It is never silently
treated as stable, and never coerced to 0.0 — repo development rule 3.

## Constants

`K_SUB = 8`, `SUB_PERM = 2000`, `NOISE_CARVE_RATE = 0.05`,
`STABILITY_ALPHA = 0.01`, seeded from `SEED`. At these values the bar is
"reproduces in >= 3 of 8 half-samples".
`SUB_PERM` is lower than `N_PERM` deliberately: the subsample test asks only
whether p clears 0.05, not where it sits relative to a Bonferroni floor, so
it does not need the resolution the headline test needs.

## Tests

- `test_unstable_label_is_not_reported_as_carves` — a synthetic label with a
  strong effect confined to one half of the days clears p<0.05 on the pooled
  sample and must come back UNSTABLE.
- `test_stable_label_survives_subsampling` — an effect present in both halves
  must come back CARVES.
- `test_rare_label_reports_untested_not_stable` — a label too rare to split
  must come back UNSTABLE_UNTESTED with a null rate.
- `test_bonferroni_set_excludes_unstable` — an UNSTABLE label with p below the
  Bonferroni threshold must not be counted in `n_survive_bonferroni`.
- `test_stability_is_grouped_by_day_not_by_row` — half-samples are half the
  DAYS; sampling rows would put both groups in every day and make the
  outlier-day pathology invisible.
- `test_noise_null_is_five_percent_not_an_arbitrary_floor` — 2/8 is noise,
  3/8 is not.
- `test_three_of_eight_beats_noise_and_is_not_penalised_by_a_majority_floor`
  — the seam between the two candidate rules; without it M43 survives.

## Mutation rows

| id  | fault injected | test that must fail |
|-----|----------------|---------------------|
| M40 | drop the stability gate, verdict = CARVES on p<0.05 alone | `test_unstable_label_is_not_reported_as_carves` |
| M41 | an unevaluable subsample result reports as fully stable | `test_rare_label_reports_untested_not_stable` |
| M42 | count UNSTABLE labels in the Bonferroni set | `test_bonferroni_set_excludes_unstable` |
| M43 | score stability against a 50% floor instead of the noise null | `test_three_of_eight_beats_noise_and_is_not_penalised_by_a_majority_floor` |
| M44 | draw half-samples by row instead of by day | `test_stability_is_grouped_by_day_not_by_row` |

All five killed 2026-08-21; source restored byte-identical after each. M43
survived its first run — the original unstable fixture sat at 1/8, which both
candidate rules reject, so nothing in the suite distinguished them. The
fixture at the 3/8 seam was added and M43 then died. Logged because a
mutation that survives is the only evidence a test is decorative, and this
one was.
