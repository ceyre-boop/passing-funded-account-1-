"""body/directive.py — the only thing AlphaZero is allowed to say.

An `EntryDirective` carries MEANING and nothing else. It deliberately has no
field for quantity, price, stop distance, or target: `CLAUDE.md` forbids
AlphaZero from calculating executable exit quantities or moving stops, and the
cheapest way to enforce that is to give it nowhere to put them.

Freshness is carried on the directive itself, not tracked by the consumer,
because "Stockfish MUST NOT accept stale directives" is a property of the
message and should travel with it.
"""
from __future__ import annotations

from nautilus_trader.core.data import Data


class DirectiveError(ValueError):
    """Refused. Never partial, never guessed."""


LONG, SHORT = 1, -1
_VALID_DIRECTIONS = (LONG, SHORT)


class EntryDirective(Data):
    """AlphaZero's output. Immutable, self-dating, and mechanically inert.

    `direction` has no zero value on purpose. A "no opinion" directive is not a
    directive -- AlphaZero stays silent instead, so a missing signal can never be
    mistaken for a flat one (dev rule 3: never silently default an unavailable
    value to numeric zero).
    """

    __slots__ = ("_instrument_id", "_direction", "_reason", "_confidence",
                 "_ts_event", "_valid_until_ns")

    def __init__(self, *, instrument_id, direction: int, reason: str,
                 confidence: float, ts_event: int, valid_for_ns: int):
        super().__init__()
        if direction not in _VALID_DIRECTIONS:
            raise DirectiveError(
                f"direction={direction!r}: must be +1 (LONG) or -1 (SHORT). "
                "There is no zero direction — silence is how AlphaZero abstains.")
        if not reason or not reason.strip():
            raise DirectiveError(
                "reason is empty: a directive with no stated meaning is exactly "
                "the 'silently invent unavailable context' failure, one layer up")
        if not (0.0 <= confidence <= 1.0):
            raise DirectiveError(f"confidence={confidence!r}: must be in [0, 1]")
        if valid_for_ns <= 0:
            raise DirectiveError(
                f"valid_for_ns={valid_for_ns!r}: a directive that never expires "
                "cannot be checked for staleness, so it must not exist")
        if ts_event < 0:
            raise DirectiveError(f"ts_event={ts_event!r}: must be non-negative")

        self._instrument_id = instrument_id
        self._direction = int(direction)
        self._reason = reason
        self._confidence = float(confidence)
        self._ts_event = int(ts_event)
        self._valid_until_ns = int(ts_event) + int(valid_for_ns)

    # --- Data contract -------------------------------------------------------
    @property
    def ts_event(self) -> int:
        return self._ts_event

    @property
    def ts_init(self) -> int:
        return self._ts_event

    # --- meaning -------------------------------------------------------------
    @property
    def instrument_id(self):
        return self._instrument_id

    @property
    def direction(self) -> int:
        return self._direction

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def confidence(self) -> float:
        return self._confidence

    @property
    def valid_until_ns(self) -> int:
        return self._valid_until_ns

    def is_fresh_at(self, now_ns: int) -> bool:
        """Freshness is asked, never assumed. A consumer that does not call this
        is the bug this method exists to make visible in review."""
        return int(now_ns) <= self._valid_until_ns

    def __repr__(self) -> str:
        side = "LONG" if self._direction == LONG else "SHORT"
        return (f"EntryDirective({self._instrument_id}, {side}, "
                f"conf={self._confidence:.2f}, reason={self._reason!r}, "
                f"ts={self._ts_event}, until={self._valid_until_ns})")
