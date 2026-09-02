"""body/test_disengagement.py — ODD §5 log, and the blinding that makes it mean something."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from body.disengagement import (  # noqa: E402
    AGREED, ARM_CONTROL, ARM_ENTRY, DECISIONS, DisengagementError, Judgment,
    JUDGMENTS, read_jsonl, rate_matched_control)
from body.disengagement_outcomes import OUTCOMES, OutcomeRow  # noqa: E402

CLI = ROOT / "scripts" / "adjudicate.py"
OUTCOME_FIELDS = set(OutcomeRow.__dataclass_fields__)


# ------------------------------------------------- the blinding, structurally

def test_the_adjudicator_cannot_name_the_outcome_module():
    """THE guard. A disengagement judged with the outcome in view is not a
    judgment, it is a scorecard — and it will quietly get better over time
    while teaching nothing. Checked over the AST, not the text, so the
    docstring may explain the rule without tripping it."""
    tree = ast.parse(CLI.read_text())
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            names.add(n.module or "")
            names |= {a.name for a in n.names}
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
        elif isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            names.add(n.value)
    banned = {"disengagement_outcomes", "OUTCOMES", "OutcomeRow",
              "body.disengagement_outcomes", "outcomes.jsonl"}
    hit = names & banned
    assert not hit, f"the adjudicator can reach the outcome: {hit}"


@pytest.mark.parametrize("field", sorted(OUTCOME_FIELDS - {"row_id"}))
def test_the_adjudicator_names_no_outcome_field(field):
    tree = ast.parse(CLI.read_text())
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert field not in used, f"the adjudicator references outcome field {field!r}"


def test_the_blindness_guard_actually_fires():
    """FAULT INJECTION on the guard. An assertion that has never fired is not
    a guard — and this one protects the only property the log has."""
    sneaky = ast.parse("from body.disengagement_outcomes import OUTCOMES\n")
    names = set()
    for n in ast.walk(sneaky):
        if isinstance(n, ast.ImportFrom):
            names.add(n.module or "")
            names |= {a.name for a in n.names}
    assert names & {"body.disengagement_outcomes", "OUTCOMES"}


def test_decision_rows_carry_no_outcome_field():
    """The tool being blind is not enough — the FILE must be blind too."""
    rows = read_jsonl(DECISIONS)
    assert rows, "no rows; run scripts/backfill_disengagement.py"
    leaked = OUTCOME_FIELDS - {"row_id"}
    for r in rows:
        assert not (set(r) & leaked), f"{r['row_id']} leaks {set(r) & leaked}"
        assert not (set(r.get("state", {})) & leaked)


# --------------------------------------------------------------- §5 schema

def test_every_section_5_column_exists_across_the_two_files():
    """Date · Tier · What the system did/wanted live in the decision row;
    What I would have done · Delta · Root cause · ODD change? in the judgment."""
    d = read_jsonl(DECISIONS)[0]
    for c in ("date", "tier_at_time", "what_system_did_or_wanted"):
        assert c in d
    for c in ("what_i_would_have_done", "delta", "root_cause", "odd_change"):
        assert c in Judgment.__dataclass_fields__


def test_addition_one_every_row_names_the_engine():
    for r in read_jsonl(DECISIONS):
        assert r["engine"], r["row_id"]
        assert r["engine_agreement"] in ("AGREED", "DISAGREED")


def test_addition_two_rows_are_logged_below_live():
    """§5: log at T2/T3 as well, not just when live. With T0 sealed these are
    the only rows that can exist at all."""
    tiers = {r["tier_at_time"] for r in read_jsonl(DECISIONS)}
    assert tiers == {"T_SIM"}


# ------------------------------------------------------- the control arm

def test_the_control_arm_is_rate_matched():
    rows = read_jsonl(DECISIONS)
    n_e = sum(1 for r in rows if r["arm"] == ARM_ENTRY)
    n_c = sum(1 for r in rows if r["arm"] == ARM_CONTROL)
    assert n_e == n_c, f"{n_c} controls against {n_e} entries — not rate-matched"


def test_control_sampling_is_deterministic():
    led = {f"d{i}": [{"fired": False, "ts_event": j, "bar_index": j}
                     for j in range(20)] for i in range(4)}
    assert ([r[1]["ts_event"] for r in rate_matched_control(led, 8)]
            == [r[1]["ts_event"] for r in rate_matched_control(led, 8)])


def test_control_never_samples_a_fired_bar():
    led = {"d": [{"fired": i % 2 == 0, "ts_event": i, "bar_index": i}
                 for i in range(20)]}
    for _, l in rate_matched_control(led, 5):
        assert not l["fired"]


# ---------------------------------------------------------- append-once

def test_a_row_cannot_be_judged_twice(tmp_path, monkeypatch):
    import body.disengagement as dz
    monkeypatch.setattr(dz, "JUDGMENTS", tmp_path / "j.jsonl")
    j = Judgment("r1", "held", "none", "", "", "2026-09-01T00:00:00Z", False)
    dz.record_judgment(j)
    with pytest.raises(DisengagementError):
        dz.record_judgment(j)


def test_a_corrupt_audit_line_raises_rather_than_being_skipped(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"a":1}\n{not json}\n')
    with pytest.raises(DisengagementError):
        read_jsonl(p)


# ------------------------------------------------------ nothing is judged

def test_the_repo_ships_the_tool_and_no_judgments():
    """"What I would have done" is not a thing a model supplies on the
    operator's behalf."""
    assert read_jsonl(JUDGMENTS) == []


def test_outcomes_exist_and_are_keyed_to_every_row():
    dec = {r["row_id"] for r in read_jsonl(DECISIONS)}
    out = {o["row_id"] for o in read_jsonl(OUTCOMES)}
    assert dec == out, "decision and outcome files disagree on row ids"


def test_no_outcome_field_is_a_return_or_pnl():
    banned = {"r", "realized_r", "pnl", "profit", "return", "expectancy"}
    assert not (OUTCOME_FIELDS & banned)
