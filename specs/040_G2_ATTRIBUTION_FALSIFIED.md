# 040 — G2's REPRO ATTRIBUTION LOOKS FALSIFIED  `[FINDING]` `[UNRATIFIED]`

**G2 is GREEN in the live buy gate right now. This document says it may not
deserve to be. An agent never flips a gate — Colin rules.**

Found 2026-08-26 as a side effect of the vintage A/B (`specs/039`), by an agent
that was not looking for it.

---

## What G2 claims

`data/agent/repro_gap_report.json`:

```json
{ "status": "ATTRIBUTED", "sealed_n": 411, "rig_n": 296,
  "attribution": { "macro_history_truncation": 126,
                   "unattributed_residual": 0 },
  "macro_ready_by_pair": { "EURUSD=X": "2020-12-31", ... },
  "macro_warmup_days": 365 }
```

The offline rig produces 296 trades against the sealed 411. G2 passes because
all 126 missing trades are attributed to one named cause — the macro caches
start 2019/2020, so pre-2021 signals cannot fire — with **zero unattributed
residual**. `specs/021` G2 requires exactly that: named causes summing to the
total, or a logged waiver.

## The experiment that contradicts it

Building the vintage A/B required macro caches spanning the full sealed period,
so both new trees (`data/cache/macro_nominal`, `macro_pub`) run **2014 → 2026**.
That removes the truncation G2 blames.

**The gap did not close.** 296 → 288.

- Per-year 2016: sealed **55**, rig **21**, with truncation and without.
- Macro signals *do* fire pre-2020 once the history exists — 11 in 2016 across
  pairs — they simply are not the missing trades.

If `macro_history_truncation` were the cause, restoring the history should
recover most of 126. It recovered approximately none.

## Why this matters more than the vintage question it fell out of

`specs/021` P7: the evaluator refuses to score any non-sealed series unless G1
passed in the same invocation. G2 is the gate that says the sealed record can be
*reproduced*. If its attribution is wrong, then:

- **the sealed 411 has no reproducing generator in this repo**, and
- G2's GREEN is resting on an explanation that a direct experiment falsifies.

G2 is currently one of three green gates. `NEXT.md` cites it as "G2 GREEN,
ATTRIBUTED with zero unattributed residual." That line is now in doubt.

## What is NOT established

- This does **not** prove G2 should be RED. It proves the *stated attribution*
  does not survive its own test. The 126 trades may have another single named
  cause that would satisfy G2 equally well.
- The experiment was run on a rig configured for a vintage A/B, not as a
  dedicated repro run. A confound is possible and has not been excluded.
- `scripts/diagnose_repro_gap.py` produced the original report and has not been
  re-run against the full-history caches. That is the obvious next step and it
  was deliberately not taken here — re-running a gate's own diagnostic and
  acting on the result inside the same session that found the problem is how a
  finding becomes a self-certification.

## The right order

1. Re-run `scripts/diagnose_repro_gap.py` against a full-history macro cache.
   Does it still report `macro_history_truncation: 126, residual: 0`? If yes,
   the diagnostic and the direct experiment disagree and the diagnostic is
   itself suspect.
2. If the attribution genuinely fails, G2 goes RED and the buy gate's verdict
   changes. That is a **ruling**, logged through `decision_logger`, not an edit.
3. Only then re-examine what the 126 trades actually are.

## Consequence to hold onto meanwhile

The vintage A/B in `specs/039` was measured on a 288-trade population
reproducing 285 of the sealed 411. Its conclusion (the edge survives) rests on
the signal-level result — 480 pair-months, zero sign flips — precisely because
that result is population-independent and therefore immune to this. **039's
conclusion does not depend on 040's resolution.** That was not luck; it is why
the signal-level table was computed.

## Provenance

Direct experiment, not inference: two macro cache trees built 2014→2026,
same rig, same gates, truncation removed, trade count observed. Reported by the
agent that hit it, which then explicitly declined to act on it.
