# daytrade/ — STOCKFISH + ALPHAZERO v1
Built 2026-08-03, day 1 of the campaign (first green day: +$300 NVDA, ledger row 1).

## The wiring
```
ALPHAZERO (alphazero_bias.py, runs on Colin's machine, --loop 300)
   └─ every 5 min: headlines -> bias [-1..+1] + urgency none|tighten|exit|stale
   └─ writes data/daytrade/bias.json          <- THE ONLY CONTRACT
STOCKFISH (stockfish_exit.py, pure function decide_exit(state))
   └─ reads urgency, runs the doctrine ladder:
      TP1 -> stop to breakeven (day cannot go red)
      TP2 -> bank the $300 goal, stop rides to TP1
      TP3 -> trail the runner off HWM ("you never know when it rips")
      urgent 'exit' -> flatten; 'tighten' -> trail distance halved
COLIN
   └─ places/adjusts the actual orders from STOCKFISH's advice lines.
```

## What's real vs placeholder (honest labels)
- STOCKFISH: **real and final in shape.** Pure, deterministic, fail-loud, one
  implementation. Replay test in `__main__` — the same log any harness must
  reproduce byte-identical (APEX DoD). Tested green in-repo.
- ALPHAZERO scoring: **placeholder** (keyword-valence table, unvalidated, says
  so in its own output). The interface (bias.json) is frozen; the brain gets
  smarter later without touching STOCKFISH. No headlines -> 'stale', never a guess.
- Order execution: **deliberately absent.** No broker API in this repo. Advice
  only; the human confirms. Same shadow-mode discipline as the carry stack.

## Run it (Colin's machine)
```
python3 daytrade/alphazero_bias.py --loop 300     # terminal 1, all session
python3 daytrade/stockfish_exit.py                # replay/self-test
```
Wire decide_exit into the local runner (Molly's Stockfish spec) by importing it
— never by copying it. Second implementations are how live diverges from test.

## Next iterations (in order, per APEX: discrete versioned steps)
1. Local runner that feeds live quotes into decide_exit and prints advice lines.
2. ALPHAZERO v2: real news feed + calibrated scoring, logged against the shot
   ledger so the bias score earns (or loses) trust on data.
3. Backtest harness importing the SAME decide_exit; diff logs vs live (DoD).
4. Elo arena (Cursus Honorum) only after 1-3 exist.
