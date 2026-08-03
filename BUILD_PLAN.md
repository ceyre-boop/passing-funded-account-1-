# BUILD_PLAN.md — the regime-first cockpit, all parts
2026-08-03 evening, after day 1 (green, +$300, target hit, no second leg needed).
This supersedes the old 4-item build order in ARCHITECTURE.md. Same architecture,
correct emphasis: **logic-based, not edge-based.** We do not predict direction.
We classify what the market is typically doing at this time, and we change how we
EXIT based on that classification.

## The thesis, stated so it can be built
Every lens traders use — volume, FVG, EMA/SMA, order blocks, breakers, ATR —
says the same thing in different dialects. Reduced: **at this time of day, the
market is doing one of three things — continuation, manipulation, or
consolidation.** Recognizing which one is live is not an edge claim about
direction; it is a claim about *what kind of day this is*, and that changes the
exit policy. Liquidity sweeps happen at the seams between the three.

Same statistical logic as the ladder: don't model a million factors, model the
mean — what typically happens over many trials — and survive the variance.

**Plan for the worst, hope for the best.** Before every trade the system must
answer: if this loses, are we still on track to (1) not lose, and (2) get back
to the daily goal tomorrow? That answer is computed BEFORE entry, not after.

---

## COMPONENT 1 — REGIME CLASSIFIER  `daytrade/regime.py`  ← build first
The heart. Everything else is plumbing around this.

**Input (all cheap, all live):** 1m/5m bars for the session, prior day's range,
opening range, ATR(14) on 5m and daily, current time ET, session VWAP, volume vs
20-day average-at-this-minute, EMA/SMA stack (9/21/50/200), FVG list, prior
day H/L and overnight H/L (liquidity pools).

**Output — one dataclass, frozen contract:**
```
RegimeRead(
  ts, symbol,
  regime: 'CONTINUATION' | 'MANIPULATION' | 'CONSOLIDATION',
  confidence: 0..1,
  time_block: 'PREOPEN'|'OPEN_DRIVE'|'MORNING'|'MIDDAY'|'AFTERNOON'|'CLOSE',
  direction_of_flow: +1|0|-1,      # NOT a prediction — where momentum IS
  evidence: {atr_pct, vwap_side, ema_stack, volume_ratio, range_pct_of_atr,
             fvg_open_count, swept_pools: [...]},
  exit_policy: 'STATIC' | 'TRAIL_WIDE' | 'TRAIL_TIGHT' | 'SCRATCH_FAST'
)
```

**Classification logic (rules, not ML — v1 must be readable and arguable):**
- CONTINUATION — range extending beyond opening range, price on one side of
  VWAP, EMA stack aligned, volume ≥ average, ATR expanding. Today's tape: pushed
  through the FVG straight to the EMA touch.
- MANIPULATION — sweep of a known pool (PDH/PDL/ONH/ONL/ORH/ORL) followed by
  rejection back inside within N bars; wick-dominant candles; volume spike with
  no range follow-through. This is where stops die — the exit policy must react.
- CONSOLIDATION — realized range < X% of ATR over the block, price oscillating
  across VWAP, EMA stack tangled, volume below average.
- Ties/low confidence → CONSOLIDATION (the safe default: nothing special happens).

**Time-block priors, from doctrine, explicit and tunable:**
09:30-10:30 OPEN_DRIVE (continuation most likely) · 10:30-11:30 MORNING
(manipulation most likely — the hard shift) · 11:30-14:00 MIDDAY (consolidation)
· 14:00-15:30 AFTERNOON (continuation or manipulation) · 15:30-16:00 CLOSE.
Priors are a starting weight, not a verdict; live evidence overrides.

**Regime → exit policy mapping (this is the whole point):**
| regime | what STOCKFISH does differently |
|---|---|
| CONTINUATION | TRAIL_WIDE — let it run, wide trail, don't choke the move |
| MANIPULATION | TRAIL_TIGHT — halve the trail, tighten toward breakeven early, assume the sweep |
| CONSOLIDATION | STATIC — leave SL/TP alone all day, don't churn on noise |
| low confidence | STATIC — never act on a guess |

**Definition of done:** classify every 5-minute block of the last 60 sessions on
NVDA/SPY, dump a table, and Colin eyeballs it against his own read. If it
disagrees with his eye, the RULES are wrong and get fixed — his read is ground
truth for v1.

---

## COMPONENT 2 — SURVIVAL PLANNER  `daytrade/survival.py`
Runs BEFORE entry. Plan for the worst, in numbers.

**Input:** account size, daily goal (1-2% of account — one number per account,
same every day), current campaign state (cumulative P&L, drawdown cushion
remaining, consecutive-loss count, days elapsed), proposed trade risk.

**Output:**
```
SurvivalCheck(
  worst_case_balance, cushion_after_loss, days_to_recover_at_goal,
  still_on_track: bool, verdict: 'GO' | 'SIZE_DOWN' | 'NO_TRADE',
  size_multiplier: float,     # doctrine: after a loss this goes DOWN, never up
  reason: str
)
```
**Rules encoded:** daily goal = 1-2% of account, fixed per account, never raised
to catch up. After a loss, size multiplier decreases (bet 2 = smaller, wider).
Two consecutive red days → NO_TRADE, cooloff. If a loss would break the eval
cushion, NO_TRADE regardless of how good the setup looks.

**Definition of done:** prints the honest sentence before every entry — "if this
loses you're at $X, cushion $Y, back on track in Z days at goal."

---

## COMPONENT 3 — STOCKFISH v2  `daytrade/stockfish_exit.py` (extend, don't fork)
Still ONE `decide_exit`. Add exactly one input field and one behavior:
- `exit_policy: str` on TradeState (from RegimeRead, default 'STATIC').
- Trail multiplier becomes policy-driven: STATIC → no trailing at all after TP2
  (levels stay put); TRAIL_WIDE → 1.5× base; TRAIL_TIGHT → 0.5× base and TP1
  breakeven arms earlier (at half the TP1 distance); SCRATCH_FAST → exit at
  breakeven the moment it's available (reserved for a confirmed sweep against us).
- ALPHAZERO's `urgency` still outranks the policy. `exit` > policy, always.
- Everything else unchanged. Same ladder, same fail-loud, same purity.

**Definition of done:** the replay test grows four variants — one per policy —
and each produces a distinct, correct log.

---

## COMPONENT 4 — ALPHAZERO v2  `daytrade/alphazero_bias.py`
Now two jobs, one file, one output contract (bias.json stays frozen, gains fields):
1. **News/sentiment** (existing, still placeholder-labeled until calibrated).
2. **Regime feed** — imports COMPONENT 1, writes the current RegimeRead into
   bias.json so the runner gets regime + urgency in one read.

```
bias.json = {ts, bias, urgency, regime, confidence, exit_policy, evidence, model}
```
Runner reads urgency + exit_policy. Nothing else changes downstream.

---

## COMPONENT 5 — STREAK & FEEDBACK  `daytrade/streak.py` + `data/streak.json`
The negative-feedback loop, made real and measurable.

**Tracks live:** current green-day streak, longest streak, days since last red,
rolling 20-day win rate, cumulative R, distance to daily goal today, campaign
cushion. Feeds the survival planner (component 2) and gets displayed.

**The reward signal — this is the part that makes ALPHAZERO learn:** at each
session close, score the regime calls. Did the block classified CONTINUATION
actually extend? Did MANIPULATION actually sweep-and-reject? Write per-block
hit/miss to `data/regime_scorecard.csv`. That accuracy number — not P&L — is how
the pattern-recognition layer earns trust. P&L is noisy; regime-call accuracy is
directly measurable, block by block, hundreds of samples per week.

**Why this matters more than it sounds:** it is the only honest way to tell
whether the regime read is real skill or coincidence, and it produces data fast
(≈78 five-minute blocks per session) instead of waiting 50 trades.

---

## COMPONENT 6 — BACKTEST HARNESS  `daytrade/backtest.py`
Replays historical bars through the SAME `decide_exit`, `apply_action`, and
`regime.classify`. Emits the identical JSONL format as the live runner so the
two can be diffed byte-for-byte (APEX DoD). Also produces the regime scorecard
over history so component 1's rules get tuned on data Colin can inspect.

---

## COMPONENT 7 — MORNING BRIEF  `daytrade/brief.py`
The pre-open blueprint, automated. Runs ~08:45 ET: overnight range, gap vs prior
close, the calendar's releases, watchlist headlines, yesterday's regime summary,
current streak/cushion, and the survival math for today's goal. Output is a short
page Colin reads in 60 seconds, then writes `plan.json` from it. This is my
standing job from the doctrine, made mechanical.

---

## BUILD ORDER (each its own commit, per APEX)
1. **COMPONENT 1 regime.py** + its 60-session eyeball table. Nothing else matters until the classifier matches Colin's read.
2. **COMPONENT 2 survival.py** — cheap, pure arithmetic, immediately useful tomorrow morning.
3. **COMPONENT 3 stockfish v2** — one field, four policies, four replay variants.
4. **COMPONENT 5 streak.py + regime scorecard** — start collecting the feedback data immediately; it needs weeks of samples, so it starts early.
5. **COMPONENT 4 alphazero v2** — wire regime into bias.json.
6. **COMPONENT 6 backtest.py** — the DoD diff, and the tuning bench for component 1.
7. **COMPONENT 7 brief.py** — last, because it is convenience, not correctness.

## Standing rules for every component
- One implementation of every decision function. Import, never copy.
- Fail loud. Stale data = loud skip, never a guess.
- Regime read is ADVICE about exit policy. It never sizes, never enters, never
  overrides the survival planner's NO_TRADE.
- Every regime call gets scored. Unscored predictions are how systems lie.
- Colin's read is ground truth for v1 rule-tuning; the scorecard is ground truth
  after enough samples exist.
