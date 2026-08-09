# Card 020 — mutation evidence (Gate 1)

Every promoted-spec requirement fault-injected: the three spine
invariants (including the spec's named comparator-flip faults), the
engine-side channel arbitration (resolve_channels), the five ruled
compute() silent-zero paths PLUS trend_strength (same species, found
in adversarial review), the C004 midnight session-boundary refusal
(wording AND the refusal itself), and the runner/ceiling caller
threading (backtest's row lives in WIRING_MUTATION_LOG.md). Each
fault applied -> named test RED -> module restored byte-identical ->
GREEN.

Known residuals carried forward, not silently dropped: the invariant-1
row is harness-scoped until 016 builds the production regime consumer;
a malformed HISTORY session with zero mean price still raises a raw
ZeroDivisionError rather than RegimeError (today's session is hardened;
history-side belongs to the compute-coverage backlog line in 020).

| test | fault applied | result |
|---|---|---|
| `test_az_spine.py::test_unavailable_regime_never_becomes_a_number` | spine consumes available() with a default instead of require() [harness-scoped] | RED under fault, GREEN after revert |
| `test_az_spine.py::test_stale_state_cannot_influence_decision` | stop checking scenario freshness at the point of use | RED under fault, GREEN after revert |
| `test_az_spine.py::test_stale_state_cannot_influence_decision` | FLIP the scenario freshness comparator (the spec's named fault) | RED under fault, GREEN after revert |
| `test_az_spine.py::test_stale_state_cannot_influence_decision` | stop rejecting expired directives | RED under fault, GREEN after revert |
| `test_az_spine.py::test_stale_state_cannot_influence_decision` | FLIP the directive expiry comparator (the spec's named fault) | RED under fault, GREEN after revert |
| `test_az_spine.py::test_resolve_channels_urgency_is_most_protective` | pick the LEAST protective urgency | RED under fault, GREEN after revert |
| `test_az_spine.py::test_resolve_channels_candidate_only_tightens` | let the directive candidate LOOSEN the policy | RED under fault, GREEN after revert |
| `test_az_spine.py::test_directive_candidate_cannot_loosen_policy_end_to_end` | ignore the directive candidate entirely (tightening lost) | RED under fault, GREEN after revert |
| `test_az_spine.py::test_thesis_oscillation_cannot_loosen_protection` | make the tighten channel symmetric (emit the widened candidate on recovery) | RED under fault, GREEN after revert |
| `test_constitution_wiring.py::test_c004_midnight_crossing_refused_by_design` | drop the deliberate session-boundary wording from the C004 refusal | RED under fault, GREEN after revert |
| `test_constitution_wiring.py::test_c004_midnight_crossing_refused_by_design` | disable the backwards-clock refusal itself | RED under fault, GREEN after revert |
| `test_regime_compute.py::test_flat_tape_yields_unavailable_not_computed_zero` | trend fabricates a computed 0.0 on a zero-ATR tape | RED under fault, GREEN after revert |
| `test_regime_compute.py::test_zero_mean_price_raises` | accept a zero mean price instead of raising | RED under fault, GREEN after revert |
| `test_regime_compute.py::test_zero_median_history_makes_expansion_unavailable` | expansion fabricates a computed 0.0 when undefined | RED under fault, GREEN after revert |
| `test_regime_compute.py::test_flat_tape_yields_unavailable_not_computed_zero` | stretch fabricates a computed 0.0 on a zero-ATR tape | RED under fault, GREEN after revert |
| `test_regime_compute.py::test_flat_tape_yields_unavailable_not_computed_zero` | autocorrelation fabricates a computed 0.0 on constant returns | RED under fault, GREEN after revert |
| `test_regime_compute.py::test_constant_index_makes_correlation_unavailable_only` | correlation fabricates a computed 0.0 on a constant series | RED under fault, GREEN after revert |
| `test_constitution_wiring.py::test_runner_replay_threads_one_session_lifetime_key_set` | runner: fresh key set per cycle instead of the session-lifetime set | RED under fault, GREEN after revert |
| `test_constitution_wiring.py::test_ceiling_simulate_threads_keys_and_batch` | ceiling: stop recording the applied partial's key | RED under fault, GREEN after revert |
| `test_constitution_wiring.py::test_ceiling_simulate_threads_keys_and_batch` | ceiling: stop passing the batch into apply_action | RED under fault, GREEN after revert |

**20/20 rows verified.**
