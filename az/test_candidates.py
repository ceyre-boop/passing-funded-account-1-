"""Gate 2: legality is a mask at generation, never a scoring penalty."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "daytrade"))

from az.candidates import (CandidateError, DIRECTIONS, ILLEGAL_NO_FILL_BAR,      # noqa: E402
                           ILLEGAL_NO_FORWARD_PATH, ILLEGAL_TOO_FEW_BARS,
                           enumerate_day, geometry)
from az.state import GRID_HHMM  # noqa: E402
from bars import ET  # noqa: E402


def _session(n=78, seed=1, start="09:30"):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(f"2026-03-02 {start}", periods=n, freq="5min", tz=ET)
    close = 100 + np.cumsum(rng.normal(0, .25, n))
    return pd.DataFrame({"Open": close + rng.normal(0, .05, n), "High": close + abs(rng.normal(0, .2, n)),
                         "Low": close - abs(rng.normal(0, .2, n)), "Close": close,
                         "Volume": rng.integers(1e4, 1e5, n).astype(float)}, index=idx)


def test_0930_is_masked_with_its_reason_not_scored_low():
    c = enumerate_day("SPY", "2026-03-02", _session(), 99.5, k_stop=1.0)
    at_open = [x for x in c if x.hhmm == "09:30"]
    assert len(at_open) == len(DIRECTIONS)
    assert all(not x.legal for x in at_open)
    assert all(x.reason == ILLEGAL_TOO_FEW_BARS for x in at_open)


def test_masked_candidates_carry_no_entry_so_they_cannot_be_graded():
    """The whole point of a mask: an illegal candidate has no geometry at all,
    so nothing downstream can accidentally score it as zero-reward."""
    c = enumerate_day("SPY", "2026-03-02", _session(), 99.5, k_stop=1.0)
    assert all(x.entry is None and x.raw is None for x in c if not x.legal)
    assert all(x.entry is not None for x in c if x.legal)


def test_slots_are_always_emitted_so_the_grid_stays_aligned():
    """Alignment across days is what makes the day-shift null exact (spec 049
    2.1). Dropping masked slots instead of emitting them would silently break it."""
    full = enumerate_day("SPY", "2026-03-02", _session(), 99.5, k_stop=1.0)
    short = enumerate_day("SPY", "2026-03-03", _session(n=40), 99.5, k_stop=1.0)
    assert len(full) == len(short) == len(GRID_HHMM) * len(DIRECTIONS)


def test_a_candidate_with_too_little_forward_path_is_ILLEGAL_not_zero_reward():
    sess = _session(n=78)
    late = sess[sess.index.strftime("%H:%M") <= "14:35"]      # 14:30 now has 1 bar after it
    c = enumerate_day("SPY", "2026-03-02", late, 99.5, k_stop=1.0)
    at_1430 = [x for x in c if x.hhmm == "14:30"]
    assert all(not x.legal for x in at_1430)
    assert at_1430[0].reason in (ILLEGAL_NO_FORWARD_PATH, ILLEGAL_NO_FILL_BAR)


def test_geometry_requires_k_stop_and_has_no_default():
    with pytest.raises(CandidateError, match="k_stop is required"):
        geometry(symbol="SPY", day="2026-03-02", hhmm="10:00", direction=1,
                 hist=None, next_open=100.0, atr14=0.5, k_stop=None)


def test_geometry_is_the_declared_rule():
    e = geometry(symbol="SPY", day="2026-03-02", hhmm="10:00", direction=1,
                 hist=None, next_open=100.0, atr14=0.5, k_stop=2.0)
    assert e.entry == 100.0                    # fill at the NEXT open, never close-at-t
    assert e.risk == 1.0                       # k_stop * ATR
    assert e.stop == 99.0                      # entry - dir * risk
    assert e.tp1 == 101.0 and e.tp2 == 102.0   # 1R / 2R
    short = geometry(symbol="SPY", day="2026-03-02", hhmm="10:00", direction=-1,
                     hist=None, next_open=100.0, atr14=0.5, k_stop=2.0)
    assert short.stop == 101.0 and short.tp1 == 99.0


def test_illegal_fraction_is_constant_on_this_lane_and_that_is_reported():
    """A finding, pinned so a future mask change is visible: on complete RTH
    sessions only the 09:30 rule ever fires, so the illegal fraction is exactly
    2/22 every day. If another rule starts firing, this test changes and someone
    has to look at why."""
    fracs = {round(sum(1 for x in enumerate_day("SPY", f"2026-03-{d:02d}", _session(seed=d), 99.5,
                                                k_stop=1.0) if not x.legal) / (len(GRID_HHMM) * 2), 4)
             for d in range(2, 8)}
    assert fracs == {round(2 / 22, 4)}
