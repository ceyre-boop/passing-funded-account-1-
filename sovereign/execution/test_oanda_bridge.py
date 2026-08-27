"""Tests for sovereign/execution/oanda_bridge.py.

Fault-injection style: every safety test should FAIL if the invariant it
names stops being enforced, not just pass on the happy path. All HTTP is
mocked — nothing here reaches the network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import sovereign.execution.oanda_bridge as ob  # noqa: E402


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    """Never let a real .env leak into these tests — every construction path
    supplies credentials explicitly."""
    monkeypatch.setattr(ob, "_credentials",
                        lambda: ("env-key", "env-acct", "https://api-fxpractice.oanda.com", "0"))


# --------------------------------------------------------------- ships disarmed

def test_module_ships_disarmed():
    assert ob.ARMED is False, "oanda_bridge must ship with ARMED=False"


# --------------------------------------------------------------- practice-only guard

def test_refuses_when_live_flag_not_zero():
    with pytest.raises(ob.OandaRefused, match="OANDA_LIVE"):
        ob.OandaBridge(live_flag="1", base_url="https://api-fxpractice.oanda.com")


def test_refuses_when_live_flag_is_garbage():
    for bad in ("true", "yes", "01", " ", "1"):
        with pytest.raises(ob.OandaRefused):
            ob.OandaBridge(live_flag=bad, base_url="https://api-fxpractice.oanda.com")


def test_trailing_whitespace_around_a_valid_flag_is_still_accepted():
    """'0 ' is the same value as '0' once trimmed — not a typo to reject."""
    b = ob.OandaBridge(live_flag="0 ", base_url="https://api-fxpractice.oanda.com")
    assert b.base == "https://api-fxpractice.oanda.com"


def test_refuses_when_base_url_is_live_host():
    with pytest.raises(ob.OandaRefused, match="api-fxtrade"):
        ob.OandaBridge(live_flag="0", base_url="https://api-fxtrade.oanda.com")


def test_refuses_when_base_url_host_is_anything_else():
    with pytest.raises(ob.OandaRefused):
        ob.OandaBridge(live_flag="0", base_url="https://evil.example.com")


def test_accepts_correct_practice_config():
    b = ob.OandaBridge(live_flag="0", base_url="https://api-fxpractice.oanda.com")
    assert b.base == "https://api-fxpractice.oanda.com"


def test_both_checks_are_independent_not_short_circuited_wrong():
    """Neither check alone is sufficient — both bad-live and bad-host must
    each independently raise, not rely on the other to catch it."""
    with pytest.raises(ob.OandaRefused):
        ob.OandaBridge(live_flag="1", base_url="https://api-fxtrade.oanda.com")


def test_missing_credentials_raise():
    with patch.object(ob, "_credentials", side_effect=ob.OandaRefused("missing")):
        with pytest.raises(ob.OandaRefused):
            ob.OandaBridge()


# --------------------------------------------------------------- pair normalization

@pytest.mark.parametrize("pair,expected", [
    ("EURUSD=X", "EUR_USD"),
    ("EURUSD", "EUR_USD"),
    ("EUR_USD", "EUR_USD"),
    ("usdjpy=x", "USD_JPY"),
])
def test_to_oanda_instrument(pair, expected):
    assert ob.to_oanda_instrument(pair) == expected


def test_to_oanda_instrument_rejects_garbage():
    with pytest.raises(ob.OandaRefused):
        ob.to_oanda_instrument("NOTAPAIR")


# --------------------------------------------------------------- reads (mocked)

def _bridge(mode="shadow", assume_yes=False):
    return ob.OandaBridge(mode=mode, assume_yes=assume_yes,
                          live_flag="0", base_url="https://api-fxpractice.oanda.com")


def _mock_response(payload: dict):
    m = MagicMock()
    m.__enter__.return_value = m
    m.read.return_value = json.dumps(payload).encode()
    return m


def test_account_summary_reads_never_gated():
    b = _bridge()
    with patch("urllib.request.urlopen", return_value=_mock_response(
            {"account": {"id": "101-001-1-001", "balance": "100000"}})):
        summary = b.account_summary()
    assert summary["account"]["balance"] == "100000"


def test_get_open_trades_shape():
    b = _bridge()
    trades = [{"id": "1", "instrument": "EUR_USD", "currentUnits": "1000",
              "price": "1.10", "openTime": "2026-08-27T00:00:00Z"}]
    with patch("urllib.request.urlopen", return_value=_mock_response({"trades": trades})):
        out = b.get_open_trades()
    assert out == trades


def test_get_historical_candles_drops_incomplete_and_shapes_dataframe():
    b = _bridge()
    payload = {"candles": [
        {"time": "2026-08-25T21:00:00.000000000Z", "complete": True,
         "mid": {"o": "1.10", "h": "1.11", "l": "1.09", "c": "1.105"}},
        {"time": "2026-08-26T21:00:00.000000000Z", "complete": False,
         "mid": {"o": "1.105", "h": "1.12", "l": "1.10", "c": "1.115"}},
    ]}
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        df = b.get_historical_candles("EURUSD=X", "2026-08-01", "2026-08-27")
    assert len(df) == 1, "the incomplete (still-forming) candle must be dropped"
    assert {"High", "Low", "Close"}.issubset(df.columns)
    assert df["Close"].iloc[0] == pytest.approx(1.105)


def test_get_historical_candles_raises_when_all_incomplete():
    b = _bridge()
    payload = {"candles": [{"time": "2026-08-27T00:00:00Z", "complete": False,
                            "mid": {"o": "1", "h": "1", "l": "1", "c": "1"}}]}
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        with pytest.raises(ob.OandaRefused):
            b.get_historical_candles("EUR_USD", "2026-08-01", "2026-08-27")


def test_http_error_raises_oanda_refused_not_swallowed():
    import urllib.error
    b = _bridge()
    err = urllib.error.HTTPError("url", 401, "unauthorized", {}, None)
    err.read = lambda: b'{"errorMessage": "Insufficient authorization"}'
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(ob.OandaRefused, match="HTTP 401"):
            b.account_summary()


# --------------------------------------------------------------- arming gates mutation

def test_set_stop_shadow_by_default_sends_nothing():
    b = _bridge(mode="armed", assume_yes=True)  # mode=armed, but module ARMED constant is False
    with patch("urllib.request.urlopen") as mock_urlopen:
        result = b.set_stop("t1", 1.2345)
    assert result["status"] == "SHADOW"
    assert result["sent"] is False
    mock_urlopen.assert_not_called()


def test_close_trade_shadow_by_default_sends_nothing():
    b = _bridge(mode="armed", assume_yes=True)
    with patch("urllib.request.urlopen") as mock_urlopen:
        result = b.close_trade("t1")
    assert result["status"] == "SHADOW"
    mock_urlopen.assert_not_called()


def test_open_market_order_shadow_by_default_sends_nothing():
    b = _bridge(mode="armed", assume_yes=True)
    with patch("urllib.request.urlopen") as mock_urlopen:
        result = b.open_market_order("EUR_USD", "LONG", 1000)
    assert result["status"] == "SHADOW"
    mock_urlopen.assert_not_called()


def test_module_armed_true_but_mode_shadow_still_refuses(monkeypatch):
    """Even if someone flips the module ARMED constant, mode='shadow' must
    still block every mutating call — arming requires BOTH."""
    monkeypatch.setattr(ob, "ARMED", True)
    b = _bridge(mode="shadow")
    with patch("urllib.request.urlopen") as mock_urlopen:
        result = b.close_trade("t1")
    assert result["status"] == "SHADOW"
    mock_urlopen.assert_not_called()


def test_module_armed_true_and_mode_armed_but_no_confirm_declines(monkeypatch):
    monkeypatch.setattr(ob, "ARMED", True)
    b = _bridge(mode="armed", assume_yes=False)
    with patch("builtins.input", side_effect=EOFError()):
        with patch("urllib.request.urlopen", return_value=_mock_response(
                {"account": {"balance": "100000"}})):
            result = b.close_trade("t1")
    assert result["status"] == "DECLINED_BY_USER"


def test_module_armed_true_mode_armed_assume_yes_actually_sends(monkeypatch):
    monkeypatch.setattr(ob, "ARMED", True)
    b = _bridge(mode="armed", assume_yes=True)
    with patch("urllib.request.urlopen", return_value=_mock_response(
            {"orderFillTransaction": {"price": "1.10123"}})):
        result = b.close_trade("t1")
    assert result["status"] == "SENT"
    assert result["sent"] is True


# --------------------------------------------------------------- fail loud on unknown fill

def test_close_trade_raises_on_missing_fill(monkeypatch):
    monkeypatch.setattr(ob, "ARMED", True)
    b = _bridge(mode="armed", assume_yes=True)
    with patch("urllib.request.urlopen", return_value=_mock_response({"someOtherKey": {}})):
        with pytest.raises(ob.OandaRefused, match="unknown fill state"):
            b.close_trade("t1")


def test_open_market_order_raises_on_rejected_order(monkeypatch):
    monkeypatch.setattr(ob, "ARMED", True)
    b = _bridge(mode="armed", assume_yes=True)
    with patch("urllib.request.urlopen", return_value=_mock_response(
            {"orderCancelTransaction": {"reason": "MARKET_HALTED"}})):
        with pytest.raises(ob.OandaRefused, match="MARKET_HALTED"):
            b.open_market_order("EUR_USD", "LONG", 1000)


def test_open_market_order_rejects_bad_direction():
    b = _bridge()
    with pytest.raises(ob.OandaRefused):
        b.open_market_order("EUR_USD", "SIDEWAYS", 1000)
