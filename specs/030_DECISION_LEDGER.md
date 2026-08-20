# 030 — THE DECISION LEDGER `[SPEC]`

**Component:** `daytrade/decision_ledger.py`, `data/daytrade/decision_ledger.jsonl`,
capture + catch-up wired into `daytrade/operator_tick.sh`
**Status:** `[SPEC]` — written 2026-08-19 alongside the code (diagnostic
substrate, no live-path change, no model calls).
**Origin:** architecture review 2026-08-19 — build a market *learning* system,
not a mechanism-confirmation system. Its ordered prescription: **first fix the
morning observation channel, then build an immutable feature/decision ledger,
then allocate a bounded exploration budget.** This card is steps one and two.

## The problem it solves, proven the expensive way

MECH-006 (the veto claim) returned `EMPTY_CHANNEL`: 22 sealed judgments, and
**zero** in the 09:30–11:00 ET window where the entry decision happens. Not
underpowered — unobserved. A system that is not watching the decision channel
cannot learn from it, and no quantity of soak days changes that.

The separation this card makes: **judgment is expensive** (needs a live model,
costs money, needs the host awake) while **observation is cheap** (needs only
discipline). Conflating them meant losing both whenever the machine slept.

## Design

**Two capture modes, never conflated.**

| mode | what it claims | when |
|---|---|---|
| `live` | what the machine actually saw, when it saw it | every tick |
| `backfill` | what was KNOWABLE at T — silent about what any model saw, because nothing was running | catch-up + history |

The honesty boundary is load-bearing: backfilled rows are valid evidence for
*"what did the market look like"* (which conditional mechanisms need) and are
never evidence for *"what did the system observe"*. Every row carries its
`source`, and pooling them for the second question is a category error.

**The morning channel is repaired two ways**: the tick captures live, and it
also runs `backfill --days 1`, so a host that slept until noon still has its
09:35–11:00 decision points reconstructed the moment it wakes — from bars with
their own timestamps, under the same point-in-time rule as spec 024's I14
(nothing newer than T enters). Headline counts are `live`-only and recorded as
null in backfill rather than guessed.

**Immutability is a hash chain**, not a promise: each row carries the sha256 of
the row before it, so a rewritten past breaks the chain rather than passing
unnoticed. Verified by the suite (same guard shape as 028/029).

**A small explicit regime vocabulary** — `TREND_UP · TREND_DOWN · COMPRESSION ·
HIGH_VOL · LOW_VOL · GAP_UP · GAP_DOWN · EVENT_DAY · UNREADABLE`. These are
DESCRIPTIVE LABELS on observed state, deliberately **not** spec 001's REGIME
decision rule, which stays unbuilt and blocked. Nothing here feeds a policy;
the vocabulary exists so every mechanism can state the domain it expects to
hold in, and so transfer can be tested as *invariance across appropriate
contexts* rather than sameness everywhere.

## Recorded per decision point

`symbol · as_of · session · et_time · max_data_ts · last · day_open ·
prior_close · gap_pct · or_high · or_low · or_complete · or_state ·
tr5 · tr20_median · compression · trend_pct_vs_ma20 · volume_last ·
headlines_last_hour · regimes[] · source · seq · prev_sha256 · row_sha256`

## Invariants

- I36: the chain verifies; a rewritten or tampered row breaks it loudly.
- I37: no row contains data newer than its own `as_of` (I14 discipline).
- I38: `live` and `backfill` rows are distinguishable in every row.
- I39: capture makes no model call and costs nothing, so it can run on every
  tick regardless of the spend cap or the stale gate.
- I40: backfill is idempotent — re-running adds no duplicate decision point.

## First population

40 sessions × 7 decision points × 16 symbols = **4,480 decision points**, all
inside the 09:35–11:00 entry window, chain intact. Regime distribution on the
NVDA slice: HIGH_VOL 118, GAP_DOWN 105, GAP_UP 98, TREND_DOWN 84, TREND_UP 73,
UNREADABLE 25, EVENT_DAY 14, COMPRESSION 5, LOW_VOL 5.

## Out of scope

Any decision, policy, or model call; spec 001's regime classifier; the
exploration budget (step three of the review's prescription, which needs this
substrate to exist first); pooling backfill rows into "what the system saw".
