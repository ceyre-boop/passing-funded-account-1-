# 006 — ALPHAZERO v2  +  007 — MORNING BRIEF   `[SPEC]` / `[SKETCH]`

---

# 006 — ALPHAZERO v2  `daytrade/alphazero_bias.py`   `[SPEC]`
Two jobs, one process, one output file. The contract stays frozen; it only gains
fields, so nothing downstream breaks.

```jsonc
// data/daytrade/bias.json — additive only, never remove a key
{
  "ts": "2026-08-04T14:35:00Z",
  // --- job 1: news/sentiment (exists, still placeholder-labeled) ---
  "bias": 0.35,                 // -1..+1, what shareholders seem to think today
  "urgency": "none",            // none | tighten | exit | stale
  "n_headlines": 12,
  // --- job 2: regime (NEW — imports spec 001) ---
  "regime": "CONTINUATION",
  "confidence": 0.71,
  "exit_policy": "TRAIL_WIDE",  // <- the only new field the runner acts on
  "evidence": { ... },          // full RegimeRead.evidence, for the log
  "rule_version": "regime-v1",
  "model": "keyword-valence v1 (placeholder) + regime-v1 (rules)"
}
```

**Loop:** every 5 min — pull headlines, score bias/urgency (existing), pull the
latest 5m bars, call `classify()`, write the merged file atomically (write temp,
rename — a half-written bias.json read mid-flight is a silent corruption).

**Runner change:** reads `exit_policy` alongside `urgency` and puts it on
TradeState. That's it. One line.

**Precedence stays:** `urgency` outranks `exit_policy`. A news shock flattens the
position regardless of how good the regime looks.

**Staleness:** if the last successful write is older than N minutes, the runner
treats `exit_policy` as `STATIC` and says so loudly on the line. A stale regime
read must never keep steering the trail.

### `[SKETCH]` — the actual learning loop, deliberately unwritten
The name "ALPHAZERO" is a promise this file does not yet keep, and it is worth
being precise about the gap rather than papering over it:

AlphaZero worked because chess is a closed simulator with a clean win condition —
it generated infinite fresh games and got ground truth for free. Markets give us
neither. We cannot play the market against itself. What we CAN do — and the quant
repo already proves the throughput exists — is replay enormous amounts of history
cheaply, and grade a *proxy* win condition: regime-call accuracy (spec 004).

So the real loop, when it gets designed, is: rolling-window refit of the
classifier's weights → strictly out-of-sample evaluation on the next window →
log the accuracy curve per iteration → keep the version that beats both baselines
out-of-sample, discard the rest. That is walk-forward learning wearing the
AlphaZero name honestly.

**Prerequisites, all of them, before this gets a spec of its own:** ≥3 months of
scored blocks · a stable `grade_version` · a pre-registered train/test protocol
written BEFORE the first fit · both baselines reported every iteration. Skipping
any of these produces a number that looks like 78/100 and means nothing —
we already lived that once (SANITY_AUDIT.md), and the whole point of this repo
is not living it twice.

---

# 007 — MORNING BRIEF  `daytrade/brief.py`   `[SKETCH]`
The standing job from the doctrine — "keep me in touch with what every trader is
looking at" — made mechanical. Runs ~08:45 ET.

**Assembles:** overnight range and gap vs prior close · today's calendar releases
with times · watchlist headlines with the bias score · yesterday's regime summary
and how the day resolved · current streak, cushion, and consecutive-red count ·
the survival math for today's goal (spec 002) · the prior-day pools that are now
liquidity (PDH/PDL/ONH/ONL) with levels.

**Output:** one short page Colin reads in 60 seconds, then writes `plan.json`
from — the human decides the levels, always. The brief informs the blueprint; it
never writes it.

**Why `[SKETCH]` and last:** it is convenience, not correctness. Every number in
it comes from components 1-6, so building it before they exist means building it
twice. It is also the piece most likely to grow scope quietly (charts, then a
dashboard, then a web app) — APEX #3 says backend provably correct first, and
this is precisely the temptation that rule was written for.

**Open question for Colin, needs an answer before it's built:** should the brief
be a file, a terminal printout, or a scheduled message that arrives on your phone
at 8:45 whether or not a session is open? The third is the most useful and the
most work. Not deciding now — noting that the decision exists.
