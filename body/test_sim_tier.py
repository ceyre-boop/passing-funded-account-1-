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


# ============================================ council review, 2026-09-01
# Two confirmed defects. Both are cases where an invariant proved in the
# decision layer is not a property of the system that touches a venue.

from body.runtime import assert_no_live_execution_client, is_live_execution_engine  # noqa: E402
from body.stockfish_strategy import quantize_protective  # noqa: E402

INST = TestInstrumentProvider.equity(symbol="SPY", venue="SIM")


def test_make_price_really_does_loosen_a_stop():
    """The defect, reproduced. If this ever stops being true the fix below is
    unnecessary — but it must be shown, not assumed."""
    assert float(INST.make_price(103.925)) == 103.92        # long stop, moved DOWN
    assert float(INST.make_price(96.075)) == 96.08          # short stop, moved UP


@pytest.mark.parametrize("level,direction", [
    (103.925, LONG), (103.925, SHORT), (96.075, LONG), (96.075, SHORT),
    (500.004, LONG), (499.996, SHORT), (100.0, LONG), (100.0, SHORT),
])
def test_quantization_never_loosens(level, direction):
    q = float(quantize_protective(level, instrument=INST, direction=direction))
    assert (q - level) * direction >= -1e-12, (
        f"quantized {level} -> {q} on direction {direction:+d}: LOOSER")


def test_quantization_lands_on_the_tick_grid():
    for level in (103.925, 96.075, 500.004):
        for d in (LONG, SHORT):
            q = float(quantize_protective(level, instrument=INST, direction=d))
            assert abs(q * 100 - round(q * 100)) < 1e-9, f"{q} is off the 1c grid"


def test_quantization_costs_at_most_one_tick():
    """Tighter is safe; tighter by a mile is a different bug."""
    for level in (103.925, 96.075, 500.004, 412.3333):
        for d in (LONG, SHORT):
            q = float(quantize_protective(level, instrument=INST, direction=d))
            assert abs(q - level) <= 0.01 + 1e-9


def test_a_long_stop_rounds_up_and_a_short_stop_rounds_down():
    assert float(quantize_protective(103.925, instrument=INST, direction=LONG)) == 103.93
    assert float(quantize_protective(96.075, instrument=INST, direction=SHORT)) == 96.07


# ---- the second T_SIM sensor -------------------------------------------------

def test_the_live_engine_sensor_is_not_vacuous():
    """FAULT INJECTION on the sensor itself. The first draft of this check
    iterated `registered_clients`, which returns ClientIds rather than client
    objects, so `isinstance(c, LiveExecutionClient)` was False for every element
    and the check passed while measuring nothing."""
    from nautilus_trader.execution.engine import ExecutionEngine
    from nautilus_trader.live.execution_engine import LiveExecutionEngine
    live = LiveExecutionEngine.__new__(LiveExecutionEngine)
    sim = ExecutionEngine.__new__(ExecutionEngine)
    assert is_live_execution_engine(live) is True
    assert is_live_execution_engine(sim) is False
    assert issubclass(LiveExecutionEngine, ExecutionEngine)


def test_a_live_execution_engine_is_refused():
    from nautilus_trader.live.execution_engine import LiveExecutionEngine
    with pytest.raises(OddError):
        assert_no_live_execution_client(
            LiveExecutionEngine.__new__(LiveExecutionEngine))


def test_an_absent_sensor_fails_closed():
    """An unverifiable claim is UNKNOWN, and UNKNOWN must not read as safe."""
    with pytest.raises(OddError):
        assert_no_live_execution_client(None)


def test_the_sensor_is_independent_of_the_clock():
    """The point of the second lock: it must not be the clock read twice.

    Checked over the CODE, not the source text — the docstring legitimately
    explains why it is not the clock, and a naive substring search flags its own
    explanation. That is the same false positive the trade_id guard hit, so it
    is worth not repeating."""
    import ast, inspect, textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(is_live_execution_engine)))
    fn = tree.body[0]
    body = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                           and isinstance(fn.body[0].value, ast.Constant)) else fn.body
    names = {n.id for b in body for n in ast.walk(b) if isinstance(n, ast.Name)}
    attrs = {n.attr for b in body for n in ast.walk(b) if isinstance(n, ast.Attribute)}
    assert not any("clock" in x.lower() for x in names | attrs), (
        f"the second sensor touches the clock: {names | attrs}")
    assert any("ExecutionEngine" in x for x in names), \
        "the sensor should be discriminating on the execution engine type"


def test_the_order_site_actually_uses_the_protective_quantizer():
    """FAULT INJECTION that a first pass missed.

    Testing `quantize_protective` in isolation proves the function is correct
    and proves NOTHING about the order that reaches the venue. Reverting the
    call site to `instrument.make_price(stop)` left every other test green —
    which is the same gap-between-proof-and-path this whole review is about,
    reproduced inside the fix for it.

    So: assert on the call site itself. `sl_trigger_price` must be bound to a
    call to `quantize_protective`, whatever else changes around it."""
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "body" / "stockfish_strategy.py"
    tree = ast.parse(src.read_text())

    bindings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "sl_trigger_price":
                fn = kw.value.func if isinstance(kw.value, ast.Call) else None
                name = (getattr(fn, "id", None) or getattr(fn, "attr", None)
                        if fn is not None else None)
                bindings.append(name)

    assert bindings, "no sl_trigger_price is bound anywhere — did the order site move?"
    assert all(b == "quantize_protective" for b in bindings), (
        f"a stop reaches the venue without side-aware quantization: {bindings}")
