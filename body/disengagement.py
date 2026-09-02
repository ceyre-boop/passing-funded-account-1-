"""body/disengagement.py — ODD §5, the disengagement log.

WHAT §5 CALLS IT
    "The primary health metric." Not P&L, not R — the record of every time the
    operator would have done something other than what the system did or wanted.

    Target: disengagements per 100 decisions, trending down.
    Rule:   three disengagements with the same root cause = an ODD defect, not
            a judgment call.

THE TWO ADDITIONS FOR A TWO-ENGINE STACK, both §5-mandated
    1. Every row records WHICH ENGINE wanted what. A disengagement where the
       two engines disagreed with each other is a different defect class from
       one where both agreed and the operator overrode them, and mixing them
       hides the handoff failures.
    2. Rows are logged at T2/T3 — and here at T_SIM — not just when live. With
       T0 sealed these are the only rows that can be generated at all, so
       paper disengagements are the whole dataset.

BLIND ADJUDICATION IS THE POINT
    A disengagement log is worthless if the judgment is made knowing the
    outcome — you cannot learn "I would have overridden this" from a row that
    already tells you who was right. So the schema is split across files:

        decisions.jsonl   what was known AT the decision. Adjudicable.
        outcomes.jsonl    what happened next. NOT importable by the CLI.
        judgments.jsonl   the operator's call, append-once.

    The outcome path lives in `body/disengagement_outcomes.py`, a module the
    adjudication CLI does not import — and a test asserts the CLI's AST never
    names it. That is the structural half; the append-once rule below is the
    procedural half.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "data" / "daytrade" / "disengagement"
DECISIONS = DIR / "decisions.jsonl"
JUDGMENTS = DIR / "judgments.jsonl"

# §5 arms. ENTRY rows are what the system did; CONTROL rows are bars it
# declined, sampled rate-matched so MECH-006 has something to compare against.
ARM_ENTRY = "ENTRY"
ARM_CONTROL = "VETO_CONTROL"

# Which engine wanted what (§5 addition 1).
ENGINE_ALPHAZERO = "ALPHAZERO"      # meaning — publishes or withholds a directive
ENGINE_STOCKFISH = "STOCKFISH"      # mechanics — authorizes, sizes, executes

AGREED = "AGREED"                   # both engines wanted the same thing
DISAGREED = "DISAGREED"             # e.g. AZ published, SF refused


class DisengagementError(RuntimeError):
    """Refused. Never partial, never guessed."""


@dataclass(frozen=True)
class DecisionRow:
    """One adjudicable row. Contains ONLY what was knowable at the decision.

    If a field here could not have been computed at `decision_ts`, it is a
    leak and the row is not blind."""
    row_id: str
    date: str                       # §5 Date
    tier_at_time: str               # §5 Tier at time
    engine: str                     # §5 addition 1 — which engine
    engine_agreement: str           # AGREED | DISAGREED
    arm: str                        # ENTRY | VETO_CONTROL
    what_system_did_or_wanted: str  # §5 column 3
    decision_ts: int
    state: dict = field(default_factory=dict)   # eagerness/vetoes/ledger, as-of

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Judgment:
    """The operator's call. §5 columns 4-7. Written blind."""
    row_id: str
    what_i_would_have_done: str     # §5 column 4
    delta: str                      # §5 column 5 — the disagreement, if any
    root_cause: str                 # §5 column 6
    odd_change: str                 # §5 column 7
    judged_at: str
    disengaged: bool                # did the operator differ from the system?

    def to_dict(self) -> dict:
        return asdict(self)


def append_jsonl(path: Path, obj: dict) -> None:
    """Append + fsync. These are audit logs; a row the OS buffered but never
    flushed is a row that never happened at the worst possible moment."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(obj, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_jsonl(path: Path) -> list[dict]:
    """Every line parses or the load fails. A corrupt line in an append-only
    audit log is data loss to investigate, not a row to skip."""
    if not path.exists():
        return []
    out = []
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise DisengagementError(f"{path.name}:{i} is corrupt — {e}") from e
    return out


def judged_ids() -> set[str]:
    return {j["row_id"] for j in read_jsonl(JUDGMENTS)}


def record_judgment(j: Judgment) -> None:
    """Append-once. A second judgment on one row is a revision made knowing
    more than the first, which is exactly what blinding exists to prevent."""
    if j.row_id in judged_ids():
        raise DisengagementError(
            f"{j.row_id} is already judged. A row is adjudicated once — "
            "re-judging it means judging with knowledge the first call did not "
            "have, and that is the failure blinding exists to prevent.")
    append_jsonl(JUDGMENTS, j.to_dict())


def rate_matched_control(ledgers_by_session: dict, n_entries: int, *,
                         seed: int = 20260901) -> list:
    """Sample declined bars at the SAME rate entries were taken.

    MECH-006 claims "the days it refuses are worse than average, not merely
    fewer." That is only testable against a control drawn at the entry rate —
    comparing 39 entries to 1,521 refusals compares two different things. The
    sample is deterministic (seeded) and stratified across sessions so it is
    not clustered in whichever day happened to be quietest.
    """
    import random
    rng = random.Random(seed)
    sessions = sorted(ledgers_by_session)
    if not sessions or n_entries <= 0:
        return []
    per = max(1, round(n_entries / len(sessions)))
    picked = []
    for day in sessions:
        declined = [l for l in ledgers_by_session[day] if not l["fired"]]
        if not declined:
            continue
        k = min(per, len(declined))
        picked.extend((day, l) for l in rng.sample(declined, k))
    rng.shuffle(picked)
    return picked[:n_entries]
