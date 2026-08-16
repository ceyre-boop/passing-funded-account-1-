# 023 mutation log — alpha_operator.py + four_books.py

2026-08-15. Method: deliberately break the invariant in source, run the named
test, require failure, restore. One row per fault. Suite before and after:
248/248.

| # | fault injected | invariant | killed by | result |
|---|---|---|---|---|
| M1 | directive authority_level 1 → 3 | I1 | test_i1_runner_side_default_context_accepts_the_emitted_directive | KILLED |
| M2 | EXIT suppression removed (`suppressed = None`) | I9 | test_i9_exit_sealed_verbatim_directive_capped_with_suppression_noted | KILLED |
| M3 | directive written to disk BEFORE the sealed record | I3 | test_i3_sealed_record_exists_before_directive_is_observable | KILLED |
| M4 | corrupt JSONL line silently skipped on load | I10 | test_i10_corrupt_persistence_line_raises_on_load | KILLED |
| M5 | resolver early-horizon guard removed | I4 | test_i4_resolution_before_horizon_is_refused_forecast_stays_open (017 ledger raises) | KILLED |
| M6 | scenario-prob renorm tolerance unbounded | prob honesty | test_probs_outside_tolerance_refused | KILLED |
| M7 | record expiry check dropped in `active_records` | veto correctness | test_veto_ignores_expired_record | KILLED |
| M8 | in-position urgency escalated to "exit" for EXIT records | unpromoted cap | test_mid_trade_record_tightens_but_never_exits (engine-side spy) | KILLED |
| M9 | one book fed a truncated bar frame | I8 | test_i8_all_books_consume_identical_bar_data | **SURVIVED first run** → harness fixed → KILLED |
| M10 | `"quantity": 100` smuggled into the directives.json payload | I2 / 018 envelope | test_i2_directive_round_trips_unchanged (from_dict refuses) | KILLED |

## The M9 finding (why this log exists)

First run of M9 survived: `run_session` computed the bars fingerprint ONCE and
stamped it onto all four book results, so the "books consumed identical data"
assertion compared a value with itself — decorative, exactly the class of test
the DoD exists to catch. Fix: each `run_book` now fingerprints the frame it was
actually handed; `run_session` compares the four independently-computed hashes.
Re-run: KILLED.

## Round 2 — after the Cato cross-vendor audit (2026-08-16)

Cato (GPT-5.4, read-only) returned `concerns`: 4 high, 5 medium, 2 low. All
eleven findings fixed same night. The headline finding: the I7 test compared
`load_ledger()` to `load_ledger()` — replay vs replay, tautological, the same
decorative-assertion class M9 exposed in the harness. Fixes shipped:

1. I7 test now captures each Forecast at genuine write time (spy with
   setdefault so run_once's own replays can't re-tautologise it) and compares
   the disk replay against those originals.
2. `resolve_open` raises on a forecast with no sealed record; `bar_age_min
   None` now counts as stale (it was silently False — a promotion-gate input
   biased toward promotion on missing data).
3. `check_triggers` returns the observed-world snapshot; state advances from
   that snapshot, never a post-judgment re-observation (lost-update window
   closed, Polygon call count halved).
4. `_write_directive` round-trips EVERY element (pre-existing included)
   through the 018 contract and refuses anything above the unpromoted cap;
   per-PID tmp name.
5. horizon/expires/confidence bounds moved into the pydantic schema (refused,
   not clamped); TTL computed once — directive expiry and record expiry are
   provably the same instant.
6. All validation now precedes all writes — a refused judgment leaves zero
   orphaned rows.
7. Resolver shock baseline is the literal prior-20-bars-before-as_of median;
   <20 bars or zero median leaves the forecast open, never a guess.
8. Market/sector evidence stores empty `symbols` (provenance honest).
9. Four-book fingerprint: per-book df copies; len-check before pop.
10. `state.json` and `directives.json` loads raise OperatorError like the
    JSONL readers; I10 test parametrised across RECORDS/EV_LOG/FC_LOG.
11. State snapshot saved in `finally` after the priced call — a persistently
    malformed response costs one call, not one per invocation.

| # | fault injected | killed by | result |
|---|---|---|---|
| M11 | replay drops evidence_ids | test_i7_replay_matches_forecasts_captured_at_write_time | KILLED |
| M12 | unpromoted-cap check on existing payloads removed | test_hostile_preexisting_directive_is_refused_not_repersisted | KILLED |
| M13 | evidence written before forecast validation | test_refused_judgment_leaves_no_orphaned_evidence | KILLED |

Suite after round 2: 261/261.

## Not mutation-testable tonight (stated, not hidden)

- I5 (no trigger → zero API calls) is covered by a direct test with a spy on
  the priced call; a mutation of `check_triggers` itself is equivalent to the
  test's own stub, so a fault row would be circular.
- The live Claude call path (`news_claude._call`) is 009's machinery, already
  under its own log; these tests stub it by design.
