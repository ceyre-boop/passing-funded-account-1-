"""The ENTERED stage gap — a pre-existing defect, fixed at the stage machine.

Before this fix `ALLOWED_ACTIONS[Stage.ENTERED]` was {HOLD, EXIT_ALL}, so any
always-on protective layer that bound tighter than the catastrophic stop before
TP1 raised StageError. Reproduced on the unmodified SF-FROZEN-002 engine via
`thesis_sl`, so it predates the volatility layer; the volatility layer was simply
the first caller ever to supply realistic always-on values.

MOVE_SL is safe in ENTERED because C003 ("no stop loosening") is stage-independent
— the only stage branch in stockfish_constitution.enforce() is C008. So a MOVE_SL
from ENTERED can only tighten, and the stage's real guarantee ("never less
protected") survives while the over-restriction ("never more protected") lifts.
"""
import pytest

from stockfish_constitution import ConstitutionError, enforce
from stockfish_exit import ALLOWED_ACTIONS, Action, Stage, StageError, TradeState, decide_exit


def _st(**kw):
    base = dict(direction=1, entry=100.0, qty=100.0, price=100.5, sl=98.0,
                tp1=101.0, tp2=102.0, trail_dist=1.0)
    base.update(kw)
    return TradeState(**base)


def test_thesis_stop_tightens_in_entered_instead_of_raising():
    """The exact reproduction of the defect. Pre-fix this raised StageError."""
    acts = decide_exit(_st(thesis_sl=99.5))
    assert [a.kind for a in acts] == ["MOVE_SL"]
    assert acts[0].sl == pytest.approx(99.5)


def test_move_sl_is_permitted_in_entered():
    assert "MOVE_SL" in ALLOWED_ACTIONS[Stage.ENTERED]


def test_take_partial_is_still_refused_in_entered():
    """Scaling out before TP1 is a real behaviour change and C003 does not
    constrain it, so it stays illegal. The fix widened ENTERED by exactly one
    action, not by convenience."""
    assert "TAKE_PARTIAL" not in ALLOWED_ACTIONS[Stage.ENTERED]
    with pytest.raises(StageError, match="TAKE_PARTIAL"):
        from stockfish_exit import _gate
        _gate(_st(), [Action("TAKE_PARTIAL", fraction=0.5, reason="illegal here")])


def test_c003_still_refuses_a_loosening_move_sl_in_entered():
    """The guarantee that makes the fix safe. If this ever fails, widening
    ENTERED became unsafe and the fix must be reverted."""
    with pytest.raises(ConstitutionError, match="C003"):
        enforce(_st(), Action("MOVE_SL", sl=97.0, reason="loosening from 98"))


def test_c003_is_stage_independent():
    """Same loosening refused from PROTECTED — proving C003 does not depend on
    stage, which is the premise the fix rests on."""
    for stage in (Stage.ENTERED, Stage.PROTECTED, Stage.SCALED, Stage.RUNNER):
        with pytest.raises(ConstitutionError, match="C003"):
            enforce(_st(stage=stage), Action("MOVE_SL", sl=97.0, reason="loosening"))
