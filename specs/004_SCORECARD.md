# 004 — REGIME SCORECARD & STREAK  `daytrade/scorecard.py`, `daytrade/streak.py`   `[SPEC]`
**The reward pathway.** This is the most important file in the plan and the
reason the whole thing can eventually learn. Build it early even though it pays
off late — it needs months of samples, so every week it doesn't exist is a week
of data we never get back.

## Why this and not P&L
P&L is a terrible teacher: ~1 sample per day, dominated by variance, and it
conflates the entry read (Colin), the exit policy (Stockfish), and luck. Regime
accuracy is a clean signal at ~78 five-minute blocks per session — hundreds of
labeled samples per week, each one checkable against what the tape actually did.

There is no ELO here because the market has no rules and no win condition. This
scorecard is the closest honest substitute: a measurable, fast-accumulating
answer to "is the pattern recognition real, or am I flattering myself?"

## What "correct" means — define it BEFORE collecting, never after
Each `RegimeRead` gets graded against the NEXT K blocks (K=3, i.e. 15 minutes):

```python
def grade(read: RegimeRead, forward_bars) -> dict:
    """Was the call right? Definitions are fixed here and versioned. Changing
    a definition after seeing results is how a system learns to lie."""
    atr = read.evidence["atr5"]
    move = signed_extension(forward_bars, read.direction_of_flow)

    if read.regime == "CONTINUATION":
        # right if flow direction extended by >= 0.75 ATR without first
        # retracing more than 0.5 ATR against it
        hit = move >= 0.75*atr and max_adverse(forward_bars) < 0.5*atr

    elif read.regime == "MANIPULATION":
        # right if price REVERSED back through the swept level and held
        hit = reversed_through(read.evidence["swept"], forward_bars) \
              and held_for(forward_bars, bars=2)

    elif read.regime == "CONSOLIDATION":
        # right if total range over the window stayed under 1 ATR
        hit = total_range(forward_bars) < 1.0*atr

    return {"hit": hit, "regime": read.regime, "confidence": read.confidence,
            "rule_version": read.rule_version, "ts": read.ts,
            "forward_move_atr": move/atr, "evidence": read.evidence}
```

Append one row per block to `data/regime_scorecard.csv`. Never overwrite, never
backfill with a changed definition — bump `grade_version` instead.

## What gets reported
```python
def report(scorecard_csv, since=None) -> dict:
    return {
      "n": ..., "overall_accuracy": ...,
      "by_regime":     {"CONTINUATION": {...}, "MANIPULATION": {...}, "CONSOLIDATION": {...}},
      "by_time_block": {...},                    # where is the read strong/weak?
      "by_confidence_bucket": {...},             # CALIBRATION — see below
      "baseline_always_consolidation": ...,      # the dumb-model floor
      "baseline_time_prior_only": ...,           # priors with no live evidence
      "lift_over_baseline": ...,
    }
```

**The two baselines are non-negotiable.** Same discipline as the zero-edge
control in SANITY_AUDIT.md: an accuracy number means nothing without the score a
brainless model gets on the identical data. If the classifier can't beat "always
say consolidation" and "time priors only," it has learned nothing, and we say so.

**Calibration matters as much as accuracy.** When it says confidence 0.8, is it
right 80% of the time? A well-calibrated 60% classifier is more useful than an
overconfident 70% one, because the exit policy can trust the confidence number.

## Streak tracker  `daytrade/streak.py` — `data/streak.json`
Live campaign state, feeds the survival planner (spec 002) and the brief.
```python
@dataclass
class StreakState:
    green_streak: int; longest_streak: int; days_since_red: int
    rolling_20d_winrate: float; cumulative_R: float
    day_pnl: float; distance_to_goal: float
    consecutive_red_days: int              # 2 => cooloff, hard rule
    campaign_cushion: float                # eval drawdown room left
    cooloff_until: Optional[str]           # date; runner refuses to arm before it
```
`update(day_result)` at every session close. The 2-red-day cooloff is enforced
here and honored by the runner — it is not advice, and it is the single rule the
friction model showed the whole ladder depends on.

## Build notes
- Scorecard grading runs offline over history via the bench (spec 005) AND live
  at session close. Same function both ways — one implementation, always.
- No component may read the scorecard to change its own behavior in v1. It is a
  measurement instrument. Closing that loop automatically is spec 006's
  `[SKETCH]` section and needs a real planning pass first.

## `[SKETCH]` — deliberately unfinished, do not build yet
- **Feeding accuracy back into the classifier automatically** (the real
  walk-forward loop: refit `TIME_PRIORS` and rule weights on the scorecard,
  rolling window, strictly OOS evaluation, log the improvement curve). This is
  THE ALPHAZERO STEP. It is also the single easiest place in this whole system
  to fool ourselves. Prerequisites before it gets designed: ≥3 months of scored
  blocks, a stable `grade_version`, and a written protocol that pre-registers the
  train/test split. Until then, humans read the report and change rules by hand,
  in a commit, before the next session — never after a loss.
- **Per-regime R-multiple attribution** (does TRAIL_WIDE actually earn more on
  CONTINUATION days than STATIC would have?). Needs counterfactual replay: run
  the same day through all four exit policies and compare. Straightforward once
  the bench exists, but it is a spec of its own.
