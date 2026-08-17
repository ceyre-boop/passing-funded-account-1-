# Spec 024 — The Discipline Layer

## Context

The knee-MRI project's review names six disciplines that decided outcomes
there: pre-registration, out-of-sample purity, deterministic caps/bands/
co-drift, a reject-only referee, first-class sim→live gap tracking, and
evidence-ranked promotion — plus shadow mode to steal wholesale. Exploration
confirmed the operator (spec 023) has none of the first three mechanically
(zero `expected_r` constructs anywhere; `build_packet` has no decision-
timestamp bound; no emission caps), while the repo already owns most of the
needed machinery unwired: `portfolio_guard.check` (G001–G006, upstream-
supplied groups), `shadow.py`'s containment idiom, `regret.py`'s
`slippage_basis="paper"` honesty, and 021's repro-gap precedent. The model is
the cheap part; this spec builds the pre-committed gates allowed to reject it.

## Deliverables (build order)

1. **`specs/024_DISCIPLINE_LAYER.md` + `specs/024_MUTATION_LOG.md` skeleton** —
   spec first (repo law). Invariants I11–I22 continue 023's numbering; all
   thresholds pre-committed in the spec, never tuned later:
   `MAX_DIRECTIVES_PER_SESSION=3`, `CODRIFT_N=2`, `CODRIFT_WINDOW_MIN=30`,
   `GAP_TRIPWIRE_R=0.05`, R band bounds [-5,+5], yield-decay = 3 declining
   sessions.

2. **Point-in-time packet (I14)** — `build_packet(symbol, *, as_of=None)`:
   bars `df[df.index <= as_of].tail(24)` (idiom already at
   `alpha_operator.py:684-693`), headlines filtered by `published_utc <= as_of`
   (missing timestamp = excluded loudly), events by `ts`; meta gains `as_of` +
   `max_data_ts`; `bar_age_min` relative to `as_of`. Sealed record gains
   `packet_as_of`/`packet_max_data_ts`. Test reconstructs the packet at T and
   asserts nothing > T, plus byte-identical reconstruction.

3. **Pre-registration (I11–I13)** — `OperatorRead` gains `expected_r_low/high`
   (schema-bounded) and `invalidation_predicates` (closed vocabulary:
   `close_below|close_above|range_exceeds_r` — machine-checkable, unlike the
   free-text `invalidators` which stays for humans). Non-ABSTAIN without an R
   band raises. Block sealed in the record before the directive. Resolver
   evaluates predicates + R-in-band over the window and appends a separate
   `{"kind": "prereg_score"}` row — 017's `Resolution` dataclass untouched.

4. **Shadow soak (I22)** — `run --shadow`: full packets/records/forecasts,
   `_write_directive` skipped entirely, record marked `"shadow": true`
   (shadow.py's serialization-marker idiom). Shadow verdicts still feed
   four_books' veto simulation — the vocabulary gets exercised for weeks
   before any fill depends on it.

5. **Emission gates (I15–I17)** — deterministic, pre-directive, in
   `_seal_and_emit`: session directive cap (judgment sealed verbatim,
   emission refused with `"emission_refused": {"gate": ...}` — the
   `suppressed_to` pattern); co-drift pause when a TIGHTEN/EXIT would be the
   2nd in 30 min across symbols sharing a group in
   `data/daytrade/operator/correlated_groups.json` (upstream-supplied,
   matching `portfolio_guard`'s contract; file absent = inert — correct for
   single-symbol NVDA, testable now via multi-symbol sandbox records).
   `portfolio_guard.check` wired **advisory-only** into the sealed record
   (`portfolio_advisory`) — Gate 7 says guards enforce last; a test proves a
   violation never blocks emission.

6. **`daytrade/gap_log.py` (new, I18–I20)** — sim→live gap as a first-class
   record: match trade_ids present in BOTH the 022 sim and paper ledger dirs;
   `classify_drift` = pre-committed constant/widening/collapsing (last-5 vs
   prior-5 mean |delta|, <10 rows = "insufficient", labeled never zero);
   `promotion_tripwire` blocks the `grade` report's promotability when
   widening beyond band. 017's `promotion_decision` itself is untouched —
   the tripwire wraps the report.

7. **Referee-first + yield curve (I21)** — four_books: veto listed as
   `"role": "primary"`, full as `"unbuilt"`; per-session
   `data/daytrade/operator/yield.jsonl` row (`veto_r - baseline_r`);
   `grade` prints the yield trajectory (`+0.011 → +0.002 → +0.000` readable
   from data) with a `YIELD_DECAY` warning on 3 monotone declines.

8. **Mutation rows** for every invariant (remove the `<= as_of` filter, drop
   the R-band requirement, disable the cap, flip co-drift comparison, break
   the tripwire, un-mark shadow) → `specs/024_MUTATION_LOG.md`; full suite.

## Reused, not rebuilt

- `portfolio_guard.check` + `PortfolioLimits`/`PositionSnapshot` (`daytrade/portfolio_guard.py:38-150`)
- shadow marker + containment idiom (`daytrade/shadow.py:36-54`)
- paper-basis honesty (`daytrade/regret.py:26-42`), 021 repro-gap precedent
- 023's sandbox test fixture, `_append_jsonl` fsync, `_read_jsonl` fail-loud

## Out of scope (declared, not deferred silently)

Correlation *detection* (groups stay a supplied config); live fills (013's
job); full-book ENTER proposals (no ratified schema); enforcing
LOCKOUT/FLATTEN_ALL (advisory only); any change to 017 gates, the 018
envelope, or the authority cap.

## Verification

`python3 -m pytest daytrade/ -q` (all new I11–I22 tests + existing 266);
mutation series per the log; then a live `--shadow --force bar` smoke run
confirming: record sealed with pre_registration + packet_as_of, zero bytes
written to directives.json, and `grade`/yield output rendering.
