# 042 — REGIME BUILD/NO-BUILD: RE-REGISTRATION DRAFT `[UNRATIFIED]`

**Status: DRAFT. Not in force. Colin ratifies or rejects; an agent never
self-ratifies a gate change.**
**Written:** 2026-08-27, after `ceiling._verdict()` returned `NO_VERDICT` on
`data/daytrade/ceiling_report.json` and before any replacement threshold was
picked or `daytrade/regime.py` was touched. That ordering is the point, same
discipline as `specs/031`: a gate written after seeing what it would yield is
not a gate.

---

## Why this document exists at all

`daytrade/ceiling.py` measured the value of regime knowledge on the tune
split (spec 008's method, unchanged) and printed:

```
VERDICT: NO_VERDICT — pre-registered rule is not evaluable
The rule thresholds on prize as a % of Z, but Z = +0.083 R/trade is
indistinguishable from zero, so the percentage (173%) is an artefact of the
denominator. Absolute figures: prize_wide +0.144 R/trade, prize_narrow
+0.052 R/trade, ratio 2.8x. Re-register a rule in R/trade BEFORE reading
these again.
```

`daytrade/regime.py` is a stub for exactly this reason — its own docstring
(lines 17-21) says the pre-registered rule is not evaluable and a human must
re-register a rule in R/trade before it gets built. `scorecard.py` and
`backtest.label_history` are both blocked behind it, and `stockfish_exit`'s
`thesis_sl` stop layer is explicitly `INACTIVE` waiting on it (comment at
`daytrade/stockfish_exit.py:236-238`: *"needs an invalidation level from the
classifier; daytrade/regime.py is a stub"*).

**Checked first, per this task's instruction: is the NO_VERDICT actually
correct, or was `ceiling.py` wrong to give up?** It is correct.
`pct_denominator_degenerate` is computed as
`abs(Z_total)/n_days < 0.15`, and the number it gates on is real:
`Z_wide = 2.0026 R` total over `n_entries = 24`, so
`Z_R_per_trade = 2.0026 / 24 = 0.0834` — below the 0.15 floor. Dividing
`prize_wide_R = 3.4559` by that near-zero `Z` is what produces
`prize_wide_pct = 172.6%`, a number that would read as "the prize is 1.7x the
baseline" when the baseline itself is statistical noise around zero. The
NO_VERDICT stands; this document is the re-registration `ceiling.py` asked
for, not a correction of it.

## What is NOT being questioned

- `daytrade/ceiling.py`'s method: the wide/narrow action-space split, the
  oracle-is-not-achievable discipline, the tune/sealed 40/20 split, the
  mechanical entry rule (OR-break, `THE_SHOT`), the pessimistic fill and
  bar-ordering logic. None of that is touched.
- The `TUNE_END` boundary (`daytrade/splits.py`, 2026-07-06) and the rule that
  the sealed 20 (now 36 on the extended cache — see below) are never read
  during tuning.
- `Z` and `W` themselves as computed — best-fixed-policy total and
  oracle-with-hindsight total. Only the **rule that reads them** is in
  question, not the numbers.
- `regime.py`'s contract (spec 001) — `classify() -> RegimeRead`, pure
  function, `rule_version` versioning, Colin's-eye-is-ground-truth
  definition of done. If a build is ratified, it builds to that contract
  unchanged.
- Spec 008's `prize_by_time_block` / `oracle_policy_hist` machinery, and its
  finding that the oracle reaches for `flatten_et` and `be_arm_frac`, almost
  never `trail_mult` — that redirection of which lever to classify survives
  regardless of how this document resolves.

## Why the original rule is not evaluable — the arithmetic

Source: `data/daytrade/ceiling_report.json`, produced by
`daytrade/ceiling.py` on the tune split (40 sessions, 24 valid entries,
2026-05-07 .. 2026-08-03 cache — this is `bars/`, not the newer
`bars_extended/` cache described below).

| quantity | value | field |
|---|---|---|
| `n_entries` (tune days with a valid OR-break) | 24 | `n_entries` |
| `Z_wide_R` (best fixed config, total) | +2.0026 R | `Z_wide` |
| `Z_wide_R_per_trade` | +0.0834 R/trade | `Z_wide / n_entries` |
| `W_wide_R` (oracle, total) | +5.4585 R | `W_wide` |
| `prize_wide_R` (`W - Z`, total) | +3.4559 R | `prize_wide_R` |
| `prize_wide_R_per_trade` | +0.1440 R/trade | `prize_wide_R_per_trade` |
| `prize_wide_pct` (`= 100 * prize/Z`) | +172.6% | `prize_wide_pct` |
| `pct_denominator_degenerate` | `true` (`0.0834 < 0.15`) | `pct_denominator_degenerate` |

The pre-registered rule (spec 008) reads `prize_wide_pct` against fixed bands
(`<10%`, `10-25%`, `>25%`). Spec 008's own bands were written assuming `Z`
would land as a meaningful positive P&L baseline — "the best fixed policy
makes real money, does the oracle make a lot more." Here `Z` per trade
(0.0834 R) is smaller than the wide grid's own noise floor: the random
config picker across the same 396 configs scores `random_picker_R_per_trade
= -2.2957/24 = -0.0957 R/trade`, and the spread between the best fixed
config and a coin-flip config selection is itself only ~0.18 R/trade. `Z` is
not distinguishable from "one arbitrary fixed policy among many mediocre
ones" — it is not a stable denominator. Dividing a real, if small, prize
(`+0.144 R/trade`) by a near-zero, noisy `Z` produces a percentage that moves
enormously for tiny changes in which config is "best fixed" — swap `Z` to
the second-best fixed config and the percentage changes by tens of points
while the prize itself barely moves. That is the definition of a degenerate
statistic, and `ceiling.py`'s own `pct_denominator_degenerate` flag catches
it correctly. **The absolute figures (`prize_wide_R_per_trade`,
`Z_R_per_trade`) are stable under that same perturbation; the percentage is
not.** That is the argument for re-registering in R/trade rather than % of Z,
independent of which threshold gets chosen.

## The population this re-registers against

Two caches exist and they are not the same population:

- `data/daytrade/bars/` — 60 NVDA sessions (2026-05-07..2026-08-03), 40 tune
  / 20 sealed. This is what `ceiling_report.json` above was computed on.
  `n_entries = 24`.
- `data/daytrade/bars_extended/` — NVDA 5m, 664 sessions total, `TUNE_END`
  unchanged at 2026-07-06, giving **402 tune entry days**
  (2024-01-02..2026-07-06) and 36 sealed sessions after it. This is what
  `daytrade/oracle_audit.py`'s extended run and `specs/037`'s trade/skip
  pre-registration both used (`data/daytrade/oracle_audit_nvda_extended.json`,
  `data/daytrade/trade_skip_result.json`).

**`ceiling.py` has not been re-run on `bars_extended/`.** The `n=24` numbers
above are what triggered the NO_VERDICT and are what this document
re-registers a replacement rule against, but any candidate below would, in
practice, be evaluated the next time someone re-runs `policy_ceiling()` on
the 402-day tune population — a much better-powered measurement than the
24-day one that exists today. The power analysis below uses the 402-day
population's measured dispersion because that is the population any future
ceiling re-run would actually use, and because using the sigma from an
n=24 sample would itself be a badly-powered estimate of the power
calculation's own input.

## Candidate replacement rules, in R/trade

Each reframes `_verdict()`'s decision without touching what `Z`/`W`/`prize`
compute — only what they get compared to and how.

### Candidate 1 — absolute R/trade tiers, same three-way structure as today

Replace the `%` bands with fixed R/trade bands, e.g. (illustrative numbers,
not proposed as final — see power section):

| tier | condition | consequence, stated honestly |
|---|---|---|
| NO_BUILD | `prize_wide_R_per_trade` below the floor | kills the regime project on this entry rule. Same "real, money-saving outcome" framing spec 008 already uses. |
| BUILD_TARGETED | floor ≤ prize < ceiling | build `regime.py` scoped to `prize_by_time_block`'s named blocks only (currently `OPEN_DRIVE`, `MORNING` on the n=24 run) |
| BUILD_FULLY | prize ≥ ceiling | build across the session, `oracle_policy_hist` becomes the real policy table |

**Consequence of a low floor** (e.g. 0.05 R/trade): the measured
`prize_wide_R_per_trade = 0.144` on n=24 would already clear it and license
a build today, on a sample this document's own power section says is too
small to trust. **Consequence of a high floor** (e.g. 0.20-0.30 R/trade,
roughly the exit-side effect size spec 008's sibling documents treat as
"genuinely rich" — see `daytrade/oracle_audit.py`'s on-the-hook prediction
of 0.15-0.30 as "realizable," >0.5 as "rich"): the measured 0.144 would NOT
clear it, and the project would not build even though the raw number is
positive. This tier is a straight port of the existing NO_BUILD /
BUILD_TARGETED / BUILD_FULLY shape and inherits its one honest weakness:
whatever floor gets picked is doing all the work, same as the original 10%
did, just now denominated in something that doesn't divide by ~0.

### Candidate 2 — percent of `W` (oracle) instead of percent of `Z`

`prize_wide_pct_of_W = 100 * prize_wide_R / W_wide_R`. On the n=24 numbers:
`3.4559 / 5.4585 = 63.3%` — the oracle earns roughly 2.7x the best fixed
policy's total. `W` is never near zero by construction (it is a max over
396 configs across 24 real days, several of which have double-digit R
swings), so this sidesteps the degenerate-denominator problem entirely.
**Consequence:** this changes what the number *means* — "how much of the
achievable ceiling is prize" rather than "how much better than the baseline
is the prize" — and spec 008's own bands (10/25%) were calibrated to the
latter framing, not this one. Re-using the same numeric bands under Candidate
2 would silently redefine the quantity being thresholded, which is exactly
the substitution-after-seeing-the-number problem spec 008's `_verdict()`
comment already refuses to do once. New bands would need their own
justification, not a reuse of the old ones.

### Candidate 3 — noise-floor relative, no fixed-policy denominator at all

Compare `prize_wide_R_per_trade` directly against the wide grid's own
no-skill floor — `random_picker_R_per_trade` (already computed:
`-0.0957` on n=24) — rather than against `Z`. E.g.: *build if
`prize_wide_R_per_trade` exceeds `random_picker_R_per_trade` by some margin,
with the margin set by the same rate-matched / shuffled-control discipline
`specs/037` already uses for the entry-side question* (`T3`/`T4` there).
**Consequence:** this is structurally the most defensible of the three
candidates, because it never divides by a quantity that can land near zero
— but it is also the most work: it requires building a null distribution
(permute config assignment across days, same move `oracle_audit.py`'s
`null_leak` already does for the exit-family question) rather than reading
one number off `ceiling_report.json`. It would need its own small
implementation before it is evaluable, unlike Candidates 1 and 2 which can
be read off the existing JSON today.

### Candidate 4 — do not re-register a build threshold; gate on entry-side edge first

Not a threshold at all: defer `regime.py` indefinitely, not because the
ceiling number is bad, but because two separate, better-powered experiments
on the *same* entry rule and the *same* extended population have already
returned negative (see the counter-argument section below). Under this
candidate the re-registration this document was asked to produce would be
withdrawn rather than filled in — the honest position is that there is
nothing to classify yet. This is listed as a candidate because it is a live
reading of the evidence, not because it resolves the NO_VERDICT the way the
task asked; see the final section for the full case.

## Power analysis

House convention, `daytrade/oracle_audit.py`'s sample-size block, reused
verbatim, not reinvented:

```
n_days = ((1.645 + 0.842) ** 2) * (sigma / delta) ** 2   # 95% one-sided, 80% power
```

**Sigma.** Per-day R stdev on the entry rule this document is about (NVDA
OR-break, `find_entry` in `ceiling.py`), measured on the 402-day extended
tune population: `0.9126` (`data/daytrade/oracle_audit_nvda_extended.json`,
`sample_size.per_day_sigma`) — corroborated by `specs/037`'s independently
computed `0.9137` on the same population via a different script
(`daytrade/oracle_audit.py`'s trade/skip baseline). These two measurements of
the same quantity agree to three significant figures; using `sigma ≈ 0.913`
below.

**Available population.** 402 tune entry days on `bars_extended/`. (The
`n=24` sample the current `ceiling_report.json` was measured on is far too
small to power any of this — it exists for the same reason spec 037 flags
its own predecessor's 39-day effective n as underpowered.)

| candidate `delta` (R/trade) | required `n` | powered on 402? |
|---|---|---|
| 0.05 | ≈2,044 | no |
| 0.10 | ≈512 | no |
| 0.113 (breakeven) | ≈402 | at the edge |
| 0.144 (the measured `prize_wide_R_per_trade` on n=24) | ≈247 | yes |
| 0.15 | ≈228 | yes |
| 0.20 | ≈128 | yes |
| 0.25 | ≈82 | yes |
| 0.30 | ≈57 | yes |

**Reading this table honestly cuts against the low end of Candidate 1.** A
floor at or below ~0.11 R/trade is not distinguishable from zero on the
population that exists even if `ceiling.py` were re-run on the full 402-day
cache today — the same shape of problem the original percent-of-Z rule had,
just moved from the denominator into the effect size itself. A floor at
0.15 R/trade or above is powered on the existing 402-day cache. This says
nothing about which floor is *correct*, only which floors are *measurable*
without collecting more entry days than exist right now.

**One asymmetry worth naming.** The 0.144 R/trade currently on record was
itself measured on n=24 — a population `daytrade/regime.py`'s own docstring
calls "far too few to trust any of this" (line 33). It is not yet known
whether that number replicates on the 402-day population; `ceiling.py` has
not been re-run there. Any threshold near 0.144 is therefore untested twice
over: once for whether the threshold itself is powered, and once for whether
the specific number it's being compared to survives a 17x larger sample.

## What this unblocks

`daytrade/regime.py` (stub, raises `NotImplementedError`, blocked on this
document per its own docstring) →
`daytrade/scorecard.py` (spec 004, "the reward pathway," cannot grade
`RegimeRead`s that don't exist) →
`daytrade/backtest.label_history` (needs `regime.py` to label history before
the scorecard can accumulate samples) →
`stockfish_exit`'s `thesis_sl` stop layer (`daytrade/stockfish_exit.py:236`,
currently `INACTIVE`, comment: *"needs an invalidation level from the
classifier; daytrade/regime.py is a stub"*) — this is the concrete downstream
consequence: until this chain moves, one whole stop-tightening layer in the
live exit engine sits dark, and `SALVAGE` (`stockfish_exit.NOT_AUTO_EMITTABLE`,
per `regime.py`'s docstring line 30) stays permanently unreachable because it
"waits on this file" by design.

## The honest counter-argument — do not build regime.py at all

The case for building, stated above, rests on a positive `prize_wide` number
on the OR-break entry rule. But that entry rule has since been tested twice,
independently, on the larger 402-day population, and both closed negative:

1. **`specs/037`'s trade/skip pre-registration** (`data/daytrade/
   trade_skip_result.json`) asked "given the OR-break fired, should the
   trade be taken at all" — a differently-shaped, better-powered question
   than the exit-ceiling one (`n=402`, T1_POWER passes at min_n=129). It
   failed on `T3_BEATS_RANDOM`, `T4_BEATS_NOISE`, and `T5_BEATS_ALWAYS_TRADE`
   — the model's OOF mean (`-0.0112 R/trade`) did not beat a rate-matched
   random skipper, a shuffled-feature control, or even always-trading.
   `verdict: "FAILED"`.
2. **The extended oracle audit** (`data/daytrade/
   oracle_audit_nvda_extended.json`) re-ran the exit-family question this
   document is about on the same 402-day population and got
   `null_leak_R = 1.5838`, ten times the `0.15` gate, `null_gate_pass:
   false`, `verdict: "NOTHING_QUOTABLE"`. The exit-side leak the n=24
   ceiling run measured as `+0.144 R/trade` does not survive the
   larger, better-powered re-run — the honest leak estimate on 402 days is
   negative (`honest_leak_R = -1.2021`, `realizable_leak_R = -0.0108`).

Read together: the two edge questions this lane can ask about the OR-break
entry — "should we take it" and "which exit family should we use if we do"
— have both come back negative on the population that actually has enough
days to answer them. The n=24 ceiling number that triggered this
re-registration is exactly the kind of small-sample positive result that a
402-day re-run should be expected to erode, and on the two adjacent
questions it already has.

**The strong case for Candidate 4 (do not build):** classifying "what kind
of day is this" is only valuable if something downstream can act on the
answer profitably. If neither taking the entry nor choosing among exit
families shows a demonstrated edge on the best-powered population available,
a regime classifier built on top of that entry has nothing to attach its
prize to — `regime.py` would be built to serve an exit-family choice that
the extended data says carries no signal. Building it now would be
building infrastructure for a channel two independent, pre-registered tests
already found empty. This is not a weaker reading than the case for
building; it may be the stronger one, and it should not be strawmanned by
comparison.

**The case against Candidate 4, stated with equal weight, is not merely
"but the number was positive once."** It has three real components:

1. **The n=24 ceiling and the two negative n=402 results are not the same
   claim.** The ceiling measures the value of PERFECT hindsight classification
   over the WIDE action space (396 configs, all six levers). The trade/skip
   test measures a specific gradient-boosted model's ability to predict R
   from ex-ante features. The oracle audit measures a depth-3 tree choosing
   among 6 pre-approved exit families from ex-ante features. A negative
   result on "can a small tree or booster extract this from these specific
   features" is evidence against those specific model classes on those
   specific features — it is not proof that no achievable classifier could
   ever do better, only that the two tried so far did not. The ceiling's own
   framing (spec 008) explicitly separates "is the information valuable"
   from "can the current tooling reach it" — the WIDE/NARROW split exists for
   this exact reason, and the same caution arguably applies to model class,
   not only to action space.
2. **The oracle audit's `NOTHING_QUOTABLE` and the ceiling's `NO_VERDICT` are
   different failures.** The oracle audit failed its OWN null gate — the
   measurement infrastructure could not distinguish signal from noise on
   this population, which is a statement about measurability, not
   necessarily about the underlying quantity being zero. It is the honest,
   pre-registered thing to report, but "unmeasurable with this method" is
   not identical to "confirmed zero."
3. **The downstream chain this document unblocks is not exit-family choice
   alone.** `thesis_sl` and `SALVAGE` are about invalidating a trade thesis
   mid-position — closer to spec 037's trade/skip question in spirit, but
   evaluated continuously rather than at entry. It has not itself been
   tested; its absence is inferred from two adjacent negative results, not
   measured directly.

Both readings are defensible on the evidence that exists. This document
takes no position on which one Colin should adopt — that is precisely what
Candidate 4 versus Candidates 1-3 is asking him to decide, and it is the
same kind of choice `specs/031`'s Reading A/B split asked for the carry
grading gate.

## Hard constraints observed while drafting this

- No threshold was picked. Candidates 1-3 give illustrative numbers only to
  make the power table concrete; none is proposed as final.
- `daytrade/regime.py`, `daytrade/ceiling.py`, `specs/008_CEILING.md`, and
  every number inside `data/daytrade/ceiling_report.json` are unmodified.
- `data/proof/` was not read or touched.
- The NO_VERDICT was checked against the raw JSON and confirmed correct, not
  assumed.

## Ratification

Unratified until Colin records a decision below and the file is re-sealed.
Rejecting this document is a complete and legitimate outcome — it means
`regime.py` stays a stub and the ceiling's NO_VERDICT stands until someone
re-registers again.

```
DECISION: [ ] adopt Candidate 1 (state floor/ceiling R/trade)
          [ ] adopt Candidate 2 (percent of W, state bands)
          [ ] adopt Candidate 3 (noise-floor relative, needs implementation)
          [ ] adopt Candidate 4 (do not build; regime.py stays blocked)
          [ ] adopt with amendments
          [ ] reject — re-register again later
BY:                        DATE:
REASON (required, written before any new ceiling run on bars_extended/):
```
