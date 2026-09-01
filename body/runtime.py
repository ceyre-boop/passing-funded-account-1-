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

from odd import RunEnvironment  # noqa: E402


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
