"""body/stockfish_strategy.py — Stockfish rides as the Nautilus Strategy.

It is the ONLY component in this package holding order authority, and it treats
an AlphaZero directive as advice, never as an instruction.

THE THREE REFUSALS (CLAUDE.md: "Stockfish MUST NOT ...")
    stale directives   -- checked against the strategy's own clock, not the
                          directive's optimism. Expired means dropped, counted.
    invented context   -- an unknown environment, an unattested precondition, or
                          a missing instrument is an explicit refusal, not a guess.
    inferred meaning   -- it never reads `reason`. That string is for the human
                          audit trail; acting on its content would be Stockfish
                          doing AlphaZero's job, and a test enforces it.

DIRECTION OF AUTHORITY IS ONE-WAY (ODD.md §1b rule 4)
    This strategy can refuse any directive for any reason. The actor has no
    channel to override it, because the actor has no order methods at all.

TWO EXECUTION WORLDS
    At a LIVE tier `execute()` raises: there is no validated edge and no live
    order path in this repo.

    At T_SIM it submits a real bracket order to the simulated venue, because the
    goal is to find out whether the car drives -- and an orphan counter is
    meaningless without positions to orphan. That path is reachable only behind
    the environment fault in `az/odd.py::authorize_entry`, which is derived from
    the kernel-injected clock and cannot be set by configuration.
"""
from __future__ import annotations

import sys
from pathlib import Path

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT / "az"), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from odd import (  # noqa: E402
    SIM_PRECONDITIONS, OddError, Precondition, RunEnvironment, Tier, Truth,
    authorize_entry, evaluate_gate,
)
from state import _atr  # noqa: E402 — one ATR implementation, not a second

from body.alphazero_actor import DIRECTIVE_TYPE  # noqa: E402
from body.directive import EntryDirective  # noqa: E402
from body.runtime import detect_environment  # noqa: E402

# Calibration-arm sizing. Not under test here: the question is whether the loop
# completes, not how big the position should be.
SIM_QUANTITY = 1
ATR_LOOKBACK = 14
MIN_BARS_FOR_ATR = 14


class StockfishStrategy(Strategy):
    """Deterministic mechanics. Consumes meaning, owns execution."""

    def __init__(self, config=None, *, tier: Tier = Tier.T2_DEFENSIVE,
                 sim_attestations: dict[str, Truth] | None = None,
                 k_stop: float = 1.0, bar_type=None):
        super().__init__(config)
        self.tier = tier
        self.k_stop = k_stop
        self._bar_type = bar_type
        # The operator attests the sim preconditions at construction. Nothing is
        # attested by default, so an unconfigured strategy fails the sim gate --
        # the strategy never infers them, least of all from the directive's
        # `reason` string, which it must not read.
        self.sim_attestations = dict(sim_attestations or {})

        self.bars: list = []
        self.received = 0
        self.rejected_stale = 0
        self.rejected_unauthorized = 0
        self.authorized = 0
        self.orders_submitted = 0
        self.faults = 0
        self.rejected_warmup = 0
        self.orders_filled = 0
        self.positions_opened = 0
        self.positions_closed = 0
        self.last_refusal: str | None = None
        self.refusal_reasons: dict[str, int] = {}

    # --- lifecycle -----------------------------------------------------------
    def on_start(self) -> None:
        self.subscribe_data(DIRECTIVE_TYPE)
        if self._bar_type is not None:
            self.subscribe_bars(self._bar_type)

    def on_bar(self, bar) -> None:
        """Stockfish keeps its own price history. It does not take prices from
        the directive -- the directive carries meaning, never mechanics."""
        self.bars.append(bar)

    # --- outcome counters: the scoreboard is completions, never R -------------
    def on_order_filled(self, event) -> None:
        self.orders_filled += 1

    def on_position_opened(self, event) -> None:
        self.positions_opened += 1

    def on_position_closed(self, event) -> None:
        self.positions_closed += 1

    @property
    def orphans(self) -> int:
        """ODD.md §1b: a position with no authorized exit. Here, any position
        still open when the run ends -- detected, never assumed to be zero."""
        return max(0, self.positions_opened - self.positions_closed)

    # --- the gate ------------------------------------------------------------
    @property
    def environment(self) -> RunEnvironment:
        return detect_environment(self.clock)

    def _sim_gate(self):
        """Build the sim checklist from what the operator actually attested.
        An unattested line evaluates UNKNOWN and fails closed."""
        checks = []
        for p in SIM_PRECONDITIONS:
            forced = self.sim_attestations.get(p.key)
            checks.append(Precondition(p.key, p.text, _forced=forced))
        return evaluate_gate(tuple(checks))

    def authorize(self, directive: EntryDirective, now_ns: int) -> tuple[bool, str]:
        """Every refusal path returns a reason. Nothing fails silently."""
        if not directive.is_fresh_at(now_ns):
            return False, (f"stale: valid until {directive.valid_until_ns}, "
                           f"now {now_ns}")
        if self.tier is Tier.T_SIM:
            # raises OddError if this is not a proven backtest — deliberately
            # not caught here, so a mis-wired live run stops loudly
            return authorize_entry(
                self.tier, self._sim_gate(),
                exit_core_in_domain=Truth.TRUE,
                entry_layer_in_domain=Truth.TRUE,
                environment=self.environment)
        return authorize_entry(
            self.tier, evaluate_gate(tier=self.tier),
            # The exit core's own domain membership is not measured yet, and
            # UNKNOWN fails closed rather than being optimistically assumed.
            exit_core_in_domain=Truth.UNKNOWN,
            entry_layer_in_domain=Truth.TRUE,
            environment=self.environment)

    # --- the only entry point from the meaning channel ------------------------
    def on_data(self, data) -> None:
        if not isinstance(data, EntryDirective):
            return
        self.received += 1
        now_ns = self.clock.timestamp_ns() if self.clock is not None else data.ts_event
        ok, why = self.authorize(data, now_ns)
        if not ok:
            self.last_refusal = why
            key = why.split(":")[0].strip()
            self.refusal_reasons[key] = self.refusal_reasons.get(key, 0) + 1
            if why.startswith("stale"):
                self.rejected_stale += 1
            else:
                self.rejected_unauthorized += 1
            return
        self.authorized += 1
        self.execute(data)

    # --- mechanics -----------------------------------------------------------
    def execute(self, directive: EntryDirective) -> None:
        """Derive the geometry and act. Sizing, stop and targets are computed
        HERE, from Stockfish's own price history -- never from the directive,
        which carries none of them."""
        if self.tier is not Tier.T_SIM:
            raise NotImplementedError(
                "no live execution path: the ODD gate cannot open in v0.1 and no "
                "edge has cleared its economic floor. See "
                "artifacts/FLOOR_TEST_RESULT.md.")
        if self.environment is not RunEnvironment.BACKTEST:
            raise OddError(
                f"execute() at T_SIM in environment {self.environment.value} — "
                "refusing. The simulated order path exists only in a backtest.")

        instrument = self.cache.instrument(directive.instrument_id)
        if instrument is None:
            raise OddError(
                f"no instrument for {directive.instrument_id} — refusing rather "
                "than inventing a contract specification")
        if len(self.bars) < MIN_BARS_FOR_ATR:
            # explicit failure over fallback: an ATR from 3 bars is not an ATR
            # Explicit failure over fallback, and counted as a refusal rather
            # than quietly vanishing between the authorize and execute stages.
            self.last_refusal = f"warmup: {len(self.bars)}/{MIN_BARS_FOR_ATR} bars"
            self.refusal_reasons["warmup"] = self.refusal_reasons.get("warmup", 0) + 1
            self.rejected_warmup += 1
            self.authorized -= 1
            return

        import pandas as pd
        hist = pd.DataFrame(
            {"High": [float(b.high) for b in self.bars[-ATR_LOOKBACK:]],
             "Low": [float(b.low) for b in self.bars[-ATR_LOOKBACK:]]})
        atr14 = _atr(hist, ATR_LOOKBACK)
        risk = self.k_stop * atr14
        if not (risk > 0):
            raise OddError(f"risk={risk!r} from atr14={atr14!r} — refusing")

        entry = float(self.bars[-1].close)
        d = directive.direction
        # the declared geometry: stop at k_stop*ATR14, tp at 2R
        stop = entry - d * risk
        target = entry + d * 2.0 * risk

        side = OrderSide.BUY if d > 0 else OrderSide.SELL
        bracket = self.order_factory.bracket(
            instrument_id=instrument.id,
            order_side=side,
            quantity=Quantity.from_int(SIM_QUANTITY),
            sl_trigger_price=instrument.make_price(stop),
            tp_price=instrument.make_price(target),
        )
        self.submit_order_list(bracket)
        self.orders_submitted += 1
