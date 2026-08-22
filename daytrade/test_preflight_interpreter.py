"""Preflight — the loop must stay importable under the interpreter launchd
actually resolves, not just the one the human's shell resolves to.

launchd jobs with no EnvironmentVariables/PATH key fall back to
`/usr/bin/python3` (macOS's stock 3.9.x), which is a different interpreter
from the homebrew 3.14 on an interactive shell's PATH. PEP 604 union
annotations (`dict | None`) are not runtime-evaluable under 3.9 without
`from __future__ import annotations`, so a single missing future-import on
any loop module makes that module — and everything that imports it — fail
at import time, silently, on every launchd tick. This is exactly the bug
that let `write_baseline_plan.py` crash for 98 consecutive ticks with no
plan.json ever written and no visible signal.

This test exercises the real failure mode directly: shell out to
`/usr/bin/python3` (never the test runner's own interpreter, which may
already be 3.10+ and would mask the bug) and import the loop's core modules.
Skips cleanly on a machine where `/usr/bin/python3` doesn't exist (e.g. CI
images without Apple's stock Python) rather than failing for an unrelated
reason.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

DT = Path(__file__).resolve().parent
SYSTEM_PYTHON3 = "/usr/bin/python3"

LOOP_MODULES = ("stockfish_exit", "runner", "ceiling", "write_baseline_plan")


@pytest.mark.skipif(
    not Path(SYSTEM_PYTHON3).exists(),
    reason=f"{SYSTEM_PYTHON3} not present on this machine",
)
def test_loop_modules_import_under_system_python3():
    """The modules on the live launchd path must import cleanly under
    /usr/bin/python3 — the interpreter launchd actually uses when its plist
    has no PATH override, regardless of what interpreter is running pytest."""
    import_stmt = "import " + ", ".join(LOOP_MODULES)
    result = subprocess.run(
        [SYSTEM_PYTHON3, "-c", import_stmt],
        cwd=DT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"`{SYSTEM_PYTHON3} -c '{import_stmt}'` failed (exit "
        f"{result.returncode}) — a loop module is not importable under the "
        f"interpreter launchd falls back to. stderr:\n{result.stderr}"
    )
