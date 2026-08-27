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

---

# 2026-08-27 — the diagnostic re-run confirms it: G2's attribution does NOT
survive. `[FINDING]` `[UNRATIFIED]`

Did what the previous section said was the right next step: re-ran
`scripts/diagnose_repro_gap.py` itself (not a hand-rolled rig) against
full-history macro caches, in a dedicated repro run (not the vintage A/B rig),
so the confound named above is now ruled out rather than merely flagged.

**Answer: no, the attribution does not survive. G2's own diagnostic reports
`PARTIAL` with a real, unattributed residual once the truncation it blames is
actually removed.**

## What was run

`sovereign/forex/data_fetcher.py`'s `ForexDataFetcher` reads rate/CPI history
from a module-level constant, `CACHE_DIR = .../data/cache/macro` — a plain
`Path`, not routed through `rate_vintage.macro_cache_dir()`. That matters
below. To run the diagnostic against genuinely full-history macro data, both
of `diagnose_repro_gap.py`'s own hardcoded macro paths had to be patched to
point at the same tree used for the actual trade generation:

1. `sovereign.forex.data_fetcher.CACHE_DIR` → `data/cache/macro_nominal`
   (then repeated against `data/cache/macro_pub`) — this is what
   `ForexBacktester` actually reads rates/CPI from when it builds trades.
2. `diagnose_repro_gap.MACRO_DIR` → the same tree — this is what the
   diagnostic's own `macro_ready()` attribution logic reads to decide whether
   a missing trade counts as "macro-history-truncation."
3. `diagnose_repro_gap.REPORT` → a **new** path
   (`data/agent/repro_gap_report_FULLHISTORY_{macro_nominal,macro_pub}.json`),
   never the pinned `repro_gap_report.json`. The original diagnostic code was
   imported and called unmodified (`diag.main()`) — no attribution logic,
   threshold, or matching tolerance was touched.

The `macro_nominal`/`macro_pub` trees (2014-01-01 → 2026-07-31, built by
`scripts/build_rate_vintages.py` under spec 039) did not exist in this
worktree; they were copied in from the sibling worktree that built them
(`agent-acb0bc8919fb8cc17`), verified byte-identical in date coverage before
use. Nothing under `data/proof/` or `SEALS.json` was touched.  A `git status`
diff of tracked files shows zero changes — only new, unpinned files were
added.

## Result

| run | rig n | missing | cache_gap | macro_history_truncation | unattributed residual | status |
|---|---|---|---|---|---|---|
| pinned `repro_gap_report.json` (2026-08-12, unchanged) | 296 | 126 | 0 | 126 | **0** | ATTRIBUTED |
| unmodified diagnostic, current sealed `data/cache/macro` (see staleness note below) | 395 | 32 | 0 | 32 | 0 | ATTRIBUTED |
| **full history, `macro_nominal`** | 387 | 25 | 0 | **0** | **25** | **PARTIAL** |
| full history, `macro_nominal` + `CARRY_RATE_VINTAGE=nominal` | 387 | 25 | 0 | 0 | 25 | PARTIAL |
| **full history, `macro_pub`** | 391 | 33 | 0 | **0** | **33** | **PARTIAL** |
| full history, `macro_pub` + `CARRY_RATE_VINTAGE=publication` | 391 | 33 | 0 | 0 | 33 | PARTIAL |

With genuine full-history macro data wired into the actual trade-generation
path, `macro_ready_by_pair` collapses to `2015-01-01` for every pair (latest
macro-cache start `2014-01-01` + 365d warmup) — there is no history left to
blame truncation on. The diagnostic's own arithmetic then reflects that: the
`macro_history_truncation` bucket goes to exactly 0, and every one of the
missing trades falls into `unattributed_residual` instead. Residual
concentrates in 2016 in both arms (24/25 and 28/33 respectively) — the same
year `specs/040`'s original experiment flagged. This is the causal test the
attribution never had: restoring the history it blames does not restore the
missing trades. **The stated cause is falsified, not merely under-evidenced.**

## The confound named in the previous section — ruled out

The earlier finding ran on a rig configured for the vintage A/B, so a confound
was possible. This run used `diagnose_repro_gap.py`'s own code, unmodified
apart from redirecting three path constants to point at the full-history tree,
and reproduces the same qualitative result (truncation → 0, real residual
persists, concentrated in 2016) independent of which full-history tree
(`macro_nominal` or `macro_pub`) or vintage mode was used. Two different
full-history trees, two different vintage-exclusion settings, same answer.
That is the opposite of a fragile result.

## Two side findings, reported because they bear directly on confidence here — neither ratified, neither acted on

**1. `CARRY_RATE_VINTAGE` does not change what data the reproduction rig
reads.** `sovereign/forex/data_fetcher.py`'s `CACHE_DIR` is a plain
module-level `Path` constant, never routed through
`rate_vintage.macro_cache_dir()`. Verified directly: running
`oos_campaign_test.get_trades()` three times, once per vintage mode
(`sealed`/`nominal`/`publication`, env var only, no path patch — i.e. exactly
how `scripts/run_vintage_ab.py` invokes it), produced **byte-identical rig
output all three times** (`n=395` in every mode) against the current sealed
tree. The switch only changes `signal_engine`'s NaN-exclusion behaviour
(`is_sealed()` in `_macro_signal_for_date`); it does not change which parquet
tree is actually read. That means `specs/039`'s reported per-mode trade-count
and avg-R split (296/288/292, differing avg R) could not have come from
reading genuinely different vintage data through this code path as currently
wired — something else must explain that split, and this document does not
resolve what. This is a `specs/039` question, out of scope here, but it
directly touches the confidence of the number this document leans on
("480 pair-months, zero sign flips" — computed by a different script,
`vintage_signal_delta.py`, not audited here). Flagged, not adjudicated.

**2. `data/agent/repro_gap_report.json` is stale relative to the cache it
describes, independent of the truncation question.** `data/cache/macro`
itself was extended by commit `6ab3505` ("Carry macro pipeline: nine preflight
failures down to two, and both are rulings", 2026-08-26 23:57) — the sealed
tree now starts 2014/2015/2016 per pair, not 2019/2020 as the report and the
diagnostic's own docstring still describe. `repro_gap_report.json` is dated
2026-08-12 and was never regenerated against the new cache. Simply re-running
the unmodified diagnostic against the **current, still-"sealed"** tree (no
patching at all) already gives a materially different result — `rig n=395`,
`missing=32`, not the pinned `296`/`126` — though it still reports
`ATTRIBUTED`/`residual=0` under the sealed convention. G2's live GREEN state
is resting on a report that misdescribes the current state of its own input
data, on top of the attribution question above.

## What the honest G2 status should be

Per the diagnostic's own stated convention (`scripts/diagnose_repro_gap.py`'s
module docstring): `PARTIAL` means residual remains and *"G2 stays RED (a
waiver via `decision_logger` is the only other path to green, and this script
never writes one)."* That is what both full-history runs report. Combined
with finding 2, the pinned `ATTRIBUTED` report is both stale (wrong numbers
for the tree it claims to describe) and, once the tree it should describe is
actually used, no longer ATTRIBUTED at all.

This document does not touch `data/agent/carry_buy_gate_state.json`. Flipping
G2 is a ruling through `decision_logger`, not an agent's call — Colin decides.
The evidence: `data/agent/repro_gap_report_FULLHISTORY_macro_nominal.json`,
`data/agent/repro_gap_report_FULLHISTORY_macro_pub.json` (both new files,
beside the pinned report, not replacing it).

## Verification

`python3 -m pytest scripts/ daytrade/ sovereign/ execution/ -q --deselect
daytrade/test_alpha_operator.py::test_i14_packet_point_in_time` →
935 passed, 1 skipped, 5 failed, 1 deselected. The 5 failures are all in
`daytrade/test_load_partial_session.py`, pre-existing and unrelated: a
hardcoded fixture assumption ("2026-08-21 is a full NVDA session, 78 bars")
no longer holds against the live cache (62 bars, a real partial/half-day
session). No file this investigation touched is anywhere near that test's
path, and `git diff --stat` on tracked files is empty for this entire
session — the failures pre-date this work.
