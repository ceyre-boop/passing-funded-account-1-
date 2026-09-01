"""body/test_wiring.py — the two engines inside a real Nautilus kernel.

WHY THIS EXISTS SEPARATELY FROM test_boundary.py
    test_boundary.py calls the methods directly. That proves the SHAPE is right
    and proves nothing about whether a directive actually travels. Per this
    repo's own maturity vocabulary, direct calls get you UNIT VERIFIED and stop
    well short of WIRED and EXERCISED -- and CLAUDE.md is explicit that WIRED
    must never be claimed on the strength of imports.

    So this runs a real BacktestEngine: a venue, an instrument, a bar stream, the
    actor and the strategy both registered with the kernel, and the directive
    crossing the message bus between them with nobody calling anything by hand.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytest.importorskip("nautilus_trader",
                    reason="run body/ under .venv-v1 (py3.13); see "
                           "artifacts/NAUTILUS_BODY_ASSESSMENT.md")

import pandas as pd  # noqa: E402
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig  # noqa: E402
from nautilus_trader.config import LoggingConfig  # noqa: E402
from nautilus_trader.model.currencies import USD  # noqa: E402
from nautilus_trader.model.data import Bar, BarType  # noqa: E402
from nautilus_trader.model.enums import AccountType, OmsType  # noqa: E402
from nautilus_trader.model.identifiers import Venue  # noqa: E402
from nautilus_trader.model.objects import Money, Price, Quantity  # noqa: E402
from nautilus_trader.test_kit.providers import TestInstrumentProvider  # noqa: E402

from body.alphazero_actor import AlphaZeroActor  # noqa: E402
from body.directive import LONG, EntryDirective  # noqa: E402
from body.stockfish_strategy import StockfishStrategy  # noqa: E402

N_BARS = 5


def _run(policy):
    engine = BacktestEngine(BacktestEngineConfig(
        trader_id="TESTER-001", logging=LoggingConfig(bypass_logging=True)))
    venue = Venue("SIM")
    engine.add_venue(venue=venue, oms_type=OmsType.NETTING,
                     account_type=AccountType.MARGIN, base_currency=USD,
                     starting_balances=[Money(1_000_000, USD)])
    inst = TestInstrumentProvider.equity(symbol="SPY", venue="SIM")
    engine.add_instrument(inst)

    bar_type = BarType.from_str(f"{inst.id}-1-MINUTE-LAST-EXTERNAL")
    start = pd.Timestamp("2026-08-31 13:30", tz="UTC")
    bars = []
    for i in range(N_BARS):
        ts = int((start + pd.Timedelta(minutes=i)).value)
        bars.append(Bar(bar_type=bar_type,
                        open=Price.from_str("500.00"), high=Price.from_str("501.00"),
                        low=Price.from_str("499.00"), close=Price.from_str("500.50"),
                        volume=Quantity.from_int(1000), ts_event=ts, ts_init=ts))
    engine.add_data(bars)

    actor = AlphaZeroActor(bar_type=bar_type, policy=policy(inst))
    strategy = StockfishStrategy()
    engine.add_actor(actor)
    engine.add_strategy(strategy)
    engine.run()
    engine.dispose()
    return actor, strategy


def _speaking_policy(inst):
    def policy(bar):
        return EntryDirective(instrument_id=inst.id, direction=LONG,
                              reason="wiring probe", confidence=0.5,
                              ts_event=bar.ts_event, valid_for_ns=60_000_000_000)
    return policy


def _abstaining_policy(inst):
    return lambda bar: None


def test_directive_crosses_the_message_bus():
    """WIRED: nobody calls on_data by hand. The kernel routes it."""
    actor, strategy = _run(_speaking_policy)
    assert actor.published == N_BARS, "actor did not publish inside the engine"
    assert strategy.received == actor.published, (
        f"message bus dropped directives: published {actor.published}, "
        f"received {strategy.received}")


def test_the_odd_gate_fires_inside_a_real_engine_run():
    """EXERCISED: the refusal happens on the real path, not in a unit stub."""
    _, strategy = _run(_speaking_policy)
    assert strategy.authorized == 0, "v0.1 must not authorize anything"
    assert strategy.rejected_unauthorized == N_BARS
    assert "may not open risk" in (strategy.last_refusal or "")


def test_no_orders_are_ever_submitted():
    """The end-to-end safety statement: a full engine run places nothing."""
    _, strategy = _run(_speaking_policy)
    assert strategy.authorized == 0


def test_abstaining_policy_publishes_nothing_through_the_bus():
    actor, strategy = _run(_abstaining_policy)
    assert actor.published == 0 and actor.abstained == N_BARS
    assert strategy.received == 0
