#!/usr/bin/env python3
"""MEASUREMENT HYGIENE — the invariant behind four repeated errors.

Four times now an instrument has included its own artifacts in what it
measured:

  M9   the four-book fingerprint compared a value with itself
  M13  a mutation row "killed" a test that did not exist
  M26  the same, again
  SF-1 the stack exam counted its own source as a second decide_exit

Different surfaces, one shape: **no measurement process may include its own
artifacts in its input set.** Left as a habit, the fifth instance will look
different enough to slip through. So it is a check, and the exclusion is
ASSERTED rather than assumed — a filter that silently removes nothing is how
this class of error survives a code review.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Directories that hold measurement OUTPUT. An instrument reading these is
# reading its own exhaust.
ARTIFACT_DIRS = ("data/daytrade/stack_exam.json", "data/daytrade/ontology_audit.json",
                 "data/daytrade/mechanism_fpr_report.json",
                 "data/daytrade/exit_quality.json")


class MeasurementError(RuntimeError):
    """An instrument measuring itself. Never downgraded to a warning."""


def caller_path(depth: int = 2) -> Path:
    """The file of whoever called into this module."""
    import inspect
    frame = inspect.stack()[depth]
    return Path(frame.filename).resolve()


def exclude_self(paths, *, self_path: Path | None = None,
                 require_exclusion: bool = True) -> list[Path]:
    """Drop the calling module's own file from a candidate input set.

    `require_exclusion=True` (the default) ASSERTS that the caller was
    actually present and removed. A no-op filter is the failure mode — it
    looks like protection and provides none — so by default this refuses to
    pass silently. Callers whose inputs genuinely cannot contain themselves
    pass require_exclusion=False and say so.
    """
    me = (self_path or caller_path()).resolve()
    kept, dropped = [], []
    for p in paths:
        q = Path(p).resolve()
        (dropped if q == me else kept).append(q)
    if require_exclusion and not dropped:
        raise MeasurementError(
            f"self-exclusion asserted but {me.name} was not in the input set. "
            "Either the filter is pointed at the wrong paths, or it is a no-op "
            "that provides no protection — both are the error this guard "
            "exists to catch. Pass require_exclusion=False if the inputs "
            "genuinely cannot contain the instrument.")
    return kept


def grep_sources(pattern: str, root: Path | None = None, *,
                 self_path: Path | None = None,
                 require_exclusion: bool = True) -> list[Path]:
    """`grep -rl` over source, with the instrument's own file removed and the
    removal asserted. This is the SF-1 error, closed."""
    root = root or (ROOT / "daytrade")
    out = subprocess.run(["grep", "-rl", pattern, str(root)],
                         capture_output=True, text=True).stdout.split()
    return exclude_self([Path(p) for p in out],
                        self_path=self_path or caller_path(),
                        require_exclusion=require_exclusion)


def assert_disjoint(inputs, outputs, *, label: str = "measurement") -> None:
    """An instrument must not read what it writes. Catches the higher-level
    version: a report that ingests the previous run's own report."""
    a = {Path(p).resolve() for p in inputs}
    b = {Path(p).resolve() for p in outputs}
    overlap = a & b
    if overlap:
        raise MeasurementError(
            f"{label}: reads and writes overlap on "
            f"{sorted(p.name for p in overlap)} — the instrument would be "
            "measuring its own exhaust")


if __name__ == "__main__":
    print(__doc__)
    print(f"artifact paths tracked: {len(ARTIFACT_DIRS)}")
    sys.exit(0)
