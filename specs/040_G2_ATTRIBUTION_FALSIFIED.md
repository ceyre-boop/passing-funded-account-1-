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

---

# 2026-08-27 (later) — the 2016 residual trades, named individually. CB
fabrication tested causally, not just by proximity: it explains almost none
of them. `[FINDING]` `[UNRATIFIED]`

Follow-up to the section above. Two things were asked: name the ~24 trades,
and test whether they are the CB-fabrication's fingerprint. Both done. The
leading hypothesis does **not** survive a direct causal test, even though a
naive proximity check looks like it might support it at first glance.

## Zeroth finding: the pinned "387/25, 2016:24" count does not reproduce,
even nominally holding code and cache fixed

Re-running the identical patch recipe from the section above (byte-verified
`macro_nominal`/`macro_pub` trees copied in from the worktree that built
them, `diff -rq` clean both directions) against **this worktree's current
HEAD** gives a different answer:

| tree | that section's result | this session's result (same tree, current HEAD) |
|---|---|---|
| `macro_nominal` | rig 387, missing 25, residual 25 (2016:24, 2017:1) | rig 336, missing 83, residual 83 (2016:21, spread 2015-2024) |
| `macro_pub` | rig 391, missing 33, residual 33 (2016:28, rest scattered) | rig 340, missing 91, residual 91 (2016:25, spread 2015-2024) |

Cause, found by inspection of the intervening commit: `1c904b8` ("JP CPI is
real, eval/funded sizing split, G2 confirmed falsified — and my clobber"),
committed after the section above, (a) replaced `JP_cpi.parquet`'s fabricated
flat 3.2 with real e-Stat data, and (b) restored `CARRY_RATE_VINTAGE`
routing that a merge had silently clobbered back to a no-op (the exact defect
finding-2 of the section above flagged as unresolved). Both changes alter
what the rig actually reads, so the trade count moved. This was **not**
caused by anything this session touched — `git diff --stat` on tracked files
is empty throughout (only `data/cache/macro_nominal/` and `data/cache/
macro_pub/`, both untracked, were added, same convention as the section
above).

Trying to go one step further and reproduce the pinned number by running the
diagnostic in the *actual* worktree that built it (`agent-acb0bc8919fb8cc17`,
still at the commit the section above used) makes it **worse**, not better:
`sovereign.forex.data_fetcher`'s JP-CPI path in that older code calls FRED
live, and the call now fails (`FRED CPI history JP: Bad Request. Invalid
value for variable series_id.`) — a live external dependency returning a
different answer at a different point in time, on the same commit, same
cache trees. That run gives rig 288, residual 123 (2016:34). Three attempts,
three different residual counts (25 / 83 / 123), none matching either of the
other two, all on "the same" experiment.

**Read this as its own finding, independent of the CB question below: the
offline rig is not a stable function of (commit, cache tree) alone — it also
depends on live network state at run time through at least one code path.
That is a second, separate way G2's premise (a reproducing generator exists)
fails, on top of the attribution question.** Not investigated further here —
out of scope for this task, flagged for whoever owns G2's disposition.

Given the number itself will not hold still, the individual-trade question is
answered against **this session's own reproducible runs** (both trees,
current HEAD, verified twice each — with the CB layer as-shipped and with it
counterfactually re-enabled, below), not against the older pinned figure.
The old figure's exact "24" was never re-obtainable during this session.

## The 2016 residual trades, named

19 trades (`macro_nominal`) / 23 trades (`macro_pub`) remain unattributed in
2016 after full macro history removes truncation as a cause, **even with the
CB layer counterfactually turned back on** (see causal test below — this is
the harder, post-CB-test residual, not the softer as-shipped one). 18 of
those are common to both vintage trees:

| pair | entry | exit | direction | R | hold(d) |
|---|---|---|---|---|---|
| EURUSD=X | 2016-01-06 | 2016-01-15 | +1 | 1.2390 | 8 |
| USDJPY=X | 2016-01-05 | 2016-01-11 | -1 | 2.6279 | 5 |
| EURUSD=X | 2016-02-03 | 2016-02-09 | +1 | 3.1997 | 5 |
| USDJPY=X | 2016-02-03 | 2016-02-09 | -1 | 4.6066 | 5 |
| AUDUSD=X | 2016-02-03 | 2016-02-08 | +1 | 1.0691 | 4 |
| EURUSD=X | 2016-03-03 | 2016-03-09 | +1 | 1.5745 | 5 |
| AUDUSD=X | 2016-03-03 | 2016-03-09 | +1 | 2.6695 | 5 |
| EURUSD=X | 2016-05-03 | 2016-05-09 | +1 | -1.6675 | 5 |
| EURUSD=X | 2016-06-03 | 2016-06-09 | +1 | 2.9539 | 5 |
| AUDUSD=X | 2016-06-03 | 2016-06-09 | +1 | 4.8823 | 5 |
| EURUSD=X | 2016-07-05 | 2016-07-11 | +1 | -1.2863 | 5 |
| AUDUSD=X | 2016-07-05 | 2016-07-11 | +1 | 0.4784 | 5 |
| EURUSD=X | 2016-08-03 | 2016-08-08 | +1 | -1.5844 | 4 |
| EURUSD=X | 2016-09-05 | 2016-09-07 | -1 | -1.1360 | 3 |
| EURUSD=X | 2016-10-04 | 2016-10-10 | -1 | 0.1662 | 5 |
| EURUSD=X | 2016-12-05 | 2016-12-06 | -1 | -2.3776 | 2 |
| USDJPY=X | 2016-12-05 | 2016-12-09 | -1 | -0.6986 | 5 |
| AUDUSD=X | 2016-12-05 | 2016-12-09 | +1 | 0.3173 | 5 |

Tree-specific extras not in the table: `macro_nominal` also has
`AUDUSD=X 2016-08-03` (direction +1, R 1.0122, hold 5); `macro_pub` also has
`AUDUSD=X {2016-01-04, 2016-04-05, 2016-05-04, 2016-09-02, 2016-10-04}`. The
18-trade core is what survives both vintage choices and does not depend on
which one is "correct" — spec 039's open question about which vintage mode
actually reads different data (finding 1 in the section above) does not
touch this list.

Computed how: `scripts/diagnose_repro_gap.py`'s own matching + macro-ready
logic (tolerance ±3 days, unmodified), driven by an ad-hoc script kept out of
the repo (`scratchpad/dump_residual.py`, not committed, listed for
provenance only) that additionally dumps the missing sealed rows' `pair`,
`entry_date`, `exit_date`, `direction`, and `R = pnl_pct/risk_pct` from
`data/proof/backtest_trades_v015_2015_2024.csv` — same file, same formula
`load_sealed()` already uses, read-only, nothing in `data/proof/` touched.

## The CB-fabrication hypothesis, tested two ways

**Way 1 — proximity (what the assignment asked first).** Nearest
`cb_decisions.json` date to each trade's entry, `macro_nominal` tree:

| population | n | mean gap (days) | within 3 days |
|---|---|---|---|
| residual 2016 trades | 21 | 1.2 | 21/21 (100%) |
| residual non-2016 trades | 62 | 3.4 | 27/62 (44%) |
| **base rate: ALL sealed 2016 trades** | 55 | 1.0 | 54/55 (98%) |
| **base rate: matched/reproduced 2016 trades** | 34 | 0.8 | 33/34 (97%) |

(`macro_pub` tree: 25/25 (100%), 66 non-2016 at 44%, base rate 54/55 (98%),
matched base rate 29/30 (97%) — same shape.)

Read naively this looks like support: residual 2016 trades are close to CB
dates. **It is not support** — the base rate is just as close. Trades that
DID reproduce in 2016 sit at the same ~97-100%-within-3-days rate as the ones
that didn't. Every 2016 trade, matched or missing, tends to land near a
`cb_decisions.json` date, because that file has 622 distinct dates over a
~10-year span (roughly one every 4-5 calendar days) concentrated on
month-boundaries — and the sealed carry engine's entries are themselves
heavily month-anchored (most of the table above enters day 3-9 of a month).
Two independently month-anchored series will look "clustered" against each
other regardless of any causal link. **Falsified by its own base rate,
exactly as the task's null-hypothesis instruction anticipated** — proximity
alone cannot distinguish reproduced from unreproduced 2016 trades.

**Way 2 — causal (stronger, not requested but decisive, so run anyway).**
`sovereign/forex/entry_engine.py`'s `CB_LAYER_DISABLED = True` means the
as-shipped rig's `CBEventTrigger` always returns zero events — CB
literally cannot produce a trade right now, by construction, independent of
proximity. So the honest test is: monkeypatch `CB_LAYER_DISABLED = False`
in-process only (imports patched at runtime; `entry_engine.py` on disk never
touched, `data/cache/cb_decisions.json` read as-is, still the fabricated
file, exactly as it was when the sealed 411 was generated) and re-run the
diagnostic. If CB explains the 2016 gap, most of the 2016 residual should
disappear when CB is put back.

| tree | CB disabled (as-shipped) | CB counterfactually enabled | 2016 recovered |
|---|---|---|---|
| `macro_nominal` | residual 83 (2016: 21) | residual 33 (2016: 19) | **2 of 21** |
| `macro_pub` | residual 91 (2016: 25) | residual 42 (2016: 23) | **2 of 25** |

Compare non-2016: `macro_nominal`'s non-2016 residual drops from 62 to 14
(48 recovered — 2022 alone goes from 22 missing to 0); `macro_pub`'s drops
from 66 to 19 (47 recovered — 2022 goes from 23 to 1, 2023 from 10 to 0,
2024 from 4 to 0). **CB re-enablement is a real, large effect for 2017-2024.
It is almost nothing for 2016** — 19-23 of the year's residual trades remain
exactly as unreproduced with the fabricated CB layer turned back on as
without it.

## Conclusion on the leading hypothesis

**Falsified for 2016, specifically, by the causal test — not merely
under-evidenced.** The CB-fabrication is real and the quarantine decision
stands on its own evidence regardless of this result (47.7% ±1bp moves,
16.5% weekend dates, no builder — see `cb_decisions.FABRICATED.md`), but it
is not what is generating the 2016 reproduction gap. Whatever 2016 needed
that the honest rig lacks, it is not central-bank surprise events — enabling
that layer recovers the *other* years' gap almost completely and 2016's gap
almost not at all. That is the opposite of what the leading hypothesis
predicted, and it is a controlled counterfactual, not a correlation.

The genuinely open question is the one `diagnose_repro_gap.py`'s own
docstring already named and parked on the restore list: "data vintage and
engine drift vs the `~/quant` generator... not separable from this
checkout." The 18-trade core table above is heavily month-anchored (entries
cluster on day 2-9 of nearly every 2016 month) in a way that superficially
resembles the CB fabrication's own monthly-resample signature — but Way 2
shows directly that resemblance is not the mechanism, since restoring the CB
layer does not touch these trades. A genuinely different monthly-cadence
signal (a macro rate/CPI differential re-evaluating on a monthly release
schedule is the obvious candidate, unverified here) or an engine/data-vintage
difference from the `~/quant` generator remain live, undistinguished
candidates. 2016's real macro drama (BOJ negative rates 2016-01-29, Brexit
referendum 2016-06-23) sits near several of these entries by date but no
causal test was run against it in this session — flagged as the next
candidate, not concluded.

## What the sealed 411 actually is, given both findings

Two independent things are now true of it, and neither is fixed by this
session:

1. **It depends on a fabricated input for a large share of its 2017-2024
   trades.** The causal test above shows CB re-enablement recovers ~48-49 of
   the ~83-91 as-shipped-residual trades outside 2016 — those specific sealed
   entries exist because `CBEventTrigger` fired on fabricated data, and the
   quarantine (correctly) makes them permanently unreproducible by the honest
   rig. That is a real, load-bearing dependency on the fabricated file for
   part of the sealed record, not merely a correlation with it.
2. **Its 2016 trades are not explained by that same dependency, and remain
   genuinely unreproduced for an unknown reason** — 18-23 trades per vintage
   tree that neither macro-history truncation nor the fabricated CB layer
   accounts for. This is the part of G2's gap that stays a real open question
   after this session, not the part that was already closed by the CB
   quarantine.

Combined: **the sealed 411 is not a reproducing record under any
configuration tried in this session** — not the as-shipped rig (CB
correctly disabled), not the CB-restored counterfactual (recovers 2017-2024
but not 2016), and not stably even across two runs of the "same" older
commit (the FRED-dependency finding above). None of this flips G2 — that
stays Colin's ruling — but "the residual has a single named cause" is not a
description that survives this session's testing, and "CB fabrication
explains the gap" specifically does not survive it either.

## Provenance

Everything above: read-only against `data/proof/backtest_trades_v015_2015_2024.csv`
and `data/cache/cb_decisions.json` (fabricated, as-is, never modified or
un-quarantined); `data/cache/macro_nominal/` and `data/cache/macro_pub/`
copied in from the worktree that built them (`agent-acb0bc8919fb8cc17`),
verified byte-identical both directions with `diff -rq` before use, left in
place as new untracked files (same convention as the section above, nothing
under `data/proof/`, `SEALS.json`, or `carry_buy_gate_state.json` touched).
CB-layer counterfactual done by runtime monkeypatch of the imported module
attribute only (`sovereign.forex.entry_engine.CB_LAYER_DISABLED`); the file
on disk is unchanged and `git diff --stat` on tracked files is empty for the
whole session. Ad-hoc analysis script kept in the session scratchpad, not
committed to this repo.

## Verification

`python3 -m pytest scripts/ daytrade/ sovereign/ execution/ -q --deselect
daytrade/test_alpha_operator.py::test_i14_packet_point_in_time` errors at
collection (`scripts/test_paper_tsmom_daily.py` imports a module,
`paper_tsmom_daily`, that does not exist anywhere in this worktree — the
`verify-referenced-scripts-exist` pattern this repo has hit before). Adding
`--ignore=scripts/test_paper_tsmom_daily.py` to get past that:
**1 failed, 965 passed, 8 skipped, 1 deselected** — the one failure is
`scripts/test_wiring_audit.py::test_no_new_unexplained_disconnects`,
flagging that same missing module as a new unallowlisted broken import. Both
failures point at the same pre-existing, unrelated defect (a referenced
script that was never committed); `git diff --stat` for this session is
empty on tracked files, so neither is this session's doing. Not fixed here —
out of scope for this task.
