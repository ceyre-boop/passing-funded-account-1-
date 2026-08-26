# 036 — G5 PAPER GATE: RE-REGISTRATION DRAFT `[UNRATIFIED]`

**Status: DRAFT. Not in force. Colin ratifies or rejects; an agent never
self-ratifies a gate change.**
**Written:** 2026-08-26, BEFORE any paper trade was logged. As of this
writing `data/trade_logs/paper_carry_trades.jsonl` is 0 bytes (created
2026-08-12). No G5 number has ever been read. That ordering is the point: a
gate changed after seeing what the change would yield is not a gate.

## Why this document exists at all

Spec 021 pre-registers G5: **≥80 closed paper trades** via
`paper_carry_log.py`, paper mean R within ±0.25R of the sealed +0.3556
(`scripts/carry_buy_gate.py::paper_gate()`, `G5_MIN_N = 80`, `G5_R_BAND =
0.25`). It stands at **0/80**. Per the current gate state, G1 GREEN, G2
GREEN, G3 needs an OOS run, G4 GREEN — G5 is the only RED that is a pure
calendar problem, not an open question about the edge.

A rate computation on the sealed record — 411 trades spanning 9.94 years —
implies G5 as written is roughly a **1.9-year gate**. Nothing was peeked at
to learn this: trade count and date span are the same fields G1 GOLDEN
already reads and reports (`golden_gate()`), and no holdout was unsealed.

That is a discovery about the *gate's cost*, not about the edge. It is
exactly the situation where the temptation to quietly move a goalpost is
strongest, so the reason is written here first, in the open, before any
paper number exists to be reverse-engineered against.

## What is NOT being questioned

- The edge itself. v015 carry: OOS Sharpe 1.25, p<0.001, 411 sealed trades,
  survives BH correction, decay ratio 2.17 ROBUST.
- G1 GOLDEN, G2 REPRO, G3 EDGE, G4 FIT. All stand exactly as written.
- The sealed 2015–2024 record. Untouched, unre-cut, unre-read.
- The `±0.25R` agreement band in G5. If G5 runs, that tolerance is unchanged
  by this document.
- **The existence of a paper gate at all.** Nothing here proposes removing
  live-execution confirmation before real capital goes on the account. The
  question is only whether 80 closed trades is the right unit for that
  confirmation.

## The arithmetic, shown

Source: `data/proof/backtest_trades_v015_2015_2024.csv` (411 rows), read the
same way `carry_buy_gate.py::load_sealed()` reads it — `R = pnl_pct /
risk_pct`, per row.

| quantity | value |
|---|---|
| n (sealed trades) | 411 |
| span | 2015-01-02 → 2024-12-09 (3,629 days = 9.936 years) |
| trades/year | 41.37 |
| trades/week | 0.793 |
| current paper n (`paper_carry_trades.jsonl`) | 0 |
| G5 requirement | 80 closed trades, mean R within ±0.25R of +0.3556 |
| **time to 80 trades at the sealed rate** | **1.93 years** |
| mean R, sealed | +0.3556 (G1 GOLDEN reference value) |
| stdev of per-trade R, sealed (population, ddof=0) | 1.6922 |

The rate figure is a pure count/date-span division over the same sealed CSV
G1 already trusts. No new data was touched to produce it.

## Both readings, stated neutrally

**Reading A — 80 is correct and this is simply a purchase with a long
delivery date.** G5 is measuring something the backtest cannot: real fills,
real swap accrual, real weekend gaps, real platform behavior, and the
operator's own discipline under live conditions. None of that lives in a
CSV. Under this reading, 1.93 years is the honest price of the only evidence
that matters, the number 80 was chosen deliberately as a round, defensible
sample size, and the correct response is to start logging tonight and accept
the calendar — this is a **2028 purchase**, not a defect in the plan.

**Reading B — 80 was set without reference to the strategy's trade rate,
and is mis-specified.** Nothing in spec 021's P4/P5/P6 registration notes a
trades-per-year computation informing the choice of 80; the number reads as
a conventional "round sample size" pulled from general practice, not derived
from this edge's actual cadence (0.79 trades/week). Under this reading, a
gate that takes ~2 years to even attempt is not a stricter standard than an
80-trade target chosen with the cadence in view — it is simply an
unexamined cost that happens to bind hardest on a slow-turnover strategy.

Both readings are defensible from the document as written. Spec 021 does not
say which is true, and this document does not decide between them — that is
Colin's call, made in the ratification block below.

## Power analysis for the actual question G5 asks

G5 does not ask "is the edge real" — G1–G4 plus the sealed 411-trade record
already answer that. G5 asks a narrower statistical question: **does live
paper mean R sit within ±0.25R of the sealed +0.3556?** That is a test of
whether a sample mean lands within a fixed band of a reference value, and it
has a real, computable sample-size requirement — the same one
`daytrade/oracle_audit.py`'s sample-size block already uses elsewhere in
this repo for an analogous claim:

```
n = ((1.645 + 0.842) ** 2) * (sigma / delta) ** 2   # 95% one-sided, 80% power
```

Applied here with `delta = 0.25` (G5's own band) and `sigma` = the
population stdev of per-trade R across all 411 sealed trades (same
convention as `oracle_audit.py`'s `statistics.pstdev`):

```
sigma   = 1.6922   (stdev of per-trade R, sealed 2015-2024, n=411)
delta   = 0.25     (G5's pre-registered band, unchanged)
n       = ((1.645 + 0.842)**2) * (1.6922 / 0.25)**2
        = 283.4  →  283 trades (rounded, oracle_audit.py convention)
```

**This is the crux finding, and it runs the opposite direction from the
intuition that motivated writing this document.** Per-trade R on this edge
is noisy — individual carry trades swing from sizeable losses to
multi-R wins, and the mean of +0.3556R sits inside a wide spread (sigma
≈1.69R, roughly 4.8× the mean). To detect a ±0.25R deviation from that mean
at 95%/80% power requires **~283 independent trades, not 80**. At the sealed
rate of 41.37 trades/year, 283 trades take **~6.85 years**.

So the honest statistical picture is: **80 trades is under-powered for the
edge-confirmation question it is nominally answering**, at delta=0.25.
G5 as currently specified will produce a mean-R number that looks green or
red almost as much on sampling noise as on any real drift — an 80-trade
paper sample has a much wider true confidence interval around the observed
mean than the ±0.25R band being tested against implies. This does not argue
for a longer gate; it argues that **mean-R-in-band, at n=80, is not doing
the statistical work its name suggests it does**, whichever of Reading A or
B otherwise applies to the calendar question.

## A second defect, found while checking the first (2026-08-26)

`specs/021_CARRY_BUY_GATE.md:119` defends the ±0.25R band with:

> "it is wide because n=80 has σ/√n ≈ 0.13R on this series"

**That number does not reconcile with any σ in the series it names.** Measured
from `data/proof/backtest_trades_v015_2015_2024.csv` (411 trades, R =
`pnl_pct/risk_pct`):

| series | σ | σ/√80 |
|---|---|---|
| per-trade R — *the quantity G5 measures* | 1.6922 | **0.1892** |
| per-trade R after swap haircut | 1.6853 | 0.1884 |
| daily inc series, non-zero days | 2.3608 | 0.2639 |
| daily inc series, all business days | 0.7747 | 0.0866 |

A σ of **1.1628** would be required to produce 0.13R at n=80. No series here has
it. The stated standard error understates the real one by ~45%.

Consequences, both of which cut against the band as written:

1. ±0.25R is **1.32** standard errors, not the ~1.9 the quoted 0.13R implies.
2. At n=80 the minimum deviation detectable at one-sided 95% / 80% power is
   **0.471R** — nearly **double** the ±0.25R band the gate is policing. G5
   cannot resolve the band it enforces. It would pass a paper series whose true
   mean R is +0.10 against a sealed +0.3556.

This is not an argument for loosening G5. It is the opposite: the gate is
**weaker than it reads**, and the direction of the error was hidden by an
unsourced justification. Recorded here rather than fixed in place, because
editing a pre-registered justification after it fails to reconcile is the
goalpost move this document exists to avoid.

## Proposed options, with time cost and statistical consequence

Presented without recommendation. Time costs use the sealed rate, 41.37
trades/year.

| option | n | time (at 41.37/yr) | statistical consequence |
|---|---|---|---|
| **Leave G5 as written** | 80, ±0.25R | 1.93 years | Runs the intended live-execution check, but at n=80 the mean-R-in-band test has materially less power than the 283-trade requirement computed above — a green or red at n=80 is a noisier signal than the ±0.25R band suggests. Honest, but the number "80" does not mean what it looks like it means. |
| **Raise n to the powered threshold (~283)** | 283, ±0.25R | ~6.85 years | The mean-R-in-band test becomes properly powered at 95%/80% for the pre-registered band. Correct statistics, but a near-7-year gate before a BUY verdict is reachable on this criterion — likely impractical as the sole gating instrument. |
| **Split G5 into execution-fidelity + a longer-running edge monitor** (structurally similar to spec 031's G5a/G5b split for a different gate) | small n (e.g. 20–40) for execution checks; large n (283, unchanged band) for the edge-confirmation question, run as a background monitor rather than a blocking gate | ~0.5–1.0 years to first checkpoint; edge monitor continues indefinitely | Separates "does live execution match the backtest's assumptions" (answerable at low n, a fidelity check, not a power-sensitive mean test) from "does the live edge match the sealed edge within ±0.25R" (properly requires ~283 trades to be well-powered, and does not need to block a verdict once G1–G4 already establish the edge exists). Consequence must be stated plainly: this issues a BUY on less mean-R evidence than the powered threshold, accepting G1–G4 plus a fidelity check as sufficient, with the edge-confirmation question demoted to a monitor whose breach revokes rather than blocks. |
| **Widen delta instead of raising n** (keep n=80, widen the band beyond ±0.25R to whatever delta is properly powered at n=80) | 80, wider band (solve `delta` for n=80: delta = sigma * sqrt(2.4869/80) ≈ 0.297R) | 1.93 years, unchanged | Restores statistical honesty at the original 80-trade budget by loosening the tolerance instead of raising the count — but a ~±0.30R band is looser than the ±0.25R Colin originally registered, and any widening decided after seeing G5 run would be the exact goalpost move this document exists to avoid. Only legitimate if ratified now, before any paper data exists. |
| **Reject this document; G5 stands, sprint starts, no other change** | 80, ±0.25R, run as-is | 1.93 years | Simplest outcome. The power gap above is registered and known but not acted on — the sprint runs, and whatever it produces at n=80 is graded against the original ±0.25R band as originally written, with the caveat that this document is on record explaining why that number is less conclusive than it appears. |

## Note on timing and precedent

Changing a gate after seeing it block a purchase is exactly the goalpost
move `specs/008_CEILING.md` and `daytrade/ceiling.py::_verdict()` refuse for
a structurally identical reason, and it is why this document exists as a
pre-registered proposal rather than as a direct edit to
`specs/021_CARRY_BUY_GATE.md`. No number from `paper_carry_trades.jsonl` has
been read while writing this. If Colin rejects this document, G5 stands at
80 trades exactly as spec 021 registered it, and the sprint takes as long as
it takes.

## Ratification

Unratified until Colin records a decision below and the file is re-sealed.
Rejecting this document is a complete and legitimate outcome.

```
DECISION: [ ] leave G5 as written (80, ±0.25R)
          [ ] raise n to powered threshold (~283, ±0.25R)
          [ ] split into execution-fidelity + background edge monitor
          [ ] widen delta to match n=80's actual power (~±0.30R)
          [ ] reject / other (specify below)
BY:                        DATE:
REASON (required, written before any paper trade data is read):
```
