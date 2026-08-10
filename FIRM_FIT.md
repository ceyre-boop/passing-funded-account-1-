# FIRM FIT — the check that should have been step one
**2026-08-02 · The result that matters: the plan as modeled was unexecutable. Caught before a dollar burned.**

> **Now encoded (2026-08-10):** the firm rules this document identified are machine-
> readable in `data/propfirm/firm_contracts.yaml` and evaluated by
> `scripts/carry_buy_gate.py` (spec `specs/021_CARRY_BUY_GATE.md`). The 2%
> recommendation below is the PRIOR for the sizing sweep, not ratified doctrine.

## THE KILL
`sovereign/propfirm/rules_engine.py` models Lucid/MFF futures rules. COLIN_V2's 67.5%@90d, the 36-day median, the whole eval-sprint — all assumed you can hold positions for carry's real durations. **You can't. Lucid's own FAQ: "Overnight holding is not permitted on sim accounts (Pro/Flex/Direct)... positions must be closed by 4:45 PM EST."**

Carry's sealed history: **median hold 6 days, p90 17 days, 72% of trades cross a weekend.** On Lucid, carry cannot exist. Not "harder" — impossible. Every simulated pass assumed holds the firm force-closes at 4:45pm daily. A year of work never hit this wall because no sim modeled the flat-by-close rule; it took reading the firm's FAQ against the trade log's hold-day column.

**Status: the futures-eval lane is DEAD for carry** (exception to verify: Phidias "Premium" claims overnight/weekend holds on futures, $180, no time limit, EOD-trailing $3k — verify their FAQ directly before spending anything).

## THE LANE THAT FITS (verified against carry's actual requirements)
Requirements from the sealed CSV: multi-day holds ✓ weekend holds (72% of trades) ✓ news holds (CB events inside 6-day holds) ✓ no/long time limit (0.8 trades/wk) ✓ static drawdown strongly preferred ✓.

**Forex swing accounts fit natively:** FTMO Swing ($100k ≈ $501, 10%→5% two-step, 10% static max DD, 5% daily, no time limit, weekend+news allowed) · Alpha Capital Swing (≈$490, same shape) · City Traders Imperium (all accounts allow weekend/news/overnight; 1-step 8% target, 5% static DD, no daily limit, ≈$382).

## COLIN_V2.1 — carry on the RIGHT rules (sim run tonight, sealed 411-trade series, block replay, every weekly start 2015-2023)
FTMO-style: +10% then +5%, static 10% DD, 5% daily loss (close-basis), no clock, bust→rebuy:

| Risk/trade | Median days to FUNDED | p10 | p90 | E[evals] | E[cost @$500] |
|---|---|---|---|---|---|
| 1.0% | 448 | 189 | 888 | 1.19 | ~$595 |
| 1.5% | 388 | 119 | 755 | 1.32 | ~$660 |
| **2.0%** | **318** | **94** | **724** | **1.70** | **~$850** |
| 3.0% | 236 | 68 | 566 | 2.68 | ~$1,340 |

No deadline exists at these firms, so "pass rate" is ~certain over time with a positive edge — the honest variables are TIME and FEES, shown above. The 5% daily loss limit is what punishes big sizing (multiple same-day exits cluster); 2% is the knee. CTI's 1-step (8% target, no daily limit) likely dominates FTMO for this profile — worth pricing as first choice.

## RED FOLDER — tested tonight at the resolution we own
CB rate-decision surprises (1,201 events, 2014-2026) → trade the surprise direction, daily bars:
- Same-day, next-day, +3-day: **no significant edge in any cell** (all |t|<1.4, net of costs ≈ 0; n=83-242).
- Notable lean: same-day close ends AGAINST the surprise ~74% of the time — consistent with news mean-reversion, but the daily bar mixes pre/post-release, so it proves nothing by itself.
- **Verdict: the 5-30min hypothesis is untouched (needs intraday data) and daily drift offers nothing. Do not trade news direction on daily bars.** The intraday test remains gated on a data key, and this result is the reason to run it before believing anything.

## WHAT CHANGED TODAY
Yesterday produced knowledge. Today produced decisions: **the futures lane is dead for carry (would have burned real fees), the forex-swing lane is quantified at 2% risk / ~$850 / median ~10 months, CTI is the firm to price first, and red-folder-on-dailies is ruled out.** Scripts: `oos_campaign_test.py` conventions; FTMO sim inline in this commit.
