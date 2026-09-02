"""body/entry_policy.py — the first entry policy that conditions on the tape.

WHAT THIS IS, AND EMPHATICALLY IS NOT
    It is the first policy that reads state instead of a clock, which is the
    whole drive milestone: the loop has only ever run under a fixed-schedule
    emitter, so nothing downstream has been exercised by a decision that
    depends on the market.

    It is NOT an edge. No entry edge is validated in this repository. The entry
    family is closed (104,680 candidates, best cell +0.0956 against a null p95
    of +0.4310 -- less structure than randomness produces), and all four
    detection-floor survivors are closed on economics. `DECLARES_NO_EDGE` is
    True and the ODD's sim gate reads it; a policy that claimed an edge would
    fail `policy_declares_no_edge` and the gate would shut.

THE SHAPE: EAGER TO TRADE, TENTATIVE TO ENTER
    Enumerate every reason NOT to enter first, then enter only if the reasons
    to trade exceed them by a declared margin:

        enter  iff  eagerness - vetoes >= MARGIN

    Both sides are logged in full on every bar, entered or not. That ledger is
    the instrument MECH-006 needs -- "AlphaZero's entry veto carries
    information a rate-matched coin does not: the days it refuses are worse
    than average, not merely fewer." MECH-006 is the one hypothesis about this
    layer still standing; MECH-003 (interrupt timing) is killed. A veto with no
    logged ledger cannot be tested against a rate-matched control, and an
    untestable veto is indistinguishable from timidity.

WHAT IT CONDITIONS ON, AND WHY THOSE
    Range expansion, because it is the one entry-adjacent quantity in this repo
    that cleared its own detection floor (0.48x of it) -- while FAILING its
    pre-registered economic floor, which is why this is a trigger and not a
    thesis. Volatility level, because a move that cannot pay costs is not an
    opportunity. Session phase and remaining time, because an intraday trade
    needs room to work.

THE PERMANENT VETO
    `no_directional_basis` fires on EVERY bar and never clears. Direction is
    null in this repository: all three directional studies sit below their
    detection floor at 2.37x, 4.08x and 29.27x. Any direction this policy picks
    is a tiebreak, not a prediction, and carrying that as a standing veto with
    real weight is the honest encoding of it.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from body.directive import LONG, SHORT, EntryDirective  # noqa: E402

# --- declared constants. Frozen: chosen before the first run, not tuned after.
ATR_LOOKBACK = 14
MIN_BARS = ATR_LOOKBACK
EXPANSION_TRIGGER = 1.30      # bar true range / ATR14 to count as expansion
MIN_ATR_FRACTION = 0.0004     # ATR14/price floor; below this a move cannot pay costs
MAX_VWAP_EXTENSION = 2.0      # in ATRs; beyond this, entering is chasing
MIN_BARS_REMAINING = 12       # a trade needs room to work
MAX_ENTRIES_PER_SESSION = 3
VALID_FOR_NS = 15 * 60 * 1_000_000_000

# --- the ledger's weights. Eagerness must beat the standing veto to fire.
W_EXPANSION = 1.0
W_VOL_ADEQUATE = 0.5
W_VETO_NO_DIRECTION = 0.8     # permanent — direction is null in this repo
W_VETO_WARMUP = 10.0          # structural, not a judgement
W_VETO_LATE = 10.0
W_VETO_BUDGET = 10.0
W_VETO_LOW_VOL = 1.0
W_VETO_EXTENDED = 1.0
MARGIN = 0.25                 # eagerness - vetoes must reach this

REASON = ("CONDITIONED ENTRY — no edge claimed; range-expansion trigger with a "
          "standing directional veto")


@dataclass
class Ledger:
    """Both sides of one decision, kept whether or not it fired."""
    ts_event: int
    fired: bool
    eagerness: float
    vetoes: float
    margin: float
    for_reasons: list = field(default_factory=list)
    against_reasons: list = field(default_factory=list)

    @property
    def score(self) -> float:
        return self.eagerness - self.vetoes


class VetoEntryPolicy:
    """`bar -> EntryDirective | None`, the same seam the null policy occupies."""

    DECLARES_NO_EDGE = True       # read by the ODD sim gate
    CALIBRATION_ARM = True        # back-compat with the same attestation
    name = "veto-v1"

    def __init__(self, *, expansion_trigger: float = EXPANSION_TRIGGER,
                 margin: float = MARGIN,
                 max_entries: int = MAX_ENTRIES_PER_SESSION,
                 bars_in_session: int = 78):
        self.expansion_trigger = expansion_trigger
        self.margin = margin
        self.max_entries = max_entries
        self.bars_in_session = bars_in_session
        self.bars: list = []
        self.emitted = 0
        self.seen = 0
        self.ledgers: list[Ledger] = []

    # --- state, all from bars at or before now ------------------------------
    def _atr(self) -> float:
        w = self.bars[-ATR_LOOKBACK:]
        return sum(float(b.high) - float(b.low) for b in w) / len(w)

    def _vwap(self) -> float:
        return self._vwap_over(self.bars)

    def _vwap_excluding_last(self) -> float:
        """VWAP as of the PRIOR bar — the reference the chasing veto needs."""
        return self._vwap_over(self.bars[:-1]) if len(self.bars) > 1 else self._vwap()

    @staticmethod
    def _vwap_over(bars) -> float:
        if not bars:
            return 0.0
        num = sum(float(b.close) * float(b.volume) for b in bars)
        den = sum(float(b.volume) for b in bars)
        return num / den if den > 0 else float(bars[-1].close)

    # --- the decision --------------------------------------------------------
    def __call__(self, bar):
        self.seen += 1
        self.bars.append(bar)
        led = self._evaluate(bar)
        self.ledgers.append(led)
        if not led.fired:
            return None
        self.emitted += 1
        return EntryDirective(
            instrument_id=bar.bar_type.instrument_id,
            direction=self._direction(bar),
            reason=REASON,
            confidence=0.0,          # no confidence is claimed, so none is asserted
            ts_event=bar.ts_event,
            valid_for_ns=VALID_FOR_NS,
        )

    def _direction(self, bar) -> int:
        """A TIEBREAK, not a prediction. Direction is null in this repo -- all
        three directional studies are below their detection floor. The sign of
        the triggering bar is used because something must be chosen, and it is
        carried as a standing veto rather than as a claim."""
        return LONG if float(bar.close) >= float(bar.open) else SHORT

    def _evaluate(self, bar) -> Ledger:
        fors: list = []
        againsts: list = [("no_directional_basis", W_VETO_NO_DIRECTION)]

        if len(self.bars) < MIN_BARS:
            againsts.append(("warmup", W_VETO_WARMUP))
            return self._ledger(bar, fors, againsts)

        atr = self._atr()
        price = float(bar.close)
        if not (atr > 0 and price > 0):
            againsts.append(("degenerate_state", W_VETO_WARMUP))
            return self._ledger(bar, fors, againsts)

        # --- reasons to trade
        tr = float(bar.high) - float(bar.low)
        expansion = tr / atr
        if expansion >= self.expansion_trigger:
            fors.append(("range_expansion", W_EXPANSION))
        if atr / price >= MIN_ATR_FRACTION:
            fors.append(("vol_adequate", W_VOL_ADEQUATE))

        # --- reasons not to
        if atr / price < MIN_ATR_FRACTION:
            againsts.append(("vol_too_low_to_pay_costs", W_VETO_LOW_VOL))
        # "Am I chasing?" is a question about where price was BEFORE this bar.
        # Measured on the current close it is self-defeating: the expansion bar
        # IS the move away from VWAP, so the trigger fires this veto against
        # itself and the two cancel. Found by a test that asserted expansion
        # could clear the standing veto and could not.
        if len(self.bars) >= 2:
            prior = float(self.bars[-2].close)
            if abs(prior - self._vwap_excluding_last()) / atr > MAX_VWAP_EXTENSION:
                againsts.append(("extended_from_vwap", W_VETO_EXTENDED))
        if (self.bars_in_session - self.seen) < MIN_BARS_REMAINING:
            againsts.append(("no_room_left_in_session", W_VETO_LATE))
        if self.emitted >= self.max_entries:
            againsts.append(("session_budget_spent", W_VETO_BUDGET))

        return self._ledger(bar, fors, againsts)

    def _ledger(self, bar, fors, againsts) -> Ledger:
        e = sum(w for _, w in fors)
        v = sum(w for _, w in againsts)
        return Ledger(ts_event=bar.ts_event, fired=(e - v) >= self.margin,
                      eagerness=e, vetoes=v, margin=self.margin,
                      for_reasons=[n for n, _ in fors],
                      against_reasons=[n for n, _ in againsts])

    # --- what MECH-006 will need -------------------------------------------
    def veto_summary(self) -> dict:
        """Counts per veto reason across every bar. The rate-matched control
        MECH-006 requires is built against these, not against entry count."""
        out: dict = {}
        for led in self.ledgers:
            for r in led.against_reasons:
                out[r] = out.get(r, 0) + 1
        return out
