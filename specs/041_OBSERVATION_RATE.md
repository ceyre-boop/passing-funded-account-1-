# 041 — OBSERVATION RATE `[ANALYSIS]` `[NO IMPLEMENTATION]`

**Question:** the sealed edge rests on 411 trades / ~41 per year, a median 6-day
hold. Sharpe 1.25 on n=411 has a standard error wide enough that the true
Sharpe could plausibly be 0.5. More observations is the only structural fix.
Audit every uncorrelated bet this repo could take and is not; rank by marginal
P(pass) gain per unit of complexity; recommend the top two.

**Method note up front:** this document does not add a strategy, run a new
hypothesis test, or touch `data/proof/`. Every number below is either read
directly from existing sealed/audited artifacts, or computed from a raw price
pull (yfinance, network confirmed reachable in this environment) using the
same *price-level delta-correlation* convention `daytrade/mechanisms.py`
already uses for `K_EFF` — not a re-run of the carry strategy itself, which
would be implementation. Scratch script: nowhere in the repo, run ad hoc and
discarded; the numbers are reproducible from the commands quoted per section.

---

## 0. A constraint the task brief doesn't mention but the repo does

`CLAUDE.md` non-negotiable #1: *"No new hypotheses get generated here. This
repo runs one already-proven edge... that work happens in the general repo,
not here."* Every candidate below that would require fitting or testing a
signal (a new pair's edge, a new exit rule, a shorter hold) is **not
buildable in this repo even if this analysis recommends it** — it would need
to clear the same bar HYP-045/HYP-059/HYP-108 cleared, in `ceyre-boop/quant`,
and be brought back with a sealed proof set before this repo could add it to
the buy gate. That reclassifies "rank the top two" into two different kinds
of answer: things this repo could plug in today (none, it turns out — see
§4), and things worth *dispatching* to the general repo because the
diversification math justifies the trip. Recommendation reflects that split.

---

## 1. Candidates considered

| # | candidate | data reachable today | requires new hypothesis (non-negotiable #1) |
|---|---|---|---|
| A | Add FX pairs beyond the 4 (`ALL_PAIRS` minus `MAJOR_PAIRS`) | yes, spot prices | already tested — see §2, re-testing without new info is re-litigation |
| B | Track 2 carry pairs (AUDCHF, NZDJPY) — built infra, never proven | yes, spot prices | yes — no sealed proof exists |
| C | Swing carry lane + daytrade cockpit run concurrently | n/a | no — but cockpit edge is closed NEGATIVE (specs 037, oracle_audit) |
| D | Shorter-horizon variant of the same carry signal | yes, sealed data already covers this | yes — an exit-rule change |

---

## 2. Candidate A — FX pairs beyond the sealed 4

`sovereign/forex/pair_universe.py` documents seven pairs already tried and
retired, each with a dated, numeric reason:

| pair | verdict | evidence quoted in the file |
|---|---|---|
| USDCAD | removed 2026-05-22 | avg +0.071%/trade vs portfolio +0.204%; no regime clearly earns |
| NZDUSD | removed (Oracle audit 2026-05-17) | Sharpe 0.22, max DD -11%, lowest in universe |
| USDCHF | removed v004 | SNB pinned -0.75% for 8yr; Sharpe -0.45 |
| AUDNZD | removed HYP-045 2026-06-02 | OOS Sharpe -0.879; excluding it lifted portfolio OOS Sharpe 0.76→1.08, p=0.002, decay 1.61 ROBUST |
| EURJPY | removed | dual-CB signal conflict, consistent loss |
| EURGBP | removed v004 | Sharpe -0.04, profit factor 1.00 |
| GBPJPY | removed 2026-05-22 | avg +0.168%/trade vs portfolio +0.243%; 2022 and 2024 both negative |

This is not an untested set — it is a **closed set**, most recently and most
rigorously by HYP-045 (permutation p=0.0033, BH survives, decay ROBUST,
canonical runner). Reopening any of these without new information is exactly
the "want to test a variant" case non-negotiable #1 forbids here. Spot-level
correlation to the sealed 4 was still computed for completeness (2015-2024
daily returns, `yfinance`):

```
avg |corr| among the 4 sealed pairs (baseline): 0.444
USDCAD  |corr| to sealed 4:  ~0.51  (moderate-high — and already rejected on edge, not correlation)
NZDUSD  |corr| to sealed 4:  ~0.62  (high — AUDUSD/NZDUSD share Oceania beta, matches file's own AUDNZD reasoning)
USDCHF  |corr| to sealed 4:  ~0.50  (moderate-high)
```

**Verdict: not a candidate.** Even where correlation is moderate, every one
of these pairs was rejected on its *own* Sharpe, not on redundancy with the
existing four — re-adding any of them reintroduces a proven-negative or
proven-marginal contributor. This is the same shape as the daytrade cockpit:
closed, don't reopen absent new information.

---

## 3. Candidate B — Track 2 carry pairs (AUDCHF, NZDJPY)

`sovereign/forex/carry_engine.py` (built, tested for ATR-fallback safety in
`test_carry_engine_atr.py`, never tested for edge) already scaffolds exactly
this: two pairs, sized at a fixed 0.3% of equity, running on interest-rate
differential alone, explicitly **not** gated by the macro/regime signal the
sealed 4 use ("This engine is NOT a signal filter... does not need to pass a
conviction gate; it just pays" — docstring). No `HYP-*` entry exists for it
in `data/proof/carry_hypothesis_lineage.json`; it has never been backtested,
sealed, or Sharpe-measured.

**Data reachability:** confirmed live via `yfinance.download('AUDCHF=X', ...)`
and `'NZDJPY=X'` — both return full daily OHLC back to 2015 in this
environment. Unlike the sealed 4's macro gate, this signal does not obviously
need the dead CPI feeds (`data/proof` memory: UK/AU CPI dead at FRED source,
JP CPI a fabricated 1978-row flat constant) — the engine's own docstring
scopes it to "interest-rate differential," i.e. policy rates, which are a
much smaller, more available data surface than the `real_rate_diff` term the
macro gate needs. That said, this is an inference from the docstring, not a
verified data-sourcing check — the general repo's hypothesis test would need
to confirm the policy-rate feed it actually uses is live before trusting any
resulting Sharpe.

**Correlation** (2015-2024 daily spot returns, same `yfinance` pull as §2):

```
                EURUSD  GBPUSD  USDJPY  AUDUSD  AUDCHF  NZDJPY
AUDCHF           -0.003  0.195   0.133   0.614   1.000   0.502
NZDJPY            0.182  0.304   0.474   0.553   0.502   1.000

avg |corr| of AUDCHF to sealed 4:  0.236
avg |corr| of NZDJPY to sealed 4:  0.378
avg |corr| among the sealed 4 themselves (baseline): 0.444
```

AUDCHF is the more diversifying of the two by a real margin — 0.236 average
vs the sealed book's own internal 0.444 — though it shares AUD exposure with
AUDUSD specifically (0.614), which is expected and bounds how independent it
can ever be. NZDJPY shares a JPY leg with USDJPY (0.474) and an AUD-bloc beta
with AUDUSD (0.553) via NZD/AUD co-movement, so it buys less new information
than AUDCHF does. A second, sparser proxy — correlating the sealed v015 daily
realized-R series (only 268 of 3625 days nonzero, since R prints only on
trade-exit days) against AUDCHF/NZDJPY daily spot returns — comes back
~0 for both (-0.01, -0.01). That number is not very informative given how
sparse the R series is against a dense return series, but it does not
contradict the spot-correlation read.

**Marginal P(pass) effect:** cannot be measured honestly without a real R
series for these pairs, which does not exist and cannot be built here without
violating both "no implementation" and non-negotiable #1. `ruin_engine.py`'s
`simulate_frontier(series_map, ...)` *can* run several equal-length series
side by side under common random numbers (see §5) — that machinery is ready
the day a sealed AUDCHF/NZDJPY series exists. Until then this is a structural
diversification argument (lower cross-correlation, independent signal type —
a static-differential "coupon" vs. the sealed book's regime-timed macro
carry), not a measured P(pass) delta.

**Complexity to reach a verdict:** low relative to a fresh hypothesis — the
engine, config, and ATR-safety tests are already written; what's missing is
the actual backtest/Sharpe/permutation-test cycle the general repo already
knows how to run (same shape as HYP-045/059). That is real but bounded work,
not a new architecture.

---

## 4. Candidate C — swing carry lane concurrent with daytrade cockpit

Rejected on the task brief's own terms. `NEXT.md` and `Plans/THE_BIG_PLAN.md`
(2026-08-26) close both of the daytrade cockpit's edge questions as
**negative**: exit-policy selection fails structurally (`null_leak` converges
*upward* with n, 1.4496→1.5813 as n:39→402, against a 0.15 gate — "more data
cannot fix this"), and entry-side trade/skip fails its pre-registered test
(`specs/037`: OOF mean R -0.0112, worse than always-trade -0.0061 and worse
than a rate-matched coin flip -0.0036 — "selection carries negative
information"). Running it concurrently with the sealed carry lane would not
add an uncorrelated *positive*-expectancy bet stream; it would add a proven
negative-expectancy one, diluting rather than raising portfolio P(pass). Not
ranked.

---

## 5. Candidate D — shorter-horizon variant of the same carry signal

The sealed set already contains the answer, and it's discouraging for this
candidate. `HYP-059` (confirmed, `carry_hypothesis_lineage.json`) found the
entire net edge lives in `time` exits (+141.9R, WR 69.8%, n=149 on the 2015-22
confirmation log) while the mechanical trailing-stop exit — the shorter,
more reactive exit — is net -49.0R (WR 25.8%) and negative in every pair.
Replaying the same read on the full 411-trade 2015-2024 sealed set by hold-day
bucket (`risk_adjusted_pnl_pct`, computed directly from
`data/proof/backtest_trades_v015_2015_2024.csv`):

```
hold_days bucket   n    mean R      sum R
(0, 2]             72   -0.000008   -0.000576
(2, 4]             38   -0.000046   -0.001741
(4, 6]            190   +0.000024   +0.004470
(6, 10]            45   +0.000011   +0.000490
(10, 100]          66   +0.000090   +0.005943
```

Both the shortest buckets (0-2 and 2-4 days) are net negative; the edge lives
in the ≥4-day holds. Forcing a shorter median hold to raise trade count would
be trading directly against evidence already sealed in this repo's own proof
set — it is not merely "a new hypothesis," it is a hypothesis the existing
data already argues against. This also matches `specs/034`'s finding that
the trailing-stop/mechanical-exit path reproduces only 1-2% of sealed
`trailing_stop` exits and that shortening/tightening any exit term is
explicitly out of scope ("tuning is forbidden here... an evaluator fitted to
the record stops being one"). Lowest-ranked candidate; not worth dispatching.

---

## 6. Can `ruin_engine.py` express a multi-strategy portfolio? — say plainly: partially

`scripts/ruin_engine.py::simulate_frontier(series_map: dict[str, tuple], ...)`
already accepts multiple *named* equal-length series and walks each one
under the **same** Monte Carlo block-start draw per path (common random
numbers) — but that machinery exists to make an edge-vs-control comparison
fair (§ docstring: "every sampled path draws ONE set of block-start indices
and applies it to the edge and to every requested control"), not to blend
two simultaneously-running strategies into one account equity curve. Each
key in `series_map` is walked through `run_single_attempt` **independently**
— there is no summation of two strategies' daily R on the same date into one
combined `(vi, vw, vopen)` triple. Practically: `series_map` can compare "the
sealed edge" against "the sealed edge + AUDCHF blended in" side by side, but
the caller has to do the blending upstream (pre-summing daily R across
strategies into a single series, the same way `carry_buy_gate.build_series`
already aggregates the 4 sealed pairs into one portfolio series today) before
handing it to `simulate_frontier` as one of the named series. That blending
step does not exist yet for any second strategy, because no second strategy
has a sealed R series to blend. This is the concrete asset the day AUDCHF/
NZDJPY (or anything else) clears a hypothesis test in the general repo: the
survival-frontier tool is ready to receive it, it just needs one function
that sums two daily-R series onto a shared calendar before the walk — not a
new simulator.

---

## 7. Ranked recommendation

| rank | candidate | marginal P(pass) potential | complexity | verdict |
|---|---|---|---|---|
| 1 | Track 2 carry (AUDCHF, NZDJPY) | unmeasured but structurally plausible — lowest cross-correlation of any candidate (0.236/0.378 vs sealed-book's own 0.444 baseline), independent signal type, infra already built | bounded — engine/tests exist, needs the standard hypothesis-test cycle in the general repo | **dispatch to `ceyre-boop/quant`**: run the same HYP-045/059-grade backtest + permutation test on AUDCHF and NZDJPY separately; bring back only what seals |
| 2 | (none inside this repo today) | — | — | every other candidate is either already closed (A, C) or contradicted by this repo's own sealed data (D) |

**Top recommendation, singular:** AUDCHF/NZDJPY via the general repo. Nothing
else clears the bar. This repo's non-negotiable #1 means the honest second
half of "recommend the top two" is that there isn't a second candidate this
repo can act on today — the observation-rate problem is real, but the fix is
not inside this repo's mandate; it is a dispatch, not a build. The one
genuinely free, in-repo action available now is the one-function extension
named in §6 (a shared-calendar daily-R summer feeding `simulate_frontier`),
which is prep work for whenever AUDCHF/NZDJPY (or anything else) seals — not
a strategy, and not built here per the task's own constraint.

---

## 8. What this analysis did not do

- Did not touch `data/proof/`.
- Did not add, tune, or backtest a strategy.
- Did not modify `sovereign/forex/carry_engine.py`, `pair_universe.py`, or
  `scripts/ruin_engine.py`.
- Did not quote a P(pass) number without a control, per `SANITY_AUDIT.md`'s
  standing rule — §3's "marginal P(pass) effect" section states plainly that
  no such number can be computed yet, rather than inventing one.
