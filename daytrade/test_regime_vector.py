"""Card 019 — invariant suite for regime_vector.py's dataclasses and accessors.

Parquet-free by spec: compute() (the bar-arithmetic path) is out of scope; these
tests exercise Dimension/RegimeVector/require() directly with literal fixtures.
The fault-injection loop in MUTATION_LOG.md is the DoD.
"""
from __future__ import annotations

import math

import pytest

from regime_vector import SPEC, Dimension, RegimeError, RegimeVector, require

TS = "2026-08-07T14:00:00+00:00"


def make_vector(unavailable: set[str] = frozenset({"market_breadth"}),
                judged: set[str] = frozenset({"event_risk"}),
                values: dict[str, float] | None = None) -> RegimeVector:
    """A complete 12-dimension vector from SPEC itself (no hardcoded copy that
    can drift). Defaults: one unavailable, one judged, the rest computed at the
    midpoint of their declared range."""
    values = values or {}
    dims: dict[str, Dimension] = {}
    for name, (lo, hi, _desc) in SPEC.items():
        if name in unavailable:
            dims[name] = Dimension(name, None, "unavailable", "not supplied in fixture")
        else:
            src = "judged" if name in judged else "computed"
            dims[name] = Dimension(name, values.get(name, (lo + hi) / 2.0), src, "fixture")
    return RegimeVector(ts=TS, symbol="NVDA", dims=dims)


# ------------------------------------------------------------- construction

def test_rejects_unknown_dimension():
    with pytest.raises(RegimeError):
        Dimension("vibes", 0.5, "computed", "not in the spec")


def test_rejects_unavailable_with_value():
    with pytest.raises(RegimeError):
        Dimension("trend_strength", 0.2, "unavailable", "contradiction")


def test_rejects_computed_without_value():
    with pytest.raises(RegimeError):
        Dimension("trend_strength", None, "computed", "contradiction")
    with pytest.raises(RegimeError):
        Dimension("trend_strength", None, "judged", "contradiction")


def test_rejects_out_of_range():
    with pytest.raises(RegimeError):
        Dimension("trend_strength", 1.5, "computed", "above [−1, 1]")
    with pytest.raises(RegimeError):
        Dimension("gap_risk", -0.5, "computed", "below [0, 1]")


def test_rejects_nan_explicitly():
    """NaN must be refused by a NAMED isnan check, not as an accident of NaN
    comparisons being False in the range check (019:91). The match below pins
    the explicit check's message, so a range-check refactor that silently stops
    catching NaN — or removal of the explicit check itself — turns this red."""
    with pytest.raises(RegimeError, match="NaN"):
        Dimension("trend_strength", float("nan"), "computed", "poisoned input")


def test_rejects_incomplete_vector():
    dims = {name: Dimension(name, None, "unavailable", "fixture")
            for name in list(SPEC)[:-1]}          # drop one SPEC key
    with pytest.raises(RegimeError):
        RegimeVector(ts=TS, symbol="NVDA", dims=dims)


# ----------------------------------------------------------------- accessors

def test_available_returns_source_not_just_value():
    """A caller of available() can always distinguish 'unavailable' from
    'computed zero': unavailable dimensions are ABSENT from the mapping, never
    surfaced as a number, while a genuine computed 0.0 is present as 0.0.

    Spec note (019:93 said this row "should currently FAIL... available() today
    collapses both into 0.0"): that premise is stale against the current module
    — available() already omits unavailable dims (regime_vector.py). Per the
    ruling on this card, the test locks in the current, correct key-absence
    semantics; the spec correction is recorded in the card summary."""
    vec = make_vector(unavailable={"market_breadth", "gap_risk"},
                      values={"trend_strength": 0.0})
    av = vec.available()
    assert "market_breadth" not in av and "gap_risk" not in av
    assert av["trend_strength"] == 0.0            # a real zero survives as a value
    assert set(av) == {k for k, d in vec.dims.items() if d.source != "unavailable"}
    assert all(v is not None for v in av.values())


def test_require_raises_on_unavailable():
    vec = make_vector(unavailable={"market_breadth"})
    with pytest.raises(RegimeError):
        require(vec, "market_breadth")


def test_require_raises_on_missing():
    vec = make_vector()
    with pytest.raises(RegimeError):
        require(vec, "not_a_dimension_at_all")


def test_require_returns_value_when_computed_or_judged():
    vec = make_vector(unavailable={"market_breadth"}, judged={"event_risk"},
                      values={"trend_strength": 0.25, "event_risk": 0.7})
    assert require(vec, "trend_strength") == 0.25   # computed
    assert require(vec, "event_risk") == 0.7        # judged
    assert not math.isnan(require(vec, "realized_volatility"))
