# Spec 026 — mutation evidence (regime / scorecard / splits)

Every invariant of the newly-built reward pathway, fault-injected end to
end: the no-lookahead guards on both sides of the loop, the ATR-unit
refusals, the never-coerce-None-to-zero rule, the safe-default confidence
floor, the v3 policy vocabulary, and the SEALED-002 unseal conditions.
Each fault -> named test RED -> restore byte-identical -> GREEN.

| test | fault applied | result |
|---|---|---|
| `test_regime.py::test_frame_reaching_past_now_et_is_refused` | THE no-lookahead guard: let a frame containing bars after now_et be classified | RED under fault, GREEN after revert |
| `test_regime.py::test_unordered_frame_raises` | accept a time-reversed frame | RED under fault, GREEN after revert |
| `test_regime.py::test_zero_atr_refuses_rather_than_turning_thresholds_into_coin_flips` | allow a zero ATR — every threshold is in ATR units, so 0 makes '< 0.6*atr' impossible | RED under fault, GREEN after revert |
| `test_regime.py::test_nan_ohlc_is_corruption_not_forward_filled` | accept NaN OHLC instead of calling a hole corruption | RED under fault, GREEN after revert |
| `test_regime.py::test_absent_pools_are_skipped_never_compared_against_zero` | coerce an unavailable pool to 0.0 — every bar then 'sweeps' it (CLAUDE.md rule 3) | RED under fault, GREEN after revert |
| `test_regime.py::test_confidence_below_floor_falls_back_to_the_safe_regime` | remove the safe-default confidence floor (APEX #2 — never act on a guess) | RED under fault, GREEN after revert |
| `test_regime.py::test_incomplete_opening_range_does_not_fire_the_breakout_rule` | call an incomplete opening range complete, firing the breakout rule pre-10:00 | RED under fault, GREEN after revert |
| `test_regime.py::test_volume_average_excludes_today` | let today's own volume into its own baseline average (self-referential ratio) | RED under fault, GREEN after revert |
| `test_regime.py::test_daily_atr_uses_only_sessions_strictly_before_today` | include TODAY in the daily ATR — lookahead through the back door | RED under fault, GREEN after revert |
| `test_regime.py::test_policy_emits_only_v3_intent_names` | emit a renamed-away legacy policy name nothing downstream reads | RED under fault, GREEN after revert |
| `test_regime.py::test_atr_is_the_MEAN_true_range_not_the_max` | swap the ATR mean for a max (the furnace-era min->max class of fault) | RED under fault, GREEN after revert |
| `test_scorecard.py::test_lookahead_is_refused_at_the_boundary` | let the forward window overlap the bar being graded | RED under fault, GREEN after revert |
| `test_splits.py::test_sealed_002_requires_a_rule_version` | read SEALED-002 without recording which rules produced the number | RED under fault, GREEN after revert |
| `test_splits.py::test_sealed_002_requires_an_unseal_reason` | allow an accidental, unlogged holdout read | RED under fault, GREEN after revert |

**14/14 rows verified.**
