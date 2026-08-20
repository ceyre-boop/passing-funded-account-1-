# 030 mutation log — the decision ledger

2026-08-19. Harness asserts each named test is green unmutated before
injecting (the M13/M26 trap, closed). Suite: 387 passed, 1 skipped.

| # | fault injected | invariant | killed by | result |
|---|---|---|---|---|
| M34 | hash chain not linked (`prev_sha256` always None) | I36 | test_i36_chain_detects_a_deleted_row | **SURVIVED first run** → test hardened → KILLED |
| M35 | row hash covers nothing (constant digest) | I36 | test_i36_chain_detects_a_rewritten_row | KILLED |
| M36 | point-in-time filter removed from `snapshot` | I37 | test_i37_snapshot_uses_nothing_newer_than_as_of | KILLED |
| M37 | backfill claims live headline counts | I38 | test_backfill_never_claims_headlines | **SURVIVED first run** → test hardened → KILLED |
| M38 | backfill dedupe removed | I40 | test_i40_backfill_is_idempotent | KILLED |

## The two survivors — both decorative tests, caught the same way

**M34.** `test_i36_chain_detects_a_deleted_row` asserted only that
`verify() == 1` after excising a row. With the chain never linked, verify
returns 1 for a *different* reason, so the test passed while proving nothing.
Fix: assert `verify() == 0` BEFORE the excision, so the test distinguishes
"a row was deleted" from "there was never a chain".

**M37.** `test_backfill_never_claims_headlines` set `POLYGON_API_KEY` but the
real `fetch_headlines` raised (no network), so the guarded branch never ran and
the assertion was vacuous. Fix: stub the feed to SUCCEED, then assert backfill
still yields null AND that the live path yields 1 — proving the stub actually
reaches the branch.

This is the third decorative test mutation-testing has caught in this repo
(M9's fingerprint, then these two). The pattern is identical every time: an
assertion that holds for a reason other than the one it claims to test. The
standing rule that catches it — inject the fault and require the *named* test
to fail — is worth more than the tests themselves.
