"""The live path is invoked differently from the test suite, and that gap hid a
ModuleNotFoundError in production for a full commit.

`daytrade/operator_tick.sh` does `cd daytrade && python3 write_baseline_plan.py`.
There is no `daytrade` package on sys.path in that context. The test suite, by
contrast, imports `daytrade.bars` as a package — which works. So a
`from daytrade import datasource` inside bars.py passed every test and broke
every tick.

These tests run the REAL invocation in a subprocess. An import style that only
works under pytest is not wired.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

DAYTRADE = Path(__file__).resolve().parent
TICK = DAYTRADE / "operator_tick.sh"

# Modules operator_tick.sh invokes as bare scripts with cwd=daytrade/.
LIVE_SCRIPTS = ["bars", "write_baseline_plan", "news_archive",
                "decision_ledger", "alpha_operator"]


@pytest.mark.parametrize("mod", LIVE_SCRIPTS)
def test_importable_the_way_the_tick_invokes_it(mod):
    """cwd=daytrade/, no package on the path — exactly what launchd does."""
    r = subprocess.run([sys.executable, "-c", f"import {mod}"],
                       cwd=DAYTRADE, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (
        f"`cd daytrade && python3 -c 'import {mod}'` failed — this is the live "
        f"invocation, and passing under pytest does not make it wired.\n"
        f"{r.stderr[-1500:]}")


def test_tick_script_still_invokes_from_inside_daytrade():
    """If the tick ever stops cd-ing into daytrade/, the test above stops
    testing the real thing and would pass vacuously."""
    src = TICK.read_text()
    assert "cd " in src and "write_baseline_plan.py" in src, (
        "operator_tick.sh no longer matches the invocation these tests model")
