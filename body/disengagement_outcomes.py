"""body/disengagement_outcomes.py — the sealed half of the §5 log.

THIS MODULE EXISTS TO BE UN-IMPORTABLE BY THE ADJUDICATOR.
    The outcome path is kept here, and nowhere else, so that "the adjudication
    tool cannot read the outcome" is a structural fact rather than a promise.
    `body/test_disengagement.py` walks the CLI's AST and fails if it names this
    module, this path, or any of these field names.

    Splitting the file is not theatre: a single module holding both paths means
    one careless import away from an adjudicator that can see the answer, and
    the failure would be invisible — the judgments would simply get better.

NO R, NO P&L.
    Outcomes are recorded in completions language and in magnitude, never in
    return. `forward_range_atr` is realized range over the following window,
    normalized by ATR at the decision — magnitude is the one thing this repo
    has measured as detectable, and it is not a P&L.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTCOMES = ROOT / "data" / "daytrade" / "disengagement" / "outcomes.jsonl"

EXIT_STOP = "STOP"
EXIT_TARGET = "TARGET"
EXIT_SESSION_CLOSE = "SESSION_CLOSE"
EXIT_NOT_TAKEN = "NOT_TAKEN"        # control arm: the system declined


@dataclass(frozen=True)
class OutcomeRow:
    row_id: str
    filled: bool
    exit_kind: str
    bars_held: int
    forward_range_atr: float        # magnitude, NOT return
    orphaned: bool

    def to_dict(self) -> dict:
        return asdict(self)
