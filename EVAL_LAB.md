# EVAL LAB — Instant Pro $10k, honest pass-probability search

> **⚠️ SUPERSEDED (2026-08-10, spec 021).** The three lab scripts are broken on this
> machine (hard-coded `/home/claude/...` paths) and model firms/rules the carry lane
> doesn't use. The methodology this lab pioneered — zero-edge control + block
> bootstrap — is preserved and enforced structurally in `scripts/carry_buy_gate.py`.

2026-08-02 · scripts: `eval_lab.py` (full 60-combo search), `eval_lab_carry_fix.py` (family-D corrections) · protocol pre-registered in script docstrings before first run

## Rules modeled
$10k · 3% daily loss (intraday, vs day-start equity) · 6% max loss (**headline = trailing, intraday** — worse interpretation; static also run) · 1:50 · MT5 CFDs · Instant (no stated target/deadline) → **PASS defined as +6% before bust within 365d**; TIMEOUT reported separately.

## Search, disclosed in full
**K = 60 combinations**: Donchian breakout (4 cfgs) + MA trend (6) + RSI-2 mean-reversion (4) × 4 risk levels, plus sealed carry × 4 risk levels. Tuned on **2015–2021 only**; holdout **2022–2026-07** never touched during selection. Costs at the pessimistic end: 3.0 pips round trip + 1.2 pips stop slippage + swap −0.006%/day (A/B/C) / −0.004R/day haircut on carry. Pessimistic mechanics: stops fill before targets in-bar, gaps fill at open, all open trades' intraday worsts assumed simultaneous, daily & max limits breach on intraday equity.

**Corrections applied mid-lab (logged, not hidden):** carry's zero-edge arm originally escaped mean-centering (it loads from CSV, not prices) — fixed by centering its R-series. Carry's 2022–24 "OOS" was inside its original development window — replaced with the true 2025–26 rig trades (60 trades, `data/oos_trades_2025_2026.json`). A double-applied swap haircut on carry was fixed.

## Results

### New daily-bar strategies: failed. Say it plainly.
| Family champion (train-selected) | train p(pass) | OOS p(pass) | Zero-edge OOS champ |
|---|---|---|---|
| Breakout Donchian-55, 2×ATR, 1% | 21.7% | 20.9% | 21.4% |
| Trend SMA-200, 2×ATR, 1% | 21.4% | 13.7% | 17.0% |
| Mean-rev RSI-2, no filter, 1% | 3.8% | 30.8% | 20.3% |

Every tuned technical family lands **inside the zero-edge (pure luck) band OOS**. No edge demonstrated on daily bars under these rules. These are dead, not "promising."

### Champion (picked on train, K=60 disclosed): carry @ 0.50% risk
| | train 15–21 | TRUE OOS 2025–26 | zero-edge same cell |
|---|---|---|---|
| p(pass ≤12mo), start-sweep | 51.2% | **42.9%** (n=28 overlapping starts) | 10.7% |
| p(pass ≤12mo), block bootstrap | — | **33.7%** | 25.9% |
| p(bust daily) | ~0% | 0% | 0% |
| p(bust max-loss) | ~1–9% | 0% observed | 21% |
| p(timeout at 12mo) | ~43% | 57% | 68% |

At 0.75–1.00% risk, OOS max-loss busts jump to **50%** — 6% of room on a strategy whose worst historical trade is −3.2R does not tolerate those sizes. 0.25% never busts but almost never reaches +6% in a year.

## THE HONEST NUMBER
**p(pass within 12 months) ≈ 35–45% — point estimate ~40%.** Out-of-sample (2025–26, data no tuning ever saw), pessimistic costs, worse drawdown interpretation, search size disclosed and luck-baselined. The OOS period contains only **~1.4 independent 12-month windows**, so the honest interval is wide: call it 25–55%.

Two things that keep this from being grim:
1. **Failure mode at 0.5% is almost never bust — it's "not there yet."** If Instant Pro truly has no deadline, timeout isn't failure; observed bust risk at 0.5% is 0% OOS / single-digit in-sample. The realistic outcome distribution is: ~40% funded-and-paying within a year, most of the rest still alive and grinding, small tail busted.
2. The zero-edge gate shows structure alone only passes ~10–26% here (no rebuys, tight room) — carry's 34–43% sits above its luck baseline, unlike every other candidate. The separation is real but thin at this sample size; it is not proof.

**What would make the number better, honestly: nothing tested here.** Not sizing, not signal tuning — the search is done and disclosed. Only more OOS time or intraday data changes the evidence.
