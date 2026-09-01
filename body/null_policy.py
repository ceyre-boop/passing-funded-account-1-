"""body/null_policy.py — the calibration arm.

WHY A DELIBERATELY WORTHLESS POLICY COMES FIRST
    Same discipline as the degraded-candidate arm in the exit work: a loop that
    only works when the policy is good is not a loop that has been tested. This
    one answers exactly one question -- does the car drive? -- and answers it
    without borrowing any credibility from a signal.

    It claims no edge and implies none:
      * emission is on a FIXED SCHEDULE, not on any property of the bar
      * `confidence` is a pinned constant, not a computed score
      * direction ALTERNATES deterministically, so not even a directional prior
        is smuggled in
      * the whole run is reproducible -- no randomness at all

    `CALIBRATION_ARM = True` is what the ODD's `policy_declares_no_edge`
    precondition reads. A policy that will not declare itself cannot pass the
    sim gate.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from body.directive import LONG, SHORT, EntryDirective  # noqa: E402

# Declared constants. Changing any of these changes what the calibration arm is,
# so they are named here rather than passed around as magic numbers.
EVERY_N_BARS = 12               # one directive per hour on 5m bars
CONSTANT_CONFIDENCE = 0.0       # no confidence is claimed, so none is asserted
VALID_FOR_NS = 15 * 60 * 1_000_000_000   # 15 minutes
REASON = "CALIBRATION ARM — no edge claimed; fixed-schedule emitter"


class NullEntryPolicy:
    """`bar -> EntryDirective | None`, matching the shape AlphaZero will take.

    When the real policy lands it drops into this signature and the harness is
    already known to work."""

    CALIBRATION_ARM = True
    name = "null"

    def __init__(self, *, every_n_bars: int = EVERY_N_BARS,
                 confidence: float = CONSTANT_CONFIDENCE):
        if every_n_bars < 1:
            raise ValueError(f"every_n_bars={every_n_bars!r}: must be >= 1")
        self.every_n_bars = every_n_bars
        self.confidence = confidence
        self.seen = 0
        self.emitted = 0

    def __call__(self, bar):
        self.seen += 1
        if self.seen % self.every_n_bars:
            return None
        # alternate strictly, so the arm carries no directional prior
        direction = LONG if self.emitted % 2 == 0 else SHORT
        self.emitted += 1
        return EntryDirective(
            instrument_id=bar.bar_type.instrument_id,
            direction=direction,
            reason=REASON,
            confidence=self.confidence,
            ts_event=bar.ts_event,
            valid_for_ns=VALID_FOR_NS,
        )
