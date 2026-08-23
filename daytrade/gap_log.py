#!/usr/bin/env python3
"""SIM→LIVE GAP — spec 024. The simulator's honesty, tracked first-class.

A simulator that has stopped predicting fills is a tripwire event, not a
monthly-review footnote. This module matches trades present in BOTH the 022
execution ledger's sim and paper lanes by trade_id, records the R delta per
trade, classifies drift by a PRE-COMMITTED rule, and exposes a tripwire that
blocks the promotion REPORT (017's promotion_decision itself is untouched —
the tripwire wraps the report, it does not edit the gate).

Pre-committed readings, decided before any data (spec 024):
  constant   — sim is a usable proxy with an offset; carry on.
  widening   — the simulator has stopped predicting fills; tripwire fires.
  collapsing — suspicious in the good direction; check the match logic.

<10 matched rows reads `insufficient` — labeled, never silently zero. Same
doctrine as regret.py's slippage_basis="paper": absence of measurement is a
stated fact, not a default.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Literal, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GAP_TRIPWIRE_R = 0.05              # pre-committed (spec 024); never tuned later
MIN_ROWS = 10
_HALF = 5                          # last-5 vs prior-5 mean |delta|
_BAND_FRAC = 0.25                  # widening/collapsing = ±25% vs prior half

Drift = Literal["constant", "widening", "collapsing", "insufficient"]


class GapError(RuntimeError):
    """Malformed ledger rows or an unusable match. Never repaired silently."""


@dataclass(frozen=True)
class GapRecord:
    trade_id: str
    sim_r: float
    real_r: float
    delta_r: float                 # real - sim; sign kept, drift uses |delta|
    sim_basis: str
    real_basis: str
    ts: str                        # the real fill's close timestamp

    def to_dict(self) -> dict:
        return asdict(self)


def _closed_r_rows(rows: list[dict]) -> dict[str, dict]:
    out = {}
    for r in rows:
        if r.get("outcome") in (None, "OPEN"):
            continue
        if r.get("superseded"):
            # A superseded row describes the SAME event as another row this
            # scan will already see (execution_ledger.mark_superseded). It
            # stays readable in the ledger — it is just never scored: counting
            # it here would double-count one real trade as two, inflating
            # whatever "positive frequency" the gap drift reads off of.
            continue
        if r.get("r_realized") is None:
            raise GapError(f"closed trade {r.get('trade_id')!r} has no r_realized — "
                           "a closed row without R cannot be gap-scored")
        out[r["trade_id"]] = r
    return out


def compute_gaps(sim_rows: list[dict], real_rows: list[dict]) -> tuple[list, dict]:
    """Match trade_ids present in BOTH lanes (spec 024 I18). One-sided trades
    are REPORTED in the second return value, never scored as a gap of zero."""
    sim = _closed_r_rows(sim_rows)
    real = _closed_r_rows(real_rows)
    both = sorted(set(sim) & set(real))
    gaps = [GapRecord(
        trade_id=tid,
        sim_r=float(sim[tid]["r_realized"]),
        real_r=float(real[tid]["r_realized"]),
        delta_r=float(real[tid]["r_realized"]) - float(sim[tid]["r_realized"]),
        sim_basis=str(sim[tid].get("mode", "sim")),
        real_basis=str(real[tid].get("mode", "paper")),
        ts=str(real[tid].get("exit_timestamp") or real[tid].get("entry_timestamp") or ""))
        for tid in both]
    unmatched = {"sim_only": sorted(set(sim) - set(real)),
                 "real_only": sorted(set(real) - set(sim))}
    return gaps, unmatched


def classify_drift(gaps: list[GapRecord]) -> Drift:
    """The pre-committed rule, exactly (spec 024 I19): mean |delta| of the
    last 5 vs the prior 5; needs >= 10 rows, else `insufficient`."""
    if len(gaps) < MIN_ROWS:
        return "insufficient"
    recent = gaps[-_HALF:]
    prior = gaps[-2 * _HALF:-_HALF]
    m_recent = sum(abs(g.delta_r) for g in recent) / _HALF
    m_prior = sum(abs(g.delta_r) for g in prior) / _HALF
    if m_recent > m_prior * (1 + _BAND_FRAC):
        return "widening"
    if m_recent < m_prior * (1 - _BAND_FRAC):
        return "collapsing"
    return "constant"


def promotion_tripwire(gaps: list[GapRecord], *,
                       band_r: float = GAP_TRIPWIRE_R) -> Optional[str]:
    """Refusal reason when the simulator has stopped predicting fills
    (spec 024 I20): drift is widening AND the recent mean |delta| exceeds the
    pre-committed band. None = no objection (including `insufficient` — an
    unmeasured gap cannot fire a tripwire, it can only be reported as
    unmeasured)."""
    drift = classify_drift(gaps)
    if drift != "widening":
        return None
    m_recent = sum(abs(g.delta_r) for g in gaps[-_HALF:]) / _HALF
    if m_recent > band_r:
        return (f"sim→live gap is WIDENING (recent mean |ΔR| {m_recent:.3f} > "
                f"band {band_r}) — the simulator has stopped predicting fills; "
                "promotion blocked until investigated")
    return None


def load_ledger_rows(mode: str) -> list[dict]:
    """Every closed-trade row of one ledger lane, via 022's own reader."""
    from sovereign.intelligence.execution_ledger import ledger_dir, _read_records
    d = ledger_dir(mode)
    rows: list[dict] = []
    if d.exists():
        for p in sorted(d.glob("*.jsonl")):
            rows.extend(_read_records(p))
    return rows


def status(*, sim_mode: str = "sim", real_mode: str = "paper") -> dict:
    gaps, unmatched = compute_gaps(load_ledger_rows(sim_mode),
                                   load_ledger_rows(real_mode))
    return {"n_gaps": len(gaps), "drift": classify_drift(gaps),
            "tripwire": promotion_tripwire(gaps), "unmatched": unmatched,
            "recent": [g.to_dict() for g in gaps[-5:]]}


if __name__ == "__main__":
    print(json.dumps(status(), indent=1))
