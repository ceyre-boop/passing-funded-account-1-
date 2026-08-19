# 028 — SEAL REGISTRY `[SPEC]`

**Component:** `SEALS.json` (repo root), `daytrade/seals.py`,
`daytrade/test_seals.py`, one mirroring line in `daytrade/splits.py`
**Status:** `[SPEC]` — written 2026-08-18, before the code.
**Origin:** Colin's doctrine note, quoted because it is the spec:

> You can't choose a verifier that's outside your judgment, but you can
> choose one that's outside your reach. […] Past-you, who didn't yet have a
> stake in the outcome, binds present-you, who does. Pre-commitment is the
> only honest external verifier a solo builder gets. […] The moment a check
> gets uncomfortable is exactly the moment its authority is doing its job —
> and the one rule worth keeping is that unsealing anything requires
> writing the reason down before looking.

The repo practices this in pieces (splits.py's reason-required holdout,
hashed sentinel manifest, pre-registered spec verdicts). This spec makes it
ONE mechanism, and puts a drift-watch on every sealed artifact — the
2eb400e live-mutant incident proved files change under us silently.

## The contract

`SEALS.json` — one entry per sealed artifact:
`{path, sha256, sealed_at, sealed_by, unseal_condition, status}` where
`unseal_condition` is a STATED trigger (never "when curious") and `status`
is `sealed | unsealed | retired`. Entries with `path: null` are
boundary-seals (e.g. TUNE_END) documented but not hashable.

`daytrade/seals.py`:
- `seal <path> --condition "…"` — hash + register; refuses an
  uncommitted/dirty target (past-you must be on the record).
- `intent <path> --reason "…"` — appends the reason to
  `data/daytrade/unseal_intents.jsonl` BEFORE any look. Written blind.
- `unseal <path>` — refuses without a prior-invocation intent for that
  path (reason-before-look, enforced mechanically, doctrine printed on
  refusal). Flips status, records the intent it consumed.
- `check` — re-hashes every `sealed` entry; drift is a loud named failure.

`daytrade/test_seals.py` — runs `check` inside the suite. The suite is the
guard that is actually always on (this repo's commits routinely bypass git
hooks; a hook would be decorative). Drift in any sealed file now fails
every test run until either the file is restored or an intent + unseal is
logged.

## Invariants

- I23: a sealed entry whose bytes changed fails `check` and the suite.
- I24: `unseal` without a prior logged intent is refused.
- I25: `intent` writes are append-only, fsync'd, and never deleted.
- I26: `retired`/`unsealed` entries are exempt from hashing (history, not
  contraband).
- I27: `seal` refuses a target with uncommitted changes.

## Out of scope

Changing any existing seal or condition; git hooks; retroactive rewriting
of `holdout_unseals.log` (it stays as history; new splits unseals mirror
into the intent log going forward).
