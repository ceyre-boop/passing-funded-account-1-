# SPEC_CHECK.md — verification order for local Claude Code (Molly)
Issued from the Cowork session 2026-08-03. This is the audit of build-order
item 1 against the specs defined in chat and frozen in ARCHITECTURE.md.
Execute top to bottom. Report PASS/FAIL per line with evidence (command output,
file:line), not prose. Anything FAIL blocks the live paper test below.

## A. Single-implementation discipline (the Stockfish contract)
- [ ] `grep -rn "def decide_exit" .` returns EXACTLY ONE hit: daytrade/stockfish_exit.py. The runner IMPORTS it. Any copy, re-implementation, or "adapted version" = FAIL.
- [ ] The runner reads ONLY `data/daytrade/bias.json`, and from it ONLY the `urgency` (and optionally `bias`) fields. No import of alphazero_bias.py into the runner. Show the import list.

## B. Doctrine ladder behavior (replay evidence, not claims)
Feed the runner's harness a synthetic path and show the action log proves each:
- [ ] TP1 crossed → MOVE_SL to entry (breakeven armed, day cannot go red).
- [ ] TP2 crossed → TAKE_PARTIAL (goal fraction) AND stop rides to TP1.
- [ ] Beyond TP2 → trailing stop follows HWM at trail_dist; never loosens.
- [ ] urgency='exit' → EXIT_ALL immediately, outranks every other rule.
- [ ] urgency='tighten' → trail distance halves on the next decision.
- [ ] urgency='stale' → treated as NO urgent action but logged loudly (stale news must not flatten a position, and must not be silently ignored either).
- [ ] Stop hit → EXIT_ALL, reason logged.
- [ ] Malformed state (NaN price, direction=0) → raises. No safe-guess fallback.
- [ ] 15:45 ET time-flatten exists in the RUNNER (it is session policy, not decide_exit's job — confirm which component owns it and that it fires).

## C. Safety spine (APEX)
- [ ] Paper endpoint pinned + host guard (already reported: broker.py:44) — show the guard rejecting a non-paper URL in a test.
- [ ] No code path can place a live order without a separate explicit flag AND a manual confirm step. Show where that gate lives.
- [ ] Every fill/adjustment appends to the session JSONL and the day's outcome goes to data/shot_ledger.csv (one row per shot, doctrine format).
- [ ] One-shot-per-day policy enforced or at minimum asserted loudly in the runner.

## D. Security (outstanding, non-code — do this before anything else runs)
- [ ] The Alpaca account PASSWORD is in the local transcript. Key rotation did NOT fix that. Change the account password at the Alpaca dashboard now, enable MFA if not on, and stop pasting any credential material into any chat — cloud or local. Account ID also appeared in chat; treat it as semi-public from here.

## E. The live paper test (only after A-D all PASS)
Run the full loop end-to-end on paper during market hours TODAY:
1. Start ALPHAZERO --loop 300 (v1 placeholder brain is fine — we are testing plumbing and interrupts, not edge).
2. Colin takes the entry per doctrine; runner manages the exit via decide_exit.
3. Mid-session, hand-edit bias.json to urgency='tighten', then 'exit', and show the runner's reaction in the log (this is the Trump-tweet drill — do it with a tiny position or when flat via a dry-run flag).
4. Deliverables back to Colin: the session JSONL, the diff-ready action log, and the ledger row.

## F. Then, next build item — recommendation from the Cowork side
Do item 3 (backtest harness importing the SAME decide_exit, diff logs vs the
runner's session JSONL) BEFORE item 2 (ALPHAZERO v2). Reason: the byte-identical
diff is the Definition of Done for everything built so far — it proves the
Stockfish layer before we make the news brain smarter. ALPHAZERO v2 gets built
against a proven exit engine, and its scores get graded vs the shot ledger.
Colin has final say on the order.
