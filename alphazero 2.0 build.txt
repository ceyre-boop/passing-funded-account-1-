# AlphaZero Entry Layer — Off-Policy Self-Play Training Loop

**Status:** design complete, zero lines of code. Greenfield.
**Handoff target:** Claude Code, one gate at a time.
**Structural model:** same four-gate discipline that closed the Stockfish core.

---

## 0. Fixed decisions (do not relitigate in implementation)

These were settled in design. Claude Code should treat them as constraints, not options.

| Decision             | Setting                                                                                                                       | Why                                                                                                                                                            |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Episode boundary     | One trading day, open → close                                                                                                 | Natural terminal boundary, same role as game-end in chess. Also matches how P&L is actually judged: "what did I make today," not "what did that one trade do." |
| Learning agent       | AlphaZero only                                                                                                                | Single learner. Simplifies the loop and removes the co-adaptation problem.                                                                                     |
| Stockfish role       | **Frozen** deterministic grader                                                                                               | Pin to a named frozen checkpoint. Exit policy does not learn, does not drift, does not get tuned inside this loop.                                             |
| Reward signal        | Realized return of the episode after Stockfish's exit policy is applied to AlphaZero's entry                                  | The agent is graded on the outcome it actually causes, downstream policy included.                                                                             |
| Action               | Entry selection: asset, direction, timestamp                                                                                  | Everything after entry is out of the agent's hands by construction.                                                                                            |
| Prediction target    | **Magnitude, not direction**                                                                                                  | Governing finding: across ten event studies, magnitude is conditionable and direction is null every time. Entry conditioning inherits this.                    |
| Counterfactual scope | Restricted to alternate entry points **within the same historical day**, using only forward data already observed on that day | We do not simulate how the market would have reacted differently. We only ask which observed entry point Stockfish would have scored best.                     |

**The honest framing:** this is offline reinforcement learning over historical days, not chess-style self-play generating novel experience. The loop is real and buildable; the label "self-play" is doing analogical work, not literal work. Do not let the analogy smuggle in assumptions that only hold when the simulator equals the real game.

---

## Gate 1 — Entry state representation

**Deliverable:** a discretized, auditable state vector describing the market at a candidate entry moment.

Requirements:

- Cross-asset, not single-name. The whole point of this layer is universe-wide selection.
- Every feature must be computable from data available **at or before** the candidate timestamp. Enforce this mechanically, not by convention — a lookahead guard that fails loudly.
- Include the computed magnitude conditioner (realized vol, vol expansion) that is already backtestable on historical bars.
- **Exclude** the LLM magnitude read from the offline training lane entirely. All history predates the model cutoff, so any LLM-derived feature is contaminated by hindsight. It gets its own live-forward lane and accrues separately.
- Ship an occupancy audit like `state_space.py` did: what fraction of observed candidate moments land in valued cells, at each candidate granularity. Pick granularity on that number, not on intuition.

**Gate closes when:** occupancy audit passes at a pre-declared threshold, lookahead guard fires correctly under fault injection.

---

## Gate 2 — Candidate entry generation and legality masking

**Deliverable:** for any historical day, the complete set of legal candidate entries.

Requirements:

- Enumerate candidates across the universe on a fixed time grid. Declare the grid; do not let it float.
- **Legality mask, not a scoring penalty.** This is the lesson from the SINGLE_NAME / `trail_mult=None` finding on the Stockfish side: 65% of transitions had TIGHTEN as an _illegal_ move, not a weakly-supported one, and treating it as low-scoring rather than illegal corrupted the comparison. Same discipline here — entries that are structurally unavailable (no liquidity, no borrow, halted, outside session, insufficient forward path remaining in the day to be graded) are masked out at generation, never scored and discarded.
- Every candidate must carry enough remaining intraday forward path for Stockfish to grade it to a genuine close. Candidates too late in the day to be graded are illegal, not zero-reward.

**Gate closes when:** legality mask is fault-injection verified, and the illegal fraction is reported per day rather than silently absorbed.

---

## Gate 3 — Off-policy value estimation

**Deliverable:** an honest estimate of expected episode return for a candidate entry under the frozen exit policy.

This is the hard gate. Two components:

**3a. Grading.** For each legal candidate on each historical day, run the frozen Stockfish exit policy forward over the observed path and record realized return. This is exact, not estimated — the path already happened and is fully known after the fact. Output is a per-day table of (candidate, realized R).

**3b. Policy evaluation.** AlphaZero proposes a policy over candidates. Estimate what that policy would have earned. Start with the direct method — score the policy's chosen candidates against the graded table — and add importance weighting only if and when the behavior policy becomes non-uniform. Report effective sample size alongside every estimate; off-policy variance is the failure mode that looks like a result.

Leak discipline, carried over unchanged:

- Leave-one-episode-out enforced **at lookup**, not at construction.
- Purged K-fold.
- Pre-registered splits, hashed at import.
- Thin cells return NO_VALUE rather than a noisy point estimate.

**Gate closes when:** grading table reproduces bit-identically from a frozen commit, and the evaluator returns NO_VALUE rather than a number when support is thin.

---

## Gate 4 — Statistical gate

**Deliverable:** two-sided SPRT, same structure as the Stockfish gate.

- No candidate policy enters the main branch without a decisive SPRT accept against the frozen baseline.
- Two-sided calibration required: a null candidate (should not accept) **and** a deliberately degraded candidate (must decisively reject). A gate that only ever accepts is not a gate.
- **Standing rule applies:** every R comparison prints the incumbent's absolute R. If all arms are negative, the header says so — do not report the winner of a losing field.
- The sealed data half stays sealed. The C5_gap:high magnitude lead is pre-registered against it and gets exactly one unseal, with reason, rule version, and frozen commit recorded.

**Gate closes when:** both calibration arms resolve correctly and the frozen baseline is minted as AZ-FROZEN-001.

---

## Risks to watch, ranked

1. **Grader exploitation.** The agent will find entries that score well specifically because of quirks in the frozen exit policy, not because they are good entries. Chess self-play has the same failure against a weak simulator. Mitigation: hold out days, and sanity-check top-scoring entries by hand before believing them.
2. **Day count, not row count.** Statistical power here is in _days_, not observations. A few thousand rows across a handful of sessions is the same trap as 36 events across 2 NVDA sessions. Declare the day count up front and treat it as the real sample size.
3. **Off-policy variance masquerading as edge.** Effective sample size reported on every estimate, no exceptions.
4. **The negative-baseline problem is still open.** Exits on the current lane are exhausted and every arm was negative; no exit mechanism recovers a negative entry. This entire spec is the test of whether entry conditioning on magnitude produces a positive baseline. If Gate 4 comes back negative across the board, that is the answer, and the correct move is to say so rather than search for a better exit.

---

## Build order

Gate 1 → Gate 2 → Gate 3a → Gate 4 calibration → Gate 3b → Gate 4 live.

Rationale: get the grading table and a working statistical gate standing before any learning happens. If the graded table shows no positive candidates anywhere, the policy layer is moot and you have saved yourself the build.
