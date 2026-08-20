# 029 — THE MECHANISM LEDGER `[SPEC]`

**Component:** `MECHANISMS.json` (root), `daytrade/mechanisms.py`,
`daytrade/test_mechanisms.py`, `--mechanisms` on `scripts/paper_carry_log.py`
**Status:** `[SPEC]` — written 2026-08-19, before the code.
**Depends on:** 028 (seals — blind pre-registration), 008/003 (the furnace
and its per-class slices), 025/026 (the permutation-null lesson), 021 (G5
paper sprint).

## Why

A weekend of pre-registered rejections produced six correct nulls and almost
no transferable knowledge. Each one killed a *parameter setting*; none left
behind a statement about **why** the market behaves that way, so nothing
compounds. Parameter search is linear in data and decays under regime shift.
A killed mechanism removes hypothesis-space volume permanently; a confirmed
one compounds across everything traded. The binding constraint is ~90
independent day-bets, so cross-sectional transfer is the only statistical
power calendar time will not sell.

## Ruling compliance (CLAUDE.md non-negotiable 1)

"No new hypotheses get generated here." 029 is **discipline infrastructure
over results this repo already produced**. It records the mechanism behind
findings that already exist, requires a transfer prediction for tests
already permitted here (exit-parameter sweeps under 003/008), scores
predictors, and retires dead space. When a confirmed mechanism implies a
genuinely NEW edge, the ledger **emits it as a candidate for the general
repo's ledger** and refuses to test it here. Earned axes are limited to
tuning space for the already-proven edge.

## Entry contract — refused without

| field | rule |
|---|---|
| `claim` | the causal reason, one sentence. "Trail 1.5 beats 0.5" is a parameter and is refused; "futures OR-breaks resolve fast or die, so protection earlier than TP1 pays" is a mechanism. |
| `transfer_prediction` | a sign pattern over asset classes — where it must HELP, where NEUTRAL, and (strongest) where it must HURT. **No transfer prediction = not a mechanism = refused.** A prediction naming only "helps everywhere" is accepted but flagged `unfalsifiable_shape`. |
| `predicted_effect_r` + `band` | numeric, logged BLIND, sealed via `seals.py` before any test runs |
| `predicted_by` | predictor identity — this is what the calibration ledger accumulates against |
| `structural_vs_lever` | promoted from `carry_hypothesis_lineage.json:150,167`'s ad-hoc prose: a structural fact can be TRUE while the deployable lever is REFUTED. Two independent status axes, never collapsed. |
| `status` | proposed \| confirmed \| killed \| narrowed |

Kills record the dead `axis × class × direction` plus evidence. `axes`
reports live / dead / earned; the furnace consults the dead list and refuses
to re-sweep retired space without a NEW mechanism claim.

## The transfer test (pre-registered)

For mechanism M with config A (ON) and B (OFF):

1. **Paired per-entry delta** `d_i = R_A(i) − R_B(i)` over the same entries.
   Pairing removes the entry-quality confound that dominated every earlier
   analysis — both arms see the identical entry, so only the mechanism
   differs.
2. **Contrast** `T = Σ_c w_c · mean(d_i : i ∈ class c)` with weights from the
   pre-registered sign pattern (+1 must-help, 0 neutral, −1 must-hurt).
3. **Null: permute class labels WITHIN calendar-day blocks**, preserving each
   entry's own `d_i`. Day-blocking is mandatory — same-day cross-symbol
   entries are nearly one bet (`specs/025:26-29`), and the ungrouped version
   is precisely the error admitted in `specs/026:60-64`. 1000 draws,
   one-sided p against the pre-registered direction.
4. **Verdict** — pre-registered, not chosen after: CONFIRMED requires
   p < 0.05 AND the realized sign pattern matching the prediction in every
   class that carried a non-zero weight. Sign match without significance =
   NARROWED. Significance with a wrong sign anywhere = KILLED (the mechanism
   as stated is false even if something real is present).

Prior art generalized here: `exit_evaluator.py:176-188`'s "clears the margin
in ≥2 of 3 classes including FUTURES" was the first breadth rule in the
repo; this replaces its hardcoded shape with a stated, testable prediction.

**Minimum detectable effect gate.** Before a test is permitted, the tool
computes the MDE from the observed per-day σ and the day-cluster count
(~90). A mechanism whose *shrunk* predicted effect (below) sits under the
MDE is refused as unmeasurable — running it could only produce noise
dressed as a result.

## The calibration ledger

Every `predicted_effect_r` is scored against its realized effect. Per
predictor, the robust ratio (median realized/predicted over that predictor's
resolved mechanisms) becomes a **shrinkage factor** applied to their future
proposals. The tool prints `predicted 0.20 → calibrated 0.03 (6 priors)` and
gates on the calibrated value.

This is the nearest thing to a verifier the builder did not select: past
predictions, scored by causality, discounting future ones. Seeds, both real
and already on the record:

| predictor | predicted | realized | ratio |
|---|---|---|---|
| reviewer (`oracle_audit.py:28-31`, pre-registered) | 0.15–0.30 R/trade | −0.028 | ≈ 0 |
| claude (the "+0.67 prize", `specs/026`) | +0.67 R/trade | ≈ 0 realizable | ≈ 0 |

Both overconfident; both logged; both shrink the next proposal.

## Two evidence channels, never conflated

- **Transfer evidence** — cross-class, from the furnace's per-class slices
  at zero extra simulation cost. For mechanisms about exits and structure.
- **Live-forward replication** — G5's 80 paper carry trades
  (`scripts/paper_carry_log.py`, currently 0/80) tagged with
  `--mechanisms`. These are **one asset class (FX carry)** and therefore
  supply replication, NOT transfer. The spec states this so no future
  session mistakes eighty correlated trades for cross-sectional power.

Note for the record: the "G2 thirty trades" framing that prompted this spec
does not exist in the live system — G2 is the carry lane's
reproduction-forensics gate (already GREEN, zero trades), and the
thirty-trade G2 lives only in `sovereign/propfirm/deployment_checklist.py`,
ICT-lane legacy that CLAUDE.md marks *never read*. The live stream is G5's
eighty.

## Invariants

- I28: a proposal without a transfer prediction is refused.
- I29: a proposal without a numeric predicted effect + band is refused.
- I30: the permutation null is day-blocked; an ungrouped null fails a named
  test.
- I31: a mechanism's predicted effect is sealed before its test runs.
- I32: a retired (dead) axis cannot be re-swept without a new mechanism.
- I33: shrinkage is applied from the predictor's own history, never chosen
  per-proposal.
- I34: structural status and lever status are independent fields; confirming
  one never sets the other.
- I35: ledger drift is caught by the suite (same guard shape as 028).

## Out of scope

New hypothesis generation (general repo); changing any existing seal, gate,
or verdict; the ICT-lane legacy checklist; rewriting
`carry_hypothesis_lineage.json` (029 links to its IDs, never edits them).
