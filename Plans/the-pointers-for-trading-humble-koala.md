# Spec 029 — The Mechanism Ledger

## Context

Parameter search is linear in data and decays under regime shift; a killed
mechanism removes hypothesis-space volume permanently and a confirmed one
compounds across everything traded. This repo has spent a weekend proving
the first half of that: six pre-registered rejections, every one a
parameter-level null, none of which left behind a reusable statement about
*why*. The ledger fixes that — every claim carries a causal reason and a
prediction about where else that reason must hold, and the predictor's own
optimism becomes a scored, decaying quantity.

**Premise correction from the survey (before anything is built):** there is
no "G2 thirty trades" in the live system. G2 is the carry lane's
reproduction-forensics gate — already GREEN, yields no trades. The
thirty-trade G2 lives only in `sovereign/propfirm/deployment_checklist.py`,
which is ICT-lane legacy that CLAUDE.md marks **never read**. The live
equivalent is **G5: 80 paper carry trades, currently 0/80, never started**
(`scripts/paper_carry_log.py`, `G5_MIN_N = 80`). Colin confirmed the carry
paper sprint is the intended stream. So: built out of **G5's eighty**.

**Ruling compliance (CLAUDE.md non-negotiable 1, "no new hypotheses get
generated here"):** 029 is discipline infrastructure over results this repo
already produced. It records mechanisms behind existing findings, demands
transfer predictions for tests already permitted here (exit-parameter sweeps
under 003/008), and scores predictors. When a confirmed mechanism implies a
genuinely NEW edge, the ledger emits it as a candidate **for the general
repo's ledger** — it is never tested here. Earned axes are limited to
tuning space for the already-proven edge.

## Deliverables

1. **`specs/029_MECHANISM_LEDGER.md`** — spec first: the ruling-compliance
   paragraph above, the entry contract, the pre-registered transfer test,
   the calibration rule, and the two evidence channels.

2. **`MECHANISMS.json`** (root, matching `SEALS.json` convention) — entries
   require, and are refused without:
   - `claim` — the causal reason, one sentence, mechanism not parameter
   - `transfer_prediction` — sign pattern over asset classes: where it must
     HELP, be NEUTRAL, and (best) where it must HURT. No transfer
     prediction = not a mechanism = refused.
   - `predicted_effect_r` + band, `predicted_by` — logged blind, sealed via
     `seals.py` before any test runs
   - `status`: proposed | confirmed | killed | narrowed
   - **`structural_vs_lever`** — HYP-059's best idea, promoted from ad-hoc
     prose to a required field: a structural fact can be true while the
     deployable lever is refuted (`carry_hypothesis_lineage.json:150,167`).
   - kills record the dead `axis × class × direction` + evidence

3. **`daytrade/mechanisms.py`** — `propose` (refuses missing transfer
   prediction / numeric band; seals it), `test` (runs the transfer test),
   `calibrate` (per-predictor curve → shrinkage factor), `axes` (live /
   dead / earned), `check` (ledger integrity, suite-enforced like seals).

4. **Transfer test** — statistical design finalized from the adversarial
   review now running. Fixed constraints inherited: paired per-entry deltas
   (config ON vs OFF) to remove the entry-quality confound; **permutation
   grouped WITHIN calendar day** (`specs/025:26-29` — same-day cross-symbol
   entries are nearly one bet; the ungrouped version is the exact error
   `specs/026:60-64` admits). Prior art to cite and generalize:
   `exit_evaluator.py:176-188`'s "≥2 of 3 classes including FUTURES".

5. **Calibration ledger** — the closest thing to a verifier Colin didn't
   select: predicted effect vs realized, per predictor, robust ratio →
   shrinkage applied to future proposals. A proposal whose *shrunk* effect
   no longer clears the minimum detectable effect is refused as not worth
   running. Seeds are real and already on the record: the reviewer's
   pre-registered 0.15–0.30 → −0.028 (`oracle_audit.py:28-31`), and my
   +0.67 "prize" → realizable ≈ 0. Both overconfident, both logged.

6. **Live tagging (the G5 link)** — `scripts/paper_carry_log.py` gains
   `--mechanisms MECH-00x,...` on `open`, carried into the existing
   `signal_layers` / decision-logger `extra`. Note honestly in the spec:
   the 80 carry trades are **one asset class**, so they supply
   *live-forward replication* evidence, not transfer evidence. Two channels,
   never conflated.

7. **Tests + mutation rows** (`daytrade/test_mechanisms.py`): refusal
   without transfer prediction, refusal without numeric band, dead-axis
   re-sweep refusal, shrinkage arithmetic, permutation grouping (a
   day-ungrouped null must fail a named test), ledger drift caught by the
   suite.

8. **`specs/README.md`** — add 029 and backfill the missing 025/026/027/028
   rows the survey found absent.

## Seed inventory (this weekend, honestly stated)

Killed: wide-trail tail capture (measured twice); futures early-protection
+ noon flatten (sealed read NOT_VALIDATED); AlphaZero interrupt timing (~0
in 11/12 cells); mechanical per-day config selection (−0.028 OOF).
Open: narrative-not-in-feature-columns for carry unwinds (027, evidence
live-forward only); veto-beats-rate-matched-random (soak).
Structural-vs-lever example to import: HYP-059's trailing exit.

## Reused

`daytrade/seals.py` (blind pre-registration + suite-enforced integrity),
`stockfish_tune.py` CLASSES/`slice_rows`/`class_report` (per-class slices at
zero sim cost), `oracle_audit.py` (permutation discipline + its documented
error), `forecast.py` calibration binning, `_append_jsonl` fsync writer.

## Out of scope

Generating new trading hypotheses here (general repo's job); changing any
existing seal, gate, or verdict; touching the ICT-lane legacy checklist;
retro-fitting `carry_hypothesis_lineage.json` (it stays as history — 029
links to its IDs, never rewrites them).

## Verification

1. `python3 daytrade/mechanisms.py check` green on the seeded ledger.
2. Refusals fire: propose without a transfer prediction, without a band, and
   re-sweep a dead axis → all refused with the reason printed.
3. Transfer test on a SEEDED KILLED mechanism reproduces its kill (the
   framework must re-derive a known negative before it is trusted on a
   positive).
4. Permutation guard: a deliberately day-ungrouped null fails its test.
5. `calibrate` reproduces the two seed data points and prints a shrinkage
   factor < 1.
6. Full suite green; explicit-path commit; mutation rows appended.
