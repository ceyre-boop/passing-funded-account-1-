# daytrade/ — STOCKFISH + ALPHAZERO v1
Built 2026-08-03, day 1 of the campaign (first green day: +$300 NVDA, ledger row 1).

## The wiring
```
ALPHAZERO (alphazero_bias.py, runs on Colin's machine, --loop 300)
   └─ every 5 min: headlines -> bias [-1..+1] + urgency none|tighten|exit|stale
   └─ writes data/daytrade/bias.json          <- THE ONLY CONTRACT
RUNNER (runner.py, the loop that makes the above usable)
   └─ reads the pre-open blueprint (data/daytrade/plan.json)
   └─ polls yfinance 1m bars -> price + timestamp (stale = loud skip, never a guess)
   └─ reads ONLY bias.json's urgency field
   └─ calls decide_exit, prints advice, logs every cycle to session_YYYY-MM-DD.jsonl
STOCKFISH (stockfish_exit.py, pure function decide_exit(state))
   └─ reads urgency, runs the doctrine ladder:
      TP1 -> stop to breakeven (day cannot go red)
      TP2 -> bank the $300 goal, stop rides to TP1
      TP3 -> trail the runner off HWM ("you never know when it rips")
      urgent 'exit' -> flatten; 'tighten' -> trail distance halved
      time-flatten -> EXIT_ALL at flatten_at_et, when a clock is supplied
COLIN
   └─ places/adjusts the actual orders from the runner's advice lines.
```

## Why yfinance and not Alpaca
The Alpaca account in this repo 403s on `/quotes/latest` and `/snapshot` and
serves SIP data only beyond a 15-minute lag — measured and documented at
`execution/alpaca.py:31-39`. That is fine for the historical work in
`backtester/`, and useless for a live cockpit. yfinance 1-minute bars carry a
timestamp, which is the part that matters: staleness is *detectable*, so a lagging
feed produces a loud skip instead of a confident wrong number.

## What's real vs placeholder (honest labels)
- STOCKFISH: **real and final in shape.** Pure, deterministic, fail-loud, one
  implementation. Replay test in `__main__` — the same log any harness must
  reproduce byte-identical (APEX DoD). Tested green in-repo.
- RUNNER: **real.** Walks the same ladder as the engine replay (verified by
  running the replay price path through it). It contains zero exit rules —
  every decision comes from the imported `decide_exit`, and state advances
  through the imported `apply_action`.
- ALPHAZERO scoring: **placeholder** (keyword-valence table, unvalidated, says
  so in its own output). The interface (bias.json) is frozen; the brain gets
  smarter later without touching STOCKFISH. No headlines -> 'stale', never a guess.
- Order execution: **deliberately absent.** No broker API in this repo. Advice
  only; the human confirms. Same shadow-mode discipline as the carry stack.

## Run it (Colin's machine)
Before the open, write the blueprint — copy `data/daytrade/plan.example.json`
to `data/daytrade/plan.json` and fill in the levels you decided on. Give either
`tp2` (a price) or `day_goal_usd` (the $300 goal, converted using qty). Set
`flatten_at_et` if you want the 11:00 flat enforced; leave it null and the rule
stays disarmed rather than guessing a time.

```
python3 daytrade/alphazero_bias.py --loop 300     # terminal 1, all session
python3 daytrade/runner.py                        # terminal 2, once in a trade
```
Useful flags: `--once` (single reading), `--interval 15` (poll seconds),
`--max-stale 180` (age at which a quote is refused), `--require-bias` (hard-fail
instead of running with no urgent channel), `--replay prices.json` (walk a price
path offline — how the ladder gets tested without a market).

```
python3 daytrade/stockfish_exit.py                # engine replay/self-test
```

## Next iterations (in order, per APEX: discrete versioned steps)
1. ~~Local runner that feeds live quotes into decide_exit and prints advice lines.~~
   **DONE 2026-08-03 — `runner.py`.**
2. ALPHAZERO v2: real news feed + calibrated scoring, logged against the shot
   ledger so the bias score earns (or loses) trust on data.
3. Backtest harness importing the SAME decide_exit; diff logs vs live (DoD).
   The runner's `session_*.jsonl` is the live side of that diff — the format was
   designed for it, so the harness has something to compare against on day one.
4. Elo arena (Cursus Honorum) only after 1-3 exist.

## Known rough edges (honest, not hidden)
- `TAKE_PARTIAL` halves qty exactly, so 163 shares becomes 81.5. The authoritative
  advice is the *fraction*; round to whole shares at the platform. A real
  instrument table (tick size, lot rounding) is an item-3-era concern.
- The runner handles one position at a time. That matches the doctrine's two-bet
  sequence — bet 2 only happens after bet 1 is closed — so it is a deliberate
  shape, not a missing feature.
