"""Spec 044 — invariants I58-I62 for the exit mechanism taxonomy.

Each invariant below is checked with a small pure function that RETURNS the
list of violations rather than asserting directly, so the same function can
be pointed at the real registry (must be empty) and, during manual fault
injection, at a real edit of `exit_taxonomy.py` (must be non-empty — see the
mutation harness kept in the scratchpad per CLAUDE.md, not committed here).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from exit_taxonomy import FALSIFIED_CLAIMS, MECHANISMS, FalsifiedClaim, Mechanism
from stockfish_exit import Stage, TradeState, stop_candidates

ROOT = Path(__file__).resolve().parent.parent
MECHANISMS_JSON = ROOT / "MECHANISMS.json"


def _reference_state() -> TradeState:
    """A trade far enough along that every stop layer is CONSTRUCTED — the
    same shape as `stockfish_exit.py --layers`'s own example."""
    return TradeState(direction=+1, entry=204.0, qty=163, price=205.9, sl=204.0,
                      tp1=204.9, tp2=205.8, trail_dist=0.8, stage=Stage.SCALED)


# ---------------------------------------------------------------- I58 helper

def check_i58(registry=MECHANISMS):
    """Every ACTIVE stop-family registry entry appears in stop_candidates(),
    and every stop_candidates() name appears in the registry (any status)."""
    violations = []
    candidate_names = {c.name for c in stop_candidates(_reference_state())}
    registry_names = {m.name for m in registry if m.family == "STOP_PLACEMENT"}

    for m in registry:
        if m.family == "STOP_PLACEMENT" and m.status == "ACTIVE" and m.name not in candidate_names:
            violations.append(f"registry ACTIVE stop {m.name!r} not in stop_candidates()")

    for name in candidate_names:
        if name not in registry_names:
            violations.append(f"stop_candidates() layer {name!r} not in registry")

    return violations


def test_i58_stop_layers_match_registry_both_directions():
    assert check_i58() == []


# ---------------------------------------------------------------- I59 helper

def check_i59(registry=MECHANISMS):
    return [m.name for m in registry if m.status != "ACTIVE" and not m.reason]


def test_i59_non_active_has_nonempty_reason():
    assert check_i59() == []


# ------------------------------------------------------- I60a/b/c helpers
#
# Amendment 1 to spec 044: the original I60 bound `Mechanism.status ==
# "FALSIFIED"`, but no registry row was ever given that status (a claim about
# a mechanism is not the mechanism itself — see `FalsifiedClaim` in
# exit_taxonomy.py), so the check was unreachable. Replaced by I60a/b/c
# against the sibling `FALSIFIED_CLAIMS` structure.

def _load_mechanisms_json(path: Path = MECHANISMS_JSON) -> dict:
    return json.loads(path.read_text())


def check_i60a(claims=FALSIFIED_CLAIMS, mechanisms_data=None):
    """Every mechanism whose status is "killed" in MECHANISMS.json appears
    EXACTLY ONCE in FALSIFIED_CLAIMS. `mechanisms_data` is injectable so a
    fault injection can simulate a future unregistered kill WITHOUT editing
    the real MECHANISMS.json on disk."""
    if mechanisms_data is None:
        mechanisms_data = _load_mechanisms_json()
    killed_ids = [m["id"] for m in mechanisms_data.get("mechanisms", [])
                  if m.get("status") == "killed"]
    claim_ids = [c.mech_id for c in claims]
    violations = []
    for kid in killed_ids:
        count = claim_ids.count(kid)
        if count != 1:
            violations.append(f"{kid}: killed in MECHANISMS.json but appears "
                              f"{count} times in FALSIFIED_CLAIMS (want exactly 1)")
    return violations


def test_i60a_every_killed_mechanism_registered_exactly_once():
    assert check_i60a() == []


def test_i60a_detects_a_future_unregistered_kill_via_injected_data():
    """Direct exercise of the invariant per the amendment's instruction:
    inject a synthetic ledger (a copy, never the real MECHANISMS.json) with a
    'killed' id that FALSIFIED_CLAIMS does not carry, and confirm check_i60a
    actually catches it — the failure mode the original I60 could not."""
    fake_data = {"mechanisms": [{"id": "MECH-999-FUTURE-KILL", "status": "killed"}]}
    violations = check_i60a(mechanisms_data=fake_data)
    assert violations != []
    assert "MECH-999-FUTURE-KILL" in violations[0]


def check_i60b(claims=FALSIFIED_CLAIMS, mechanisms_data=None):
    """Every mech_id in FALSIFIED_CLAIMS resolves to a real MECHANISMS.json id."""
    if mechanisms_data is None:
        mechanisms_data = _load_mechanisms_json()
    ids = {m["id"] for m in mechanisms_data.get("mechanisms", [])}
    return [c.mech_id for c in claims if c.mech_id not in ids]


def test_i60b_claim_ids_resolve_in_mechanisms_json():
    assert check_i60b() == []


def check_i60c(claims=FALSIFIED_CLAIMS, registry=MECHANISMS):
    """Every name in any claim's `about` tuple resolves to a real Mechanism.name."""
    names = {m.name for m in registry}
    violations = []
    for c in claims:
        for about_name in c.about:
            if about_name not in names:
                violations.append(f"{c.mech_id}: about {about_name!r} is not a "
                                  "registered Mechanism.name")
    return violations


def test_i60c_about_names_resolve_to_registry():
    assert check_i60c() == []


def test_urgency_tighten_and_mech_003_do_not_contradict():
    """The one place I60c and the registry must agree by hand: `urgency_tighten`
    stays ACTIVE (the channel is wired) while MECH-003 in FALSIFIED_CLAIMS
    records that its VALUE CLAIM died — both true at once, not a conflict."""
    tighten = next(m for m in MECHANISMS if m.name == "urgency_tighten")
    mech_003 = next(c for c in FALSIFIED_CLAIMS if c.mech_id == "MECH-003")
    assert tighten.status == "ACTIVE"
    assert "urgency_tighten" in mech_003.about


# ---------------------------------------------------------------- I61 helper

# The parameter firewall, operationally: a run of digits counts as a
# "standalone numeric literal" only if the character immediately before the
# run is not a letter — TP1 / C003 / ATR14 are names, not parameters.
PARAM_LEAK_RE = re.compile(r"(?<![A-Za-z0-9])\d+")


def has_standalone_numeric_literal(text: str) -> bool:
    return bool(PARAM_LEAK_RE.search(text))


@pytest.mark.parametrize("text,expected", [
    ("TP1", False),
    ("C003", False),
    ("ATR14", False),
    ("0.5", True),
    ("2x", True),
    ("3 ATR", True),
    ("11:00", True),
])
def test_i61_regex_matches_spec_examples(text, expected):
    assert has_standalone_numeric_literal(text) is expected


def check_i61(registry=MECHANISMS):
    return [m.name for m in registry if has_standalone_numeric_literal(m.definition)]


def test_i61_no_definition_leaks_a_parameter():
    assert check_i61() == []


# ---------------------------------------------------------------- I62 helper

def check_i62(registry=MECHANISMS):
    valid_fields = set(TradeState.__dataclass_fields__.keys())
    violations = []
    for m in registry:
        for field_name in m.requires:
            if field_name not in valid_fields:
                violations.append(f"{m.name}: requires {field_name!r} is not a "
                                  "TradeState field")
    return violations


def test_i62_requires_are_real_tradestate_fields():
    assert check_i62() == []


# --------------------------------------------------------- registry sanity

def test_registry_is_nonempty_and_frozen_dataclass():
    assert len(MECHANISMS) > 0
    assert all(isinstance(m, Mechanism) for m in MECHANISMS)
    m = MECHANISMS[0]
    with pytest.raises(Exception):
        m.name = "mutated"  # frozen dataclass must refuse attribute assignment


def test_falsified_claims_is_nonempty_and_frozen_dataclass():
    assert len(FALSIFIED_CLAIMS) > 0
    assert all(isinstance(c, FalsifiedClaim) for c in FALSIFIED_CLAIMS)
    c = FALSIFIED_CLAIMS[0]
    with pytest.raises(Exception):
        c.mech_id = "mutated"  # frozen dataclass must refuse attribute assignment


def test_mech_001_is_not_forced_into_falsified_claims():
    """The documented, deliberately-not-reconciled ledger discrepancy: prose
    says MECH-001 died, MECHANISMS.json says status="proposed". I60a binds on
    the ledger's own status field, so MECH-001 correctly stays absent here."""
    data = _load_mechanisms_json()
    mech_001 = next(m for m in data["mechanisms"] if m["id"] == "MECH-001")
    assert mech_001["status"] == "proposed"
    assert "MECH-001" not in {c.mech_id for c in FALSIFIED_CLAIMS}
