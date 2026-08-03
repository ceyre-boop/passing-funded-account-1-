# MONDAY_OPEN — 2026-08-03 runbook (one page, follow top to bottom)

## Before 9:30 ET
1. Buy: **Tradeify $25K Select Evaluation** — confirm cart says SELECT (not Instant/Lightning). Rules verified: $1,500 target · $1,000 EOD trailing (locks at $25,100 EOD) · no daily loss limit · 40% consistency checked at pass-request only (big day delays, never breaches) · 1 mini/10 micro · 3+ days.
2. Platform: whatever Tradeify offers US residents (Tradovate/NinjaTrader) — instrument **MES**.
3. Calendar check (9:30 "Alta — Market open check" block): today's release = **ISM Manufacturing 10:00 ET** (exp 54.0 vs 53.3 prior).

## The shot (THE_SHOT.md v2 — mechanical, zero judgment)
- 9:30–10:00: mark opening range high/low on MES. No orders.
- 10:05–11:00: first 5-min bar that **closes** outside the OR → enter market on that close, direction of the break.
- Stop: far side of trigger bar. **Size: micros = 140 ÷ (5 × stop-points), max 10.** (10-pt bar → 2 micros · 7 pts → 4 · 5 pts → 5 · 3 pts or less → 10, accept smaller day.)
- Target: **+$300 day P&L**, OCO with stop. No breakeven moves, no trailing, no partials.
- Nothing closed by 15:45 → flatten at market.
- No close outside OR by 11:00 → **no trade today.** Log it, done.

## The week's plan ($300/day × 5 green days = $1,500, pass)
| green day | cum | biggest-day share | cushion |
|---|---|---|---|
| 1 | $300 | 100% (fine — rule checks at pass) | $1,000 |
| 2 | $600 | 50% | $1,000 |
| 3 | $900 | 33% | $1,000 |
| 4 | $1,200 | 25% | $1,100 |
| 5 | $1,500 | **20% → REQUEST PASS** | $1,400 |

Red day = −$140. Three straight reds still leaves $580 cushion. After 2 consecutive red DAYS: 5-trading-day cooloff, no exceptions.

Releases this week: Mon ISM Mfg 10:00 · Tue JOLTS 10:00 · Wed ADP 8:15 + ISM Svcs 10:00 · Thu NO-SHOT DAY · Fri NFP 8:30 (OR = 9:30–9:45 on pre-open releases, trigger window from 9:50).

## Honest expectations (select_pass_planner.py MC)
5 green days ≈ **2.5–4 weeks of trading days**, not one calendar week (green days arrive ~1 in 3 at assumed 60% trigger × ~50% win). P(bust) stays 2–10% at this sizing — the account survives; time is the cost. Log every shot in `data/shot_ledger.csv`; ~50 shots before any claim about the real win rate.

## Session artifacts (all pushed)
EVAL_LAB.md (60-combo search, honest 40%) · ONE_DAY_PASS.md (ladder math) · friction_ladder.py · THE_SHOT.md + v2 · select_pass_planner.py (this account's tables) · shot_ledger.csv (empty, waiting for row 1).
