# THE_SHOT — the exact mechanical rule that fires "take this trade"
Locked 2026-08-03 05:15 ET, before the open. No edits mid-campaign. Changes require a new version committed BEFORE the next attempt, never after a loss.

## Colin's amendments (binding)
- **Budget: 2 fees. HARD.** No third fee this campaign, no relitigating after a red morning.
- BOGO at checkout → 2 fees may equal 4 accounts. Each account = one first-quality shot.

## The rule (Stockfish half — zero judgment after 9:30)

**Qualifying day:** a red-folder US release between 8:15–10:00 ET. This week: Mon (ISM Mfg 10:00), Tue (JOLTS 10:00), Wed (ADP 8:15 + ISM Svcs 10:00), Fri (NFP 8:30). **Thursday = mandatory no-shot day.**

**Instrument:** US500 CFD. Fixed for the whole campaign. (Swappable exactly once, before attempt 1 — never after.)

1. **Opening range (OR):** 9:30–10:00 for a 10:00 release; 9:30–9:45 for a pre-open release. Mark high and low. No orders during OR.
2. **Trigger:** the first 5-minute bar that CLOSES outside the OR, inside the window [release + 5 min → 11:00]. Close above OR-high = long. Close below OR-low = short. **Direction comes from the break, not from opinion about the number** — price interprets the regime, we don't. (Tenet 4: confirm, don't predict.)
3. **Entry:** market, on that bar's close.
4. **Stop:** far side of the trigger bar. Size the position so stop-to-entry = **2.8% of account** (0.2% buffer under the 3% daily limit for slippage).
5. **Target:** **+6.0% of account** (2.14R), placed immediately as OCO with the stop.
6. **Management: none.** No breakeven move, no trail, no partials, no screen-watching. Orders in, walk away.
7. **Time stop:** neither side hit by 15:45 ET → flatten at market, log the partial R. Account lives, next qualifying day same rule.

**Skip conditions (all mechanical):** no 5-min close outside OR by 11:00 → no shot today. Spread blown out (>2× normal) on the trigger bar → skip that bar, next closing bar still valid inside the window.

## Campaign rules (the ladder half)
- One shot per day, max. A stopped-out account is retired from shot duty (a fresh account restores clean 2.14R geometry; a wounded one needs 3.2R — worse bet).
- Bust once → next qualifying day, fresh account, identical rule.
- **Bust twice consecutively → 5-trading-day cooloff. Calendar-block it before attempt 1.**
- Every shot logged in `data/shot_ledger.csv` win or lose: date, release, OR levels, trigger bar, entry/stop/target, R outcome, setup grade, cooloff honored Y/N. ~50 logged shots before any claim about what p actually is. Passing ≠ edge; only the ledger measures edge.

## What the 2-fee budget actually buys (from friction_ladder.py)
| shots available | p=31% (coin+costs) | p=40% | p=45% |
|---|---|---|---|
| 2 (no BOGO: 2 accounts) | 52% | 64% | 70% |
| 4 (BOGO: 4 accounts) | 77% | 87% | 91% |

**Checkout checklist (before paying):** does BOGO apply to this account size · trailing vs static on the 6% · minimum trading days before first payout · consistency rule · max-profit-per-day cap.

## Honest label
This rule's per-shot p is UNPROVEN. It was chosen for structure (fixed R geometry, catalyst-anchored volatility, zero mid-trade judgment), not because any backtest blessed it — daily bars can't test a 5-minute trigger and pretending otherwise is the sin this repo exists to not commit. The ledger will tell us what p is, one shot at a time. The campaign math works at coin-flip p anyway — that's the whole point of the ladder.
