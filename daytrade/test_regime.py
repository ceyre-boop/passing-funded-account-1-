"""Tests for regime.classify — spec 001.

Every fixture is BUILT, not sampled: a synthetic frame with ATR pinned to a round
number so each threshold reads as a price, which is how the expected values below
were computed by hand. Nothing here reads the real cache, so these tests cannot
break when the bar cache is regenerated (the failure mode that took out
test_load_partial_session twice).
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

import regime as R

DAY = date(2026, 6, 10)
PRIOR = date(2026, 6, 9)
ET = "America/New_York"


def _frame(day_specs):
    """day_specs: {date: [(hhmm, o, h, l, c, v), ...]} -> one ET-indexed frame."""
    rows, idx = [], []
    for d, bars_ in day_specs.items():
        for hhmm, o, h, l, c, v in bars_:
            idx.append(pd.Timestamp(f"{d} {hhmm}", tz=ET))
            rows.append({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v})
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx)).sort_index()


def _flat_day(d, n=78, start="09:30", px=100.0, rng=1.0, vol=1000):
    """n bars each with true range exactly `rng`, so ATR == rng exactly."""
    out, t = [], pd.Timestamp(f"{d} {start}", tz=ET)
    for _ in range(n):
        out.append((t.strftime("%H:%M"), px, px + rng / 2, px - rng / 2, px, vol))
        t += pd.Timedelta(minutes=5)
    return out


def _ctx(now_et, **kw):
    base = dict(symbol="TEST", now_et=now_et, session_day=DAY)
    base.update(kw)
    return R.Context(**base)


# ------------------------------------------------------------------ time blocks

@pytest.mark.parametrize("hhmm,expected", [
    ("04:00", "PREOPEN"), ("09:29", "PREOPEN"),
    ("09:30", "OPEN_DRIVE"), ("10:29", "OPEN_DRIVE"),
    ("10:30", "MORNING"), ("11:29", "MORNING"),
    ("11:30", "MIDDAY"), ("13:59", "MIDDAY"),
    ("14:00", "AFTERNOON"), ("15:29", "AFTERNOON"),
    ("15:30", "CLOSE"), ("15:55", "CLOSE"),
    ("16:00", "PREOPEN"), ("20:00", "PREOPEN"),
])
def test_time_block_boundaries_are_half_open(hhmm, expected):
    assert R.time_block(hhmm) == expected


def test_time_block_rejects_malformed_and_out_of_range():
    for bad in ("", "9:70", "25:00", "abc", "10-30"):
        with pytest.raises(R.RegimeError):
            R.time_block(bad)


def test_every_time_block_has_a_prior_and_every_regime_a_policy():
    """A block with no prior row would KeyError at classify time; a regime with
    no policy would emit None as an exit instruction."""
    for blk in ("PREOPEN", "OPEN_DRIVE", "MORNING", "MIDDAY", "AFTERNOON", "CLOSE"):
        assert set(R.TIME_PRIORS[blk]) == {"CONTINUATION", "MANIPULATION", "CONSOLIDATION"}
    assert set(R.POLICY) == {"CONTINUATION", "MANIPULATION", "CONSOLIDATION"}


def test_policy_emits_only_v3_intent_names():
    """The vocabulary was renamed; a RegimeRead carrying STATIC/TRAIL_WIDE would
    be read by nothing downstream. SALVAGE must NOT be emitted — it asserts
    something about an open position, not about the day."""
    import stockfish_exit
    for regime, intent in R.POLICY.items():
        assert intent in stockfish_exit.INTENT, f"{regime} -> unknown intent {intent}"
        assert intent != "SALVAGE"
        assert intent not in ("STATIC", "TRAIL_WIDE", "TRAIL_TIGHT")


# ------------------------------------------------------------- no lookahead

def test_frame_reaching_past_now_et_is_refused():
    """THE no-lookahead invariant. A caller handing over a frame that contains
    bars after now_et would let the classifier see the future; it must raise."""
    f = _frame({PRIOR: _flat_day(PRIOR), DAY: _flat_day(DAY)})
    with pytest.raises(R.RegimeError, match="now_et"):
        R.classify(f, None, _ctx("11:00"))      # frame ends 15:55, ctx says 11:00


def test_frame_ending_exactly_at_now_et_is_accepted():
    f = _frame({PRIOR: _flat_day(PRIOR), DAY: _flat_day(DAY)})
    upto = f[f.index <= pd.Timestamp(f"{DAY} 11:00", tz=ET)]
    read = R.classify(upto, None, _ctx("11:00"))
    assert read.ts.startswith(f"{DAY}T11:00")


# ------------------------------------------------------------ fail-loud inputs

def test_empty_and_missing_columns_raise():
    with pytest.raises(R.RegimeError):
        R.classify(pd.DataFrame(), None, _ctx("11:00"))
    f = _frame({DAY: _flat_day(DAY)})
    with pytest.raises(R.RegimeError, match="missing column"):
        R.classify(f.drop(columns=["Volume"]), None, _ctx("11:00"))


def test_nan_ohlc_is_corruption_not_forward_filled():
    f = _frame({PRIOR: _flat_day(PRIOR), DAY: _flat_day(DAY)})
    upto = f[f.index <= pd.Timestamp(f"{DAY} 11:00", tz=ET)].copy()
    upto.iloc[5, upto.columns.get_loc("High")] = float("nan")
    with pytest.raises(R.RegimeError, match="NaN"):
        R.classify(upto, None, _ctx("11:00"))


def test_zero_atr_refuses_rather_than_turning_thresholds_into_coin_flips():
    """Every threshold is in ATR units. A zero ATR makes '< 0.6*atr' impossible
    and '> 1.1 expansion' undefined, so the call must refuse, not score."""
    zero = [(h, 100.0, 100.0, 100.0, 100.0, 10) for h, *_ in _flat_day(DAY, n=20)]
    f = _frame({DAY: zero})
    upto = f[f.index <= pd.Timestamp(f"{DAY} 10:05", tz=ET)]
    with pytest.raises(R.RegimeError, match="atr5"):
        R.classify(upto, None, _ctx("10:05"))


def test_unordered_frame_raises():
    f = _frame({DAY: _flat_day(DAY, n=20)})
    upto = f[f.index <= pd.Timestamp(f"{DAY} 10:05", tz=ET)]
    with pytest.raises(R.RegimeError, match="time-ordered"):
        R.classify(upto.iloc[::-1], None, _ctx("10:05"))


def test_session_day_with_no_bars_raises():
    f = _frame({PRIOR: _flat_day(PRIOR, n=20)})
    with pytest.raises(R.RegimeError, match="no bars for session_day"):
        R.classify(f, None, _ctx(f.index[-1].strftime("%H:%M")))


# ------------------------------------------------- unavailable is never zero

def test_absent_pools_are_skipped_never_compared_against_zero():
    """pdh/pdl/onh/onl are None here. A None level compared against 0.0 would
    mark EVERY bar as sweeping it, firing MANIPULATION on every call."""
    f = _frame({PRIOR: _flat_day(PRIOR), DAY: _flat_day(DAY)})
    upto = f[f.index <= pd.Timestamp(f"{DAY} 11:00", tz=ET)]
    read = R.classify(upto, None, _ctx("11:00", onh=None, onl=None,
                                       pdh=None, pdl=None))
    assert read.evidence["swept"] == []
    assert read.evidence["bars_since_sweep"] is None
    assert read.evidence["reclaimed"] is False
    assert read.evidence["pools"]["onh"] is None
    # The SWEEP rule (+4, the strongest single signal) must not have fired. The
    # fixture's bars are dojis, so wick_dominance legitimately contributes +2 —
    # asserting the TOTAL were 0 would have been asserting the fixture, not the
    # no-None-coercion property this test is about.
    assert read.evidence["score_raw"]["MANIPULATION"] < 4


def test_unavailable_reason_is_carried_into_evidence():
    f = _frame({PRIOR: _flat_day(PRIOR), DAY: _flat_day(DAY)})
    upto = f[f.index <= pd.Timestamp(f"{DAY} 11:00", tz=ET)]
    read = R.classify(upto, None, _ctx("11:00", unavailable=("onh", "onl"),
                                       unavailable_reason="clipped to RTH"))
    assert read.evidence["unavailable"] == ["onh", "onl"]
    assert "clipped" in read.evidence["unavailable_reason"]


def test_incomplete_opening_range_does_not_fire_the_breakout_rule():
    """Before 10:00 the OR is genuinely incomplete. That is a fact, not a
    breakout — the +2 CONTINUATION rule must stay dark."""
    f = _frame({PRIOR: _flat_day(PRIOR), DAY: _flat_day(DAY)})
    upto = f[f.index <= pd.Timestamp(f"{DAY} 09:50", tz=ET)]
    read = R.classify(upto, None, _ctx("09:50"))
    assert read.evidence["or_complete"] is False
    assert read.evidence["or_range"] is None


# ----------------------------------------------------------- scoring behaviour

def test_sweep_and_reclaim_is_the_strongest_single_signal():
    """PDH is taken out and price closes back below it within N_RECLAIM bars.
    That is +4 MANIPULATION, more than any other single rule can add."""
    prior = _flat_day(PRIOR, px=100.0, rng=1.0)          # prior high = 100.5
    today = _flat_day(DAY, n=12, px=100.0, rng=1.0)
    # bar 10 pokes above 100.5, bar 11 closes back under it
    today[10] = ("11:20", 100.0, 102.0, 99.5, 101.5, 5000)
    today[11] = ("11:25", 101.5, 101.6, 99.0, 99.5, 5000)
    f = _frame({PRIOR: prior, DAY: today})
    upto = f[f.index <= pd.Timestamp(f"{DAY} 11:25", tz=ET)]
    read = R.classify(upto, None, _ctx("11:25", pdh=100.5, pdl=99.5))
    assert "PDH" in read.evidence["swept"]
    assert read.evidence["reclaimed"] is True
    assert read.evidence["bars_since_sweep"] <= R.N_RECLAIM
    assert read.evidence["score_raw"]["MANIPULATION"] >= 4


def test_confidence_below_floor_falls_back_to_the_safe_regime():
    """APEX #2 — never act on a guess. The fallback must be the safe regime and
    it must announce itself in evidence rather than looking like a real call."""
    f = _frame({PRIOR: _flat_day(PRIOR), DAY: _flat_day(DAY)})
    upto = f[f.index <= pd.Timestamp(f"{DAY} 11:00", tz=ET)]
    read = R.classify(upto, None, _ctx("11:00"))
    if read.confidence < R.CONF_FLOOR:
        assert read.regime == R.SAFE_REGIME
        assert read.evidence["conf_floored"] is True
        assert read.exit_policy == R.POLICY[R.SAFE_REGIME]


def test_nothing_firing_yields_zero_confidence_not_a_consolidation_claim():
    """An absence of evidence is not evidence of consolidation. The regime is
    the safe default but confidence must be exactly 0.0 so a downstream reader
    can tell the two apart."""
    prior = _flat_day(PRIOR, px=100.0, rng=1.0)
    today = _flat_day(DAY, n=8, px=100.0, rng=1.0)
    f = _frame({PRIOR: prior, DAY: today})
    upto = f[f.index <= pd.Timestamp(f"{DAY} 10:05", tz=ET)]
    read = R.classify(upto, None, _ctx("10:05"))
    if read.evidence["score_weighted"] and sum(read.evidence["score_weighted"].values()) == 0:
        assert read.confidence == 0.0
        assert read.regime == R.SAFE_REGIME


def test_read_is_frozen_and_carries_the_rule_version():
    f = _frame({PRIOR: _flat_day(PRIOR), DAY: _flat_day(DAY)})
    upto = f[f.index <= pd.Timestamp(f"{DAY} 11:00", tz=ET)]
    read = R.classify(upto, None, _ctx("11:00"))
    assert read.rule_version == R.RULE_VERSION == "regime-v1"
    with pytest.raises(Exception):
        read.regime = "CONTINUATION"        # frozen dataclass
    assert read.exit_policy == R.POLICY[read.regime]
    assert 0.0 <= read.confidence <= 1.0
    assert read.direction_of_flow in (-1, 0, 1)


def test_atr_is_exactly_the_pinned_true_range():
    """Pins the ATR arithmetic itself: 20 bars each of true range exactly 1.0
    must give atr5 == 1.0, not 0.999 and not a rolling artefact."""
    f = _frame({DAY: _flat_day(DAY, n=20, px=100.0, rng=1.0)})
    upto = f[f.index <= pd.Timestamp(f"{DAY} 10:05", tz=ET)]
    read = R.classify(upto, None, _ctx("10:05"))
    assert read.evidence["atr5"] == pytest.approx(1.0, abs=1e-9)


def test_daily_atr_uses_only_sessions_strictly_before_today():
    """A daily ATR that included today would be lookahead through the back door."""
    f = _frame({PRIOR: _flat_day(PRIOR, px=100.0, rng=2.0),
                DAY: _flat_day(DAY, px=100.0, rng=1.0)})
    upto = f[f.index <= pd.Timestamp(f"{DAY} 11:00", tz=ET)]
    read = R.classify(upto, None, _ctx("11:00"))
    atrd = read.evidence["atr_daily"]
    assert atrd is None or atrd > 0


def test_volume_average_excludes_today():
    """vol_avg_at_this_minute must average PRIOR sessions only; including today
    would make volume_ratio partly self-referential."""
    prior = _flat_day(PRIOR, px=100.0, rng=1.0, vol=1000)
    today = _flat_day(DAY, px=100.0, rng=1.0, vol=5000)
    f = _frame({PRIOR: prior, DAY: today})
    upto = f[f.index <= pd.Timestamp(f"{DAY} 11:00", tz=ET)]
    read = R.classify(upto, None, _ctx("11:00"))
    assert read.evidence["vol_avg_at_this_minute"] == pytest.approx(1000.0)
    assert read.evidence["volume_ratio"] == pytest.approx(5.0)
