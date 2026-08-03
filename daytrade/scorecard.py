#!/usr/bin/env python3
"""REGIME SCORECARD — spec 004. NOT BUILT. Blocked on regime.py (spec 001).

This is the reward pathway and the reason the system can eventually learn. It is
third in the build order despite paying off last, because it needs months of
samples and every week it does not exist is a week of labels never recovered.

WHAT IS MISSING:
  - `regime.classify` does not exist, so there are no RegimeReads to grade.

THE CONTRACT, ready to build the moment 001 lands (spec 004, verbatim):
    grade(read, forward_bars) -> dict     # K=3 blocks = 15 minutes forward
      CONTINUATION  hit if flow extended >= 0.75 ATR without first retracing 0.5 ATR
      MANIPULATION  hit if price reversed back through the swept level and held 2 bars
      CONSOLIDATION hit if total forward range stayed under 1.0 ATR
    report(csv) -> accuracy, by_regime, by_time_block, by_confidence_bucket,
                   baseline_always_consolidation, baseline_time_prior_only, lift

TWO DEFECTS IN THE SPEC TO FIX WHEN BUILDING — do not implement it as written:
  1. `evidence["swept"]` is a LIST. `reversed_through(swept, ...)` is undefined
     when two pools were swept, and the classifier can emit MANIPULATION from the
     wick-dominance or volume-spike rules with NO sweep at all, leaving it empty.
     Grading only the swept subset silently biases accuracy on a self-selected
     sample. Decide the multi-sweep rule and the no-sweep case explicitly.
  2. The three grade definitions are not equally hard — CONSOLIDATION's test is
     satisfied by most quiet windows, CONTINUATION's is strict. So overall
     accuracy mostly measures how often the classifier said CONSOLIDATION.
     Report PER-REGIME lift as the headline; treat overall accuracy as decoration.

Definitions are fixed BEFORE collection and versioned via `grade_version`.
Changing a definition after seeing results is how a system learns to lie.
"""
raise NotImplementedError("scorecard.py is not built — blocked on regime.py (spec 001).")
