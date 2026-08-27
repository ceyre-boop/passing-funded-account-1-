"""sovereign/execution/oanda_bridge.py — OANDA v20 REST bridge, PRACTICE ONLY.

`sovereign/execution/forex_exit_manager.py:514` has imported
`from sovereign.execution.oanda_bridge import OandaBridge` since that file was
written — this module never existed until now (the third instance of this
repo's named-import-never-written pattern: `ctrader_bridge`,
`sovereign.data.adapter`). It supplies exactly the interface
`forex_exit_manager` already calls:

    bridge.get_open_trades()                     -> reconcile()
    bridge.get_historical_candles(pair, ...)      -> LiveMarketProvider._candles()
    bridge.set_stop(trade_id, price)              -> run_daily() LIVE path
    bridge.close_trade(trade_id)                  -> run_daily() LIVE path

and adds one capability neither caller uses yet: `open_market_order()`, for a
FUTURE decision (flagged, not made here) about routing paper-carry ENTRIES
through a real broker fill instead of the tape. See the module docstring in
`daytrade/paper_carry_runner.py` and the task report that shipped this file —
wiring `open_market_order()` into the paper entry path is explicitly NOT done
here; it changes three files' documented invariant ("never touches a broker")
and was called out as a question rather than guessed.

SAFETY — enforced in code, not by convention
----------------------------------------------
1. PRACTICE ENDPOINT ONLY. `_check_practice()` independently asserts BOTH
   `OANDA_LIVE == "0"` AND the base URL host is exactly
   `api-fxpractice.oanda.com`. Either check failing raises `OandaRefused` at
   CONSTRUCTION — a single check would be one env-var typo from live; two
   independent checks are not.
2. SHIPS DISARMED. Matches `daytrade/broker.py` / `daytrade/paper_carry_runner.py`'s
   three-coordinated-edits seriousness:
     a. `ARMED = False` module constant below — a reviewable diff to flip.
     b. `mode="armed"` passed per-construction (mirrors `Broker(mode=...)`).
     c. Interactive `'send'` confirmation before the FIRST mutating call in a
        session, unless the caller supplied `assume_yes=True`.
   All three must hold or `_mutate()` never reaches the network. Reads
   (account summary, instrument list, candles, open trades) are NEVER gated —
   they cannot place or move anything.
3. FAIL LOUD ON AN UNKNOWN FILL. `open_market_order()` and `close_trade()`
   parse the OANDA response for an explicit fill (`orderFillTransaction`).
   If the shape is missing or the order was rejected/cancelled, this raises
   `OandaRefused` — it never returns a fabricated fill.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from execution.alpaca import load_env  # noqa: E402 — generic .env loader, not Alpaca-specific

PRACTICE_HOST = "api-fxpractice.oanda.com"

# ═══════════════════════════════════════════════════════════════════════════
#  THE TOGGLE — flipping this to True is step (a) of 3 of arming this bridge.
#  Steps (b) mode="armed" and (c) interactive/--yes confirmation still gate
#  every mutating call even when this is True.
# ═══════════════════════════════════════════════════════════════════════════
ARMED = False


class OandaRefused(RuntimeError):
    """The bridge declined to act, or the broker's response could not be
    trusted as a real fill. Never downgraded to a warning."""


# ── pair normalization ──────────────────────────────────────────────────────

def to_oanda_instrument(pair: str) -> str:
    """'EURUSD=X' / 'EURUSD' / 'EUR_USD' -> 'EUR_USD'. Raises on anything that
    doesn't cleanly resolve to a 6-letter FX pair — no guessing on a
    correctness-critical mapping."""
    sym = pair.upper().replace("=X", "").replace("_", "")
    if len(sym) != 6 or not sym.isalpha():
        raise OandaRefused(f"cannot map {pair!r} to an OANDA instrument (expected 6 letters)")
    return f"{sym[:3]}_{sym[3:]}"


# ── credentials / practice-only guard ───────────────────────────────────────

def _check_practice(live_flag: str, base_url: str) -> str:
    """Two INDEPENDENT checks. Both must pass. Raises OandaRefused otherwise."""
    if (live_flag or "").strip() != "0":
        raise OandaRefused(
            f"refusing to construct OandaBridge: OANDA_LIVE={live_flag!r}, not '0'. "
            "This bridge is practice-only by construction.")
    host = urllib.parse.urlparse(base_url).hostname or ""
    if host != PRACTICE_HOST:
        raise OandaRefused(
            f"refusing base URL {base_url!r}: host is {host!r}, not {PRACTICE_HOST!r}. "
            "Practice-only by construction — going live is not a config change here.")
    return base_url.rstrip("/")


def _credentials() -> tuple[str, str, str, str]:
    """(api_key, account_id, base_url, live_flag) from .env, never cached empty."""
    import os
    load_env()
    api_key = os.environ.get("OANDA_API_KEY", "").strip()
    account_id = os.environ.get("OANDA_ACCOUNT_ID", "").strip()
    base_url = os.environ.get("OANDA_BASE_URL", "").strip()
    live_flag = os.environ.get("OANDA_LIVE", "").strip()
    if not api_key or not account_id or not base_url:
        raise OandaRefused(
            "OANDA_API_KEY / OANDA_ACCOUNT_ID / OANDA_BASE_URL missing. All three are "
            "required. Put them in .env (gitignored).")
    return api_key, account_id, base_url, live_flag


class OandaBridge:
    """OANDA v20 REST client. Practice-only by construction; every mutating
    call routes through `_mutate()`, which enforces the 3-step arming ritual.
    """

    def __init__(self, *, mode: str = "shadow", assume_yes: bool = False,
                 api_key: Optional[str] = None, account_id: Optional[str] = None,
                 base_url: Optional[str] = None, live_flag: Optional[str] = None):
        if mode not in ("shadow", "armed"):
            raise ValueError(f"mode must be shadow|armed, got {mode!r}")
        env_key, env_acct, env_base, env_live = _credentials()
        self._key = api_key or env_key
        self.account_id = account_id or env_acct
        self.base = _check_practice(live_flag if live_flag is not None else env_live,
                                    base_url or env_base)
        self.mode = mode
        self.assume_yes = assume_yes
        self._confirmed = False

    # -- transport ---------------------------------------------------------
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

    def _req(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        url = f"{self.base}{path}"
        _check_practice("0", self.base)  # re-checked per request, not just at init
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            raise OandaRefused(f"{method} {path} -> HTTP {e.code}: {detail}") from e
        except Exception as e:
            raise OandaRefused(f"{method} {path} failed: {e!r}") from e

    # -- reads (never gated — cannot place or move anything) ---------------
    def account_summary(self) -> dict:
        return self._req("GET", f"/v3/accounts/{self.account_id}/summary")

    def list_instruments(self) -> list:
        return self._req("GET", f"/v3/accounts/{self.account_id}/instruments").get("instruments", [])

    def get_open_trades(self) -> list:
        """Shape matches what `forex_exit_manager.reconcile()` reads:
        id/tradeID, instrument, currentUnits, price, openTime."""
        return self._req("GET", f"/v3/accounts/{self.account_id}/openTrades").get("trades", [])

    def get_historical_candles(self, pair: str, start: str, end: str,
                                granularity: str = "D"):
        """Returns a pandas DataFrame indexed by date with High/Low/Close
        columns — the exact shape `LiveMarketProvider` and the ATR helper
        (`ForexSignalEngine._compute_atr_pct`) already expect. Only COMPLETE
        candles are included (never the still-forming bar — 'never revise').
        """
        import pandas as pd
        instrument = to_oanda_instrument(pair)
        params = urllib.parse.urlencode({
            "granularity": granularity, "price": "M",
            "from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z",
        })
        resp = self._req("GET", f"/v3/instruments/{instrument}/candles?{params}")
        rows = []
        for c in resp.get("candles", []):
            if not c.get("complete"):
                continue
            mid = c["mid"]
            rows.append({
                "time": c["time"], "Open": float(mid["o"]), "High": float(mid["h"]),
                "Low": float(mid["l"]), "Close": float(mid["c"]),
            })
        if not rows:
            raise OandaRefused(f"no complete candles returned for {instrument} "
                               f"[{start}..{end}] granularity={granularity}")
        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df.pop("time")).dt.tz_localize(None)
        return df

    # -- arming ritual -------------------------------------------------------
    def _confirm(self, desc: str) -> bool:
        if self._confirmed or self.assume_yes:
            self._confirmed = True
            return True
        acct = self.account_summary()
        print("\n" + "-" * 68)
        print("  ARMING — this sends a REAL order to the OANDA PRACTICE account.")
        print(f"  account {self.account_id}  "
              f"balance {acct.get('account', {}).get('balance')}  endpoint {self.base}")
        print(f"  first action: {desc}")
        print("-" * 68)
        try:
            ok = input("  type 'send' to confirm this session: ").strip().lower() == "send"
        except EOFError:
            ok = False
        self._confirmed = ok
        if not ok:
            print("  not confirmed — nothing sent\n")
        return ok

    def _mutate(self, desc: str, fn):
        """Every write path funnels through here. Returns a REFUSED/SHADOW/
        DECLINED_BY_USER/SENT record — never a silent no-op (CLAUDE.md
        non-negotiable 3)."""
        if not (ARMED and self.mode == "armed"):
            print(f"           [shadow] WOULD {desc}")
            return {"status": "SHADOW", "sent": False}
        if not self._confirm(desc):
            return {"status": "DECLINED_BY_USER", "sent": False}
        result = fn()
        return {"status": "SENT", "sent": True, "response": result}

    # -- mutating: exit management (what forex_exit_manager already calls) --
    def set_stop(self, trade_id: str, price: float) -> dict:
        desc = f"SET STOP on trade {trade_id} -> {price:.5f}"
        return self._mutate(desc, lambda: self._req(
            "PUT", f"/v3/accounts/{self.account_id}/trades/{trade_id}/orders",
            {"stopLoss": {"price": f"{price:.5f}", "timeInForce": "GTC"}}))

    def close_trade(self, trade_id: str) -> dict:
        desc = f"CLOSE trade {trade_id}"

        def _do():
            resp = self._req("PUT", f"/v3/accounts/{self.account_id}/trades/{trade_id}/close")
            fill = resp.get("orderFillTransaction")
            if fill is None or "price" not in fill:
                raise OandaRefused(
                    f"close on trade {trade_id} returned no orderFillTransaction — "
                    f"unknown fill state, refusing to record a close. Raw: {resp!r}")
            return resp

        return self._mutate(desc, _do)

    # -- mutating: entry (built, NOT wired into the paper loop — see module
    #    docstring; the decision to route paper entries through this is
    #    flagged, not made here) ------------------------------------------
    def open_market_order(self, pair: str, direction: str, units: float, *,
                          stop_loss_price: Optional[float] = None) -> dict:
        """MARKET order, FOK time-in-force. Carry entries hold a median 6 days
        — there is no execution-latency reason to accept a partial fill or a
        resting limit at entry, so FOK (fill completely now or not at all) is
        the natural fit, same logic as `daytrade/broker.py`'s market orders."""
        if direction not in ("LONG", "SHORT"):
            raise OandaRefused(f"direction must be LONG or SHORT, got {direction!r}")
        instrument = to_oanda_instrument(pair)
        signed_units = abs(units) if direction == "LONG" else -abs(units)
        order = {"type": "MARKET", "instrument": instrument,
                 "units": f"{signed_units:.0f}", "timeInForce": "FOK",
                 "positionFill": "DEFAULT"}
        if stop_loss_price is not None:
            order["stopLossOnFill"] = {"price": f"{stop_loss_price:.5f}", "timeInForce": "GTC"}
        desc = f"OPEN {instrument} {direction} {abs(units):.0f} units (FOK)"

        def _do():
            resp = self._req("POST", f"/v3/accounts/{self.account_id}/orders", {"order": order})
            fill = resp.get("orderFillTransaction")
            if fill is None or "price" not in fill:
                cancel = resp.get("orderCancelTransaction")
                reason = cancel.get("reason") if cancel else "no orderFillTransaction in response"
                raise OandaRefused(
                    f"open {instrument} {direction} did not produce a fill "
                    f"({reason}) — refusing to record an order as filled. Raw: {resp!r}")
            return resp

        return self._mutate(desc, _do)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    """Connectivity check. READ-ONLY: account summary + instrument list +
    open trades. Never places or moves anything."""
    import argparse
    ap = argparse.ArgumentParser(description="OANDA practice connectivity check (read-only)")
    ap.add_argument("--pair", default="EUR_USD")
    a = ap.parse_args()
    bridge = OandaBridge(mode="shadow")
    print(f"endpoint {bridge.base}")
    acct = bridge.account_summary().get("account", {})
    print(f"account  {acct.get('id')}  balance={acct.get('balance')}  "
          f"currency={acct.get('currency')}  openTradeCount={acct.get('openTradeCount')}")
    instruments = bridge.list_instruments()
    print(f"instruments available: {len(instruments)}")
    trades = bridge.get_open_trades()
    print(f"open trades: {len(trades)}")


if __name__ == "__main__":
    main()
