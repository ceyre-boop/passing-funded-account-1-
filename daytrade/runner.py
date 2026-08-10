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

QUOTE SOURCE: Alpaca real-time bid/ask first, yfinance 1-minute bars behind it.
Re-measured 2026-08-03 on this account: /quotes/latest returns 200 with a usable
book (NVDA 206.89/207.20, 0.15%), which corrects the older note at
execution/alpaca.py:31-39. `?feed=sip` is still 403, so the live feed is IEX —
thin enough on some names to need the crossed/locked/wide guards, which is
exactly why yfinance stays as the fallback rather than being deleted.

Usage
    python3 daytrade/runner.py --plan data/daytrade/plan.json
    python3 daytrade/runner.py --plan ... --once
    python3 daytrade/runner.py --plan ... --replay path/to/prices.json
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))   # import the engine beside us
from stockfish_exit import (TradeState, decide_exit, apply_actions,  # noqa: E402
                            policy_params, effective_stop, resolve_channels)
import broker as broker_mod  # noqa: E402

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
BIAS = ROOT / "data" / "daytrade" / "bias.json"
DIRECTIVES = ROOT / "data" / "daytrade" / "directives.json"
LOGDIR = ROOT / "data" / "daytrade"
EVENTS_DIR = ROOT / "data" / "daytrade"      # spec 012 event logs live beside the session logs
SOURCE_VERSION = "runner-1"                  # producer identity on every emitted event

REQUIRED = ("symbol", "direction", "entry", "qty", "sl")


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

    # R-multiple geometry. Live, you know your fill and your stop; the ladder
    # should follow from them rather than being hand-computed under time pressure.
    entry, stop = float(plan["entry"]), float(plan["sl"])
    risk = abs(entry - stop)
    if risk <= 0:
        raise ValueError(f"entry {entry} and sl {stop} give zero risk — no R geometry")
    plan["risk_per_share"] = risk

    if plan.get("tp1") is None:
        if plan.get("tp1_r") is None:
            raise ValueError("plan needs tp1 or tp1_r")
        plan["tp1"] = entry + d * float(plan["tp1_r"]) * risk
    if plan.get("trail_dist") is None:
        if plan.get("trail_r") is None:
            raise ValueError("plan needs trail_dist or trail_r")
        plan["trail_dist"] = float(plan["trail_r"]) * risk

    named = [k for k in ("tp2", "tp2_r", "day_goal_usd") if plan.get(k) is not None]
    if len(named) != 1:
        raise ValueError(f"plan needs exactly one of tp2, tp2_r, day_goal_usd — got {named}")
    if "tp2_r" in named:
        plan["tp2"] = entry + d * float(plan["tp2_r"]) * risk
    elif "day_goal_usd" in named:
        qty = float(plan["qty"])
        if qty <= 0:
            raise ValueError(f"day_goal_usd needs a positive qty, got {qty}")
        # the "$300 goal" of the doctrine, expressed as a price level
        plan["tp2"] = entry + d * (float(plan["day_goal_usd"]) / qty)
    return plan


def state_from_plan(plan: dict) -> TradeState:
    """Build the engine state. Exit-policy params come from the plan because the
    regime classifier (spec 001) does not exist yet — until it does, the exit
    style is Colin's read, set before the open, which is the doctrine anyway.

    Either name a preset in `exit_policy`, or set the params directly. Naming a
    preset AND overriding its params is refused rather than silently resolved —
    an ambiguous risk profile is not something to guess at 09:31.
    """
    # PRESENCE, not truthiness. `trail_mult: null` is a MEANINGFUL value — it is
    # how a plan says "never trail" (STATIC). Filtering None out as "unset" made
    # the engine fall back to its 1.0 default and trail at full width, which is
    # the exact opposite of what the plan asked for, silently. Caught in
    # pre-flight the night before training day 1; it would have run live.
    explicit = {k: plan[k] for k in ("trail_mult", "be_arm_frac", "hold_past_tp2")
                if k in plan}
    preset = plan.get("exit_policy")
    if preset and explicit:
        raise ValueError(
            f"plan sets exit_policy={preset!r} AND overrides {sorted(explicit)} — "
            "pick one. Presets are in stockfish_exit.POLICY_PARAMS.")
    params = policy_params(preset, plan) if preset else explicit

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
        **params,
    )


# ------------------------------------------------------------------ the quote

class Reading(NamedTuple):
    price: float
    ts: datetime
    age: float
    source: str
    detail: dict


def _age(ts: datetime, max_stale: int, symbol: str, what: str) -> float:
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age > max_stale:
        raise QuoteUnavailable(
            f"newest {symbol} {what} is {age:.0f}s old (limit {max_stale}s) — "
            "market closed, feed lagging, or symbol wrong")
    return age


def fetch_alpaca(symbol: str, max_stale: int, max_spread_pct: float) -> Reading:
    """Real-time NBBO-style quote from Alpaca's default feed.

    Measured 2026-08-03 on this account: /quotes/latest returns 200 and NVDA
    quoted 206.89/207.20 — a 0.15% spread, perfectly usable. This contradicts
    execution/alpaca.py's older note that these endpoints 403 and that IEX is
    useless; see that file's corrected header. `?feed=sip` is still 403, so this
    is IEX, which carries a small share of volume — hence the spread and
    crossed/locked guards below, and the yfinance fallback behind them.

    Reuses execution.quotes.Quote rather than re-deriving spread arithmetic.
    """
    import os
    import urllib.error
    import urllib.request

    sys.path.insert(0, str(ROOT))
    from execution.alpaca import load_env
    from execution.quotes import _from_api

    load_env()
    kid = os.environ.get("ALPACA_API_KEY", "").strip()
    sec = os.environ.get("ALPACA_SECRET_KEY", "").strip()
    if not kid or not sec:
        raise QuoteUnavailable("no Alpaca credentials in .env")

    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/quotes/latest"
    req = urllib.request.Request(url, headers={
        "APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = json.loads(r.read())
    except Exception as e:
        raise QuoteUnavailable(f"alpaca quote failed for {symbol}: {e!r}") from e

    if "quote" not in payload:
        raise QuoteUnavailable(f"alpaca returned no quote for {symbol}")
    q = _from_api(symbol, payload["quote"])

    if not q.is_usable():
        raise QuoteUnavailable(
            f"{symbol} book unusable: bid {q.bid} ask {q.ask} "
            f"(crossed={q.is_crossed()} locked={q.is_locked()})")
    if q.spread_pct > max_spread_pct:
        raise QuoteUnavailable(
            f"{symbol} spread {q.spread_pct:.2%} exceeds {max_spread_pct:.2%} — "
            "thin book, not a price worth acting on")

    ts = datetime.fromisoformat(q.ts_utc.replace("Z", "+00:00"))
    return Reading(q.mid, ts, _age(ts, max_stale, symbol, "quote"), "alpaca",
                   {"bid": q.bid, "ask": q.ask, "bid_size": q.bid_size,
                    "ask_size": q.ask_size, "spread_pct": round(q.spread_pct, 6)})


def fetch_yfinance(symbol: str, max_stale: int) -> Reading:
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

    return Reading(price, ts, _age(ts, max_stale, symbol, "bar"), "yfinance",
                   {"kind": "1m bar close"})


def fetch_quote(symbol: str, max_stale: int, *, source: str,
                max_spread_pct: float) -> Reading:
    """Alpaca first, yfinance behind it. Every rejection is reported, not hidden.

    'auto' falls back only on QuoteUnavailable — a refused quote, not a bug. The
    fallback is announced on the line it happens, because a silent downgrade from
    a real bid/ask to a minute-old bar close is exactly the kind of quiet change
    that makes a log untrustworthy later.
    """
    if source == "yfinance":
        return fetch_yfinance(symbol, max_stale)
    if source == "alpaca":
        return fetch_alpaca(symbol, max_stale, max_spread_pct)
    try:
        return fetch_alpaca(symbol, max_stale, max_spread_pct)
    except QuoteUnavailable as e:
        print(f"           .. alpaca quote refused ({e}) — falling back to yfinance")
        return fetch_yfinance(symbol, max_stale)


# ------------------------------------------------------------------- the bias

def read_directive_urgency(symbol: str) -> tuple[str | None, str]:
    """Spec 016: the runner consumes directive INTERRUPTS only, at the default
    (unpromoted) authority level — tighten is the ceiling; an EMERGENCY from an
    unpromoted model is refused by evaluate(), which is the point of the
    authority table. Recommendations are logged in the note, never in force.

    Absent file = no directives = behavior identical to before this card.
    Malformed content prints a loud note and steers NOTHING — same convention
    as the bias channel's MISSING/UNREADABLE: an advisory input must not kill
    the cockpit, and it must not steer it while broken either.
    """
    if not DIRECTIVES.exists():
        return None, "no directives"
    from context_directive import (ContextDirective, DirectiveError,
                                   ReceiverContext, evaluate)
    try:
        raw = json.loads(DIRECTIVES.read_text())
        ds = [ContextDirective.from_dict(d) for d in raw]
    except (json.JSONDecodeError, DirectiveError, TypeError, KeyError,
            ValueError, OSError) as e:
        # OSError: the file can vanish between exists() and read (review
        # finding 11) — an advisory input must not kill the cockpit.
        return None, f"DIRECTIVES UNREADABLE ({e}) — steering nothing"
    dec = evaluate(ds, ReceiverContext(symbol=symbol))
    note = dec.why
    if dec.policy_candidate is not None:
        note += f" [recommendation {dec.policy_candidate} logged, not in force]"
    return dec.urgent, note


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
            f"qty {s.qty:g}  [{s.stage.value}]  bias:{bias_note}{age_note}")
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
        max_stale: int, require_bias: bool, broker=None,
        quote_source: str = "auto", max_spread_pct: float = 0.01,
        resume: bool = False) -> int:
    from trade_events import (JsonlEventLog, TornTailError, TradeEvent,
                              rebuild, to_trade_state, events_from_decision)
    symbol = plan["symbol"]
    live = replay is None
    src = "live" if live else "replay"

    # ---- Gate 2 (spec 012): the event log is the trade's authoritative
    # memory. Explicit resume only — a surprise reconstruction at 09:31 is
    # worse than an operator choice, and silently forking an open trade's
    # history is worse than either.
    session_date = datetime.now(ET).date()
    trade_id = f"{symbol}-{session_date}"
    events_path = EVENTS_DIR / f"events_{session_date}_{symbol}.jsonl"
    event_log = JsonlEventLog(events_path)
    try:
        events = event_log.load()
    except TornTailError:
        if not resume:
            raise                       # non-resume: the message guides the operator
        # A torn tail IS the crash Gate 2 defends against: the final line never
        # fully landed, so the decision it described was never durable — same
        # as crashing before the append. Dropping it is safe and LOUD.
        events = event_log.load(tolerate_torn_tail=True)
        removed = event_log.truncate_torn_tail()      # or the next append would
        print(f"# !! {events_path.name} had a TORN FINAL LINE (crash mid-append) "
              f"— {removed} byte(s) truncated; resuming from {len(events)} "
              "durable event(s)")
    if events and not resume and rebuild(events).closed:
        # The plan's rule: a CLOSED trade's log rotates aside, it never blocks
        # a fresh session and it is never overwritten.
        n = 0
        while (rotated := events_path.with_name(
                f"{events_path.stem}.closed-{n}.jsonl")).exists():
            n += 1
        events_path.rename(rotated)
        _dfd = os.open(events_path.parent, os.O_RDONLY)   # same durability
        try:                                              # obligation as creation
            os.fsync(_dfd)
        finally:
            os.close(_dfd)
        print(f"# previous CLOSED trade rotated aside -> {rotated.name}")
        events = []
    if events and resume:
        mem = rebuild(events)
        if mem.closed:
            raise RuntimeError(f"{events_path} is a CLOSED trade — nothing to "
                               "resume. Move it aside to start fresh.")
        if mem.pending_reduction_keys:
            raise RuntimeError(
                f"{events_path}: reduction(s) {sorted(mem.pending_reduction_keys)} "
                "were SUBMITTED but never CONFIRMED — the crash landed between "
                "the two appends. Reconciling that window is card 013's job and "
                "it is not wired; refusing to guess whether the broker filled. "
                "Resolve the log by hand before resuming.")
        s = to_trade_state(mem)
        if {k: v for k, v in plan.items() if not k.startswith("_")} != mem.plan:
            print("# !! RESUME: the CLI plan differs from the trade's recorded "
                  "plan — the LOG's plan wins (the log knows what was traded); "
                  "the CLI plan is ignored for this session")
        plan = dict(mem.plan)      # the trade's OWN recorded plan wins — the log knows
        applied_reduction_keys: set = set(mem.applied_reduction_keys)
        print(f"# RESUMED from {events_path.name}: {len(events)} events, "
              f"stage {s.stage.value}, sl {s.sl:g}, qty {s.qty:g}, "
              f"revision {s.state_revision}, {len(applied_reduction_keys)} "
              "applied key(s)")
    elif events:
        raise RuntimeError(
            f"{events_path} already holds {len(events)} event(s) for an open "
            f"trade and --resume was not given. Refusing to silently fork "
            "today's history — resume it, or move the file aside deliberately.")
    else:
        if resume:
            raise RuntimeError(f"--resume given but {events_path} holds no durable "
                               "events (missing, or only a torn line that was "
                               "truncated) — nothing to reconstruct")
        s = state_from_plan(plan)
        applied_reduction_keys = set()
        events = event_log_open = [TradeEvent(
            event_id=f"{trade_id}-0", trade_id=trade_id, sequence=0,
            event_type="POSITION_OPENED",
            occurred_at=datetime.now(timezone.utc).isoformat(),
            source_version=SOURCE_VERSION,
            payload={"plan": {k: v for k, v in plan.items()
                              if not k.startswith("_")}})]
        event_log.append(event_log_open[0])

    print(f"# runner {src} | {symbol} {'LONG' if s.direction > 0 else 'SHORT'} "
          f"entry {s.entry} qty {s.qty:g} sl {s.sl} tp1 {s.tp1} tp2 {s.tp2:.2f} "
          f"trail {s.trail_dist} flatten {s.flatten_at_et or 'off'}")
    mode = f"broker {broker.mode} -> {broker.base}" if broker else "advice only, no broker"
    print(f"# policy {s.exit_policy} | trail_mult {s.trail_mult} | be_arm {s.be_arm_frac} "
          f"| hold_past_tp2 {s.hold_past_tp2} | risk/share ${plan.get('risk_per_share', 0):.2f}")
    print(f"# {mode}. ctrl-c to stop.\n")

    step = 0
    # C005 is now LIVE-EFFECTIVE (Gate 2): the key set above is either fresh
    # for a new trade or reconstructed from the event log on --resume, WITH the
    # state it belongs to — the crash-retry scenario is defended, and the
    # destroy/resume test proves it through this exact loop.
    while True:
        clock = datetime.now(ET).strftime("%H:%M:%S")

        if live:
            try:
                r = fetch_quote(symbol, max_stale, source=quote_source,
                                max_spread_pct=max_spread_pct)
            except QuoteUnavailable as e:
                # loud, and the cycle is over — decide_exit is NOT called
                print(f"{clock} !! NO USABLE QUOTE — cycle skipped, ladder not advanced\n"
                      f"           {e}")
                sys.stdout.flush()
                if once:
                    return 1
                time.sleep(interval)
                continue
            price = r.price
            age_note, qts_iso = f" q{r.age:.0f}s/{r.source}", r.ts.isoformat()
            qdetail = {"source": r.source, **r.detail}
        else:
            if step >= len(replay):
                print(f"\n# replay exhausted after {step} price(s)")
                return 0
            price, age_note, qts_iso = float(replay[step]), " replay", None
            qdetail = {"source": "replay"}

        urgency, bias_note = read_urgency(require_bias)
        if bias_note in ("MISSING", "UNREADABLE"):
            print(f"{clock} !! BIAS {bias_note} at {BIAS} — running with no urgent channel")

        # Spec 016: directive interrupts merge with the bias channel through
        # the engine's ONE arbitration (most protective wins). No directives
        # file -> None -> byte-identical to the pre-016 runner.
        directive_urgency, directive_note = read_directive_urgency(symbol)
        if directive_note.startswith("DIRECTIVES UNREADABLE"):
            print(f"{clock} !! {directive_note}")
        _, merged_urgency, _ = resolve_channels(
            policy=s.exit_policy, policy_candidate=None,
            urgents=(urgency, directive_urgency))
        if directive_urgency is not None:
            bias_note = f"{bias_note}+directive:{directive_urgency}"

        s.price = price
        s.urgent = merged_urgency
        # A real clock arms time-flatten. In replay there is no honest clock, so
        # the rule stays disarmed and the replay stays reproducible.
        s.now_et = datetime.now(ET).strftime("%H:%M") if live else None

        actions = decide_exit(s)

        # ---- Gate 2 ordering: decide -> pre-validate (pure) -> append+fsync
        # -> apply. A crash after append is safe (the log IS the authority and
        # rebuild reflects the decision); a crash before append loses only a
        # decision that never took effect. The constitution runs a PURE dry-run
        # first so the log can never claim an action it would refuse — enforce
        # is a predicate (spec 011), so this is dry-run-then-commit, not a
        # second engine. (Approximation, documented: in a multi-action batch
        # the later actions pre-validate against the pre-apply state; benign
        # for the engine's actual batch shapes, and the real enforcement still
        # runs inside apply_actions.)
        from stockfish_constitution import enforce as _enforce
        _enforce(s, actions=actions)
        for _a in actions:
            # now_et= included: apply_action forwards it into C004, so the
            # dry-run must too — omitting it was a demonstrated parity hole
            # (adversarial review: dry-run passed, apply refused, false events
            # already durable).
            _enforce(s, _a, applied_keys=frozenset(applied_reduction_keys),
                     now_et=s.now_et)
        n_before = len(events)
        events = events_from_decision(events, s, actions,
                                      occurred_at=datetime.now(timezone.utc).isoformat(),
                                      source_version=SOURCE_VERSION,
                                      post_apply=False)
        for ev in events[n_before:]:
            event_log.append(ev)

        apply_actions(s, actions, applied_reduction_keys)

        print_cycle(clock, symbol, price, s, bias_note, age_note, actions)

        # Broker is downstream of the decision, never upstream of it. Whatever it
        # does or refuses to do, the engine already decided and the ladder already
        # advanced — so a broker failure never silently changes the exit policy.
        orders = []
        if broker is not None:
            for intent in broker_mod.intents_from_actions(actions, symbol):
                orders.append(broker.send(intent))

        rec = {
            "ts": datetime.now(timezone.utc).isoformat(), "source": src, "step": step,
            "symbol": symbol, "price": price, "quote_ts": qts_iso, "quote": qdetail,
            "hwm": s.hwm, "sl": s.sl, "qty": s.qty,
            "stage": s.stage.value, "stop_layer": effective_stop(s).name,
            "tp1_done": s.tp1_done, "tp2_done": s.tp2_done,
            # the MERGED urgency — what actually steered this cycle. The replay
            # harness feeds rec["urgent"] straight back to the engine, so
            # logging the pre-merge bias value would break the DoD diff the
            # moment a directive ever fires.
            "urgent": merged_urgency, "bias_note": bias_note,
            "actions": [{"kind": a.kind, "sl": a.sl, "fraction": a.fraction,
                         "reason": a.reason} for a in actions],
            "broker_mode": broker.mode if broker else "off",
            "orders": orders,
        }
        if step == 0:
            # Stamp the resolved plan into the first record. The harness then
            # reconstructs state from the session's OWN provenance instead of
            # being handed a plan on the command line — where the default was
            # plan.example.json, which is the wrong plan for every real session
            # and quietly produced a passing DoD diff for a config nobody traded.
            rec["plan"] = {k: v for k, v in plan.items() if not k.startswith("_")}
        log_cycle(rec)

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
    ap.add_argument("--quotes", choices=("auto", "alpaca", "yfinance"), default="auto",
                    help="auto: alpaca real-time bid/ask, yfinance 1m bars behind it")
    ap.add_argument("--max-spread", type=float, default=0.01,
                    help="reject an alpaca quote wider than this fraction (default 1%%)")
    ap.add_argument("--resume", action="store_true",
                    help="reconstruct today's open trade from its event log "
                         "(spec 012) instead of starting from the plan. "
                         "Explicit by design — never automatic.")
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
                   max_stale=a.max_stale, require_bias=a.require_bias, broker=broker,
                   quote_source=a.quotes, max_spread_pct=a.max_spread,
                   resume=a.resume)
    except KeyboardInterrupt:
        print("\n# stopped by hand — position untouched, nothing was ever sent anywhere")
        return 0


if __name__ == "__main__":
    sys.exit(main())
