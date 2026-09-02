# Clearing the board — three open questions, closed or sized

**Date:** 2026-09-01. Every effect below is printed with its detection floor.
No number is quoted without its n.

---

## 1 · MECH-006 — the last open hypothesis on the entry layer

> *"AlphaZero's entry veto carries information a rate-matched coin does not: the
> days it refuses are worse than average, not merely fewer."*

39 entered bars against 39 rate-matched refused bars, forward magnitude in ATRs:

| arm | n | mean fwd range | median |
|---|---|---|---|
| ENTERED | 39 | 3.0114 | 3.1580 |
| REFUSED (control) | 39 | 3.2259 | 3.0088 |

**Effect −0.2145 ATR · pooled sd 1.2439 · n_eff 19.5 · MDE 0.7097 · 3.31× below floor.**

### VERDICT: UNMEASURED, NOT REFUTED

The effect is 3.3× smaller than the smallest thing 39 decisions per arm could
detect. Note it also points the *wrong way* — entered bars had slightly *less*
forward range than refused ones — but at this n that is noise, and calling it a
null would claim knowledge the data does not contain.

**Cost of an answer: ~416 decisions per arm**, against the 39 available.

---

## 2 · The semantic lane — the one information source never tested

Every null in this repository is on **price-derived features**: the entry grid,
the exit sweep, the four event studies, the self-play learner, three learned-exit
attempts, and the other lane's five families. Nine independent nulls, all asking
whether the recent price path predicts the next one.

The operator asks a different question — it reads news, classifies evidence,
forms a semantic judgment. **That source has never been tested at any scale.**

```
forecasts written   17        resolved 13        unresolvable 4        open 0
distinct days        3        (2026-08-16 .. 2026-08-18)
stale at decision    0/13     carrying policy_regret  0/13
```

| n | detectable Brier effect | |
|---|---|---|
| 13 | 0.2069 | what exists |
| 50 | 0.1055 | gate minimum |
| 150 | 0.0609 | needed for 3-regime stratification |

### The distance is much shorter than it looks

At the observed **5.7 forecasts per active day**, reaching the gate's 50 takes
**about seven more active days.** The binding constraint is not forecasts per
session — it is **sessions run**, and only three exist across the whole span.

**And it cannot be backtested.** The forecaster is an LLM with a training
cutoff; pointed at a historical session it may already know how the day ended.
Sealing is the entire mechanism and there is no retro-sealing against a model
that might remember. This lane accumulates forward only.

---

## 3 · Magnitude versus direction — does the last positive finding carry?

The event studies produced exactly one asymmetry that survived its floor:
magnitude is measurable, direction is not. Every model built since has predicted
direction and returned null. So: can the same 13 features predict *how far*
rather than *which way*?

| target | best \|corr\| | feature | floor | ratio |
|---|---|---|---|---|
| DIRECTION r_long | +0.0176 | vol_ratio | 0.1244 | 7.06× below |
| DIRECTION r_short | −0.0238 | atr_frac | 0.1244 | 5.22× below |
| MAGNITUDE max(long, short) | +0.0295 | bars_since_high | 0.1244 | 4.21× below |
| MAGNITUDE \|long − short\| | −0.0289 | atr_frac | 0.1244 | 4.30× below |

Magnitude is **1.24×** more predictable than direction. Nothing.

### VERDICT: NO ASYMMETRY THAT SURVIVES THE FLOOR

The event studies' magnitude finding **does not carry into this feature set**. It
stays confined to the scheduled-event windows where it was measured. That closes
it as a general property, which is worth as much as confirming it would have
been — it removes the last reason to keep searching price features here.

*(16,797 decision points over 400 sessions. 100 most-recent sessions sealed and
untouched: 2026-04-06 .. 2026-08-26.)*

---

## Where that leaves the board

| lane | state | distance to an answer |
|---|---|---|
| Price features, direction | **closed**, nine nulls | — |
| Price features, magnitude | **closed** here by test 3 | — |
| Entry veto (MECH-006) | **unmeasured** | ~416 decisions/arm vs 39 |
| Semantic / news | **untested** | **~7 active sessions** |

Two of the three tests closed or sized a question against the repo's own
standard. The third — the only lane running on information this project has
never tried — turns out to be the nearest one to a real test.
