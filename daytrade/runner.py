#!/usr/bin/env python3
"""RUNNER — the local cockpit loop. Build-order item 1 (ARCHITECTURE.md:83-88).

    live quote  ->  decide_exit(state)  ->  advice line printed

It owns no exit logic. Every rule comes from `stockfish_exit.decide_exit`, which
is IMPORTED, never copied — a second implementation is how live diverges from
test (ARCHITECTURE.md Layer 2). State evolves through `stockfish_exit.apply_action`
for the same reason.

SAFETY (APEX spine #1, as amended 2026-08-03): a PAPER broker is permitted, and
it is OFF unless you ask for it. Default `--broker off` prints advice and nothing
else. `--broker shadow` shows the orders it would send. `--broker armed` sends
them to the Alpaca paper endpoint after an interactive confirm. Live capital is
out of scope and daytrade/broker.py refuses any non-paper host.

FAIL LOUD (spine #2): a missing or stale quote prints a loud skip line and the
cycle is ABANDONED — decide_exit is not called. The ladder never advances on a
price we don't trust. Malformed plans raise on load rather than defaulting.

QUOTE SOURCE: yfinance 1-minute bars. Alpaca is not usable here — that account
403s on /quotes/latest and /snapshot and serves SIP only beyond a 15-minute lag
(measured, documented at execution/alpaca.py:31-39).

Usage
    python3 daytrade/runner.py --plan data/daytrade/plan.json
    python3 daytrade/runner.py --plan ... --once
    python3 daytrade/runner.py --plan ... --replay path/to/prices.json
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))   # import the engine beside us
from stockfish_exit import TradeState, decide_exit, apply_action  # noqa: E402
import broker as broker_mod  # noqa: E402

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
BIAS = ROOT / "data" / "daytrade" / "bias.json"
LOGDIR = ROOT / "data" / "daytrade"

REQUIRED = ("symbol", "direction", "entry", "qty", "sl", "tp1", "trail_dist")


class QuoteUnavailable(RuntimeError):
    """No price we are willing to act on. Never downgraded to a guess."""


# ---------------------------------------------------------------- the blueprint

def load_plan(path: Path) -> dict:
    """Read the pre-open blueprint. Anything ambiguous raises — Layer 0 says the
    blueprint is set BEFORE the open, so there is nothing to infer at runtime.
    """
    if not path.exists():
        raise FileNotFoundError(f"no plan at {path} — write the blueprint before the open")
    plan = json.loads(path.read_text())

    missing = [k for k in REQUIRED if plan.get(k) is None]
    if missing:
        raise ValueError(f"plan {path} missing required field(s): {', '.join(missing)}")

    d = plan["direction"]
    if isinstance(d, str):
        d = {"long": 1, "short": -1}.get(d.strip().lower())
    if d not in (1, -1):
        raise ValueError(f"direction must be long/short or +1/-1, got {plan['direction']!r}")
    plan["direction"] = d

    has_tp2, has_goal = plan.get("tp2") is not None, plan.get("day_goal_usd") is not None
    if has_tp2 == has_goal:
        raise ValueError("plan needs exactly one of tp2 or day_goal_usd, not both and not neither")
    if has_goal:
        qty = float(plan["qty"])
        if qty <= 0:
            raise ValueError(f"day_goal_usd needs a positive qty, got {qty}")
        # the "$300 goal" of the doctrine, expressed as a price level
        plan["tp2"] = float(plan["entry"]) + d * (float(plan["day_goal_usd"]) / qty)
    return plan


def state_from_plan(plan: dict) -> TradeState:
    return TradeState(
        direction=plan["direction"],
        entry=float(plan["entry"]),
        qty=float(plan["qty"]),
        price=float(plan["entry"]),          # hwm starts at entry, updated on the first tick
        sl=float(plan["sl"]),
        tp1=float(plan["tp1"]),
        tp2=float(plan["tp2"]),
        trail_dist=float(plan["trail_dist"]),
        goal_fraction=float(plan.get("goal_fraction", 0.5)),
        flatten_at_et=plan.get("flatten_at_et"),
    )


# ------------------------------------------------------------------ the quote

def fetch_quote(symbol: str, max_stale: int) -> tuple[float, datetime, float]:
    """Newest 1-minute close and how old it is. Raises rather than guessing."""
    import pandas as pd
    import yfinance as yf

    try:
        df = yf.download(symbol, period="1d", interval="1m",
                         progress=False, auto_adjust=False)
    except Exception as e:
        raise QuoteUnavailable(f"yfinance download failed for {symbol}: {e!r}") from e

    if df is None or df.empty:
        raise QuoteUnavailable(f"no 1m bars returned for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    ts = df.index[-1].to_pydatetime()
    ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
    price = float(df["Close"].iloc[-1])
    if price != price or price <= 0:
        raise QuoteUnavailable(f"unusable close {price!r} for {symbol}")

    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age > max_stale:
        raise QuoteUnavailable(
            f"newest {symbol} bar is {age:.0f}s old (limit {max_stale}s) — "
            "market closed, feed lagging, or symbol wrong")
    return price, ts, age


# ------------------------------------------------------------------- the bias

def read_urgency(require: bool) -> tuple[str | None, str]:
    """ONLY the urgency field crosses from ALPHAZERO (ARCHITECTURE.md:53-54).

    'exit'/'tighten' pass through. 'none'/'stale' become None — a stale reader
    must never manufacture an interrupt. Missing or unreadable returns None and
    a note that gets printed every single cycle.
    """
    if not BIAS.exists():
        if require:
            raise RuntimeError(f"--require-bias set but {BIAS} does not exist")
        return None, "MISSING"
    try:
        u = json.loads(BIAS.read_text()).get("urgency")
    except Exception as e:
        if require:
            raise RuntimeError(f"--require-bias set but {BIAS} is unreadable: {e}") from e
        return None, "UNREADABLE"

    if u in ("exit", "tighten"):
        return u, u
    if u in ("none", "stale", None):
        return None, str(u)
    return None, f"UNRECOGNISED:{u!r}"


# -------------------------------------------------------------------- output

def print_cycle(clock: str, symbol: str, price: float, s: TradeState,
                bias_note: str, age_note: str, actions) -> None:
    head = (f"{clock} {symbol} {price:9.2f}  hwm {s.hwm:9.2f}  sl {s.sl:9.2f}  "
            f"qty {s.qty:g}  bias:{bias_note}{age_note}")
    for i, a in enumerate(actions):
        tail = a.kind if a.kind != "MOVE_SL" else f"MOVE_SL -> {a.sl:.2f}"
        if a.kind == "TAKE_PARTIAL":
            tail = f"TAKE_PARTIAL {a.fraction:.0%}"
        print(f"{head if i == 0 else ' ' * len(head)} | {tail:22s} {a.reason}")
        sys.stdout.flush()


def print_flatten(symbol: str, price: float, reason: str) -> None:
    bar = "=" * 68
    print(f"\n{bar}\n  *** FLATTEN NOW — EXIT_ALL ***   {symbol} @ {price:.2f}\n"
          f"  reason: {reason}\n"
          f"  advice only — place the order yourself\n{bar}\n")
    sys.stdout.flush()


def log_cycle(rec: dict) -> None:
    LOGDIR.mkdir(parents=True, exist_ok=True)
    path = LOGDIR / f"session_{datetime.now(ET).date()}.jsonl"
    with path.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")


# ---------------------------------------------------------------------- loop

def run(plan: dict, *, interval: int, once: bool, replay: list | None,
        max_stale: int, require_bias: bool, broker=None) -> int:
    s = state_from_plan(plan)
    symbol = plan["symbol"]
    live = replay is None
    src = "live" if live else "replay"

    print(f"# runner {src} | {symbol} {'LONG' if s.direction > 0 else 'SHORT'} "
          f"entry {s.entry} qty {s.qty:g} sl {s.sl} tp1 {s.tp1} tp2 {s.tp2:.2f} "
          f"trail {s.trail_dist} flatten {s.flatten_at_et or 'off'}")
    mode = f"broker {broker.mode} -> {broker.base}" if broker else "advice only, no broker"
    print(f"# {mode}. ctrl-c to stop.\n")

    step = 0
    while True:
        clock = datetime.now(ET).strftime("%H:%M:%S")

        if live:
            try:
                price, qts, age = fetch_quote(symbol, max_stale)
            except QuoteUnavailable as e:
                # loud, and the cycle is over — decide_exit is NOT called
                print(f"{clock} !! NO USABLE QUOTE — cycle skipped, ladder not advanced\n"
                      f"           {e}")
                sys.stdout.flush()
                if once:
                    return 1
                time.sleep(interval)
                continue
            age_note, qts_iso = f" q{age:.0f}s", qts.isoformat()
        else:
            if step >= len(replay):
                print(f"\n# replay exhausted after {step} price(s)")
                return 0
            price, age_note, qts_iso = float(replay[step]), " replay", None

        urgency, bias_note = read_urgency(require_bias)
        if bias_note in ("MISSING", "UNREADABLE"):
            print(f"{clock} !! BIAS {bias_note} at {BIAS} — running with no urgent channel")

        s.price = price
        s.urgent = urgency
        # A real clock arms time-flatten. In replay there is no honest clock, so
        # the rule stays disarmed and the replay stays reproducible.
        s.now_et = datetime.now(ET).strftime("%H:%M") if live else None

        actions = decide_exit(s)
        for a in actions:
            apply_action(s, a)

        print_cycle(clock, symbol, price, s, bias_note, age_note, actions)

        # Broker is downstream of the decision, never upstream of it. Whatever it
        # does or refuses to do, the engine already decided and the ladder already
        # advanced — so a broker failure never silently changes the exit policy.
        orders = []
        if broker is not None:
            for intent in broker_mod.intents_from_actions(actions, symbol):
                orders.append(broker.send(intent))

        log_cycle({
            "ts": datetime.now(timezone.utc).isoformat(), "source": src, "step": step,
            "symbol": symbol, "price": price, "quote_ts": qts_iso,
            "hwm": s.hwm, "sl": s.sl, "qty": s.qty,
            "tp1_done": s.tp1_done, "tp2_done": s.tp2_done,
            "urgent": urgency, "bias_note": bias_note,
            "actions": [{"kind": a.kind, "sl": a.sl, "fraction": a.fraction,
                         "reason": a.reason} for a in actions],
            "broker_mode": broker.mode if broker else "off",
            "orders": orders,
        })

        if any(a.kind == "EXIT_ALL" for a in actions):
            print_flatten(symbol, price,
                          next(a.reason for a in actions if a.kind == "EXIT_ALL"))
            return 0

        step += 1
        if once:
            return 0
        if live:
            time.sleep(interval)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="local runner: quotes -> decide_exit -> advice")
    ap.add_argument("--plan", default=str(ROOT / "data" / "daytrade" / "plan.json"))
    ap.add_argument("--interval", type=int, default=15, help="seconds between live polls")
    ap.add_argument("--once", action="store_true", help="one cycle, then stop")
    ap.add_argument("--replay", metavar="FILE", help="JSON list of prices — offline test path")
    ap.add_argument("--max-stale", type=int, default=180, help="seconds before a quote is refused")
    ap.add_argument("--require-bias", action="store_true", help="hard-fail if bias.json is absent")
    ap.add_argument("--broker", choices=("off", "shadow", "armed"), default="off",
                    help="off: advice only. shadow: print the orders. armed: send them (paper).")
    ap.add_argument("--yes", action="store_true", help="skip the arming confirmation prompt")
    a = ap.parse_args(argv)

    plan = load_plan(Path(a.plan))
    replay = json.loads(Path(a.replay).read_text()) if a.replay else None
    if replay is not None and not isinstance(replay, list):
        raise ValueError(f"--replay {a.replay} must contain a JSON list of prices")
    if a.broker == "armed" and replay is not None:
        raise ValueError("refusing --broker armed with --replay: replayed prices are not "
                         "a market, and orders placed against them would be nonsense")

    broker = None if a.broker == "off" else broker_mod.Broker(mode=a.broker, assume_yes=a.yes)

    try:
        return run(plan, interval=a.interval, once=a.once, replay=replay,
                   max_stale=a.max_stale, require_bias=a.require_bias, broker=broker)
    except KeyboardInterrupt:
        print("\n# stopped by hand — position untouched, nothing was ever sent anywhere")
        return 0


if __name__ == "__main__":
    sys.exit(main())
