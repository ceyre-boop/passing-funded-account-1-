# State-space granularity — chosen before anything is fit on it

Recorded 2026-08-29, Phase 1 component 1 DoD. Population is the ratified build
lane: SPY premarket, `day <= splits.TUNE_END`, 2,619 sessions → **2,116 episodes**,
17,603 transitions. Reference config is the shipped `CASH_INDEX` policy
(`frozen_policy.POLICIES`, pinned in SF-FROZEN-002).

## The selection rule

**Finest granularity whose `frac_obs_valued` ≥ 0.85, at `min_paths = 30`.**

0.85 is the complement of the occupancy audit's own OK threshold
(`state_space.Occupancy.verdict`: OK at `frac_obs_in_thin ≤ 0.15`). 0.65 is the
MARGINAL-equivalent. The rule reads **coverage only** — no R, no outcome, no
config performance enters it.

## Measured

| granularity | states | cells | valued | frac_cells_valued | frac_obs_valued | verdict |
|---|---|---|---|---|---|---|
| minimal | 92 | 92 | 31 | 33.7% | **0.9097** | OK |
| **coarse_both** | 131 | 131 | 41 | 31.3% | **0.8905** | **OK — selected** |
| full | 357 | 357 | 46 | 12.9% | 0.7198 | MARGINAL |

**Selected: `COARSENINGS["coarse_both"]`** — the finest grid clearing 0.85.

## Disclosure

All three were run before the rule was written down. The rule is a function of
coverage alone and no outcome statistic was consulted, but the ordering is on the
record rather than implied: I saw the coverage table, then declared. Nothing has
been fit on the grid yet — the SPRT has not run and no candidate exists — so the
DoD's "recorded before anything is fit on it" is satisfied.

## Why this is not the earlier failure

The same sweep on the 336-episode basket gives `frac_obs_valued` **0.4613** with
**3 of 73 cells valued**. That is the thin-tablebase failure mode named in the
plan, and it is why the build lane is SPY and the basket is an apply-only lane
that is never re-fitted.

## Carried forward

- On the basket, **2,946 of 4,563 transitions (65%) have `r_if_tightened = None`**
  — every `SINGLE_NAME` episode, because that class ships `trail_mult=None` and
  Tighten is structurally inert. On SPY the count is **0**. The apply step must
  report this rather than average over it: two-thirds of the basket ships a
  two-action space.
- `min_paths = 30` is `state_space.audit`'s own default and is unchanged.
