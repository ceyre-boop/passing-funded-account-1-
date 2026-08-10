# COLIN v2 — The Eval Sprint

> **⚠️ SUPERSEDED (2026-08-10, spec 021).** The 5% eval sizing and its pass tables
> were calibrated to an 8%/8% *trailing*-DD contract with a 90-day clock — the
> futures lane `FIRM_FIT.md` later ruled dead for carry. The swing-lane firms
> actually available (CTI 1-Step, Alpha Swing, FTMO Swing) have **static** drawdown
> and **no deadline**, which removes this document's premise. Sizing doctrine is now
> re-derived under the real contracts by `scripts/carry_buy_gate.py`
> (spec `specs/021_CARRY_BUY_GATE.md`) and ratified before any purchase.

**Alta Investments | 2026-08-01**
*V1 proved the edge and sized it for a lifetime. V2 sizes it for a deadline.*

Successor to `COLIN_V1.md`. Same edge, same sealed 411-trade proof file, zero changes to the signal engine. The only thing V2 changes is **risk per trade during the evaluation, and what happens after a bust.**

---

## THE ONE IDEA

An eval is not an account. It is a **ticket**. Busting a ticket costs the fee — nothing else. So the eval phase should not be sized to survive; it should be sized to **finish before the clock runs out**, accepting that some tickets burn. V1 sizing (0.5–1%) treats the eval like real money and times out. V2 sizing treats it like what it is.

This was tested, not assumed: every adaptive policy that tried to be clever — sizing up when behind schedule, sprinting on a cushion, guarding against the worst-known trade — **lost to plain static aggression**, because every one of them protects against a bust that only costs a fee. Hindsight-optimal sizing per window only reaches **74.1%** at 90 days; static 5% gets 66.9% of windows. There is almost nothing left for cleverness to win.

## THE SPEC

| Phase | Risk/trade | Rule |
|---|---|---|
| **Evaluation** | **5% static** | Every signal the engine fires, same size, no runners logic changes, nothing else touched |
| On bust | — | **Rebuy immediately, same day.** No post-mortem, no size change. The math already priced the bust in. |
| **Funded** | **1% static** | The moment the eval passes, V1 sizing takes over. 5% never touches a funded account. Ever. |

Budget **4 eval fees** per campaign (historical max needed at 90d: 4). Expected: **1.65**.

Why 5% and not more: 6–8% was tested — pass rate moves ≤2 points (68.8% at 6%, 69.1% at 8%) while attempts and fee burn climb. 5% is the knee of the curve. Why not two parallel accounts: tested — outcomes are ~fully correlated (same trades), union adds ~2 points for double the fees. Not worth it.

## THE NUMBERS (what you asked for)

**P(funded), campaign clock, rebuys included** — every 3-day start point across the full 2015–2024 history, real trade sequences, exact trailing-DD rules:

| Calendar budget | **P(funded)** | Median day funded | E[evals burned] |
|---|---|---|---|
| **30 days** | **27%** | 19 | 1.2 |
| 60 days | 51% | 29 | 1.4 |
| **90 days** | **67%** | 36 | 1.65 |
| 120 days | 73% | 40 | 1.7 |
| 150 days | 78% | 42 | 1.8 |
| **180 days** | **82%** | 45 | 1.9 |

**The 80% you want exists — at ~6 months, not 90 days.** At 90 days the ceiling for ANY sizing policy on this edge is 74% (hindsight-optimal). 67% is 90% of that ceiling. The binding constraint is not courage or math — it's that carry fires ~10 completed trades per 90 days, and a quarter of 30-day windows contain **zero**. That's why 30 days caps at ~28-31% at any risk level. **Sizing cannot manufacture signals.** The only lever that moves the 30-day number is a second, faster, *validated* signal source — that's V3 territory, and it goes through the same proof gate carry did.

Year-by-year honesty (P(funded ≤90d) by start year): 2016 was 87%, 2022 was 80% — but **2020 was 25%** and 2019 was 50%. Flat-rate regimes starve the edge. If the campaign lands in one, the answer is rebuy and hold the process, not resize.

## WHAT THE FUNDED ACCOUNT PAYS (after you pass)

$100k funded, 1% risk (V1 sizing), 90% split, monthly payout sweeps, 467 rolling year-windows of real history:

| | p10 (bad year) | **Median** | p90 (good year) |
|---|---|---|---|
| **Annual payout** | ~$200 | **~$10,000** | **~$35,000** |
| Monthly equivalent | ~$0 | ~$850 | ~$2,900 |

**P(losing the funded account in a year): ~2.6%.** COLIN_V1's equity-curve number (median $14.8k/yr) is the same engine before payout friction — splits and sweep timing cost a few thousand of it.

One $100k funded account is a **$10k median year**. The JJ-style path to real income is horizontal: pass again, run multiple funded accounts, same edge, same process — each additional account is another ~$10k median with its own 2.6% mortality. That scaling decision comes AFTER the first payout, not before.

## FEES (the actual cost of the ticket)

Total campaign cost = fee × evals burned. At E[1.65], budget-4:

| Per-eval fee | Expected campaign cost | Worst case (4 burns) |
|---|---|---|
| $150 | ~$250 | $600 |
| $500 | ~$825 | $2,000 |

Pick the firm by **rules fit** (trailing DD that locks at start, no consistency rule that fights 5% sizing, no time limit under 90d), then by fee — not the reverse.

## WHAT V2 DOES NOT CHANGE

- The signal engine, the pairs, the gates, the exits — untouched. **The edge is the edge.**
- `RISK_CONSTITUTION.md` still governs real money. Eval-mode 5% is a documented, scoped exception that exists because eval balances are not money — the fee is the money.
- Shadow mode on the exit manager, the unlock protocol, the live gates — all still in force. **This document authorizes a sizing policy for simulated/eval balances, not live ignition.**

## HONESTY BOX

- Source is the sealed 2015–2024 backtest. Live n=4. Every number above inherits backtest risk. The daily paper-challenge loop is what converts these from projections into evidence.
- Intratrade drawdown is not in the CSV (exit R only) — real trailing DD can clip paths the sim passes. Direction of bias: slightly optimistic. 
- 5% × the worst trade in history (-3.22R) = **-16% = instant bust.** That trade WILL recur. It is priced into the 67% — when it happens, it is the plan working, not failing. Rebuy.

*Sim: `scripts/colin_v2_campaign_sim.py` — reruns everything above from the proof CSV in ~3 min.*
