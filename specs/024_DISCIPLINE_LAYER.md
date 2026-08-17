# 024 — DISCIPLINE LAYER `[SPEC]`

**Component:** extensions to `daytrade/alpha_operator.py` and
`daytrade/four_books.py`; new `daytrade/gap_log.py`;
config contracts `data/daytrade/operator/correlated_groups.json` and
`limits.json`.
**Status:** `[SPEC]` — written 2026-08-17, before the code.
**Depends on:** 023 (operator), 017 (ledger/promotion), 014 (guards, shadow
idiom, regret basis), 022 (execution ledger dirs), 021 (repro-gap precedent).
**Origin:** six disciplines from the knee-MRI CV project's review — the
pattern underneath all of them: *the model is the cheap part; outcomes are
decided by pre-committed gates allowed to reject the model.*

## Pre-committed constants (sealed here, never tuned after data is seen)

| constant | value | meaning |
|---|---|---|
| `MAX_DIRECTIVES_PER_SESSION` | 3 | emitted directives per symbol per ET day |
| `CODRIFT_N` | 2 | Nth same-group TIGHTEN/EXIT in the window pauses |
| `CODRIFT_WINDOW_MIN` | 30 | co-drift lookback minutes |
| `GAP_TRIPWIRE_R` | 0.05 | mean recent \|sim−paper\| R beyond this + widening = block promotion report |
| expected-R band bounds | [-5, +5] | schema-enforced, refused outside |
| yield-decay warning | 3 sessions | monotone declining veto-vs-baseline delta |
| drift classification | last-5 vs prior-5 mean \|delta\|, ≥10 rows | <10 rows = `insufficient`, labeled, never zero |

Pre-committed drift readings (point 5's three interpretations, decided now):
- **constant** — sim is a usable proxy with an offset; carry on.
- **widening** — the simulator has stopped predicting fills; tripwire fires,
  promotion report blocked until investigated.
- **collapsing** — suspicious in the good direction; investigate the match
  logic before celebrating.

## The six disciplines

1. **Pre-registration.** Every non-ABSTAIN judgment carries a machine-checkable
   `pre_registration` block sealed BEFORE the outcome: expected R band
   (`expected_r_low <= expected_r_high`, both in [-5,+5]; missing = refused)
   and `invalidation_predicates` from a closed vocabulary
   (`close_below | close_above | range_exceeds_r`). Free-text `invalidators`
   stays for humans; predicates are the scoreable subset. The resolver
   evaluates both against the window and appends a `prereg_score` row.
   A forecast you can't automatically score is a forecast that will be
   remembered as correct.
2. **Point-in-time packets.** `build_packet(symbol, as_of=T)` includes no bar,
   headline, or event newer than T; `max_data_ts` is recorded in the sealed
   record and asserted by test. Claude cannot evaluate a setup it has already
   seen resolve.
3. **Caps and co-drift.** Deterministic pre-emission gates, in order: session
   directive cap; co-drift pause (Nth same-group interrupt inside the window
   is one decision with N× size — pause, don't average). Judgment is still
   sealed verbatim; only emission is refused, recorded as
   `emission_refused: {gate, detail}`. `portfolio_guard.check` is wired
   advisory-only into the record (Gate 7 enforces last).
4. **Referee first.** The veto book is the book of record — a system that can
   only abstain/tighten/exit has bounded downside and produces the cleanest
   signal on whether discretion has edge. Full-discretion stays `unbuilt`.
5. **Sim→live gap, first-class.** `gap_log.py` matches trade_ids present in
   BOTH sim and paper ledgers, classifies drift per the pre-committed rule,
   and its tripwire blocks the `grade` report's promotability. 017's
   `promotion_decision` is untouched — the tripwire wraps the report.
6. **Yield curve.** Per-session `yield.jsonl` row (veto R − baseline R);
   `grade` prints the trajectory; three monotone declines print `YIELD_DECAY`.
   Promote the overlay that improved out-of-sample, never the best story.

Plus **shadow soak** (stolen wholesale): `run --shadow` produces full packets,
records, and forecasts with ZERO directive writes. Shadow verdicts still feed
the four-book veto simulation, so the directive vocabulary is exercised for
weeks before any fill depends on it.

## Invariants (I-numbering continues 023's)

- I11: a non-ABSTAIN judgment without a valid expected-R band is refused.
- I12: the `pre_registration` block is sealed before any directive is
  observable.
- I13: predicate evaluation is deterministic — same window, same verdicts.
- I14: no field of a packet built at T references data newer than T; two
  builds at the same T are byte-identical.
- I15: the 4th directive of a session is refused; the judgment is still sealed.
- I16: a 2nd same-group TIGHTEN/EXIT inside 30 min is refused (co-drift);
  with no groups file the gate is inert.
- I17: a portfolio_guard violation NEVER blocks emission (advisory only).
- I18: a gap row requires the trade_id in BOTH ledgers; one-sided trades are
  reported, not scored.
- I19: drift classification follows the pre-committed rule exactly; <10 rows
  reads `insufficient`.
- I20: a widening drift beyond `GAP_TRIPWIRE_R` blocks the promotion report.
- I21: every four-books session appends exactly one yield row.
- I22: `--shadow` writes zero bytes to `directives.json`; the record is
  marked `"shadow": true` and still feeds the veto book.

## Out of scope (declared)

Correlation detection (groups are a supplied config, matching
portfolio_guard's contract); live fills (013); ENTER proposals for the full
book; enforcing LOCKOUT/FLATTEN_ALL; any change to 017 gates, the 018
envelope, or the authority cap.
