# V3 Research — Passing in 30–90 Days
**Alta Investments | 2026-08-01**
*What it actually takes, what we have, what has to be built.*

---

## INDUSTRY BASE RATES (what everyone else does)

From 300k+ tracked accounts across 10 firms: **5–15% pass on first attempt**, mean **3 attempts** to pass, only **~7% of all eval buyers ever see a payout**, and **~50% of funded traders lose the account within 90 days**. Traders who pass average **3.2 trades/day at 0.5–1% risk**; traders who fail average 6.8 trades/day at 2–3%. Half of all failures are max-drawdown hits.

Read that twice: the people who pass are not the ones with the best ideas — they're the ones with **daily trade frequency and small size**. Frequency, not aggression, is the passing trait.

## THE REQUIREMENTS MAP (what 30–90d @ 80% costs)

Monte Carlo, rebuy-on-bust campaigns, trailing 8% DD, near-optimal risk per cell. IID numbers first, then reality:

| Edge profile | Trades/week | P(30d) IID | P(90d) IID |
|---|---|---|---|
| Carry-like (49% WR, +1.4/-0.85R) | 1 | 54% | 94% |
| Carry-like | **3** | **93%** | ~100% |
| JJ-style (55% WR, 1:1.5) | **5** | **99.7%** | ~100% |
| Scalper (60% WR, 1:1) | 3 | 92% | ~100% |

**Calibration check — IID lies.** The same IID model says carry at ~1 trade/week passes 94% in 90d. The block replay on real 2015–2024 sequences says **67%**. Real markets cluster: dead months, losing streaks, regime droughts. Haircut factor measured on carry: **×0.72 at 90d, ×0.50 at 30d.** Applied to the table: a validated 3–5 trades/week edge realistically lands **~65–85% at 30 days and ~90%+ at 90 days.** That clears your bar. Nothing slower does.

**The law this table enforces: below ~3 trades/week, no sizing policy on earth reaches 80% in 90 days.** Sizing cannot manufacture signals (proven again in COLIN_V2 — the 90d oracle ceiling for carry is 74%).

## AUDIT OF WHAT WE ALREADY OWN (trial run tonight, real logs)

| Candidate | Actual frequency | Verdict |
|---|---|---|
| **Carry (v015, proven)** | **0.8 trades/week** | 27% @30d / 67% @90d. Too slow. Keeps its job as the FUNDED-phase engine. |
| **ICT london_a** | **0.2 trades/week** (28 trades in 1,001 days) | Campaign MC: 21% @30d even at 3% risk. **Dead as a fast path.** Worse: the old `challenge_simulator.py` verdict ("100% pass, 12 days") assumed **0.8 trades/DAY — 28× the real signal rate.** That number was garbage-in. Struck. |
| Petroulas earnings bets | Episodic (a few/quarter) | Real upside, wrong shape for eval fuel. It's the "make bank" layer AFTER funding, exactly as you framed it. |

**Conclusion: nothing we own today passes in 30–90 days at 80%. The edge that does it has to be built.** That is not a setback — it's the first time the requirement has ever been stated in numbers.

## THE BUILD (V3 candidate: Session Reversion, NQ futures)

The one profile that fits the requirement AND has a published playbook we already dissected (JJ Simon notes, this repo's research folder): intraday session mean-reversion on NQ.

- **Signal**: session open (9:30 ET, 2pm, 8:30 news) → one continuation off the open drive → after consolidation, break of structure back toward the open/fair price → 1–3 reversion entries. 1-minute structure, 30–60 min horizon.
- **Static everything**: 1:1.5 R:R, fixed $ risk, no runners, no breakeven (per the trailing-DD logic already documented in `2026-08-01_research-note_jj-simon-prop-firm-math.md`).
- **Frequency**: 2–5 trades/day available; even taking the best 1/day = 5/week — clears the bar with room.
- **Target stats to validate against**: ≥52% WR at 1:1.5 (breakeven is 40% — that's a 12-point cushion demand, deliberately strict).

## THE GATE (non-negotiable, same bar carry cleared)

1. **Data**: NQ intraday (1m/5m). Blocked tonight — cloud sandbox can't reach Yahoo; the Databento/Polygon keys in the general repo's `.env` can pull it from your machine, or we wire it next session.
2. **Backtest**: ≥2 years intraday, costs included, sealed CSV like carry's.
3. **Paper sprint**: 4 weeks live-shadow, target **n≥80 trades** (at 4/day that's 3 weeks). Live WR within the backtest CI or it doesn't ship.
4. **Then** eval sprint: 1–1.5% risk (high frequency doesn't need V2's 5% — frequency replaces aggression), rebuy on bust.

**Timeline: ~5–7 weeks from data-in-hand to first eval purchase.** Faster than waiting out carry's 180-day 80%, and it produces a second engine you keep forever.

## THE HONEST PROBABILITY (your question, answered straight)

- **P(pass ≤30d) with a VALIDATED session edge: ~65–85%.** P(≤90d): **~90%+.** Large-sample (10k campaigns/cell), clustering-haircut applied.
- **Those numbers are conditional on validation passing.** Tonight they are a spec, not a property we own. Anyone who quotes you 80% without that conditional is selling something.
- Meanwhile the unconditional path we already own: **67% @90d (COLIN_V2)** — available today, zero build time.

## THE CYCLE YOU DESCRIBED (on the record, it's sound)

Pass fast → run funded hot-ish → maybe blow it in 6 months → rebuy → repeat, with Petroulas earnings shots layered on top. The math agrees with you on one condition: **the funded account's expected payouts before death must exceed the cost of the next campaign.** At median ~$10k/yr payouts (COLIN_V2) vs ~$250–825 campaign cost, that condition is comfortably met even if the account dies annually. Your instinct is arithmetically correct — it just needs the fast-pass engine built first.

*Sims: `scripts/colin_v2_campaign_sim.py` + requirements-map MC (this doc). Sources: FPFX Tech 300k-account dataset via atmosfunded.com; damnpropfirms.com pass-rate compilation.*

---
## ADDENDUM 2026-08-01 — RERUN CONFIRMED (10,000 campaigns/cell, fresh seeds)

| Profile | IID P(30d)/P(90d) | Weather-calibrated P(30d)/P(90d) |
|---|---|---|
| **JJ-style 55% WR, 1:1.5, 5/wk** | 99.8% / ~100% | **~75% / ~94%** |
| Scalper 60% WR, 1:1, 3/wk | 92.2% / ~100% | ~65% / ~85% |
| Carry-like @3/wk (hypothetical) | 93.7% / ~100% | ~64% / ~85% |

Weather model = drought/streak regime-switching, calibrated until the carry cell reproduces the real
block-replay (27%/67%) before judging the fast profiles. Sim: `scripts/requirements_map_mc.py`.
**Selected V3 target: JJ-style 55% WR / 1:1.5 / ≥5 trades-week.** Live-sim account opened same day:
`data/propfirm/jj_sim_account.json`, tracked via `scripts/jj_sim_tracker.py`, dashboard `dashboard/jj_dashboard.html`.
