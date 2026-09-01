"""body/runtime.py — where are we actually running?

THE ONE RULE
    This must be a KERNEL FACT, never a configuration flag. T_SIM is a door in a
    wall built on purpose, and a boolean in a config file is precisely the kind of
    key that ends up set in the wrong place at the wrong time.

    Nautilus injects the clock: a `BacktestEngine` hands its components a
    `TestClock`, a live `TradingNode` hands them a `LiveClock`. Nothing the
    operator writes changes that, and nothing in this repo can ask for a
    `TestClock` in a live node without deliberately constructing one.

    Verified against the installed package: a strategy's `self.clock` inside a
    BacktestEngine is a `TestClock` (`isinstance` True, `LiveClock` False).

FAIL CLOSED
    Anything that is not provably a TestClock is reported LIVE, not UNKNOWN --
    the more dangerous of the two readings, on purpose. A clock we cannot
    identify is not a simulator.
"""
from __future__ import annotations

import sys
from pathlib import Path

from nautilus_trader.common.component import LiveClock, TestClock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "az") not in sys.path:
    sys.path.insert(0, str(ROOT / "az"))

from odd import OddError, RunEnvironment  # noqa: E402


def detect_environment(clock) -> RunEnvironment:
    """Derive the environment structurally from the kernel-injected clock.

    There is deliberately no override parameter, no env var, and no config hook.
    Adding one would defeat the entire guard."""
    if clock is None:
        return RunEnvironment.LIVE          # unidentified is treated as dangerous
    if isinstance(clock, TestClock):
        return RunEnvironment.BACKTEST
    if isinstance(clock, LiveClock):
        return RunEnvironment.LIVE
    return RunEnvironment.LIVE


# --------------------------------------------------- the second sensor

def is_live_execution_engine(exec_engine) -> bool:
    """True iff this kernel's execution engine is the LIVE one.

    A SEPARATE SENSOR, ON PURPOSE
        `detect_environment` reads the injected clock. The T_SIM attestation
        used to be a hardcoded `Truth.TRUE` beside a comment claiming it was
        "independently re-checked from the injected clock" — which made it the
        same sensor read twice, and two locks with one sensor is one lock.

        This reads a different object entirely: which ExecutionEngine class the
        kernel constructed. `LiveExecutionEngine` is a strict subclass used only
        by a live TradingNode; a BacktestEngine builds the plain
        `ExecutionEngine`. Nothing an operator writes changes which one exists.

    WHY NOT THE CLIENT LIST
        `exec_engine.registered_clients` returns `ClientId`s, NOT client
        objects, and there is no `get_client`. So
        `isinstance(c, LiveExecutionClient)` over that list is trivially False
        for every element — a check that passes while measuring nothing. That
        vacuous version was written and caught during review; the test below
        pins the distinction so it cannot come back.
    """
    from nautilus_trader.live.execution_engine import LiveExecutionEngine
    return isinstance(exec_engine, LiveExecutionEngine)


def assert_no_live_execution_client(exec_engine) -> None:
    """Refuse to attest a simulated venue when the kernel is a live one.

    Raises rather than returning False: a live execution engine underneath a
    simulation tier is a corrupted system, not a declined attestation."""
    if exec_engine is None:
        raise OddError(
            "no execution engine to inspect — refusing to attest no_live_venue "
            "from an absent sensor. An unverifiable claim is UNKNOWN, and "
            "UNKNOWN fails closed.")
    if is_live_execution_engine(exec_engine):
        raise OddError(
            f"{type(exec_engine).__name__} is a LIVE execution engine — refusing "
            "to attest no_live_venue. T_SIM exists only for a simulated kernel.")
