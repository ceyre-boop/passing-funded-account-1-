# The Observed−Predicted Roadmap

> Not this week. A sequenced roadmap with triggers, written 2026-08-22 while the
> findings were fresh. Supersedes this file's previous contents (the walk-forward
> deferral, committed at `f928aa8`; and the splash-loop plan at `9f3b014`).

## Context

The goal is a system whose trading can be studied scientifically: every decision
carries what the system *believed* at the moment it acted, so **observed − predicted**
can be computed, and stable terms, laws and definitions derived from the deltas rather
than asserted.

A proposal arrived to add a `predicted_world` block to `plan.json` with six fields.
Two recon passes established that **the predicted world is already built and running**
— it simply cannot be joined to outcomes.

### What already exists (do not rebuild any of this)

| surface | what it seals, before the outcome | file |
|---|---|---|
| `pre_registration` block | `expected_r_low`, `expected_r_high`, named `invalidation_predicates`; **refused for any non-ABSTAIN verdict without a band** | `alpha_operator.py:601-616` |
| `Forecast` | 5-way `scenario_probs` validated to sum to 1.0, `direction`, `horizon_min`, `confidence`, `evidence_ids` | `forecast.py:46-99` |
| grading | multi-class Brier vs a uniform baseline **on the identical cases**, binned calibration, directional accuracy, stale / false-urgent / missed-shock rates | `forecast.py:120-273` |
| anti-leak | `resolve()` structurally refuses a resolution timestamped before `as_of + horizon` | `forecast.py:182-187` |
| observed state | 6,893 hash-chained point-in-time `decision_point` rows, strictly `hist[index <= as_of]` | `decision_ledger.py:105,163-180` |
| evidence | sealed at judgment time; `reliability=None` means unrated, never a defaulted 0.5 | `evidence.py:74-115` |
| deltas | `realized_r` vs the sealed band → `r_in_band`; realized/oracle/mfe efficiency; predicted vs realized effect with automatic per-predictor shrinkage | `alpha_operator.py:1013-1066`, `exit_quality.py`, `mechanisms.py:95-136` |

### The three defects that actually block the science

1. **The join does not exist.** `records.jsonl.trade_id` is hardcoded `None`
   (`alpha_operator.py:859` and `:966`) — all 24 rows null. `decisions_*.jsonl`
   accepts `evidence_ids` (`execution_ledger.py:432`); `runner.py:646-670` never
   passes it — both rows `[]`. **The fields exist on both sides of the join and
   neither end is plugged in.** A belief cannot be connected to the trade it produced.

2. **The one working delta scores against a mutable file.** `alpha_operator.py:1027`
   reads `data/daytrade/plan.json` *from disk at resolution time* for the risk
   denominator. Its own docstring says the plan risk is "sealed at decision time." It
   is sealed nowhere. The mechanical writer overwrites `plan.json` once per session by
   design, so `realized_r` can be computed against geometry the model never saw.

3. **`policy_regret_r` is 0 of 13 and bricks a gate.** Added 2026-08-09 with a
   *consumer* (`forecast.py:324`) and no supplier anywhere in the repo. Because a gate
   with no data fails closed by design (`forecast.py:314-325`, an adversarial-review
   finding), AlphaZero **cannot be promoted** — not on merit, but because a field was
   added without a writer.

Defect 3 is the precedent governing everything below: **every field added here without
a live scoring path stayed empty indefinitely.** `policy_regret_r` 0/13,
`realized_r`/`r_in_band` 0/10, `signal_weights` 0 suppliers, `news_scorecard.hit` 1/10
over 16 days. Not one self-corrected.

### The proposal, adjudicated

| proposed field | ruling |
|---|---|
| `active_vix_gate` | **Rejected — category error.** `PAIR_VIX_GATES` is keyed on FX pairs (`EURUSD=X`…), has zero references in `daytrade/`, and is the in-sample-tuned parameter already logged as an exposure in `NEXT.md`. |
| `prob_win` | **Rejected — second source of truth.** `Forecast.scenario_probs` already carries a validated distribution and is what `brier()`/`calibration_error` score. A free-standing `prob_win` could disagree with the graded number and has no scorer, no baseline, no gate. |
| `trend_classification` | **Rejected — already exists three times.** `decision_ledger.regimes` runs on 6,893 rows with a 9-label vocabulary; also `regime_vector.trend_strength`; also the unbuilt `regime.py`. |
| `engine_version` | **Rejected — already exists five times.** `STRATEGY_VERSION`, `SOURCE_VERSION`, `CHECKPOINT_ID`, `model_version`, `prompt_version`. |
| `expected_mae` | **Accepted, as a registered mechanism.** Genuinely absent; exists only post-hoc as `regret.mae_r`. |
| `bars_to_tp1` | **Accepted, as a registered mechanism.** Nothing in the repo predicts duration. |

---

## Stage 0 — The join and the seal (no new fields)

**Trigger: after Monday's tick proves the loop runs unattended.** Nothing else starts
before that.

1. **Populate `trade_id`** — `alpha_operator.py:859,966`. Stamp the derivable
   `{symbol}-{ET date}` key at seal time. The record is append-only by design, so the
   operator cannot back-fill; it must stamp the key it can derive.
2. **Populate `evidence_ids`** — `runner.py:646-670`. Pass `evidence_ids=` through to
   `log_executed_trade()`; `execution_ledger.py:432` already accepts it.
3. **Seal the plan into the record.** Embed the plan geometry (or its hash plus
   `risk_per_share`) in the sealed record at `alpha_operator.py:955-981`, and make
   `_score_pre_registration` read *that* rather than `PLAN` from disk. A correctness
   fix, not a feature — the difference between a measurement and an anecdote.
4. **Add `forecast_id` to the ledger row** so the join is direct rather than
   reconstructed from symbol + date, which is many-to-one and lossy (24 records over
   ~7 days; `trade_id` is session-level and `runner.py:631-639` raises on a second
   same-day trade).

**Verification:** one replay produces a forecast row and a ledger row that join on
`trade_id`; `_score_pre_registration` returns the same `realized_r` after `plan.json`
is deliberately overwritten mid-flight — which today it would not.

## Stage 1 — Unbrick the promotion gate

**Trigger: immediately after Stage 0.** Either supply `policy_regret_r` from
`regret.py` — which already computes `counterfactual_delta_r` — or remove its gate and
record why. **Do not leave a consumer with no supplier.** Either way, pair every
remaining null with an explicit reason token from the repo's existing idiom set
(`unscoreable_reason`, `available: False`, `UNKNOWN`, `EMPTY_CHANNEL`, `NO_VERDICT`).

## Stage 2 — `expected_mae` and `bars_to_tp1`, as registered mechanisms

**Trigger: Stage 0 complete AND enough resolved forecasts to clear MDE.**

Decision 2026-08-22: the full `specs/029_MECHANISM_LEDGER.md` contract, not
descriptive-only. `daytrade/mechanisms.py` enforces it in code:

- **A causal claim, not a parameter** (`:36`; `mechanisms.py:391-394`). *"Trail 1.5
  beats 0.5" is refused.* Something like *"entries taken into compression resolve
  faster and take less heat, so predicted duration and predicted MAE are jointly
  informative about entry quality"* is a mechanism.
- **A transfer prediction naming where it must HELP, be NEUTRAL, and where it must
  HURT** (I28). No transfer prediction = refused. "Helps everywhere" is permanently
  tagged `unfalsifiable_shape`.
- **A numeric predicted effect and band, sealed on a clean tree before the first
  measurement** (I29, I31; `seals.py` refuses a dirty target).
- **A named predictor, with automatic shrinkage from their own history.** Empirical
  priors on record: reviewer +0.225 → −0.028; claude +0.67 → ≈0. **Expect a first
  proposal to shrink to near zero.**
- **The MDE gate.** At 39 independent days and 8.7 effective instruments, a shrunk
  effect below the minimum detectable effect is refused as `UNMEASURABLE` rather than
  run. **This may refuse the mechanism outright — that is a true answer, not a
  failure, and it states how much more tape the claim needs.**
- **A named automated test whose deliberate violation fails the suite**
  (`CLAUDE.md:103-108`), certified by someone other than the spec's author
  (`specs/031:3-4` — an agent never self-ratifies).

## Stage 3 — The exploration budget (spec 030, step 3)

**Trigger: Stages 0–2 complete. Design as `[UNRATIFIED]` first, ratify, then build.**

`specs/030_DECISION_LEDGER.md:8-10` prescribed three steps toward a *learning* system
rather than a mechanism-confirmation system: fix the observation channel, build the
immutable ledger, **then allocate a bounded exploration budget.** Steps 1 and 2 are
built. Step 3 appears nowhere else in the repo — no spec, no code, no bandit or
epsilon machinery.

This is what makes the rest an experiment rather than a record. A system that always
takes its best guess only ever confirms what it already believes; a bounded, capped
fraction of decisions spent on *informative* rather than *optimal* choices generates
the variation the deltas need.

Design it against this repo's own warnings: `EMPTY_CHANNEL` (MECH-006 saw zero
divergences and correctly reported *no evidence* rather than *no effect*), the
`measure.py` hygiene invariant (an instrument must not read its own exhaust), and spec
030's category boundary (backfilled rows are evidence for *what the market did*, never
for *what the system observed*).

---

## Explicitly out of scope

- **Any walk-forward rig.** Still deferred per `f928aa8`. Stage 0 is its actual
  prerequisite — WFA over unjoined predictions is meaningless.
- **`regime.py` / `trend_classification`.** Blocked on a human re-registration of spec
  008's rule, which is not evaluable (divides by Z, Z≈0). Note also that
  `specs/035_ONTOLOGY_STABILITY` records regime labels **flipping** between n=438 and
  n=674. The vocabulary is not yet stable enough to found definitions on — and that
  instability is the first real research question, not a bug to route around.
- **Any new regime vocabulary.** There are already three.

## Verification

The roadmap is working when, in order:

1. A forecast row and a ledger row for the same session **join on `trade_id`**, and
   the ledger row names the `forecast_id` that predicted it.
2. `_score_pre_registration` is provably immune to `plan.json` changing between
   decision and resolution, demonstrated by a test that overwrites it mid-flight.
3. No schema field is null without an accompanying reason token, and
   `policy_regret_r` either has a writer or no longer arms a gate.
4. A mechanism for `expected_mae`/`bars_to_tp1` is either sealed with a band and
   tested, or refused as `UNMEASURABLE` with the required n stated.
5. `MECHANISMS.json` gains its first entry that was pre-registered, measured, and
   **survived**. The ledger currently holds 3 killed, 3 proposed, 0 confirmed.

## The point, kept in view

The textbook is not written by adding fields. It is written by the deltas between what
was committed to in advance and what the tape actually did — and the repo already
knows this better than the proposal that prompted it (`specs/028:8-14`):

> *"You can't choose a verifier that's outside your judgment, but you can choose one
> that's outside your reach. Past-you, who didn't yet have a stake in the outcome,
> binds present-you, who does. Pre-commitment is the only honest external verifier a
> solo builder gets."*
