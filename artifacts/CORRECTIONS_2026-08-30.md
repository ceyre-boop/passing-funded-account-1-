# Corrections from the adversarial pass — two rulings reversed

The adversarial pass (`artifacts/ADVERSARIAL_PASS.md`) reproduced every ruling's
numbers from source before attacking them. Two do not survive. Both were
independently re-verified by the architect seat before being accepted here.

## 1. `Tablebase.build` discards 65–75% of transitions — granularity ruling REVERSED

`build()` buckets transitions by `phase`, then writes `self.cells[state] = Cell(...)`
inside that per-phase loop. `State` has no phase field (`r_b, hold_b, atr_b,
carry_b, time_block, weekend`), so a state seen at several bar positions is
**overwritten**, and only the last-written phase survives into `coverage()`.

Visible in the record I already published without noticing: I quoted
`observations: 5944` for `coarse_both` against a corpus of **17,603 transitions**.

| granularity | transitions | as-built obs | discarded | as-built | **corrected** |
|---|---|---|---|---|---|
| minimal | 17,603 | 4,484 | 74.5% | 0.9097 | **0.9788** |
| coarse_both | 17,603 | 5,944 | 66.2% | 0.8905 | **0.9640** |
| full | 17,603 | 6,052 | 65.6% | 0.7198 | **0.8950** |

The declared rule — *finest grid with `frac_obs_valued` ≥ 0.85* — is unchanged and
was not the problem. Applied to a correctly computed statistic it selects
**`full` (357 cells)**, not `coarse_both`. **Granularity ruling reversed.**

The basket's quoted 0.4613 is tainted by the same bug and is withdrawn.

**Open design question, not decided here:** should a cell be keyed by `state`
alone (so all phases aggregate, which is what "tablebase" implies) or by
`(state, phase)` (so a state at bar 1 and bar 9 are different cells, since they
have different remaining horizon)? The overwrite is unambiguously a bug either
way. Which fix is correct is a ruling. `hold_b` already encodes time coarsely,
which is an argument for state-alone.

## 2. `C5_gap:high` is a SIGN effect, not a magnitude effect — reading REVERSED

`phase0_conditioning.py:78` terciles the **signed** gap. So `high` is 100%
gap-ups and `low` is 100% gap-downs — the "monotone gradient" is an ordering in
sign, not in size.

| basis | low | mid | high |
|---|---|---|---|
| signed gap | −0.1399 · 0% cfg>0 | −0.1082 · 0% | **+0.0137 · 68%** |
| **\|gap\| (true magnitude)** | −0.1210 · 0% | −0.0774 · 3% | **−0.0240 · 9%** |

On genuine magnitude the effect largely collapses. **The claim written into
`121d305` — "the entry is not less wrong in a direction, it is less wrong on big
days" — is false and is withdrawn.**

The adversary also shows the dismissal was too quick: **3** of 13 cells fall
outside the band, not 1; the exact shuffle tail is 3/4000 (Bonferroni p ≈ 0.0098);
and against a same-half baseline the cell is OUT in **both** halves (z = +2.64,
+3.33) — it does **not** fail a half-split as I claimed.

`C5_gap:high` remains a **lead requiring its own pre-registration on the sealed
half**. That label survives; the three reasons I gave for it do not.

**Consequence for Phase 2.** The framing "forecast magnitude, not direction" is
now unsupported by its strongest single piece of evidence. Note the nuance: the
cell is direction-balanced (363 long / 342 short), so this is not "predict which
way price goes" — it is conditioning on a signed, observable market state. That
is not what the ten event studies falsified, but it is also not magnitude.
**Phase 2's framing needs re-deriving before `dispersion.py` is pointed at
anything.**

## 3. `trail` ACTIVE — WEAKENED, not reversed

δ = 0.10 did **not** rig the test: no δ in (0.001, 0.100] accepts H1 (max LLR
+1.53 against a +2.7726 bound). The real defect is dilution — **72 of the 91
episodes the SPRT consumed had ΔR ≡ 0**, and on the 486 episodes where the
candidate actually acted, mean ΔR = **+0.1105, above δ**, with the SPRT there
INCONCLUSIVE.

That is the hollowness problem in a subtler form than the one already guarded
against: the overall deviation rate was a healthy 23%, but the *consumed prefix*
was mostly non-acting. **A future gate must report deviation over the consumed
prefix, not over the whole population.**

The mirror run (incumbent = no-trail) also returns ACCEPT_H0. That is correct
behaviour for a one-sided test whose δ exceeds the effect in both directions, not
a bug — but it is a second face of the structural limit already recorded in
`gate/CALIBRATION_RECORD.md`.

## 4. SURVIVES: `time_decay` OUT, and volatility-with-no-k

Volatility survives decisively: a sign-flip null preserving cross-arm correlation
puts max-over-8-draws at mean **+18.50 R** against the **+18.19 R** observed —
**p = 0.396**. Refusing to select a k was correct.

## 5. "No exit mechanism recovers a negative entry" — NOT airtight

The population's own best fixed config (total −28.92 R) restricted to gap-up days
returns **+111.96 R over 705 trades**, with no in-cell config selection. That is
an entry filter rather than an exit mechanism, so the sentence survives as
literally worded — but the stronger reading it was being used to justify does not.
