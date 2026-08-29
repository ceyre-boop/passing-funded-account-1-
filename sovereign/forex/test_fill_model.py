"""I68 — BaseFill reproduces ForexBacktester._apply_costs exactly; PessimisticFill can only hurt."""
import copy
import math

import pandas as pd
import pytest

from sovereign.forex.fill_model import BaseFill, FillError, PessimisticFill, make_fill, net_r
from sovereign.forex.forex_backtester import ForexBacktester

TRADES = [
    {"pair": "EURUSD=X", "entry": 1.1234, "direction": 1, "hold_days": 5, "entry_date": pd.Timestamp("2018-03-01"), "pnl_pct": 0.004, "risk_pct": 0.01},
    {"pair": "GBPUSD=X", "entry": 1.3011, "direction": -1, "hold_days": 12, "entry_date": pd.Timestamp("2016-07-04"), "pnl_pct": -0.011, "risk_pct": 0.01},
    {"pair": "USDJPY=X", "entry": 109.87, "direction": 1, "hold_days": 1, "entry_date": pd.Timestamp("2021-06-01"), "pnl_pct": 0.0, "risk_pct": 0.01},
    {"pair": "AUDUSD=X", "entry": 0.7123, "direction": -1, "hold_days": 29, "entry_date": pd.Timestamp("2022-10-10"), "pnl_pct": 0.02, "risk_pct": 0.01},
    {"pair": "AUDNZD=X", "entry": 1.0801, "direction": 1, "hold_days": 0, "entry_date": pd.Timestamp("2019-01-02"), "pnl_pct": 0.001, "risk_pct": 0.01},
]


@pytest.mark.parametrize("t", TRADES, ids=[t["pair"] for t in TRADES])
def test_base_fill_equals_apply_costs(t):
    ref = ForexBacktester._apply_costs([copy.deepcopy(t)], pair=t["pair"])[0]
    sf, wf = BaseFill().cost_fracs(pair=t["pair"], entry_price=t["entry"], direction=t["direction"],
                                   hold_bars=t["hold_days"], entry_date=t["entry_date"])
    assert round(sf, 6) == ref["cost_spread_frac"]
    assert round(wf, 6) == ref["cost_swap_frac"]
    assert math.isclose(net_r(gross_pnl_pct=t["pnl_pct"], spread_frac=sf, swap_frac=wf, risk_pct=t["risk_pct"]),
                        ref["pnl_pct"] / t["risk_pct"], rel_tol=0, abs_tol=1e-12)


def test_base_fill_exits_at_close():
    assert BaseFill().exit_price(close=1.5, open_next=1.4) == 1.5


def test_pessimistic_costs_strictly_worse():
    t = TRADES[0]
    b = BaseFill().cost_fracs(pair=t["pair"], entry_price=t["entry"], direction=1, hold_bars=5, entry_date=t["entry_date"])
    p = PessimisticFill(spread_mult=2.0, slip_mult=2.0, delay_bars=1).cost_fracs(
        pair=t["pair"], entry_price=t["entry"], direction=1, hold_bars=5, entry_date=t["entry_date"])
    assert p[0] > b[0]                      # wider spread + slippage
    assert p[1] <= b[1]                     # one more financing day on a pay-carry pair
    assert p[0] == pytest.approx(2.0 * b[0])


def test_pessimistic_delay_fills_at_next_open_and_halts_without_it():
    f = PessimisticFill(spread_mult=1.0, slip_mult=1.0, delay_bars=1)
    assert f.exit_price(close=1.10, open_next=1.09) == 1.09
    with pytest.raises(FillError, match="open_next"):
        f.exit_price(close=1.10, open_next=float("nan"))
    assert PessimisticFill(spread_mult=1.0, slip_mult=1.0, delay_bars=0).exit_price(close=1.10, open_next=1.09) == 1.10


@pytest.mark.parametrize("kw", [dict(spread_mult=0.5, slip_mult=1.0, delay_bars=1),
                                dict(spread_mult=1.0, slip_mult=float("nan"), delay_bars=1),
                                dict(spread_mult=1.0, slip_mult=1.0, delay_bars=2)])
def test_pessimism_cannot_help_the_candidate(kw):
    with pytest.raises(FillError):
        PessimisticFill(**kw)


def test_make_fill_requires_every_pessimistic_parameter():
    assert isinstance(make_fill("base"), BaseFill)
    with pytest.raises(FillError, match="required"):
        make_fill("pessimistic", spread_mult=2.0, slip_mult=2.0)
    with pytest.raises(FillError):
        make_fill("optimistic")
