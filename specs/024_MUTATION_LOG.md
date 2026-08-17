# 024 mutation log — the discipline layer

2026-08-17. Method unchanged from 023: break the invariant in source, run the
named test, require failure, restore. Suite before: 266/266; after: 290/290.

| # | fault injected | invariant | killed by | result |
|---|---|---|---|---|
| M17 | pre-registration requirement dropped | I11 | test_i11_non_abstain_requires_expected_r_band | KILLED |
| M18 | `<= as_of` bar filter removed | I14 | test_i14_packet_point_in_time | KILLED |
| M19 | post-as_of events included in packet | I14 | test_i14_packet_point_in_time | KILLED |
| M20 | session directive cap disabled | I15 | test_i15_session_cap_refuses_fourth_directive | KILLED |
| M21 | co-drift threshold off-by-one (N vs N-1) | I16 | test_i16_codrift_pause_two_symbols_same_group | KILLED |
| M22 | portfolio advisory made enforcing | I17 | test_i17_guard_violation_never_blocks_emission | KILLED |
| M23 | shadow flag ignored at emission | I22 | test_i22_shadow_writes_zero_directives | KILLED |
| M24 | drift classification armed at 5 rows | I19 | test_i19_under_ten_rows_reads_insufficient | KILLED |
| M25 | tripwire band ignored (fires on any widening) | I20 | test_gap_log.py::test_tripwire_silent_when_widening_but_inside_band | KILLED |
| M26 | prereg R-band check hardwired True | I13 | test_i13b_out_of_band_outcome_scores_false | **false-killed first** (bad test target — same trap as round-2 M13), real negative test added, then KILLED |

Additional coverage without fault rows (stated): I12 (prereg sealed with the
record — direct assertion), I18 (one-sided trades reported not scored —
direct), I21 (yield row per session — direct in test_four_books), shadow
records excluded from the session cap (direct). I16b (gate inert without a
groups file) is the absence of a trigger, tested directly.

## Process note

M26 initially reported KILLED because the mutation harness was pointed at a
nonexistent test name — pytest's collection error is indistinguishable from a
genuine failure in the return code. Second occurrence of this trap (M13 was
the first). Rule going forward: a mutation row is only credible when the named
test PASSES on unmutated source in the same session — both M13 and M26 were
re-run properly and genuinely killed.
