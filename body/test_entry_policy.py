"""body/test_entry_policy.py — the first policy that conditions on the tape."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "az")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytest.importorskip("nautilus_trader", reason="run body/ under .venv-v1 (py3.13)")

from body.directive import LONG, SHORT, EntryDirective  # noqa: E402
from body.entry_policy import (MARGIN, MIN_BARS, VetoEntryPolicy,  # noqa: E402
                               W_VETO_NO_DIRECTION)


class _Bar:
    """Minimal bar: the policy reads only OHLCV, ts and instrument id."""
    def __init__(self, o, h, l, c, v=1000.0, ts=0):
        self.open, self.high, self.low, self.close, self.volume = o, h, l, c, v
        self.ts_event = ts
        self.bar_type = type("BT", (), {"instrument_id": "SPY.SIM"})()


def quiet(n, ts0=0):
    """Bars with a tight range — no expansion trigger."""
    return [_Bar(500.0, 500.2, 499.8, 500.0, ts=ts0 + i) for i in range(n)]


def expanding(ts=999):
    return _Bar(500.0, 503.0, 497.0, 502.0, ts=ts)


def feed(policy, bars):
    return [policy(b) for b in bars]


# ---------------------------------------------------------- the no-edge contract

def test_declares_no_edge():
    """The ODD sim gate reads this. A policy claiming an edge shuts the gate --
    correctly, since no entry edge is validated in this repo."""
    assert VetoEntryPolicy.DECLARES_NO_EDGE is True


def test_confidence_is_never_asserted():
    p = VetoEntryPolicy()
    feed(p, quiet(MIN_BARS))
    d = p(expanding())
    if d is not None:
        assert d.confidence == 0.0


# ------------------------------------------------------------ the standing veto

def test_the_directional_veto_fires_on_every_single_bar():
    """Direction is null here — all three directional studies sit below their
    detection floor. That is carried as a permanent veto with real weight, not
    as a footnote."""
    p = VetoEntryPolicy()
    feed(p, quiet(40))
    assert len(p.ledgers) == 40
    assert all("no_directional_basis" in l.against_reasons for l in p.ledgers)


def test_the_directional_veto_has_weight_that_eagerness_must_beat():
    assert W_VETO_NO_DIRECTION > 0
    p = VetoEntryPolicy()
    feed(p, quiet(MIN_BARS))
    led = p.ledgers[-1]
    assert led.vetoes >= W_VETO_NO_DIRECTION


def test_direction_is_a_tiebreak_read_off_the_bar():
    """Not a prediction. Up bar -> long, down bar -> short, and nothing else."""
    p = VetoEntryPolicy()
    feed(p, quiet(MIN_BARS))
    assert p._direction(_Bar(500.0, 503.0, 497.0, 502.0)) == LONG
    assert p._direction(_Bar(500.0, 503.0, 497.0, 498.0)) == SHORT


# ------------------------------------------------------------- tentative to enter

def test_never_emits_during_warmup():
    p = VetoEntryPolicy()
    out = feed(p, [expanding(ts=i) for i in range(MIN_BARS - 1)])
    assert all(d is None for d in out)
    assert all("warmup" in l.against_reasons for l in p.ledgers)


def test_a_quiet_tape_produces_no_entries():
    """Eager, but it does not trade nothing happening."""
    p = VetoEntryPolicy()
    out = feed(p, quiet(60))
    assert all(d is None for d in out)


def test_expansion_can_clear_the_standing_veto():
    """Tentative is not paralysed — the loop has to be able to drive."""
    p = VetoEntryPolicy()
    feed(p, quiet(MIN_BARS))
    assert p(expanding()) is not None


def test_session_budget_is_respected():
    p = VetoEntryPolicy(max_entries=2)
    feed(p, quiet(MIN_BARS))
    emitted = [d for d in (p(expanding(ts=1000 + i)) for i in range(10)) if d]
    assert len(emitted) == 2
    assert any("session_budget_spent" in l.against_reasons for l in p.ledgers)


def test_no_entry_without_room_left_in_the_session():
    p = VetoEntryPolicy(bars_in_session=MIN_BARS + 2)
    feed(p, quiet(MIN_BARS))
    for i in range(6):
        p(expanding(ts=2000 + i))
    assert any("no_room_left_in_session" in l.against_reasons for l in p.ledgers)


# ------------------------------------------------------------------ the arithmetic

def test_entry_is_exactly_the_declared_inequality():
    """enter iff eagerness - vetoes >= MARGIN. No hidden branch."""
    p = VetoEntryPolicy()
    feed(p, quiet(MIN_BARS))
    p(expanding())
    for led in p.ledgers:
        assert led.fired == ((led.eagerness - led.vetoes) >= led.margin)


def test_a_ledger_is_kept_for_every_bar_entered_or_not():
    """MECH-006 needs the refusals, not just the entries."""
    p = VetoEntryPolicy()
    bars = quiet(MIN_BARS) + [expanding(ts=500)] + quiet(5, ts0=600)
    feed(p, bars)
    assert len(p.ledgers) == len(bars)
    assert sum(1 for l in p.ledgers if not l.fired) > 0


def test_veto_summary_counts_bars_not_entries():
    """The rate-matched control MECH-006 requires is built against bars."""
    p = VetoEntryPolicy()
    feed(p, quiet(30))
    assert p.veto_summary()["no_directional_basis"] == 30


def test_margin_is_a_real_gate():
    """FAULT INJECTION: an unreachable margin must produce zero entries."""
    p = VetoEntryPolicy(margin=99.0)
    feed(p, quiet(MIN_BARS))
    assert p(expanding()) is None


def test_deterministic():
    a, b = VetoEntryPolicy(), VetoEntryPolicy()
    bars = quiet(MIN_BARS) + [expanding(ts=1)]
    assert [d is None for d in feed(a, bars)] == [d is None for d in feed(b, bars)]


def test_emits_only_entry_directives():
    p = VetoEntryPolicy()
    feed(p, quiet(MIN_BARS))
    d = p(expanding())
    assert d is None or isinstance(d, EntryDirective)
