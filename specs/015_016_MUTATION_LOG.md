# Cards 015+016 — mutation evidence (Gate 5)

Evidence lifecycle (dup-never-raises-urgency, forward-only states,
inclusive freshness, explicit unrated, conflict surfacing, scoped
delivery), the authority registry (unpromoted cap, audited rollback),
and the runner's directive wiring (merge, replay-faithful logging,
default authority, malformed-file refusal) all fault-injected:
fault -> named test RED -> restore -> GREEN.

| test | fault applied | result |
|---|---|---|
| `test_evidence.py::test_evidence_validates` | accept unknown evidence types | RED under fault, GREEN after revert |
| `test_evidence.py::test_unrated_source_is_explicit_never_defaulted` | let a None reliability travel without its 'unrated' label | RED under fault, GREEN after revert |
| `test_evidence.py::test_unrated_source_is_explicit_never_defaulted` | default an unrated source to 0.5 (the zero-fill the card forbids) | RED under fault, GREEN after revert |
| `test_evidence.py::test_canonicalization_groups_the_same_story` | stop lowercasing in canonicalization (same story splits into two) | RED under fault, GREEN after revert |
| `test_evidence.py::test_lifecycle_is_forward_only_and_idempotent` | let the lifecycle run backwards | RED under fault, GREEN after revert |
| `test_evidence.py::test_duplicates_update_provenance_but_never_raise_urgency` | let a louder recap raise the group's urgency (the card's core rule) | RED under fault, GREEN after revert |
| `test_evidence.py::test_stale_recap_carries_no_urgency` | flip the inclusive freshness boundary | RED under fault, GREEN after revert |
| `test_evidence.py::test_digested_story_steers_nothing` | give a digested story residual urgency | RED under fault, GREEN after revert |
| `test_evidence.py::test_conflict_is_surfaced_never_averaged` | hide the conflict | RED under fault, GREEN after revert |
| `test_evidence.py::test_scoped_delivery_nvda_vs_market` | deliver every story to every symbol | RED under fault, GREEN after revert |
| `test_directive_authority.py::test_abstention_rejects_unknown_reason_and_anonymity` | accept an unknown abstention reason | RED under fault, GREEN after revert |
| `test_directive_authority.py::test_unpromoted_grants_cap_at_recommend` | grant interrupt authority without a promotion_ref | RED under fault, GREEN after revert |
| `test_directive_authority.py::test_rollback_never_escalates_a_revoked_model` | rollback escalates a revoked model back to the default (review finding 4) | RED under fault, GREEN after revert |
| `test_directive_authority.py::test_rollback_is_explicit_and_audited` | derive granted_level from the first grant instead of the whole trail | RED under fault, GREEN after revert |
| `test_directive_authority.py::test_runner_tightens_on_valid_directive_and_logs_merged_urgency` | drop the directive channel from the urgency merge | RED under fault, GREEN after revert |
| `test_directive_authority.py::test_runner_tightens_on_valid_directive_and_logs_merged_urgency` | log the pre-merge urgency (replay diff would lie) | RED under fault, GREEN after revert |
| `test_directive_authority.py::test_runner_refuses_emergency_from_unpromoted_authority` | runner grants itself interrupt authority | RED under fault, GREEN after revert |
| `test_directive_authority.py::test_runner_malformed_directives_steer_nothing` | let a malformed directives file steer the position | RED under fault, GREEN after revert |

**18/18 rows verified.**
