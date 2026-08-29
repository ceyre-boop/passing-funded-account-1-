# The SPRT gate, calibrated two-sided — 2026-08-29

Harness recovered from tag `pre-rollback-2026-08-29` (`sovereign/forex/sprt.py`,
144 lines + 127 of tests, 17 passing) and re-homed to `gate/` because it is
lane-neutral: it compares a candidate to a frozen incumbent and knows nothing
about carry, daytrade, or any instrument.

Population: SPY premarket, `day <= splits.TUNE_END`, **2,116 episodes**.
Incumbent: shipped `CASH_INDEX` policy (`frozen_policy.POLICIES`, SF-FROZEN-002).
δ = 0.10 R (declared plausible-improvement), α = 0.05, β = 0.20.

## Result

| arm | decision | stop | mean ΔR | deviation | σ (source) |
|---|---|---|---|---|---|
| null — shipped vs itself | ACCEPT_H0 | 312 | +0.00000 | 0.0% | 1.0000 (floor, differences degenerate) |
| degraded — trail ×0.2 | ACCEPT_H0 | **31** | −0.05377 | 20.7% | 0.2319 (paired-difference SD) |
| degraded — trail ×0.05 | ACCEPT_H0 | **27** | −0.07525 | 20.6% | 0.2585 (paired-difference SD) |

**CALIBRATED** — the gate recognises a null and rejects a degradation.

## The structural limit, recorded because it will matter

**A one-sided SPRT cannot label "worse" differently from "no change."** H1 is
"improvement of at least δ", so both a null and a degradation correctly reject
it and both print `ACCEPT_H0`. The calibration evidence is therefore not the
verdict string but:

- **stopping speed** — 31 steps degraded vs 312 null, a 10× difference
- **deviation rate** — 20.7% vs 0.0%

Any future gate report that quotes the decision without both of these is
uninterpretable. This is the same failure that made last night's carry gate
hollow: 47 of 97 units had ΔR ≡ 0 and voted for H0 for free.

## What the first degradation attempt found instead

The first candidate was `trail_mult × 3` — widening the trail, expected to be
worse. It was **better**: total R −132.42 against the incumbent's −172.71, mean
ΔR **+0.019**. The harness's own verdict function refused it as "not actually
degraded" rather than scoring it.

That is an independent re-confirmation of **MECH-001** on 2,116 episodes:
loosening a trail helps, tightening it hurts (×0.2 → −286.49, ×0.05 → −331.93,
against −172.71). It also means the degradation had to be built by *choking* the
trail, not widening the stop.

## σ, corrected from last night

σ is the SD of the **paired difference**, not of the level. Last night's carry
gate used per-unit incumbent R (level) SD 0.758, which inflated δ to ~2× the
whole edge. Here the paired-difference SD is 0.23–0.26. When differences are
exactly degenerate (the null arm) there is no spread to estimate, so σ falls to
a declared floor of 1.0 and the record says so rather than dividing by zero.

## Required for the real candidate

Split the result by cell status. At `coarse_both`/`min_paths=30`, **90 of 131
cells are `NO_VALUE`** and route to a default action. That default is a policy;
its deviation has nothing to do with the value function. `gate.calibrate.
split_by_cell_status()` exists for this and valued/unvalued must never be pooled
into a headline number.
