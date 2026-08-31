"""body/stockfish_strategy.py — Stockfish rides as the Nautilus Strategy.

It is the ONLY component in this package holding order authority, and it treats
an AlphaZero directive as advice, never as an instruction.

THE THREE REFUSALS (CLAUDE.md: "Stockfish MUST NOT ...")
    stale directives   -- checked against the strategy's own clock, not the
                          directive's optimism. Expired means dropped, counted.
    invented context   -- an unknown instrument, or a directive for something
                          not subscribed, is an explicit refusal, not a guess.
    inferred meaning   -- it never reads `reason`. That string is for the human
                          audit trail; acting on its content would be Stockfish
                          doing AlphaZero's job.

DIRECTION OF AUTHORITY IS ONE-WAY (ODD.md §1b rule 4)
    This strategy can refuse any directive for any reason. The actor has no
    channel to override it, because the actor has no order methods at all.

WHY IT REFUSES EVERYTHING TODAY
    `authorize()` routes through az/odd.py, whose gate cannot pass in v0.1: the
    T0 unseal authorization is absent and nine preconditions are UNKNOWN, which
    fails closed. That is the correct behaviour, not a stub -- there is no
    validated edge to authorize.
"""
from __future__ import annotations

import sys
from pathlib import Path

from nautilus_trader.trading.strategy import Strategy

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "az") not in sys.path:
    sys.path.insert(0, str(ROOT / "az"))

from odd import Tier, Truth, authorize_entry, evaluate_gate  # noqa: E402

from body.alphazero_actor import DIRECTIVE_TYPE  # noqa: E402
from body.directive import EntryDirective  # noqa: E402


class StockfishStrategy(Strategy):
    """Deterministic mechanics. Consumes meaning, owns execution."""

    def __init__(self, config=None, *, tier: Tier = Tier.T2_DEFENSIVE):
        super().__init__(config)
        self.tier = tier
        self.received = 0
        self.rejected_stale = 0
        self.rejected_unauthorized = 0
        self.authorized = 0
        self.last_refusal: str | None = None

    def on_start(self) -> None:
        self.subscribe_data(DIRECTIVE_TYPE)

    # --- the gate ------------------------------------------------------------
    def authorize(self, directive: EntryDirective, now_ns: int) -> tuple[bool, str]:
        """Every refusal path returns a reason. Nothing fails silently."""
        if not directive.is_fresh_at(now_ns):
            return False, (f"stale: valid until {directive.valid_until_ns}, "
                           f"now {now_ns}")
        ok, why = authorize_entry(
            self.tier, evaluate_gate(),
            # The exit core's own domain membership is not measured yet, and
            # UNKNOWN fails closed rather than being optimistically assumed.
            exit_core_in_domain=Truth.UNKNOWN,
            entry_layer_in_domain=Truth.TRUE,
        )
        return ok, why

    # --- the only entry point from the meaning channel ------------------------
    def on_data(self, data) -> None:
        if not isinstance(data, EntryDirective):
            return
        self.received += 1
        now_ns = self.clock.timestamp_ns() if self.clock is not None else data.ts_event
        ok, why = self.authorize(data, now_ns)
        if not ok:
            self.last_refusal = why
            if why.startswith("stale"):
                self.rejected_stale += 1
            else:
                self.rejected_unauthorized += 1
            return
        self.authorized += 1
        self.execute(data)

    def execute(self, directive: EntryDirective) -> None:
        """Where mechanics would be computed and an order submitted.

        Deliberately unimplemented. Reaching it means the ODD gate opened, which
        cannot happen in v0.1, and building an execution path before there is an
        edge to execute is the thing this repo keeps refusing to do. Sizing,
        stop distance and targets get derived HERE, from the frozen exit core --
        never from the directive, which carries none of them."""
        raise NotImplementedError(
            "no execution path: the ODD gate cannot open in v0.1 and no edge has "
            "cleared its economic floor. See artifacts/FLOOR_TEST_RESULT.md.")
