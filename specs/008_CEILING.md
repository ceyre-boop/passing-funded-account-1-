# 008 — THE CEILING MEASUREMENT  `daytrade/ceiling.py`   `[SPEC]`  ← RUN BEFORE 001
**W − Z is the entire prize.** Measure it before building the thing that chases
it. ~100 lines on top of the bench (spec 005), and the highest-leverage hour in
the plan.

---

## Which engine does the prize belong to? — ALPHAZERO. But read the trap first.

**W − Z is the value of KNOWING what kind of day it is.** Knowing is
classification, classification is ALPHAZERO, so the prize is ALPHAZERO's prize
and this number is the go/no-go on the entire regime project.

**But it is denominated in STOCKFISH's action space.** The prize can only be
collected through actions the exit engine can actually take. Today that is one
variable with three settings (trail_mult 1.5 / 0.5 / None).

### Therefore a small W − Z is AMBIGUOUS, and the ambiguity is fatal if ignored
| finding | meaning | correct response |
|---|---|---|
| information has no value | knowing the regime genuinely doesn't change outcomes | kill the regime project |
| channel is too narrow | the knowledge is valuable, Stockfish has no way to express it | **build a different action space, not a different classifier** |

A perfect classifier handed three trail settings is a grandmaster allowed three
moves. Measuring the ceiling over only the shipped policies and reading a small
number as "regime isn't worth building" is a false negative that kills the right
project for the wrong reason.

### The fix: measure over a WIDER action space than we intend to ship
```python
# NARROW — the three policies as designed in spec 003
NARROW = ["STATIC", "TRAIL_WIDE", "TRAIL_TIGHT"]

# WIDE — every lever Stockfish COULD have. Deliberately larger than the ship set.
WIDE = grid(
    trail_mult   = [None, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0],
    be_arm_frac  = [0.25, 0.5, 1.0],        # how early breakeven arms
    partial_frac = [0.0, 0.25, 0.5, 0.75],  # how much gets banked at TP2
    flatten_et   = ["11:00", "12:00", "15:45", None],
    hold_past_tp2= [True, False],
)
```
Produces two ceilings instead of one:
- **W_wide − Z** — the true ceiling on regime knowledge, whatever the lever.
- **W_narrow − Z** — what is reachable with the policies as currently designed.

**W_wide big + W_narrow small is the most actionable outcome in the whole plan:**
the information is worth having and we are pointing it at the wrong lever. That
result redirects the build instead of ending it.

---

## The four (now six) numbers

```python
def policy_ceiling(sessions, action_space) -> dict:
    """For each session: replay the day's entry under EVERY action config.
    Then aggregate. No classifier involved anywhere — this is pure
    'what was available', measured before anything is built to capture it."""
    return {
      "always_static":     ...,   # X — one fixed policy, all days
      "always_trail_wide": ...,   # Y
      "best_fixed_policy": ...,   # Z — best SINGLE config applied to every day
      "oracle_narrow":     ...,   # W_narrow — best config per day, perfect hindsight
      "oracle_wide":       ...,   # W_wide  — same, over the wide grid
      "prize_narrow":      ...,   # W_narrow - Z
      "prize_wide":        ...,   # W_wide  - Z
      # WHERE the prize lives — this is what redirects the build
      "prize_by_time_block": {...},   # is it all in OPEN_DRIVE? then only classify there
      "prize_by_day_type":   {...},   # cluster days by realized behavior, not by our labels
      "oracle_policy_hist":  {...},   # which configs does the oracle actually pick?
      "n_sessions": ..., "entries_simulated": ...,
    }
```

### Reading the result — decided NOW, before the number exists
Pre-registering the decision rule is the point; deciding what counts as "big
enough" after seeing the number is how every honest measurement gets laundered
into a justification.

- **prize_wide < 10% of Z** → the information has little value at any lever.
  Regime project does not get built. Ship fixed `best_fixed_policy` and move on.
  This is a real, acceptable, money-saving outcome.
- **prize_wide 10-25%** → worth building, but ONLY where `prize_by_time_block`
  says the value is. Classify one or two blocks, not the whole session.
- **prize_wide > 25%** → build it fully, and `oracle_policy_hist` tells us which
  levers matter, which becomes spec 003's real policy table.
- **prize_wide >> prize_narrow (2x or more)** → redesign the action space FIRST.
  The classifier is fine; the exit engine's vocabulary is the bottleneck.

---

## Method — pessimistic, per the house convention
- Entries come from the actual sessions (recorded) plus, for the 60-day history
  where no real entry exists, a **mechanical entry rule stated up front** (spec
  THE_SHOT's OR-break) so the entry is identical across every policy. The
  comparison is exit-only; entries must never vary between arms.
- Costs at the pessimistic end: spread crossed, stops fill through, gaps at the
  open — same as EVAL_LAB.md.
- Report dollars AND R-multiples. Dollars are what Colin feels; R is what
  compares across days.
- **The oracle is not achievable.** It is an upper bound with perfect hindsight,
  stated as such in the output header every single time it prints. No component
  may ever cite the oracle as an expected result.

## Hard constraints
- Imports `decide_exit` / `apply_action`. Zero decision logic of its own.
- Runs on the **tune split only** (oldest 40 sessions — see below). The sealed 20
  are not touched by the ceiling measurement either. A ceiling measured on the
  holdout contaminates the holdout.
- No classifier import. This file must be runnable before `regime.py` exists.

---

## The 40/20 split — enforced in code, not in good intentions
The tuning-and-evaluating-on-the-same-60-sessions flaw is real and this is the
fix. It lives here because this is the first file that touches the sessions.

```python
# daytrade/splits.py  — single source of truth, imported everywhere
TUNE_END   = "<date of the 40th-oldest session>"   # written ONCE, committed, never edited
def tune_sessions():    return [s for s in all_sessions() if s.date <= TUNE_END]
def sealed_sessions():
    """RAISES unless caller passes unseal_reason= and rules are frozen
    (rule_version committed and unchanged in git for >= 1 commit)."""
```
Every tuning run, every eyeball table, every ceiling number: tune split only.
The sealed 20 produce exactly one number, once, after `rule_version` is frozen.
If the rules change afterward, the holdout is burned — a NEW holdout must be cut
from future sessions, and the old number is retired, not reused.

---

## `[SKETCH]` — after the ceiling is known
- **Clustering days by realized behavior instead of by our three labels.** If the
  oracle's policy choices cluster into groups that do NOT map onto
  continuation/manipulation/consolidation, that is enormously informative — it
  means the market's natural day-types differ from the doctrine's, and the
  classifier should target the former while the human keeps the latter for
  reading the tape. Needs the ceiling data first.
- **Ceiling on ENTRY timing** (what if entries also varied?). Out of scope on
  purpose: entry is Colin's, permanently, per doctrine. Measuring its ceiling
  invites automating it, and that is not what this system is for.
