#!/usr/bin/env python3
"""BROKER — Alpaca PAPER order execution. Permitted by the 2026-08-03 amendment
to safety spine #1 (ARCHITECTURE.md:75-95), under the four conditions it names.

    STOCKFISH says     ->  this module translates      ->  Alpaca paper
    "MOVE_SL 204.00"       "PATCH the protective stop"     (or prints it, in shadow)

It holds NO exit policy. Every decision arrives as an `Action` from
`stockfish_exit.decide_exit`; this file only knows how to express those decisions
as orders. If you find yourself adding a price comparison here, it belongs in
the engine instead.

THE FOUR CONDITIONS, enforced in code
-------------------------------------
a. PAPER ONLY  — `_check_host` refuses any base URL whose host is not
   `paper-api.alpaca.markets`. Going live means editing that constant: a
   reviewable diff, not a config typo or a stray env var.
b. SHADOW BY DEFAULT — mode='shadow' prints the order and sends nothing. Arming
   is per-run and per-process; nothing on disk remembers being armed.
c. CONFIRM FIRST SEND — armed mode prompts once, showing account + order, unless
   the run was started with an explicit --yes.
d. EVERYTHING LOGGED — send() returns a record for the caller's session JSONL,
   including refusals and errors. An unlogged order is the silent data loss
   CLAUDE.md non-negotiable #3 forbids.

FAIL LOUD: no order is ever "approximately" placed. Missing position, missing
credentials, unexpected HTTP — all raise or return an explicit REFUSED record.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from execution.alpaca import load_env  # noqa: E402  — ONE credential path, reused

PAPER_HOST = "paper-api.alpaca.markets"
PAPER_BASE = f"https://{PAPER_HOST}/v2"


class BrokerRefused(RuntimeError):
    """The broker declined to act. Never downgraded to a warning."""


# ------------------------------------------------------------------ intents

@dataclass
class OrderIntent:
    """One order, fully specified, before anything is sent."""
    kind: str                       # STOP_SYNC | REDUCE | CLOSE_ALL
    symbol: str
    reason: str                     # carried verbatim from the engine's Action
    stop_price: Optional[float] = None
    fraction: Optional[float] = None

    def describe(self) -> str:
        if self.kind == "STOP_SYNC":
            return f"protective stop -> {self.stop_price:.2f}"
        if self.kind == "REDUCE":
            return f"reduce position by {self.fraction:.0%} (market)"
        return "close entire position (market)"


def intents_from_actions(actions, symbol: str) -> list[OrderIntent]:
    """Translate engine Actions into order intents. Pure, no network.

    HOLD produces nothing — the whole point of 'set it and forget it'.

    One cycle can legitimately emit several MOVE_SLs (the TP2 cycle moves the
    stop to TP1, then immediately trails it off the high). Sending both would be
    two round trips and would briefly rest the stop at a level the engine had
    already superseded within the same tick. Only the LAST stop level in a batch
    is real, so they collapse to one order — with every superseded reason kept
    in the record, because the ledger should show the whole thought.
    """
    out: list[OrderIntent] = []
    for a in actions:
        if a.kind == "HOLD":
            continue
        if a.kind == "MOVE_SL":
            if a.sl is None:
                raise BrokerRefused("MOVE_SL carries no stop price")
            out.append(OrderIntent("STOP_SYNC", symbol, a.reason, stop_price=a.sl))
        elif a.kind == "TAKE_PARTIAL":
            if not a.fraction or not (0 < a.fraction < 1):
                raise BrokerRefused(f"TAKE_PARTIAL fraction out of range: {a.fraction!r}")
            out.append(OrderIntent("REDUCE", symbol, a.reason, fraction=a.fraction))
        elif a.kind == "EXIT_ALL":
            out.append(OrderIntent("CLOSE_ALL", symbol, a.reason))
        else:
            raise BrokerRefused(f"no broker translation for action {a.kind!r}")

    stops = [i for i in out if i.kind == "STOP_SYNC"]
    if len(stops) > 1:
        keep, dropped = stops[-1], stops[:-1]
        keep.reason = " ; superseded: ".join([keep.reason] + [d.reason for d in dropped])
        out = [i for i in out if i.kind != "STOP_SYNC" or i is keep]
    return out


# ------------------------------------------------------------------- client

def _check_host(base_url: str) -> str:
    host = urllib.parse.urlparse(base_url).hostname or ""
    if host != PAPER_HOST:
        raise BrokerRefused(
            f"refusing base URL {base_url!r}: host is {host!r}, not {PAPER_HOST!r}. "
            "Spine #1 permits the paper endpoint only.")
    return base_url


class Broker:
    """Alpaca paper client. Every mutating call routes through send()."""

    def __init__(self, mode: str = "shadow", *, base_url: str = PAPER_BASE,
                 assume_yes: bool = False):
        if mode not in ("shadow", "armed"):
            raise ValueError(f"mode must be shadow|armed, got {mode!r}")
        self.mode = mode
        self.base = _check_host(base_url).rstrip("/")
        self.assume_yes = assume_yes
        self._confirmed = False
        self._key = self._sec = None

    # -- auth -------------------------------------------------------------
    def _headers(self) -> dict:
        if self._key is None:
            import os
            load_env()
            kid = os.environ.get("ALPACA_API_KEY", "").strip()
            sec = os.environ.get("ALPACA_SECRET_KEY", "").strip()
            if not kid or not sec:
                # Assign nothing on failure. Caching an empty string here would let
                # the next call sail past this check and put an unauthenticated
                # request on the wire — a 401 that reads like a credential problem
                # but is really a bug in this function.
                raise BrokerRefused(
                    "ALPACA_API_KEY / ALPACA_SECRET_KEY missing. Both are required — "
                    "the key id alone cannot authenticate. Put them in .env (gitignored).")
            self._key, self._sec = kid, sec
        return {"APCA-API-KEY-ID": self._key, "APCA-API-SECRET-KEY": self._sec,
                "Content-Type": "application/json"}

    # -- transport --------------------------------------------------------
    def _req(self, method: str, path: str, body: dict | None = None):
        url = f"{self.base}{path}"
        _check_host(url)                       # re-checked per request, not just at init
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            raise BrokerRefused(f"{method} {path} -> HTTP {e.code}: {detail}") from e
        except Exception as e:
            raise BrokerRefused(f"{method} {path} failed: {e!r}") from e

    # -- reads ------------------------------------------------------------
    def account(self) -> dict:
        return self._req("GET", "/account")

    def position(self, symbol: str) -> dict | None:
        try:
            return self._req("GET", f"/positions/{symbol}")
        except BrokerRefused as e:
            if "HTTP 404" in str(e):
                return None                    # flat is a fact, not an error
            raise

    def open_stop(self, symbol: str) -> dict | None:
        """The protective stop currently working on this symbol, if any."""
        q = urllib.parse.urlencode({"status": "open", "symbols": symbol})
        for o in self._req("GET", f"/orders?{q}") or []:
            if o.get("type") in ("stop", "stop_limit"):
                return o
        return None

    # -- the one mutating path -------------------------------------------
    def send(self, intent: OrderIntent) -> dict:
        """Execute one intent. Returns a log record in every outcome."""
        rec = {"intent": asdict(intent), "mode": self.mode, "desc": intent.describe()}

        if self.mode == "shadow":
            print(f"           [shadow] WOULD SEND: {intent.describe()}  ({intent.reason})")
            return {**rec, "status": "SHADOW", "sent": False}

        try:
            pos = self.position(intent.symbol)
            if pos is None:
                raise BrokerRefused(
                    f"no open {intent.symbol} position — refusing to place a protective "
                    "or closing order against nothing")
            held = abs(float(pos["qty"]))
            long_side = float(pos["qty"]) > 0

            if not self._confirm(intent, held):
                return {**rec, "status": "DECLINED_BY_USER", "sent": False}

            if intent.kind == "CLOSE_ALL":
                res = self._req("DELETE", f"/positions/{intent.symbol}")
            elif intent.kind == "REDUCE":
                qty = self._whole(held * intent.fraction)
                if qty <= 0:
                    raise BrokerRefused(
                        f"{intent.fraction:.0%} of {held:g} rounds to 0 shares — nothing to reduce")
                res = self._req("POST", "/orders", {
                    "symbol": intent.symbol, "qty": str(qty),
                    "side": "sell" if long_side else "buy",
                    "type": "market", "time_in_force": "day"})
            else:                               # STOP_SYNC
                res = self._sync_stop(intent, held, long_side)

            return {**rec, "status": "SENT", "sent": True, "response": res}

        except BrokerRefused as e:
            print(f"           !! BROKER REFUSED: {e}")
            return {**rec, "status": "REFUSED", "sent": False, "error": str(e)}

    def _sync_stop(self, intent: OrderIntent, held: float, long_side: bool) -> dict:
        """Move the protective stop: patch the working one, else place it.

        Stateless on purpose — it reads the live order book each time rather than
        trusting a cached order id, so a stop cancelled by hand self-heals.
        """
        px = str(round(intent.stop_price, 2))
        existing = self.open_stop(intent.symbol)
        if existing:
            return self._req("PATCH", f"/orders/{existing['id']}",
                             {"stop_price": px, "qty": str(self._whole(held))})
        return self._req("POST", "/orders", {
            "symbol": intent.symbol, "qty": str(self._whole(held)),
            "side": "sell" if long_side else "buy",
            "type": "stop", "stop_price": px, "time_in_force": "day"})

    @staticmethod
    def _whole(q: float) -> int:
        """Shares are integers. Truncate, never round up past what is held."""
        return int(q)

    def _confirm(self, intent: OrderIntent, held: float) -> bool:
        if self._confirmed or self.assume_yes:
            self._confirmed = True
            return True
        acct = self.account()
        print("\n" + "-" * 68)
        print("  ARMING — this sends a REAL order to the Alpaca PAPER account.")
        print(f"  account {acct.get('account_number')}  equity ${float(acct.get('equity', 0)):,.2f}"
              f"  status {acct.get('status')}")
        print(f"  endpoint {self.base}")
        print(f"  first order: {intent.symbol}  {intent.describe()}  (holding {held:g})")
        print("-" * 68)
        try:
            ok = input("  type 'send' to confirm this session: ").strip().lower() == "send"
        except EOFError:
            ok = False
        self._confirmed = ok
        if not ok:
            print("  not confirmed — nothing sent, run continues in advice-only terms\n")
        return ok


if __name__ == "__main__":
    # Connectivity check. Read-only: account + position, never an order.
    import argparse
    ap = argparse.ArgumentParser(description="Alpaca paper connectivity check (read-only)")
    ap.add_argument("--symbol", default="NVDA")
    a = ap.parse_args()
    b = Broker(mode="shadow")
    print(f"endpoint {b.base}")
    try:
        acct = b.account()
        print(f"account  {acct.get('account_number')}  status={acct.get('status')}  "
              f"equity=${float(acct.get('equity', 0)):,.2f}  "
              f"buying_power=${float(acct.get('buying_power', 0)):,.2f}")
        pos = b.position(a.symbol)
        print(f"position {a.symbol}: " + (f"{pos['qty']} @ {pos['avg_entry_price']}" if pos else "flat"))
        stop = b.open_stop(a.symbol)
        print(f"stop     {a.symbol}: " + (f"{stop['stop_price']} (id {stop['id'][:8]})" if stop else "none working"))
    except BrokerRefused as e:
        print(f"!! {e}")
        sys.exit(1)
