# specs/ — the written build, before the code
Everything here is a SPEC. Nothing in this directory is running code. Read
`000_RULINGS_AND_ORDER.md` first — it settles two open design questions and fixes
the build sequence.

| # | file | component | status | build order |
|---|---|---|---|---|
| 000 | RULINGS_AND_ORDER | rulings, sequence, status legend | — | read first |
| 005 | BACKTEST | `daytrade/backtest.py` | `[SPEC]` | **1st** |
| 001 | REGIME | `daytrade/regime.py` | `[SPEC]` | 2nd |
| 004 | SCORECARD | `daytrade/scorecard.py`, `streak.py` | `[SPEC]` | 3rd |
| 002 | SURVIVAL | `daytrade/survival.py` | `[SPEC]` | 4th |
| 003 | STOCKFISH_V2 | extend `stockfish_exit.py` | `[SPEC]` | 5th |
| 006 | ALPHAZERO_V2 | extend `alphazero_bias.py` | `[SPEC]` | 6th |
| 007 | BRIEF (in 006 file) | `daytrade/brief.py` | `[SKETCH]` | last |

## Status legend
- `[BUILT]` exists, tested, in the repo
- `[SPEC]` fully specified, not written. **Safe to build from.**
- `[SKETCH]` direction right, details deliberately unfinished. **Do not build
  from a SKETCH without a planning pass** — building it now bakes in a guess
  we'd have to tear out.

## Already built (not specs — real code)
`daytrade/stockfish_exit.py` · `daytrade/runner.py` · `daytrade/broker.py` ·
`daytrade/alphazero_bias.py` (v1 placeholder brain) — see `daytrade/README.md`.

## The five rules that govern every file here
1. **One implementation of every decision.** Runner and harness are I/O; they
   import engines, they never re-implement. If a rule can close, reduce, or move
   a stop, it lives in `decide_exit`.
2. **Fail loud.** Stale data, malformed state, unknown enum → raise. Never a
   silent fallback to a "safe" guess.
3. **Every prediction gets scored, against a dumb baseline.** An accuracy number
   without the score a brainless model gets on identical data is not evidence.
4. **Rule changes commit BEFORE the next session.** Never after a loss.
5. **No look-ahead, ever.** `classify()` sees bars ≤ N; `grade()` sees N+1..N+K
   and never feeds back into a classification. Assert it at the boundary.

## Where the ideas came from
`I_AM_A_GOOD_TRADER.md` — Colin's doctrine, the entry read, irreplaceable.
`ARCHITECTURE.md` — layer map and the APEX safety spine.
`BUILD_PLAN.md` — the regime-first thesis these specs decompose.
`SANITY_AUDIT.md` — why every number here needs a baseline. Read it before
building anything that produces a percentage.
