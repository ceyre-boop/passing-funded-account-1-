# Plan — Build order item 1: the local runner

## Context

`ARCHITECTURE.md` is the spec for this repo as of 2026-08-03, and its build order
names four steps. Steps 2-4 are explicitly gated behind step 1:

> 1. Local runner: live quotes -> decide_exit -> advice lines printed. (imports,
>    never copies, stockfish_exit.py)

Right now `daytrade/stockfish_exit.py` is a correct, deterministic exit engine
with no way to feed it. The only thing that exercises `decide_exit` is the
hardcoded replay in its own `__main__`. During a live trade Colin has no path
from a real price to an advice line — he'd be running the doctrine from memory,
which is exactly what the exit engine exists to prevent.

This plan builds that path and nothing else. Scope confirmed with Colin:
**item 1 only** (items 2-4 deferred), **yfinance polling** for quotes,
**equities** (NVDA-style dollars-per-share) for the point math.

Two constraints drive the design:

- **`execution/alpaca.py:31-39` rules out the obvious source.** That account 403s
  on `/quotes/latest` and `/snapshot`; it serves SIP data only beyond a 15-minute
  lag. Documented, measured, not a bug to fix. Hence yfinance.
- **No second implementation of the exit policy, ever** (ARCHITECTURE.md Layer 2).
  The runner imports `decide_exit`. It does not re-derive a single rule.

## What gets built

### 1. `daytrade/runner.py` — NEW, the whole deliverable

A single-trade advisor loop. Reads a plan, polls a price, calls `decide_exit`,
prints advice, logs every cycle. Never touches a broker — printing is the output.

**Inputs**
- `--plan data/daytrade/plan.json` — the blueprint, set BEFORE the open per Layer 0
  doctrine. JSON: `symbol, direction, entry, qty, sl, tp1, trail_dist` plus either
  `tp2` or `day_goal_usd`. If `day_goal_usd` is given and `tp2` is not, compute
  `tp2 = entry + direction * (day_goal_usd / qty)` — this is the "$300 goal" of
  the doctrine expressed in dollars instead of hand-computed price levels.
  Giving both, or neither, raises. Same for any missing field: fail loud.
- `--interval 15` seconds, `--once` for a single reading, `--replay FILE` for an
  offline price path (a JSON list of floats) so the ladder is testable without a
  live market.

**Quote fetch — fail loud, no silent staleness**
`yf.download(symbol, period="1d", interval="1m")`, take the last bar's close and
its index timestamp. The timestamp is the point: it makes staleness *detectable*.
If the newest bar is older than `--max-stale 180` seconds, or the frame is empty,
print a loud `STALE`/`NO QUOTE` line and **skip the cycle without calling
decide_exit**. A stale print must never advance the ladder.

Deliberately not reusing the yfinance helpers in `sovereign/forex/signal_engine.py:115-140`
or `macro_engine.py` — those are daily-bar fetches wrapped in bare `except: pass`,
the exact silent-failure pattern ARCHITECTURE.md's safety spine forbids.

**Bias coupling — urgency only**
Read `data/daytrade/bias.json`, take **only** the `urgency` field (Layer 2:
"reads ONLY the urgency field from bias.json"). Mapping, stated explicitly in
code and in the log:
- `"exit"` / `"tighten"` -> passed through to `TradeState.urgent`
- `"none"` / `"stale"` -> `None` (a stale reader must not fabricate an interrupt)
- file missing or malformed -> `None`, plus a loud warning line **every cycle**,
  and `--require-bias` to hard-fail instead. Loud, never silent.

**The loop**
Build `TradeState` once from the plan, then per cycle: fetch quote -> read
urgency -> `decide_exit(state)` -> print advice -> apply actions back into state
-> append a JSONL record. On `EXIT_ALL`, print the flatten advice in a way that
cannot be missed and stop the loop.

**Session log — `data/daytrade/session_YYYY-MM-DD.jsonl`**
One record per cycle: `ts, symbol, price, quote_ts, hwm, sl, tp1_done, tp2_done,
urgent, actions[]`. This format is designed now because it is the artifact build-order
item 3 will diff against a backtest run. Getting it right here is what makes the
DoD achievable later; getting it wrong means rewriting the runner then.

### 2. `daytrade/stockfish_exit.py` — two small additive changes

**`apply_action(state, action) -> None`.** The `__main__` replay currently does
`if a.kind == "MOVE_SL": s.sl = a.sl` inline (line 103). That inline handling is
already a partial second implementation of state-application, and the runner would
make it a third. Extract it once, into the module that owns the policy. Both the
replay and the runner call it. Item 3's harness will too.

**Optional time-flatten.** ARCHITECTURE.md Layer 2 says "Hard stop and time-flatten
always live," but `TradeState` has no clock and `decide_exit` has no time branch —
a real gap between spec and code. Colin's doctrine is flat before 11:00 and he was
flat before 11 on day 1, so this is live doctrine going unenforced. Add
`flatten_at_et: Optional[str] = None` and `now_et: Optional[str] = None` to
`TradeState`, and a branch in `decide_exit` that returns `EXIT_ALL` when the clock
reaches it. **Defaults are `None`, so with no clock supplied nothing changes** —
the existing replay output stays byte-identical. Time-flatten belongs in
`decide_exit`, never in the runner; putting it in the runner would be the second
implementation the spec forbids.

### 3. Supporting files

- `data/daytrade/plan.example.json` — a filled-in NVDA blueprint matching ledger
  row 1 (entry 204, qty 163), so the format is self-documenting.
- `daytrade/README.md` — update the run instructions, mark build-order item 1 done.
- `ARCHITECTURE.md` — tick item 1 in the build order; leave the rest untouched.

## Explicitly NOT in scope

- No broker calls, no order placement (safety spine #1). `execution/alpaca.py` and
  `execution/paper_trading.py` are not imported — the latter is a broken orphan
  anyway, importing `integration.production_engine` and `meta_evaluator`, neither
  of which exists in this repo.
- No ALPHAZERO changes. v1 stays the labeled placeholder it says it is.
- No backtest harness, no Elo arena.
- No multi-position support. One trade at a time — that is the doctrine's own
  two-bet sequence, not a limitation to design around.

## Verification

1. **Byte-identical replay (the DoD that matters most here).** Capture
   `python3 daytrade/stockfish_exit.py > /tmp/before.txt` *before* touching the
   file; after the `apply_action` + time-flatten edits, run again and
   `diff` against it. Must be empty. If the refactor changed behavior, it was
   the wrong refactor.
2. **Offline ladder walk.** `python3 daytrade/runner.py --plan data/daytrade/plan.example.json
   --replay <the same price path as the replay test>` must print the same
   ladder progression: TP1 -> breakeven, TP2 -> partial + stop to TP1, then
   trailing. Proves the runner drives the engine correctly with no market.
3. **Live single reading.** `python3 daytrade/runner.py --plan data/daytrade/plan.example.json
   --once` during market hours prints a real NVDA price, a real quote timestamp,
   and one advice line.
4. **Staleness fails loud.** Run `--once` against a nonsense symbol and against a
   `--max-stale 0`; both must print the loud skip line and must not print a price
   or an action. Confirm `decide_exit` was never called on a stale print.
5. **Bias interrupt end-to-end.** Hand-write `urgency: "exit"` into
   `data/daytrade/bias.json`, run one replay cycle, confirm `EXIT_ALL` and loop
   halt. Restore the file afterward.
6. **Session log shape.** Confirm `data/daytrade/session_*.jsonl` has one
   well-formed record per cycle and that actions round-trip as JSON.
7. **Import hygiene.** `grep -n "^import\|^from" daytrade/runner.py` — expect only
   stdlib, yfinance, and `stockfish_exit`. Any `execution.*` import is a bug.

## Commit shape

Per safety spine #4 (discrete versioned growth), one commit:
`daytrade: local runner — live quotes through the same decide_exit, advice out, no broker`
