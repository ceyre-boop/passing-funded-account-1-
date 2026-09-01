"""body/test_sim_tier.py — the closed track, and the lock on its door.

T_SIM exists so the full loop can be exercised without an edge. It is also a
door in a wall built on purpose, so the tests that matter here are not "does it
open" but "can it be opened from the wrong side".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "az")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytest.importorskip("nautilus_trader",
                    reason="run body/ under .venv-v1 (py3.13)")

import pandas as pd  # noqa: E402
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig  # noqa: E402
from nautilus_trader.common.component import LiveClock, TestClock  # noqa: E402
from nautilus_trader.config import LoggingConfig  # noqa: E402
from nautilus_trader.model.currencies import USD  # noqa: E402
from nautilus_trader.model.data import Bar, BarType  # noqa: E402
from nautilus_trader.model.enums import AccountType, OmsType  # noqa: E402
from nautilus_trader.model.identifiers import Venue  # noqa: E402
from nautilus_trader.model.objects import Money, Quantity  # noqa: E402
from nautilus_trader.test_kit.providers import TestInstrumentProvider  # noqa: E402

from odd import SIM_PRECONDITIONS, OddError, RunEnvironment, Tier, Truth  # noqa: E402

from body.alphazero_actor import AlphaZeroActor  # noqa: E402
from body.directive import LONG, SHORT, EntryDirective  # noqa: E402
from body.null_policy import CONSTANT_CONFIDENCE, REASON, NullEntryPolicy  # noqa: E402
from body.runtime import detect_environment  # noqa: E402
from body.stockfish_strategy import StockfishStrategy  # noqa: E402

ALL_TRUE = {p.key: Truth.TRUE for p in SIM_PRECONDITIONS}


def directive(**kw):
    base = dict(instrument_id="SPY.SIM", direction=LONG, reason="probe",
                confidence=0.0, ts_event=1_000, valid_for_ns=10**12)
    base.update(kw)
    return EntryDirective(**base)


# --------------------------------------------- environment is a kernel fact

def test_backtest_kernel_is_detected_structurally():
    assert detect_environment(TestClock()) is RunEnvironment.BACKTEST


def test_live_kernel_is_detected():
    assert detect_environment(LiveClock()) is RunEnvironment.LIVE


@pytest.mark.parametrize("obj", [None, object(), "BACKTEST", 1])
def test_anything_unidentifiable_reads_as_live(obj):
    """FAIL CLOSED: an unrecognised clock is not a simulator. Note the string
    "BACKTEST" — a config value must never be mistaken for a kernel fact."""
    assert detect_environment(obj) is RunEnvironment.LIVE


def test_detect_environment_takes_no_override():
    """FAULT INJECTION: an override parameter would defeat the entire guard."""
    import inspect
    params = list(inspect.signature(detect_environment).parameters)
    assert params == ["clock"], f"detect_environment grew parameters: {params}"


# ------------------------------------------------------- the strategy's door

def test_t_sim_strategy_faults_outside_a_backtest():
    s = StockfishStrategy(tier=Tier.T_SIM, sim_attestations=ALL_TRUE)
    with pytest.raises(OddError):
        # no kernel attached -> clock is None -> LIVE -> fault
        s.authorize(directive(), now_ns=1_000)


def test_execute_refuses_at_every_live_tier():
    for tier in (Tier.T3_HALT, Tier.T2_DEFENSIVE, Tier.T1_RESTRICTED, Tier.T0_NOMINAL):
        with pytest.raises(NotImplementedError):
            StockfishStrategy(tier=tier).execute(directive())


def test_execute_at_t_sim_outside_a_backtest_faults():
    """Second, independent lock: even if authorization were bypassed."""
    with pytest.raises(OddError):
        StockfishStrategy(tier=Tier.T_SIM, sim_attestations=ALL_TRUE).execute(directive())


def test_unattested_sim_preconditions_fail_closed():
    """A strategy nobody configured must not drive."""
    s = StockfishStrategy(tier=Tier.T_SIM)          # no attestations at all
    assert not s._sim_gate().passed


@pytest.mark.parametrize("missing", [p.key for p in SIM_PRECONDITIONS])
def test_dropping_any_single_attestation_shuts_the_gate(missing):
    att = {k: v for k, v in ALL_TRUE.items() if k != missing}
    s = StockfishStrategy(tier=Tier.T_SIM, sim_attestations=att)
    assert not s._sim_gate().passed


# ------------------------------------------------- the calibration arm

def test_policy_declares_itself():
    assert NullEntryPolicy.CALIBRATION_ARM is True


def test_policy_emits_on_a_fixed_schedule_not_on_the_bar():
    p = NullEntryPolicy(every_n_bars=3)
    bars = [_FakeBar(i) for i in range(9)]
    out = [p(b) for b in bars]
    assert [i for i, d in enumerate(out) if d is not None] == [2, 5, 8]


def test_policy_alternates_direction_so_it_carries_no_prior():
    p = NullEntryPolicy(every_n_bars=1)
    dirs = [p(_FakeBar(i)).direction for i in range(6)]
    assert dirs == [LONG, SHORT, LONG, SHORT, LONG, SHORT]


def test_policy_confidence_is_a_pinned_constant():
    p = NullEntryPolicy(every_n_bars=1)
    assert {p(_FakeBar(i)).confidence for i in range(5)} == {CONSTANT_CONFIDENCE}


def test_policy_labels_itself_in_the_reason():
    assert "CALIBRATION ARM" in REASON and "no edge claimed" in REASON


def test_policy_is_deterministic():
    a = [d.direction if d else None for d in (NullEntryPolicy()(_FakeBar(i)) for i in range(30))]
    b = [d.direction if d else None for d in (NullEntryPolicy()(_FakeBar(i)) for i in range(30))]
    assert a == b


class _FakeBar:
    def __init__(self, i):
        self.ts_event = 1_000 + i
        self.bar_type = type("BT", (), {"instrument_id": "SPY.SIM"})()


# --------------------------------------------------- the loop actually drives

def _drive(n_bars=60):
    engine = BacktestEngine(BacktestEngineConfig(
        trader_id="SIMDRIVE-001", logging=LoggingConfig(bypass_logging=True)))
    engine.add_venue(venue=Venue("SIM"), oms_type=OmsType.NETTING,
                     account_type=AccountType.MARGIN, base_currency=USD,
                     starting_balances=[Money(1_000_000, USD)])
    inst = TestInstrumentProvider.equity(symbol="SPY", venue="SIM")
    engine.add_instrument(inst)
    bar_type = BarType.from_str(f"{inst.id}-5-MINUTE-LAST-EXTERNAL")
    start = pd.Timestamp("2026-08-31 13:30", tz="UTC")
    bars = []
    for i in range(n_bars):
        ts = int((start + pd.Timedelta(minutes=5 * i)).value)
        px = 500.0 + (i % 7) * 0.25          # deterministic, some range
        bars.append(Bar(bar_type=bar_type,
                        open=inst.make_price(px), high=inst.make_price(px + 0.75),
                        low=inst.make_price(px - 0.75), close=inst.make_price(px + 0.10),
                        volume=Quantity.from_int(1000), ts_event=ts, ts_init=ts))
    engine.add_data(bars)
    actor = AlphaZeroActor(bar_type=bar_type, policy=NullEntryPolicy())
    strat = StockfishStrategy(tier=Tier.T_SIM, sim_attestations=ALL_TRUE,
                              bar_type=bar_type)
    engine.add_actor(actor)
    engine.add_strategy(strat)
    engine.run()
    engine.dispose()
    return actor, strat


def test_the_full_loop_completes_and_fills():
    actor, strat = _drive()
    assert actor.published > 0
    assert strat.received == actor.published
    assert strat.authorized > 0
    assert strat.orders_submitted > 0
    assert strat.orders_filled > 0, "no fills — the car did not drive"


def test_orphans_are_counted_not_assumed_zero():
    _, strat = _drive()
    assert strat.orphans == max(0, strat.positions_opened - strat.positions_closed)


def test_warmup_is_a_counted_refusal_not_a_silent_drop():
    """Directives arriving before ATR14 has 14 bars must be visibly refused."""
    _, strat = _drive()
    assert strat.rejected_warmup > 0
    assert strat.refusal_reasons.get("warmup") == strat.rejected_warmup
