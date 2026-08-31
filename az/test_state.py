"""Gate 1 invariants, spec 048 §7. Each has a deliberate violation that fails it."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "daytrade"))

from az.state import (GRANULARITIES, LookaheadError, MIN_DAYS, StateError,  # noqa: E402
                      assert_no_lookahead, audit, discretize, raw_features)
from bars import ET  # noqa: E402


def _session(n=78, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz=ET)
    close = 100 + np.cumsum(rng.normal(0, 0.25, n))
    return pd.DataFrame({"Open": close + rng.normal(0, .05, n), "High": close + abs(rng.normal(0, .2, n)),
                         "Low": close - abs(rng.normal(0, .2, n)), "Close": close,
                         "Volume": rng.integers(1e4, 1e5, n).astype(float)}, index=idx)


# --- I76: the lookahead guard ------------------------------------------------

def test_features_are_blind_to_the_future():
    """The guard passes on honest features — corrupting post-t bars changes nothing."""
    s = _session()
    out = assert_no_lookahead(s, "11:00", prior_close=99.5)
    assert set(out) == {"vol", "expansion", "trend", "gap", "vwap"}


def test_guard_catches_a_broken_truncation(monkeypatch):
    """I76's deliberate violation.

    A feature that only ever sees `truncate_at(df, t)` cannot leak — the
    truncation makes it structurally impossible, which is the design. So the
    thing that CAN break is the truncation itself, and that is what the guard
    exists to catch. Break it (hand back the whole session) and corrupted future
    bars move the features, which must raise."""
    import az.state as st
    monkeypatch.setattr(st, "truncate_at", lambda df, t: df)   # the bug
    with pytest.raises(LookaheadError, match="does not exist yet"):
        st.assert_no_lookahead(_session(), "11:00", prior_close=99.5)


def test_guard_message_names_the_offending_features(monkeypatch):
    import az.state as st
    monkeypatch.setattr(st, "truncate_at", lambda df, t: df)
    with pytest.raises(LookaheadError) as ei:
        st.assert_no_lookahead(_session(), "11:00", prior_close=99.5)
    msg = str(ei.value)
    assert any(f in msg for f in ("vol", "trend", "vwap", "expansion"))


# --- feature hygiene ---------------------------------------------------------

def test_refuses_to_build_a_state_on_a_dead_atr():
    """Never silently default a required scalar. A flat frame has ATR 0."""
    idx = pd.date_range("2026-03-02 09:30", periods=10, freq="5min", tz=ET)
    flat = pd.DataFrame({c: [100.0] * 10 for c in ("Open", "High", "Low", "Close")}
                        | {"Volume": [1e4] * 10}, index=idx)
    with pytest.raises(StateError, match="ATR14"):
        raw_features(flat, prior_close=100.0)


def test_nan_bucket_is_minus_one_not_merged():
    raw = {"vol": .01, "expansion": float("nan"), "trend": 0., "gap": 0., "vwap": 0.}
    s = discretize(raw, "10:00", GRANULARITIES["medium"])
    assert s.expansion_b == -1


# --- I77: occupancy counts DAYS ----------------------------------------------

def _rows(days, per_day, raw):
    return [{"day": f"2026-01-{d:02d}", "hhmm": "10:00", "raw": dict(raw)}
            for d in range(1, days + 1) for _ in range(per_day)]


def test_occupancy_counts_days_not_rows():
    """The load-bearing invariant. 5 days x 40 candidates is 200 rows but only 5
    days — under a 30-day floor that cell is THIN, and a row-counter would call
    it valued."""
    raw = {"vol": .01, "expansion": 0., "trend": 0., "gap": 0., "vwap": 0.}
    occ = audit(_rows(5, 40, raw), "medium", GRANULARITIES["medium"])
    assert occ.candidates == 200 and occ.days == 5
    assert occ.cells_valued == 0, "5 days must not clear a 30-day floor"
    assert occ.frac_candidates_in_valued_cells == 0.0


def test_occupancy_values_a_cell_with_enough_days():
    raw = {"vol": .01, "expansion": 0., "trend": 0., "gap": 0., "vwap": 0.}
    occ = audit(_rows(MIN_DAYS + 5, 2, raw), "medium", GRANULARITIES["medium"])
    assert occ.cells_valued == 1 and occ.frac_candidates_in_valued_cells == 1.0
