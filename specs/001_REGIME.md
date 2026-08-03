# 001 — REGIME CLASSIFIER  `daytrade/regime.py`   `[SPEC]`
The heart of ALPHAZERO. Answers one question every 5 minutes: **what kind of day
is this right now?** Never what price will do next.

## Contract

```python
# daytrade/regime.py
from dataclasses import dataclass
from typing import Literal, Optional

Regime     = Literal["CONTINUATION", "MANIPULATION", "CONSOLIDATION"]
ExitPolicy = Literal["STATIC", "TRAIL_WIDE", "TRAIL_TIGHT", "SCRATCH_FAST"]
TimeBlock  = Literal["PREOPEN","OPEN_DRIVE","MORNING","MIDDAY","AFTERNOON","CLOSE"]

@dataclass(frozen=True)
class RegimeRead:
    ts: str                    # ISO, the bar close this read describes
    symbol: str
    regime: Regime
    confidence: float          # 0..1
    time_block: TimeBlock
    direction_of_flow: int     # +1 / 0 / -1 — where momentum IS, NOT a forecast
    exit_policy: ExitPolicy    # the ONLY thing downstream acts on
    evidence: dict             # every number that produced the call (see below)
    rule_version: str          # "regime-v1" — bump on ANY rule change, ever

def classify(bars_5m, bars_1m, context) -> RegimeRead: ...
```

`evidence` must carry every input that moved the decision, so a call can be
argued with after the fact and so the scorecard (spec 004) can regress on it:
```
{ or_high, or_low, or_range, price, vwap, vwap_side,
  ema9, ema21, ema50, ema200, ema_stack: "aligned_up"|"aligned_down"|"tangled",
  atr5, atr_daily, atr_expansion,        # atr5 now / atr5 median this block
  range_pct_of_atr,                      # realized block range / atr5
  volume, vol_avg_at_this_minute, volume_ratio,
  fvg_open: [{high, low, side, created_ts}],
  pools: {pdh, pdl, onh, onl, orh, orl},
  swept: [ "PDH", ... ],                 # pool taken out this block
  reclaimed: bool,                       # price back inside within N bars
  bars_since_sweep }
```

## Classification rules — v1 is RULES, not ML, on purpose
Readable and arguable. When it disagrees with Colin's eye, we point at the line
that's wrong and fix it. An ML version cannot be argued with, and we have no
labeled data yet — the scorecard is what creates that data.

```python
def classify(bars_5m, bars_1m, ctx) -> RegimeRead:
    ev = build_evidence(bars_5m, bars_1m, ctx)      # pure, no side effects
    tb = time_block(ctx.now_et)
    prior = TIME_PRIORS[tb]                          # dict regime -> weight

    score = {"CONTINUATION": 0.0, "MANIPULATION": 0.0, "CONSOLIDATION": 0.0}

    # --- CONTINUATION: range extending, one side of VWAP, stack aligned, vol OK
    if ev.price_outside(ev.or_high, ev.or_low):        score["CONTINUATION"] += 2
    if ev.vwap_side != 0 and ev.ema_stack != "tangled" \
       and sign(ev.vwap_side) == stack_sign(ev.ema_stack): score["CONTINUATION"] += 2
    if ev.atr_expansion > 1.1:                          score["CONTINUATION"] += 1
    if ev.volume_ratio >= 1.0:                          score["CONTINUATION"] += 1
    # today's tape (2026-08-03): pushed through the FVG straight to the EMA touch

    # --- MANIPULATION: a pool got swept and price came back. stops die here.
    if ev.swept and ev.reclaimed and ev.bars_since_sweep <= N_RECLAIM:  # N=3
        score["MANIPULATION"] += 4                      # strongest single signal
    if ev.wick_dominance > WICK_THRESH:                 score["MANIPULATION"] += 2
    if ev.volume_ratio > 1.5 and ev.range_pct_of_atr < 0.5:
        score["MANIPULATION"] += 2                      # volume spike, no follow-through

    # --- CONSOLIDATION: small range, chopping VWAP, stack tangled, thin volume
    if ev.range_pct_of_atr < RANGE_QUIET:               score["CONSOLIDATION"] += 2  # 0.6
    if ev.vwap_crosses >= 2:                            score["CONSOLIDATION"] += 2
    if ev.ema_stack == "tangled":                       score["CONSOLIDATION"] += 1
    if ev.volume_ratio < 0.8:                           score["CONSOLIDATION"] += 1

    for r in score: score[r] *= prior[r]                # time-of-day weighting
    regime, conf = argmax_and_confidence(score)         # conf = top / sum(all)

    # SAFE DEFAULT — never act on a guess (doctrine + APEX #2)
    if conf < CONF_FLOOR:                               # 0.45
        regime, conf = "CONSOLIDATION", conf

    return RegimeRead(..., exit_policy=POLICY[regime], rule_version="regime-v1")
```

### Time-of-day priors (doctrine, tunable, never a verdict on their own)
```python
TIME_PRIORS = {
  "PREOPEN":    {...},                                   # 04:00-09:30
  "OPEN_DRIVE": {"CONTINUATION":1.3,"MANIPULATION":1.0,"CONSOLIDATION":0.8},  # 09:30-10:30
  "MORNING":    {"CONTINUATION":0.9,"MANIPULATION":1.3,"CONSOLIDATION":1.0},  # 10:30-11:30 the hard shift
  "MIDDAY":     {"CONTINUATION":0.8,"MANIPULATION":0.9,"CONSOLIDATION":1.3},  # 11:30-14:00
  "AFTERNOON":  {"CONTINUATION":1.1,"MANIPULATION":1.1,"CONSOLIDATION":0.9},  # 14:00-15:30
  "CLOSE":      {"CONTINUATION":1.0,"MANIPULATION":1.2,"CONSOLIDATION":0.9},  # 15:30-16:00
}
# TODO(tune): these six rows are Colin's screen-time turned into numbers. They
# are the FIRST thing the scorecard should re-fit once ~200 scored blocks exist.
# Do not hand-tune them after a losing day — that is rule-changing-after-a-loss.
```

### Regime → exit policy (the entire payoff)
```python
POLICY = {"CONTINUATION": "TRAIL_WIDE",     # let it run, don't choke the move
          "MANIPULATION": "TRAIL_TIGHT",    # assume the sweep is coming for us
          "CONSOLIDATION": "STATIC"}        # leave levels alone, don't churn
# SCRATCH_FAST is NOT emitted by v1. It is reserved for a confirmed sweep
# against an open position — see spec 003, marked [SKETCH] there for a reason.
```

## Definition of done
1. `classify` runs over every 5-minute block of the last 60 NVDA sessions via the
   backtest bench (spec 005) and dumps `data/regime_labels.csv`.
2. Colin reviews a sampled table. Where it disagrees with his read, the failing
   rule is identified by line and fixed. **His eye is ground truth for v1.**
3. Only after it matches his read does it get wired into anything live.

## Hard constraints
- Pure function. No I/O, no network, no clock reads — `ctx` carries the time.
- Never sizes, never enters, never overrides survival's NO_TRADE.
- Fail loud on malformed bars. A gap in the data is a raise, never an assumption.
- `rule_version` bumps on ANY rule change so the scorecard never mixes versions.

## Deliberately NOT in v1 — `[SKETCH]`, needs a real planning pass
- **Multi-timeframe agreement** (does the 4h regime confirm the 5m?). Obvious next
  step, but it multiplies the state space and we have zero scored data yet.
- **Order block / breaker detection.** Colin ranks these below FVG for a reason;
  the definitions are fuzzier and every ICT source draws them differently. Needs
  its own spec with Colin defining the exact construction rules.
- **Cross-asset context** (SPY/QQQ regime as a prior on NVDA's). Almost certainly
  real and useful. Not until single-asset works.
- **Learned weights replacing the hand-scored table.** This is the actual
  walk-forward ALPHAZERO. It cannot start until the scorecard has months of
  labels. Writing it earlier would be fitting noise — the exact thing SANITY_AUDIT
  exists to prevent.
