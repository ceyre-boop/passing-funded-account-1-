# ARCHITECTURE.md — the build, as Colin actually needs it
Written 2026-08-03, end of the first live morning (green, +$300, ledger row 1).
This is the architecture the quant repo never quite delivered, restated from
scratch in its final form after a morning of it growing and sharpening. Any
Claude session (cloud Cowork or local Claude Code) should treat THIS document
as the spec. History and derivations live in the other .md files; this is the
destination.

## The one-paragraph version
Colin is the trader. The system is his cockpit, not his replacement. It has
exactly three moving parts: **ALPHAZERO** reads the world and produces the
morning bias plus rolling urgent updates; **STOCKFISH** turns every open trade's
exit into fixed mechanical rules that cannot be argued with mid-trade; **COLIN**
does the one thing no model can — the discretionary entry read (I_AM_A_GOOD_TRADER.md).
Underneath them, the **LADDER** (campaign math) decides how eval attempts are
bought and retried, and the **LEDGER** measures everything one shot at a time.
Nothing in the system touches a broker; advice out, human executes.

## Layer 0 — the doctrine (I_AM_A_GOOD_TRADER.md) — Colin, irreplaceable
- Market regime triad: always consolidating, manipulating, or continuing.
- NY clock: 9:30-10:30 is the day's first true move; 10:30 is a hard regime shift.
- Two-bet sequence: bet 1 small continuation (1-5m, ~50/50, take the $300 and
  be done); bet 2 ONLY after a loss, SMALLER size with WIDER room at the FVG
  retrace, playing out over days. Size down after losses, never up.
- Too tight TP/SL = early = wrong. Lenient placement is the discretionary skill.
- The exit is the profession: wrong entry + perfect exit = still profitable.
- Daily blueprint set BEFORE the open: TP1 don't lose / TP2 the goal ($300) /
  TP3 trail the unknowable rip.

## Layer 1 — ALPHAZERO (daytrade/alphazero_bias.py) — the world-reader
- Runs on Colin's machine every 5-10 min all session (--loop 300).
- Morning job: the daily bias — what do the shareholders think today, what is
  every trader looking at, is the consensus "definitely up" or "definitely down."
  Colin confirms it in price action; the bias makes his call easy, not for him.
- Rolling job: re-score headlines as they land. A Trump-tweet-class shock sets
  urgency='exit'; a hard bias lurch sets urgency='tighten'. No data = 'stale',
  loudly, never a guess.
- Output contract: data/daytrade/bias.json {ts, bias -1..+1, urgency, detail}.
  THIS FILE IS THE ONLY INTERFACE to Stockfish. Freeze it; improve the brain
  behind it freely (v1 is a labeled placeholder keyword table).
- Rescoped honestly: this is walk-forward learning, not literal self-play —
  markets are not a closed simulator. Train rolling, evaluate strictly OOS,
  log the improvement curve. Never chase the AlphaZero metaphor into an
  architecture that doesn't map.

## Layer 2 — STOCKFISH (daytrade/stockfish_exit.py) — the exit machine
- ONE pure function: decide_exit(state) -> actions. No second implementation
  anywhere, ever. Backtest and live import the same function; the DoD is
  byte-identical logs from the same input stream.
- The ladder it enforces: TP1 hit -> stop to breakeven (rule #1: do not lose on
  the day). TP2 hit -> bank the day-goal partial, stop rides to TP1. Beyond ->
  dynamic trail off the favorable extreme. Hard stop and time-flatten always live.
- Interrupts: reads ONLY the urgency field from bias.json. 'exit' outranks
  everything; 'tighten' halves the trail. Otherwise set-it-and-forget-it —
  Colin's hands stay off the keyboard by design.
- Fail loud. Malformed state raises. No silent safe-guess fallbacks, ever.

## Layer 3 — the LADDER (friction_ladder.py, ONE_DAY_PASS.md) — the funding math
- Evals are renewal processes, not single exams: cheap fee + free restarts +
  identical attempts = attempt count beats edge quality (93-98% funded by
  attempt 5-7 whether p=31% or 45%) — PROVIDED the cooloff holds.
- Campaign rules: identical attempts, one qualifying shot per day, bust once =
  fresh reset, bust twice consecutively = mandatory 5-day cooloff (the entire
  reason busts stay cheap), fee budget fixed before attempt 1 (currently: 2).
- The eval is the Stockfish problem (solvable, structural). Live profitability
  is the AlphaZero problem (unbounded, no restarts) — the actual business,
  never to be confused with a green PASSED checkbox.

## Layer 4 — the LEDGER (data/shot_ledger.csv) — the only judge
- Every shot logged, win or bust: date, context, entry/stop/target, R, grade,
  cooloff honored. Two ledgers never blur: eval outcomes measure the structure;
  shot R-multiples measure the trader. ~50 shots before any claim about the
  real win rate. ALPHAZERO's bias score gets graded against this too.

## Safety spine (APEX, applies to every layer)
1. **AMENDED 2026-08-03 (Colin's call, logged before any executing code existed).**
   Was: "No broker APIs in this repo. Advice out; the human executes. Paper/live
   separation is physical, not a flag." Now: a PAPER broker API is permitted,
   under four conditions that replace the physical separation with an
   enforced one —
   a. **Paper endpoint only.** The client refuses any base URL that is not
      `paper-api.alpaca.markets`. Pointing it at live requires editing the guard
      itself, which is a reviewable diff, not a config typo.
   b. **Shadow by default.** Every run prints the order it would send and sends
      nothing. Sending requires `--broker armed` explicitly, every time. There is
      no persisted "armed" state to forget about.
   c. **Confirm before the first send.** Arming prompts once, interactively, with
      the account and order summary shown. `--yes` skips it only for a run Colin
      started deliberately.
   d. **The ledger still rules.** Every send and every rejection is logged to the
      session JSONL like any other cycle. An unlogged order is the same silent
      data loss non-negotiable #3 forbids.
   Rationale: the eval is a Stockfish problem — mechanical, bounded, and the
   part of the doctrine a machine executes better than a human at 9:31. Live
   capital stays out of scope; that is the AlphaZero problem and it is unsolved.
2. Fail loud, never silent.
3. Backend provably correct before any dashboard.
4. Discrete versioned growth — each piece its own commit, no big rewrites.
5. Rule changes commit BEFORE the next attempt, never after a loss.

## Build order from here (Molly / local Claude Code lane)
1. ~~Local runner: live quotes -> decide_exit -> advice lines printed.~~ **DONE
   2026-08-03 — `daytrade/runner.py`.** Imports decide_exit + apply_action,
   never copies. yfinance 1m bars (Alpaca 403s under 15 min, see
   execution/alpaca.py:31-39); stale quote = loud skip, ladder does not advance.
   Logs every cycle to data/daytrade/session_YYYY-MM-DD.jsonl — that file is the
   live half of step 3's diff.
2. ALPHAZERO v2: real feed + calibrated scoring, graded vs the ledger.
3. Backtest harness through the SAME decide_exit; diff logs vs live (DoD).
4. Cursus Honorum Elo arena — only after 1-3 exist and are proven.

## Why this repo exists
The quant repo grew into a research factory — hypotheses, gates, sleeves,
3,000+ files — and the thing Colin actually needed got buried: a small cockpit
where HIS read takes the trade, fixed rules take the exit, cheap structure
funds the account, and one honest ledger keeps score. This repo is that
cockpit. Keep it small. If something feels missing, it probably belongs in the
quant repo, not here.
