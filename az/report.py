#!/usr/bin/env python3
"""az/report.py — N_days is mechanical, not remembered. Spec 049 §7, I80.

The unit error twice over: 54 snapshots per trade made a Phase 1 grid read
4.4%-thin when it was 57.5%-thin by trade; 40 candidates per day would do the
same here. Rows within a day share a session, a regime, the news and the path.
A day with 40 legal candidates is ONE observation.

So a candidate count may not be emitted without its N_days beside it, and that is
enforced by a test that scans this package rather than by anyone remembering.
Precedent: az/state.py::Occupancy already ships every count beside its denominator.
"""
from __future__ import annotations

from dataclasses import dataclass, field

ROWS_LABEL = "not the sample size"


@dataclass(frozen=True)
class Tally:
    """Sample-size-honest counts. `n_days` is required and always rendered."""
    n_days: int
    n_candidates: int
    n_legal: int = 0
    n_illegal: int = 0
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.n_days, int) or self.n_days < 0:
            raise ValueError(f"n_days must be a non-negative int, got {self.n_days!r}")
        if self.n_legal + self.n_illegal not in (0, self.n_candidates):
            raise ValueError(
                f"legal({self.n_legal}) + illegal({self.n_illegal}) must equal "
                f"candidates({self.n_candidates}) or both be 0")

    @property
    def illegal_fraction(self) -> float:
        return self.n_illegal / self.n_candidates if self.n_candidates else 0.0

    def header(self) -> str:
        """The ONE rendering. Always both numbers, rows always labelled."""
        s = (f"N_days = {self.n_days:,}   "
             f"candidate rows = {self.n_candidates:,} ({ROWS_LABEL})")
        if self.n_candidates and (self.n_legal or self.n_illegal):
            s += f"   legal {self.n_legal:,}   illegal {self.n_illegal:,} ({self.illegal_fraction:.1%})"
        for k, v in self.extra.items():
            s += f"   {k} {v}"
        return s

    def __str__(self) -> str:
        return self.header()
