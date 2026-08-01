# Nightcap — 2026-08-01
**The full arc in one page: hype → audit → retraction → true out-of-sample test → what's real.**

## What JJ-style actually is (plain words)
Trade the NASDAQ session opens. When a session opens (9:30, 2pm, 8:30 news), price lurches one way on the burst of volume. Take one trade WITH that lurch, then when it stalls and cracks back, take 1-3 trades betting it reverts toward where it opened. Fixed risk, fixed 1:1.5 target, no runners, no breakeven, done in under an hour. Fire it every session = ~5+ trades/week.

## Why it "passed 90%+" in the sims (the autopsy)
Three multipliers stacked — only one of them real:
1. **Frequency (real, structural):** 65 trades in 90 days finishes the race; carry's 10 often can't.
2. **Rebuys (real, structural — and the trap):** busting costs only the fee. With free retries, **a zero-edge coin flip passes 85-97% of campaigns at that frequency.** Most of the "90%+" was this.
3. **The assumed edge (not real — never measured):** 55% WR / 1:1.5 was a spec. It added the last few points and it has zero logged trades behind it.
So: ~85% structure, ~10% assumption, 0% measurement. Retracted accordingly (SANITY_AUDIT.md).

**Where edge actually lives:** not in P(pass) — in (a) evals burned per pass, and (b) whether the funded account pays. A no-edge passer burns 2x the fees and collects $0.

## Tonight's true out-of-sample test (the win)
The sealed backtest ends 2024-12-09. The repo's data runs to 2026-07-03. **The sealed v015 engine was run, fully offline, on 18 months it has never seen.** Same rig both periods (two minor inputs unavailable offline, so the rig is a slightly degraded v015 — both periods equally; sealed CSV comparisons don't apply, rig-vs-rig does):

| Metric | Rig in-sample 2015-24 | **Rig OOS 2025-26** | Read |
|---|---|---|---|
| Trades/year | ~30 | **40** | **Fires at full rate on unseen data** |
| Win rate | 50.0% | **48.3%** (n=60) | **Replicates** (CI ±13pp) |
| Avg R | +0.429 | **+0.100** | Softer — 1.7 SE gap: **inconclusive, watch it** (EURUSD/USDJPY dragged; GBP/AUD fine) |
| V2 campaign P(90d) | 50.3% | **46.7%** | Holds — but remember zero-edge base ≈50%: this metric can't see edge |
| **E[evals burned]** | 1.56 | **1.95** | **The honest signal: edge softness shows up in fees first — exactly where the audit said to look** |
| V2 campaign P(30d) | — | 23.5% | Matches the supply ceiling story |

**Bottom line:** the machine survived first contact with unseen data — signal rate and win rate carried over; per-trade profit came in soft on a small sample and gets watched, not celebrated or panicked over. And the audit's framework predicted where the softness would appear (fees, not pass rate). The process works.

## What trades on the dashboard now
- **LANE 1 — ACTIVE: ALTA-V2 eval sim.** Carry signals, $100k Lucid-style rules, 5% eval sizing per COLIN_V2, tracked like real money. This is what survived: real trades, real replay, honest CI (67.5% ± 14.5pp @90d on the sealed series; 46.7% on the OOS rig).
- **LANE 2 — PARKED: JJ-style.** No probability gets quoted until NQ intraday data → backtest → 80-trade paper sprint. The 55%/1:1.5 spec is the hypothesis; JJ-SIM-001 is the instrument; the zero-edge baselines (84.7%/97%) are the bar its results must clear.

*Scripts: `oos_2025_2026_runner.py`, `oos_campaign_test.py` — both rerunnable from repo root, fully offline.*
