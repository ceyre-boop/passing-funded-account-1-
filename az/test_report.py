"""I80: a candidate count may never be emitted without N_days. Enforced by scan."""
import ast
import re
from pathlib import Path

import pytest

from az.report import ROWS_LABEL, Tally

AZ = Path(__file__).resolve().parent
# words that mean "a count of candidate rows"
COUNT_RE = re.compile(r"n_candidates|len\(\s*(rows|candidates|cands)\s*\)|candidate[_ ]rows")


def test_tally_renders_both_numbers_and_labels_rows():
    t = Tally(n_days=2620, n_candidates=104800, n_legal=99000, n_illegal=5800)
    h = t.header()
    assert "N_days = 2,620" in h
    assert "104,800" in h and ROWS_LABEL in h
    assert "illegal 5,800" in h


def test_tally_refuses_a_bad_day_count():
    with pytest.raises(ValueError, match="n_days"):
        Tally(n_days=-1, n_candidates=10)


def test_tally_refuses_parts_that_do_not_sum():
    with pytest.raises(ValueError, match="must equal"):
        Tally(n_days=5, n_candidates=10, n_legal=3, n_illegal=4)


def _emitting_statements(path: Path):
    """Every print()/f-string statement in a module, as source text."""
    tree = ast.parse(path.read_text())
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            out.append((node.lineno, ast.unparse(node)))
    return out


def test_no_candidate_count_is_printed_without_n_days():
    """I80's teeth. A print() that formats a candidate count must also carry
    n_days — or go through Tally, which always emits both."""
    offenders = []
    for py in sorted(AZ.glob("*.py")):
        if py.name.startswith("test_"):
            continue
        for lineno, src in _emitting_statements(py):
            if COUNT_RE.search(src) and "n_days" not in src and "Tally" not in src \
               and ".header()" not in src and "tally" not in src.lower():
                offenders.append(f"{py.name}:{lineno}  {src[:90]}")
    assert not offenders, (
        "candidate count emitted without N_days (spec 049 I80):\n  " + "\n  ".join(offenders))
