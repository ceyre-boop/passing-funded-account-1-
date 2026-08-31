"""The MDE-at-discovery rule, and the scanner that makes it mechanical."""
import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gate.discovery import ABOVE, BELOW, DiscoveryError, Finding, require_above_floor  # noqa: E402

SCAN_DIRS = (ROOT / "gate", ROOT / "az")
# a formatted candidate EFFECT
EFFECT_RE = re.compile(r"observed_effect|effect_size|mean_delta|median_per_trade|per_trade_effect")


def test_a_below_floor_effect_is_named_and_stopped():
    """C5_gap, the case that motivated the rule."""
    f = Finding("C5_gap:high", 0.0137, 1.2934, 705, "trades")
    assert f.verdict == BELOW
    assert round(f.mde, 4) == 0.1211
    assert 8 < f.ratio < 10
    assert not f.proceeds
    with pytest.raises(DiscoveryError, match="BELOW NOISE FLOOR"):
        require_above_floor(f)


def test_an_above_floor_effect_proceeds():
    f = Finding("big", 0.9, 1.0, 100, "days")
    assert f.verdict == ABOVE and f.proceeds
    assert require_above_floor(f) is f


def test_the_header_never_emits_an_effect_without_its_mde():
    h = Finding("x", 0.0137, 1.2934, 705).header()
    assert "+0.0137" in h and "MDE" in h and "0.1211" in h and BELOW in h


def test_more_units_lower_the_floor():
    """The same effect can be below the floor in a small study and above it in a
    large one — which is the whole point of computing it at discovery."""
    small = Finding("e", 0.05, 1.0, 100)
    large = Finding("e", 0.05, 1.0, 10_000)
    assert small.verdict == BELOW and large.verdict == ABOVE
    assert large.mde < small.mde


def test_a_zero_effect_is_infinitely_below_the_floor():
    assert Finding("z", 0.0, 1.0, 500).ratio == float("inf")
    assert Finding("z", 0.0, 1.0, 500).verdict == BELOW


@pytest.mark.parametrize("bad", [
    dict(name="a", observed_effect=float("nan"), per_unit_sd=1.0, n_units=10),
    dict(name="a", observed_effect=0.1, per_unit_sd=0.0, n_units=10),
    dict(name="a", observed_effect=0.1, per_unit_sd=1.0, n_units=0),
    dict(name="a", observed_effect=0.1, per_unit_sd=1.0, n_units=10.5),
])
def test_guards(bad):
    with pytest.raises(DiscoveryError):
        Finding(**bad)


def test_n_units_must_be_the_independent_unit_not_rows():
    """Passing rows inflates n and understates the MDE — the same unit error the
    N_days rule exists to prevent. Pinned by contrast, since no type system can
    tell days from rows.

    C5_gap is the worked example, and it is stronger than expected: even counting
    ROWS (705 trades x 54 snapshots = 38,070) it is still 1.20x below its own
    floor. It would need 55,131 — which is exactly the figure the sealed-half
    arithmetic independently produced. Row-counting does not rescue it; it only
    makes the margin look narrower than it is."""
    honest = Finding("e", 0.0137, 1.2934, 705, "trades")
    inflated = Finding("e", 0.0137, 1.2934, 705 * 54, "ROWS — wrong unit")
    enough = Finding("e", 0.0137, 1.2934, 55_131, "trades")

    assert honest.verdict == BELOW and round(honest.ratio, 1) == 8.8
    assert inflated.verdict == BELOW, "row-counting narrows the margin but does not clear the floor"
    assert 1.0 < inflated.ratio < 1.5
    assert inflated.mde < honest.mde, "more units always lowers the floor"
    assert enough.verdict == ABOVE, "55,131 is the point at which it would have been detectable"


def _printed(path: Path):
    tree = ast.parse(path.read_text())
    return [(n.lineno, ast.unparse(n)) for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print"]


def test_no_effect_is_printed_without_its_mde():
    """Same pattern as the N_days scanner: route emissions through the Finding
    header rather than widening a regex."""
    offenders = []
    for d in SCAN_DIRS:
        for py in sorted(d.glob("*.py")):
            if py.name.startswith("test_"):
                continue
            for lineno, src in _printed(py):
                if EFFECT_RE.search(src) and "mde" not in src.lower() \
                   and "header()" not in src and "Finding" not in src:
                    offenders.append(f"{py.parent.name}/{py.name}:{lineno}  {src[:88]}")
    assert not offenders, ("candidate effect emitted without its MDE:\n  " + "\n  ".join(offenders))
