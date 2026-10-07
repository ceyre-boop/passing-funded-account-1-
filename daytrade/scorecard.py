#!/usr/bin/env python3
"""REGIME SCORECARD — spec 004. The reward pathway.

P&L is a terrible teacher: one sample a day, dominated by variance, and it
conflates the entry read (Colin), the exit policy (Stockfish), and luck. Regime
accuracy is a clean signal at ~78 five-minute blocks a session — hundreds of
labelled samples a week, each one checkable against what the tape actually did.
There is no ELO here because the market has no rules and no win condition. This
file is the closest honest substitute: a measurable answer to "is the pattern
recognition real, or am I flattering myself?"

THE DEFINITIONS ARE FIXED BEFORE COLLECTION AND VERSIONED. `GRADE_VERSION` is
part of the definition set, and so is every threshold constant and the frozen
`TIME_PRIORS` copy below. Changing any of them means bumping the version and
starting a new file — never editing a definition in place, never backfilling.
Changing a definition after seeing results is how a system learns to lie.

WHAT IS PURE AND WHAT IS NOT. `grade()` and `report()` are pure: no network, no
clock reads, no file I/O, no imports of modules that touch any of those. The
caller supplies the time (`read.ts`) and the bars. All file I/O lives in
`append_rows()`, which is the only function here that knows a path exists. That
split is not tidiness: a grader that reads a clock cannot be replayed over
history, and spec 004 requires the SAME implementation to run offline over the
bench and live at session close.

NO LOOKAHEAD, CHECKED AT THE BOUNDARY. `grade()` sees forward blocks N+1..N+K
(K=3, 15 minutes) and must never let them touch the window being graded. Every
forward bar timestamp must be strictly after `read.ts`, and the check raises —
it is not an `assert`, because asserts vanish under `-O` and the repo runs
pytest with `-B`. The invariant must fail just as loudly in production as in the
suite.

NEVER A SILENT ZERO. Two distinct failure modes, deliberately handled
differently:
  - A MISSING SCALE IS A RAISE. `evidence["atr5"]` is the unit every threshold
    here is expressed in. Absent, non-finite, zero or negative, there is no
    scale at all — and a 0 would make `< 0.5*atr` and `< 1.0*atr` trivially
    false and `>= 0.75*atr` trivially true, i.e. it would invent answers. Raise.
  - A MISSING FACT IS AN UNGRADEABLE ROW, NOT A MISS. A row that cannot be
    graded gets `hit=None`, `gradeable=False` and a typed `reason`. Scoring it
    as a miss would be a fabricated label; dropping it silently would bias the
    sample. It is recorded, counted, and excluded from rates.
A malformed forward window (missing column, NaN in OHLC, duplicate or unsorted
timestamps) is a raise, never an assumption. A gap in the bars is a fact about
the data, and guessing past it produces a confident wrong number.

TWO DEFECTS IN SPEC 004, RULED ON HERE RATHER THAN IMPLEMENTED AS WRITTEN:

 1. `evidence["swept"]` IS A LIST AND MAY BE EMPTY. The classifier can emit
    MANIPULATION from the wick-dominance or volume-spike rules with no sweep at
    all, and spec 004's `reversed_through(swept, ...)` is undefined both then and
    when two pools were swept. Grading only the swept subset would silently
    measure accuracy on a self-selected sample.
    RULING: an empty `swept`, or a swept set whose levels are all unknown
    (`evidence["pools"]` missing the name, or holding None because the pool is
    genuinely unavailable), is UNGRADEABLE with a typed reason — never a miss,
    never a zero. With several swept pools whose levels ARE known, the GOVERNING
    level is the EXTREME one: the furthest in the direction of flow, i.e. the
    hardest to reclaim. Reclaiming the extreme implies reclaiming the rest, so
    the extreme is the only choice that does not make the test easier as the
    sweep list grows.

 2. THE THREE DEFINITIONS ARE NOT EQUALLY HARD. CONSOLIDATION's test is
    satisfied by most quiet windows; CONTINUATION's is strict. Overall accuracy
    therefore mostly measures how often the classifier said CONSOLIDATION.
    RULING: PER-REGIME LIFT IS THE HEADLINE. `report()` says so in its own
    output (`headline`, `overall_accuracy_is_decoration`) so a reader who sees
    only the dict cannot mistake the decoration for the result.

EVERY RATE SHIPS WITH ITS n, AND EVERY ACCURACY SHIPS BESIDE THE DUMB BASELINE
ON THE IDENTICAL ROWS. Same discipline as the zero-edge control in
SANITY_AUDIT.md: an accuracy number means nothing without the score a brainless
model gets on the same data. If the classifier cannot beat "always say
CONSOLIDATION" and "time priors only", it has learned nothing, and this file
says so. Computing the counterfactual baselines needs all three definitions
scored on every window, so `grade()` scores all three — not only the one the
read called — and that is why `hit_continuation` / `hit_manipulation` /
`hit_consolidation` are in every row.

CALIBRATION MATTERS AS MUCH AS ACCURACY. A well-calibrated 60% classifier is
more useful than an overconfident 70% one, because the exit policy can trust the
confidence number. `by_confidence_bucket` carries the gap per bucket, and
`brier` / `brier_skill_score` (consumed by spec 026 gate G-S6) score the
confidences as probabilities against the base rate. A BRIER SKILL SCORE OF 0
MEANS NO BETTER THAN THE BASE RATE — a classifier that always emits the base
rate scores exactly 0, and anything negative is worse than saying nothing.

NO COMPONENT MAY READ THE SCORECARD TO CHANGE ITS OWN BEHAVIOR IN v1. This is a
measurement instrument. Closing that loop automatically is spec 006's `[SKETCH]`
and needs a real planning pass first. Humans read the report and change rules by
hand, in a commit, before the next session — never after a loss.

`read` is a `RegimeRead` from `regime.py` (spec 001), which is not built yet.
This module is duck-typed against it and imports it only under TYPE_CHECKING, so
`import scorecard` succeeds on its own today and the scorecard can be built,
tested and reviewed before the classifier exists.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Sequence

import pandas as pd

if TYPE_CHECKING:                       # pragma: no cover - typing only
    from regime import RegimeRead       # noqa: F401  (regime.py, spec 001, unbuilt)


class ScorecardError(RuntimeError):
    """Malformed input, or a definition/version conflict. Always loud."""


# --- THE VERSIONED DEFINITION SET --------------------------------------------
# Everything in this block is part of `GRADE_VERSION`. A change to ANY of it —
# a threshold, K, the priors table, the governing-level rule — means a new
# version string and a new output file, not an edit in place. Rows graded under
# different versions must never be mixed in one rate.
GRADE_VERSION = "grade-v1"

FORWARD_BLOCKS = 3                      # K: 3 x 5m = the 15 minutes being judged

CONTINUATION_EXTENSION_ATR = 0.75       # must extend this far with the flow...
CONTINUATION_ADVERSE_ATR = 0.50         # ...without FIRST retracing this far against it
CONSOLIDATION_RANGE_ATR = 1.00          # total forward range must stay under this
MANIPULATION_HOLD_BARS = 2              # closes required on the reclaimed side

REGIMES: tuple[str, ...] = ("CONTINUATION", "MANIPULATION", "CONSOLIDATION")

OHLCV = ("Open", "High", "Low", "Close", "Volume")

# Frozen copy of spec 001's doctrine time-of-day priors, for the
# `baseline_time_prior_only` arm ONLY. It is a COPY on purpose: importing it
# from regime.py would let a later tune of the classifier's priors silently
# redefine this baseline and make today's number incomparable with next
# month's. Re-cut it deliberately, with a bumped GRADE_VERSION, or not at all.
# PREOPEN is `{...}` in spec 001 — genuinely unspecified. It stays None, and
# rows in it are EXCLUDED from this baseline with the exclusion counted, never
# assigned a regime and never scored zero.
TIME_PRIORS: dict[str, dict[str, float] | None] = {
    "PREOPEN":    None,
    "OPEN_DRIVE": {"CONTINUATION": 1.3, "MANIPULATION": 1.0, "CONSOLIDATION": 0.8},
    "MORNING":    {"CONTINUATION": 0.9, "MANIPULATION": 1.3, "CONSOLIDATION": 1.0},
    "MIDDAY":     {"CONTINUATION": 0.8, "MANIPULATION": 0.9, "CONSOLIDATION": 1.3},
    "AFTERNOON":  {"CONTINUATION": 1.1, "MANIPULATION": 1.1, "CONSOLIDATION": 0.9},
    "CLOSE":      {"CONTINUATION": 1.0, "MANIPULATION": 1.2, "CONSOLIDATION": 0.9},
}


def _prior_argmax(weights: dict[str, float] | None) -> str | None:
    """The regime a no-evidence time-prior model would name. None when the block
    has no published prior — the caller must exclude, not guess."""
    if weights is None:
        return None
    best = max(weights.values())
    winners = sorted(n for n, w in weights.items() if w == best)
    if len(winners) != 1:
        # A tie has no argmax. Refusing is the only honest answer; defaulting to
        # the alphabetically-first regime would be a coin flip wearing a prior's
        # clothes.
        return None
    return winners[0]


TIME_PRIOR_ARGMAX: dict[str, str | None] = {
    block: _prior_argmax(weights) for block, weights in TIME_PRIORS.items()
}

CONFIDENCE_BUCKETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0),
)


# --- typed reasons a row can be ungradeable ----------------------------------
# A reason string is part of the record. "ungradeable" with no reason is the
# same bug as a zero with no reason.
class Ungradeable:
    EMPTY_FORWARD_WINDOW = "EMPTY_FORWARD_WINDOW"
    SHORT_FORWARD_WINDOW = "SHORT_FORWARD_WINDOW"
    NO_FLOW_DIRECTION = "NO_FLOW_DIRECTION"
    NO_SWEPT_LEVEL = "NO_SWEPT_LEVEL"
    SWEPT_LEVEL_UNAVAILABLE = "SWEPT_LEVEL_UNAVAILABLE"
    UNKNOWN_REGIME = "UNKNOWN_REGIME"


NO_GRADEABLE_ROWS = "NO_GRADEABLE_ROWS"

ROW_FIELDS: tuple[str, ...] = (
    "ts", "symbol", "regime", "confidence", "time_block", "direction_of_flow",
    "exit_policy", "rule_version", "grade_version", "gradeable", "reason",
    "hit", "hit_continuation", "hit_manipulation", "hit_consolidation",
    "forward_move_atr", "atr5", "forward_bars", "forward_first_ts",
    "forward_last_ts", "evidence",
)


# --- helpers: each one is the definition, in one place ------------------------

def _require_direction(direction: int) -> int:
    if direction not in (1, -1):
        raise ScorecardError(
            f"direction_of_flow must be +1 or -1 to measure a signed move, got "
            f"{direction!r}; a flat read is UNGRADEABLE "
            f"({Ungradeable.NO_FLOW_DIRECTION}), never a zero move")
    return int(direction)


def signed_extension(forward_bars: pd.DataFrame, direction: int) -> float:
    """How far the tape travelled WITH the flow, in price, over the window.

    Measured from the window's first OPEN (the first price available after the
    read, so no part of the classification window is used) to the furthest
    favourable extreme: the highest High for +1 flow, the lowest Low for -1. A
    negative result is real and means the window never traded in the direction
    called — it is not clamped, because clamping it to 0 would erase exactly the
    worst calls.
    """
    direction = _require_direction(direction)
    open0 = float(forward_bars["Open"].iloc[0])
    if direction > 0:
        return float(forward_bars["High"].max()) - open0
    return open0 - float(forward_bars["Low"].min())


def max_adverse(forward_bars: pd.DataFrame, direction: int) -> float:
    """Worst excursion AGAINST the flow, in price, from the window's first open.

    Non-negative by construction: a window that never traded against the flow
    had an adverse excursion of zero, which is a measured zero and says so
    (`forward_bars` was present, the lows were above the open). Spec 004 writes
    `max_adverse(forward_bars)` with no direction; that signature is
    underspecified — "adverse" has no meaning without a side — so direction is
    explicit here.
    """
    direction = _require_direction(direction)
    open0 = float(forward_bars["Open"].iloc[0])
    if direction > 0:
        return max(0.0, open0 - float(forward_bars["Low"].min()))
    return max(0.0, float(forward_bars["High"].max()) - open0)


def total_range(forward_bars: pd.DataFrame) -> float:
    """Highest high minus lowest low over the window. Direction-free."""
    return float(forward_bars["High"].max()) - float(forward_bars["Low"].min())


def reversed_through(level: float, forward_bars: pd.DataFrame, direction: int) -> bool:
    """Did price CLOSE back through the swept level, against the flow?

    The sweep ran through `level` in the direction of flow, so a reversal is a
    close on the far side: below the level for +1 flow, above it for -1. Closes,
    not wicks — a wick back through a level is the sweep continuing, which is the
    very thing MANIPULATION claims is over.
    """
    direction = _require_direction(direction)
    closes = forward_bars["Close"].astype(float)
    if direction > 0:
        return bool((closes < level).any())
    return bool((closes > level).any())


def held_for(forward_bars: pd.DataFrame, level: float, direction: int,
             bars: int = MANIPULATION_HOLD_BARS) -> bool:
    """Did the reclaim HOLD for `bars` consecutive closes on the reversed side?

    Consecutive, not cumulative: two separate single-bar pokes back through the
    level are a chop, not a reversal, and spec 004's "reversed back through the
    swept level and held" is a claim about persistence. Spec 004 writes
    `held_for(forward_bars, bars=2)`; the level and side are required to know
    what is being held, so they are explicit here.
    """
    direction = _require_direction(direction)
    if bars < 1:
        raise ScorecardError(f"held_for needs bars >= 1, got {bars!r}")
    run = 0
    for close in forward_bars["Close"].astype(float):
        on_reversed_side = close < level if direction > 0 else close > level
        run = run + 1 if on_reversed_side else 0
        if run >= bars:
            return True
    return False


def governing_swept_level(evidence: dict[str, Any], direction: int,
                          strict: bool = True) -> tuple[float | None, str | None]:
    """The level a MANIPULATION read must be judged against, or (None, reason).

    Ruling on spec defect 1 (see the module docstring): with several swept pools
    the governing level is the EXTREME one — the furthest in the direction of
    flow — because reclaiming it implies reclaiming the others, so the test does
    not get easier as the sweep list grows. Pools whose level is None are
    genuinely unavailable and are skipped, never read as 0.0: a pool at price
    zero is not a thing, and treating it as one would hand every reversal a
    free hit.

    `strict` is True when the READ ITSELF claimed MANIPULATION: then a missing
    `swept` key is a broken read and raises, because its own claim cannot be
    checked. It is False for the COUNTERFACTUAL score of MANIPULATION on a read
    that called something else — a CONTINUATION read is not malformed for
    lacking sweep bookkeeping, so that counterfactual is simply unavailable and
    the row says so rather than refusing to grade the call that was made.
    """
    direction = _require_direction(direction)
    if "swept" not in evidence:
        if not strict:
            return None, Ungradeable.SWEPT_LEVEL_UNAVAILABLE
        raise ScorecardError(
            "evidence['swept'] is missing; the classifier contract (spec 001) "
            "guarantees the key, and inferring an empty sweep from its absence "
            "would turn a broken read into a quietly graded one")
    swept = evidence["swept"]
    if not isinstance(swept, (list, tuple)):
        raise ScorecardError(
            f"evidence['swept'] must be a list of pool names, got {type(swept).__name__}")
    if len(swept) == 0:
        return None, Ungradeable.NO_SWEPT_LEVEL

    pools = evidence.get("pools")
    if pools is None:
        return None, Ungradeable.SWEPT_LEVEL_UNAVAILABLE
    if not isinstance(pools, dict):
        raise ScorecardError(
            f"evidence['pools'] must be a dict of name -> level, got "
            f"{type(pools).__name__}")

    levels: list[float] = []
    for name in swept:
        level = pools.get(name)
        if level is None:
            continue                     # genuinely unavailable: skip, never 0.0
        value = float(level)
        if not math.isfinite(value):
            raise ScorecardError(
                f"pool {name!r} has non-finite level {level!r}; a level that is "
                f"not a price cannot be reclaimed or not-reclaimed")
        levels.append(value)

    if not levels:
        return None, Ungradeable.SWEPT_LEVEL_UNAVAILABLE
    return (max(levels) if direction > 0 else min(levels)), None


# --- validation ---------------------------------------------------------------

def _require_atr(evidence: dict[str, Any]) -> float:
    """atr5 or nothing. See the module docstring: this is a raise, not a zero."""
    if not isinstance(evidence, dict):
        raise ScorecardError(
            f"read.evidence must be a dict, got {type(evidence).__name__}")
    if "atr5" not in evidence:
        raise ScorecardError(
            "evidence['atr5'] is missing; every grade threshold is expressed in "
            "ATR, so without it there is no scale and nothing can be graded. "
            "Refusing rather than scoring 0.")
    raw = evidence["atr5"]
    if raw is None or isinstance(raw, bool):
        raise ScorecardError(f"evidence['atr5'] is not a number: {raw!r}")
    try:
        atr = float(raw)
    except (TypeError, ValueError) as exc:
        raise ScorecardError(f"evidence['atr5'] is not a number: {raw!r}") from exc
    if not math.isfinite(atr):
        raise ScorecardError(f"evidence['atr5'] is not finite: {raw!r}")
    if atr <= 0.0:
        raise ScorecardError(
            f"evidence['atr5'] must be > 0, got {atr!r}; a zero or negative ATR "
            f"makes '< 0.5*atr' impossible and '>= 0.75*atr' automatic, i.e. it "
            f"invents answers")
    return atr


def _validate_forward_bars(forward_bars: Any) -> pd.DataFrame:
    """Structure only. Malformed is a raise; empty/short is the caller's
    ungradeable decision, because an absent window is a fact, not a defect."""
    if not isinstance(forward_bars, pd.DataFrame):
        raise ScorecardError(
            f"forward_bars must be a DataFrame of 5m bars, got "
            f"{type(forward_bars).__name__}")
    missing = [c for c in OHLCV if c not in forward_bars.columns]
    if missing:
        raise ScorecardError(f"forward_bars is missing column(s) {missing}")
    if len(forward_bars) == 0:
        return forward_bars
    if not isinstance(forward_bars.index, pd.DatetimeIndex):
        raise ScorecardError(
            f"forward_bars needs a DatetimeIndex to prove it sits after the "
            f"read, got {type(forward_bars.index).__name__}")
    if forward_bars.index.has_duplicates:
        raise ScorecardError(
            "forward_bars has duplicate timestamps; one block graded twice is "
            "a fabricated sample")
    if not forward_bars.index.is_monotonic_increasing:
        raise ScorecardError(
            "forward_bars is not sorted ascending; 'first retraced' and 'held "
            "for 2 bars' are statements about order")
    for col in ("Open", "High", "Low", "Close"):
        values = pd.to_numeric(forward_bars[col], errors="coerce").astype(float)
        if not bool(values.notna().all()) or not bool(values.map(math.isfinite).all()):
            raise ScorecardError(
                f"forward_bars['{col}'] holds a missing or non-finite value; a "
                f"gap is a raise here, never an assumption")
    return forward_bars


def _assert_no_lookahead(read_ts: str, forward_bars: pd.DataFrame) -> None:
    """The boundary invariant: the forward window must not overlap the window
    being classified. Every forward timestamp strictly after `read.ts`.

    A raise, not an `assert`: asserts disappear under `python -O`, and this
    invariant is the one thing that separates a scorecard from a fantasy.
    """
    try:
        ts = pd.Timestamp(read_ts)
    except (TypeError, ValueError) as exc:
        raise ScorecardError(f"read.ts is not a timestamp: {read_ts!r}") from exc
    if pd.isna(ts):
        raise ScorecardError(f"read.ts is not a timestamp: {read_ts!r}")

    index = forward_bars.index
    if ts.tzinfo is None and index.tz is not None:
        ts = ts.tz_localize(index.tz)
    elif ts.tzinfo is not None and index.tz is None:
        ts = ts.tz_localize(None)

    first = index.min()
    if first <= ts:
        raise ScorecardError(
            f"LOOKAHEAD: forward window starts at {first} which is not strictly "
            f"after the read at {ts}. The forward bars must cover blocks "
            f"N+1..N+{FORWARD_BLOCKS} only — a window that includes the block "
            f"being graded scores the classifier on its own input.")


# --- grading ------------------------------------------------------------------

def _row(read: "RegimeRead", *, atr: float | None, gradeable: bool,
         reason: str | None, hit: bool | None, per_regime: dict[str, bool | None],
         forward_move_atr: float | None, forward_bars: pd.DataFrame) -> dict[str, Any]:
    index = forward_bars.index
    return {
        "ts": read.ts,
        "symbol": read.symbol,
        "regime": read.regime,
        "confidence": read.confidence,
        "time_block": read.time_block,
        "direction_of_flow": read.direction_of_flow,
        "exit_policy": getattr(read, "exit_policy", None),
        "rule_version": read.rule_version,
        "grade_version": GRADE_VERSION,
        "gradeable": gradeable,
        "reason": reason,
        "hit": hit,
        "hit_continuation": per_regime.get("CONTINUATION"),
        "hit_manipulation": per_regime.get("MANIPULATION"),
        "hit_consolidation": per_regime.get("CONSOLIDATION"),
        "forward_move_atr": forward_move_atr,
        "atr5": atr,
        "forward_bars": int(len(forward_bars)),
        "forward_first_ts": (None if len(index) == 0 else str(index.min())),
        "forward_last_ts": (None if len(index) == 0 else str(index.max())),
        "evidence": read.evidence,
    }


def grade(read: "RegimeRead", forward_bars: pd.DataFrame) -> dict[str, Any]:
    """Was the call right? PURE: no I/O, no clock, no network.

    Definitions are fixed here and versioned (`GRADE_VERSION`). All three are
    scored on the same window — not only the one the read named — because the
    counterfactual baselines in `report()` are not computable otherwise.

    Returns one row. `hit` is None exactly when `gradeable` is False, and then
    `reason` names why in a typed string. An ungradeable row is NOT a miss.
    """
    if read.regime not in REGIMES:
        raise ScorecardError(
            f"unknown regime {read.regime!r}; grading an unrecognised label "
            f"would invent a definition. Known: {list(REGIMES)}")

    atr = _require_atr(read.evidence)            # raises before anything else is read
    bars = _validate_forward_bars(forward_bars)

    if len(bars) == 0:
        return _row(read, atr=atr, gradeable=False,
                    reason=Ungradeable.EMPTY_FORWARD_WINDOW, hit=None,
                    per_regime={}, forward_move_atr=None, forward_bars=bars)

    _assert_no_lookahead(read.ts, bars)

    if len(bars) < FORWARD_BLOCKS:
        return _row(read, atr=atr, gradeable=False,
                    reason=Ungradeable.SHORT_FORWARD_WINDOW, hit=None,
                    per_regime={}, forward_move_atr=None, forward_bars=bars)

    direction = read.direction_of_flow
    has_direction = direction in (1, -1)

    per_regime: dict[str, bool | None] = {}
    reasons: dict[str, str] = {}

    # CONSOLIDATION — direction-free, always computable once atr5 and bars exist.
    per_regime["CONSOLIDATION"] = total_range(bars) < CONSOLIDATION_RANGE_ATR * atr

    # CONTINUATION — "extended >= 0.75 ATR without FIRST retracing 0.5 ATR
    # against it". Walked in order, adverse extreme counted before the
    # favourable one inside each bar: the same pessimistic intrabar convention
    # ceiling.simulate() uses, because 5m bars do not say which came first and
    # assuming the good one is how backtests lie.
    forward_move_atr: float | None = None
    if not has_direction:
        per_regime["CONTINUATION"] = None
        reasons["CONTINUATION"] = Ungradeable.NO_FLOW_DIRECTION
    else:
        forward_move_atr = signed_extension(bars, direction) / atr
        open0 = float(bars["Open"].iloc[0])
        need_ext = CONTINUATION_EXTENSION_ATR * atr
        allow_adv = CONTINUATION_ADVERSE_ATR * atr
        worst_adverse_so_far = 0.0
        hit_cont = False
        for _, bar in bars.iterrows():
            high, low = float(bar["High"]), float(bar["Low"])
            if direction > 0:
                adverse, favour = open0 - low, high - open0
            else:
                adverse, favour = high - open0, open0 - low
            worst_adverse_so_far = max(worst_adverse_so_far, adverse, 0.0)
            if favour >= need_ext:
                hit_cont = worst_adverse_so_far < allow_adv
                break
        per_regime["CONTINUATION"] = hit_cont

    # MANIPULATION — reversed back through the governing swept level and held.
    if not has_direction:
        per_regime["MANIPULATION"] = None
        reasons["MANIPULATION"] = Ungradeable.NO_FLOW_DIRECTION
    else:
        level, why = governing_swept_level(
            read.evidence, direction, strict=(read.regime == "MANIPULATION"))
        if level is None:
            per_regime["MANIPULATION"] = None
            reasons["MANIPULATION"] = why or Ungradeable.SWEPT_LEVEL_UNAVAILABLE
        else:
            per_regime["MANIPULATION"] = (
                reversed_through(level, bars, direction)
                and held_for(bars, level, direction, bars=MANIPULATION_HOLD_BARS))

    hit = per_regime[read.regime]
    return _row(read, atr=atr, gradeable=hit is not None,
                reason=(None if hit is not None else reasons.get(read.regime)),
                hit=hit, per_regime=per_regime,
                forward_move_atr=forward_move_atr, forward_bars=bars)


# --- reporting ----------------------------------------------------------------

def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _rate(hits: Sequence[bool | None]) -> dict[str, Any]:
    """A rate with its n attached, always. n=0 reports None and says why —
    a 0.0 accuracy on nothing is indistinguishable from a 0.0 on a hundred
    misses, and this repo does not ship numbers that cannot be told apart."""
    scored = [h for h in hits if h is not None]
    if not scored:
        return {"n": 0, "accuracy": None, "reason": NO_GRADEABLE_ROWS}
    return {"n": len(scored), "accuracy": _mean([1.0 if h else 0.0 for h in scored])}


def _lift(accuracy: float | None, baseline: float | None) -> float | None:
    if accuracy is None or baseline is None:
        return None
    return accuracy - baseline


def _confidence(row: dict[str, Any]) -> float:
    raw = row.get("confidence")
    if raw is None or isinstance(raw, bool):
        raise ScorecardError(f"row {row.get('ts')!r} has no usable confidence: {raw!r}")
    value = float(raw)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ScorecardError(
            f"row {row.get('ts')!r} has confidence {value!r} outside [0,1]; it is "
            f"scored as a probability by brier/brier_skill_score")
    return value


def report(rows: Iterable[dict[str, Any]], since: str | None = None) -> dict[str, Any]:
    """Score a collection of graded rows. PURE: takes ROWS, never a path.

    Reads no file so that the bench and the live session-close path share one
    implementation (spec 004 build note: "same function both ways").

    PER-REGIME LIFT IS THE HEADLINE. Overall accuracy is decoration and is
    labelled as such in the returned dict, because the three definitions are not
    equally hard and overall accuracy mostly measures how often the classifier
    said CONSOLIDATION (spec defect 2, module docstring).
    """
    all_rows = list(rows)
    versions = {r.get("grade_version") for r in all_rows}
    if len(versions) > 1:
        raise ScorecardError(
            f"rows mix grade versions {sorted(map(str, versions))}; definitions "
            f"are versioned precisely so they are never averaged together")

    if since is not None:
        # Parsed, not string-compared: ISO strings only sort correctly when every
        # row carries the identical offset and precision, which nothing enforces.
        cutoff = pd.Timestamp(since)
        if pd.isna(cutoff):
            raise ScorecardError(f"since is not a timestamp: {since!r}")
        kept = []
        for r in all_rows:
            ts = pd.Timestamp(r["ts"])
            if pd.isna(ts):
                raise ScorecardError(f"row has unparseable ts {r['ts']!r}")
            if ts.tzinfo is None and cutoff.tzinfo is not None:
                ts = ts.tz_localize(cutoff.tzinfo)
            elif ts.tzinfo is not None and cutoff.tzinfo is None:
                ts = ts.tz_localize(None)
            if ts >= cutoff:
                kept.append(r)
        all_rows = kept

    scored = [r for r in all_rows if r.get("hit") is not None]
    ungradeable = len(all_rows) - len(scored)
    headline = ("per-regime lift is the headline; overall_accuracy is decoration "
                "because the three definitions are not equally hard")

    base: dict[str, Any] = {
        "grade_version": (versions.pop() if len(versions) == 1 else None),
        "since": since,
        "rows_in": len(all_rows),
        "ungradeable": ungradeable,
        "headline": headline,
        "overall_accuracy_is_decoration": True,
    }

    if not scored:
        return {
            **base,
            "n": 0,
            "overall_accuracy": None,
            "by_regime": {},
            "by_time_block": {},
            "by_confidence_bucket": {},
            "baseline_always_consolidation": {"n": 0, "accuracy": None,
                                              "reason": NO_GRADEABLE_ROWS},
            "baseline_time_prior_only": {"n": 0, "accuracy": None,
                                         "reason": NO_GRADEABLE_ROWS,
                                         "excluded_unknown_block": 0,
                                         "excluded_prediction_ungradeable": 0},
            "lift_over_baseline": None,
            "brier": None,
            "brier_skill_score": None,
            "reason": NO_GRADEABLE_ROWS,
        }

    hits = [bool(r["hit"]) for r in scored]
    overall = _mean([1.0 if h else 0.0 for h in hits])

    # --- baseline 1: always say CONSOLIDATION, on the IDENTICAL rows ----------
    always_consol = _rate([r.get("hit_consolidation") for r in scored])

    # --- baseline 2: time priors only, no live evidence -----------------------
    prior_hits: list[bool] = []
    excluded_block = 0
    excluded_pred = 0
    for r in scored:
        predicted = TIME_PRIOR_ARGMAX.get(r.get("time_block"))
        if predicted is None:
            excluded_block += 1          # PREOPEN/unknown: excluded and COUNTED
            continue
        outcome = r.get(f"hit_{predicted.lower()}")
        if outcome is None:
            excluded_pred += 1           # the prior's own call was ungradeable here
            continue
        prior_hits.append(bool(outcome))
    prior_rate = _rate(prior_hits)
    baseline_time_prior = {
        **prior_rate,
        "excluded_unknown_block": excluded_block,
        "excluded_prediction_ungradeable": excluded_pred,
        "prediction_by_block": dict(TIME_PRIOR_ARGMAX),
    }

    # --- by regime: the headline, each arm beside its own baseline ------------
    by_regime: dict[str, Any] = {}
    for regime in REGIMES:
        arm = [r for r in scored if r.get("regime") == regime]
        if not arm:
            by_regime[regime] = {"n": 0, "accuracy": None, "reason": NO_GRADEABLE_ROWS}
            continue
        arm_rate = _rate([r["hit"] for r in arm])
        arm_consol = _rate([r.get("hit_consolidation") for r in arm])
        arm_prior: list[bool] = []
        for r in arm:
            predicted = TIME_PRIOR_ARGMAX.get(r.get("time_block"))
            if predicted is None:
                continue
            outcome = r.get(f"hit_{predicted.lower()}")
            if outcome is not None:
                arm_prior.append(bool(outcome))
        arm_prior_rate = _rate(arm_prior)
        by_regime[regime] = {
            **arm_rate,
            "baseline_always_consolidation": arm_consol,
            "baseline_time_prior_only": arm_prior_rate,
            "lift_over_always_consolidation": _lift(arm_rate["accuracy"],
                                                    arm_consol["accuracy"]),
            "lift_over_time_prior_only": _lift(arm_rate["accuracy"],
                                               arm_prior_rate["accuracy"]),
            "mean_confidence": _mean([_confidence(r) for r in arm]),
        }

    # --- by time block --------------------------------------------------------
    by_time_block: dict[str, Any] = {}
    for block in sorted({str(r.get("time_block")) for r in scored}):
        arm = [r for r in scored if str(r.get("time_block")) == block]
        arm_rate = _rate([r["hit"] for r in arm])
        arm_consol = _rate([r.get("hit_consolidation") for r in arm])
        predicted = TIME_PRIOR_ARGMAX.get(block)
        if predicted is None:
            prior_arm = {"n": 0, "accuracy": None, "reason": "NO_PUBLISHED_PRIOR"}
        else:
            prior_arm = _rate([r.get(f"hit_{predicted.lower()}") for r in arm])
        by_time_block[block] = {
            **arm_rate,
            "baseline_always_consolidation": arm_consol,
            "baseline_time_prior_only": prior_arm,
            "lift_over_always_consolidation": _lift(arm_rate["accuracy"],
                                                    arm_consol["accuracy"]),
            "time_prior_prediction": predicted,
        }

    # --- calibration ----------------------------------------------------------
    by_confidence_bucket: dict[str, Any] = {}
    for low, high in CONFIDENCE_BUCKETS:
        label = f"{low:.1f}-{high:.1f}"
        last = high >= 1.0

        def in_bucket(row: dict[str, Any], low: float = low, high: float = high,
                      last: bool = last) -> bool:
            # Half-open [low, high) everywhere except the top bucket, which is
            # closed so confidence == 1.0 lands somewhere instead of vanishing.
            c = _confidence(row)
            return low <= c <= high if last else low <= c < high

        arm = [r for r in scored if in_bucket(r)]
        if not arm:
            by_confidence_bucket[label] = {"n": 0, "accuracy": None,
                                           "reason": NO_GRADEABLE_ROWS}
            continue
        arm_rate = _rate([r["hit"] for r in arm])
        mean_conf = _mean([_confidence(r) for r in arm])
        by_confidence_bucket[label] = {
            **arm_rate,
            "mean_confidence": mean_conf,
            # positive => overconfident: it claimed more than it delivered
            "calibration_gap": mean_conf - arm_rate["accuracy"],
            "baseline_always_consolidation": _rate(
                [r.get("hit_consolidation") for r in arm]),
        }

    brier = _mean([(_confidence(r) - (1.0 if r["hit"] else 0.0)) ** 2 for r in scored])
    brier_base = _mean([(overall - (1.0 if h else 0.0)) ** 2 for h in hits])
    if brier_base == 0.0:
        # Every row hit, or every row missed: the base rate is a perfect forecast
        # and the skill score is 0/0. Undefined, said out loud.
        brier_skill = None
        brier_skill_reason: str | None = "BASE_RATE_BRIER_IS_ZERO"
    else:
        brier_skill = 1.0 - brier / brier_base
        brier_skill_reason = None

    baselines = [b for b in (always_consol["accuracy"], prior_rate["accuracy"])
                 if b is not None]
    if baselines:
        strongest = max(baselines)
        versus = ("always_consolidation"
                  if strongest == always_consol["accuracy"] else "time_prior_only")
        lift = {"value": overall - strongest, "versus": versus,
                "baseline": strongest, "n": len(scored), "headline": headline}
    else:
        lift = {"value": None, "versus": None, "baseline": None, "n": len(scored),
                "reason": NO_GRADEABLE_ROWS, "headline": headline}

    return {
        **base,
        "n": len(scored),
        "overall_accuracy": overall,
        "by_regime": by_regime,
        "by_time_block": by_time_block,
        "by_confidence_bucket": by_confidence_bucket,
        "baseline_always_consolidation": always_consol,
        "baseline_time_prior_only": baseline_time_prior,
        "lift_over_baseline": lift,
        "brier": brier,
        "brier_skill_score": brier_skill,
        "brier_base_rate": overall,
        "brier_baseline": brier_base,
        "brier_skill_score_reason": brier_skill_reason,
    }


# --- the only I/O in this file ------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
SCORECARD_CSV = ROOT / "data" / "regime_scorecard.csv"


def _csv_value(key: str, value: Any) -> str:
    """One row field as text. None becomes an EMPTY cell, never 0 and never
    False: an empty cell reads as "not available" to every consumer, and a 0 in
    an ungradeable row would be a fabricated label on disk forever."""
    if value is None:
        return ""
    if key == "evidence":
        return json.dumps(value, sort_keys=True, default=str)
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


def append_rows(rows: Iterable[dict[str, Any]], path: Path | str | None = None) -> int:
    """Append graded rows to the scorecard CSV. APPEND-ONLY, FOREVER.

    Never truncates, never rewrites, never backfills. The header is written once,
    when the file is created. An existing file whose header or grade_version
    disagrees with these rows is a REFUSAL, not a migration: mixing two
    definition sets in one column layout destroys the only property that makes
    this file evidence — that every row was scored by the same rules, fixed
    before the data was seen. A changed definition means a bumped GRADE_VERSION
    and a new file.

    This is the only function in this module that touches a filesystem; `grade()`
    and `report()` stay pure so they can be replayed.
    """
    batch = list(rows)
    if not batch:
        return 0

    versions = {r.get("grade_version") for r in batch}
    if len(versions) != 1:
        raise ScorecardError(
            f"refusing to append rows with mixed grade versions "
            f"{sorted(map(str, versions))}")
    version = versions.pop()
    if not version:
        raise ScorecardError("refusing to append a row with no grade_version")

    target = Path(path) if path is not None else SCORECARD_CSV
    target.parent.mkdir(parents=True, exist_ok=True)
    header = list(ROW_FIELDS)

    if target.exists() and target.stat().st_size > 0:
        with target.open("r", newline="") as fh:
            reader = csv.reader(fh)
            existing_header = next(reader, None)
            if existing_header != header:
                raise ScorecardError(
                    f"{target} has header {existing_header} but these rows write "
                    f"{header}; a changed column layout means a changed definition "
                    f"set — bump GRADE_VERSION and write a new file instead of "
                    f"mixing them")
            idx = header.index("grade_version")
            for line in reader:
                if not line:
                    continue
                if line[idx] != version:
                    raise ScorecardError(
                        f"{target} already holds rows graded under "
                        f"{line[idx]!r}; refusing to append {version!r} into the "
                        f"same file")
        write_header = False
    else:
        write_header = True

    with target.open("a", newline="") as fh:
        writer = csv.writer(fh)
        if write_header:
            writer.writerow(header)
        for r in batch:
            missing = [k for k in ROW_FIELDS if k not in r]
            if missing:
                raise ScorecardError(
                    f"row is missing field(s) {missing}; a short row would shift "
                    f"every later column")
            writer.writerow([_csv_value(k, r[k]) for k in ROW_FIELDS])
    return len(batch)
