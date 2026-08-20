# 029 mutation log — the mechanism ledger

2026-08-19. Method unchanged: break the invariant in source, run the named
test, require failure, restore. **Process fix applied from the M13/M26
trap:** the harness now asserts the named test PASSES on unmutated source
before injecting, so a nonexistent-test false kill is impossible.
Suite before: 349/349 (+ scripts). After: 362 passed, 1 skipped.

| # | fault injected | invariant | killed by | result |
|---|---|---|---|---|
| M27 | transfer-prediction requirement dropped | I28 | test_i28_no_transfer_prediction_refused | KILLED |
| M28 | predicted-effect requirement dropped | I29 | test_i29_no_predicted_effect_refused | KILLED |
| M29 | day-blocking removed from the permutation null | I30 | test_i30_day_blocking_changes_the_null | KILLED |
| M30 | shrinkage pooling removed (one miss zeroes a predictor forever) | I33 | test_i33_shrinkage_pools_and_accumulates | KILLED |
| M31 | retrospective entries allowed to calibrate | I33 | test_i33_retrospective_and_unregistered_never_calibrate | KILLED |
| M32 | structural/lever axes allowed to collapse | I34 | test_i34_structural_and_lever_are_independent | KILLED |
| M33 | unfalsifiable-shape flag removed | I28 | test_help_everywhere_is_flagged_unfalsifiable | KILLED |

## First real run — the framework re-derives a known negative

MECH-001 ("a wider trailing stop captures more of the winning tail, so
trailing beats static wherever trends persist") was seeded as `proposed`
with its original help-everywhere prediction, then tested cold:

```
n_entries 336 · n_day_clusters 39
per_class_mean_delta  SINGLE_NAME -0.0469 · CASH_INDEX +0.0445 · FUTURES +0.0212
contrast +0.0187 · p 0.116 · sign_pattern_matched False
minimum_detectable_effect_r 0.0817
VERDICT: KILLED
```

Three things worth keeping:

1. **The kill is for the right reason.** Trailing genuinely HURTS single
   names (−0.047) while helping mildly elsewhere — so the mechanism as
   stated ("helps wherever trends persist") is false even though something
   real is present. Under the old leaderboard framing this was "tmNone wins";
   as a mechanism it is a falsified transfer claim, which is reusable.
2. **The paired design roughly doubled the power.** MDE on paired per-entry
   deltas is 0.082 R/trade at 39 day-clusters, against ~0.18 for unpaired R
   at the same clustering. Pairing removes the entry-quality variance that
   dominated every earlier analysis.
3. **Most of what this repo has chased was under the MDE all along.** The
   futures candidate's +0.086 tune-split margin sat at the detection floor;
   the +0.03 margins were beneath it. The MDE gate now refuses those tests
   before they burn a holdout.

## First soak-channel run — MECH-006, and the structural blocker it exposed

`mechanisms.py test-soak MECH-006` (2026-08-19), veto book vs
`expected_rate_random_veto` over the live soak window:

```
verdict EMPTY_CHANNEL · n_sessions 1 · n_divergences 0 · edge 0.0
```

**The channel is empty for a STRUCTURAL reason, not a sample-size one.**
All three veto-shaped judgments the soak has ever produced fired between
13:23 and 13:56 ET. The OR-break entry window is 10:00–11:00 ET. Across the
entire soak, **zero judgments have landed in the entry window** (records per
ET hour: 8→1, 9→1, then nothing until 12:00). A veto cannot bite a decision
the operator was asleep for.

Consequence, stated so no future session re-derives it: at current tick
coverage MECH-006 accrues **no evidence per day, indefinitely**. More soak
days add nothing. The unblock condition is coverage during 09:30–11:00 ET;
root cause is host sleep (`pmset` wake at 07:55 fires, but `caffeinate -s`
holds only on AC power with the lid open).

This is exactly what the ledger is for: without the mechanism framing, the
soak would have kept running for weeks and produced a confident-looking
0.0 edge that was never a measurement at all. `EMPTY_CHANNEL` is a distinct
verdict from `UNMEASURABLE` for that reason, and a test pins the difference.

## CORRECTION 2026-08-19 — the first-run claim above is WITHDRAWN

The section "First real run — the framework re-derives a known negative"
reported MECH-001 as `KILLED`. **That verdict was over-claimed and is
withdrawn.** Re-run under the corrected rules (see the spec's 2026-08-19
amendment): **INDETERMINATE**. Contrast +0.019 against an MDE of 0.082,
p=0.116 — the claim is untested at this power, not refuted. The dead axis
that run wrote (`trail_mult` across all three classes) has been removed from
the ledger.

What survives from that section: the paired design's power gain (MDE 0.082
vs ~0.18 unpaired) and the observation that most margins this repo has
chased sit at or under the detection floor. Both stand. What does not
survive is calling an underpowered result a kill — failure mode F10,
dead-list poisoning, committed by me within hours of writing the guard
against it.

The three remaining dead axes (MECH-002/003/004) rest on OTHER evidence —
the sealed holdout read, the competence report, the oracle audit — not on
this test, and are unaffected.
