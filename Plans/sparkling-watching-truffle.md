# Plan — Gate 2 wiring: the runner emits 012 events (one week, one gate)

## Context

Colin's 1-week plan: close exactly one of the six wiring gaps from the 2026-08-10
architect audit — "the runner does not emit 012 events" — and nothing else. All
seven gates are IMPLEMENTED + UNIT VERIFIED (154 tests, 156/156 mutation rows,
independently re-run); none has touched a real decision cycle. Gate 2 unlocks
Gates 3/4/6 becoming EXERCISED later, so it goes first and alone. The card's
own spec (`specs/012_STOCKFISH_EVENT_MEMORY.md`) carries two binding wiring
obligations recorded at review: **append-and-fsync BEFORE apply** (a crash
between decide and log must not lose a decision that took effect), and
**fsync the parent directory on log creation**.

## Step-1 finding (loop-shape compatibility) — CONFIRMED COMPATIBLE, two adaptations

Verified against the current code, per Colin's step 1:

The runner's loop (`runner.py` ~379-392) is `decide_exit(s)` →
`apply_actions(...)` → `log_cycle(rec)`. Append-before-apply fits between
decide and apply WITHOUT restructuring, because `decide_exit` already performs
the semantic transitions (hwm update, stage advances) before returning actions
— so everything the translator needs exists pre-apply. Two adaptations are
required, and they are findings, not paper-overs:

1. **`events_from_decision` assumes POST-apply state** (`trade_events.py:323` —
   it subtracts the batch's mutating count to reconstruct pre-apply revisions).
   Called pre-apply, the revision base is simply `state.state_revision` with no
   subtraction. Fix: a `post_apply: bool = True` parameter; default preserves
   the 21/21-verified behavior byte-for-byte, the runner passes
   `post_apply=False`. One translator, two documented call points — not a
   second implementation.
2. **The constitution could refuse an action the log already claims happened.**
   `apply_actions` enforces per-action; with append first, a refused action
   would leave a false event. Fix: a pure PRE-VALIDATION pass before append —
   `enforce(state, actions=acts)` plus per-action `enforce(state, a,
   applied_keys, ...)` against the pre-apply state. `enforce` is a predicate
   (spec 011's whole design), so this is dry-run-then-commit, not a second
   engine. The only approximation: in a `[TAKE_PARTIAL, MOVE_SL]` batch the
   second action pre-validates against pre-partial state — benign because a
   partial changes qty, not sl, and the real enforcement still runs inside
   apply_actions; documented in the code.

Resulting cycle order: **decide → pre-validate (pure) → append+fsync → apply →
log_cycle**. Crash windows: before append = decision lost, next cycle
re-decides from prior facts (safe); after append before apply = memory died
anyway, rebuild reflects the appended decision (correct — the log is the
authority); broker divergence in that window is 013's reconciliation, out of
scope this week.

## Work items

### 1. `trade_events.py` — two small, spec-obligated changes
- `events_from_decision(..., post_apply: bool = True)`: revision base
  `state.state_revision` when False (no subtraction). Existing tests untouched.
- `JsonlEventLog.append`: on first creation of the file, fsync the parent
  directory (the spec's power-loss obligation). `os.open(dir, O_RDONLY)` +
  `os.fsync` + close, macOS/Linux safe.

### 2. `runner.py` — the wiring (I/O only, ruling 1)
- New module constant `EVENTS_DIR = LOGDIR` (events file:
  `events_{date}_{symbol}.jsonl`), monkeypatchable like LOGDIR.
- At session start: create `JsonlEventLog`; emit `POSITION_OPENED` (payload =
  the resolved plan, trade_id `f"{symbol}-{date}"`) — unless resuming (below).
- In the loop, between `decide_exit` and `apply_actions`:
  1. pre-validate the batch (pure enforce calls),
  2. `events = events_from_decision(events, s, actions, occurred_at=<utc now>,
     source_version="runner-1", post_apply=False)` and append each new event
     through the log (fsync per append — JsonlEventLog already does),
  3. then `apply_actions(s, actions, applied_reduction_keys)` as today.
  Reuse the SAME caller-owned key set — no second key mechanism.
- **Resume is explicit, never automatic**: new `--resume` flag / `run(...,
  resume: bool)`. With it, if the events file exists and is not closed:
  `rebuild(load())` → `to_trade_state` → keys from
  `mem.applied_reduction_keys` (the C005 crash payoff, now live). Without the
  flag, an existing non-closed events file for today is a LOUD refusal to
  start (never silently fork a trade's history); a closed one rotates aside.
  Auto-resume is rejected deliberately: a surprise reconstruction at 09:31 is
  worse than an explicit operator choice.

### 3. `test_runner_event_wiring.py` — the proofs (via `run()`'s replay path,
same monkeypatch pattern as `test_directive_authority._run_replay`)
- **Agreement**: one replay session → session JSONL and event stream agree
  action-for-action (kinds/sl/fractions per cycle), POSITION_OPENED carries
  the resolved plan.
- **Append-before-apply ordering**: monkeypatch `apply_actions` to raise on
  cycle N; assert the events file already contains cycle N's events while the
  state mutation never landed. (This is the test the ordering mutation must
  turn red.)
- **The real card-012 DoD through the real loop** (Colin's step 3): run the
  replay through `run()` for k cycles, kill everything in-process, restart
  `run(..., resume=True)` with the remaining replay prices, and diff the
  produced actions + final state against one unbroken `run()` over the full
  replay. **Zero diff = first genuinely EXERCISED evidence Gate 2 has had.**
  Parametrize the kill point over at least three cycle indices, including
  immediately after the TP2 partial (the C005-sensitive point).
- **Refusal**: starting without `--resume` over an existing open events file
  refuses loudly; a closed file does not block.
- **Byte-parity**: with events emission active, the session JSONL schema and
  `backtest.py` replay diff are unchanged (events are additive I/O).

### 4. `mutation_check_gate2_wiring.py` → `specs/GATE2_WIRING_MUTATION_LOG.md`
Copy the hardened driver template (cache purge, PYTHONDONTWRITEBYTECODE,
refuse-log-on-failure). Rows at minimum:
- drop the append call entirely → agreement test red
- move append AFTER apply → ordering test red
- `post_apply=False` → `True` at the runner call site (wrong revision base) →
  destroy/resume test red (keys mismatch)
- resume uses a fresh key set instead of `mem.applied_reduction_keys` → the
  TP2-kill-point resume test red
- drop the pre-validation pass → a test with a poisoned batch (constitution
  refusal) showing no event is appended → red
- drop the directory fsync → (structural; pin by asserting the code path
  exists via a behavior test on first-create if feasible, else document as
  untestable-without-power-loss and exclude honestly)

### 5. Update the record honestly (Colin's step 4)
- `specs/README.md`: Gate 2 row → WIRED, and EXERCISED only if the
  destroy/resume-through-real-loop proof is clean, evidence paths named
  (`GATE2_WIRING_MUTATION_LOG.md`, the test file). If anything falls short,
  the row says exactly what.
- Strike Gate 2 from the "Operational completeness" list (or annotate what
  remains); leave Gates 3-7 lines untouched.
- Re-read the status line next day with fresh eyes before calling the week
  done (per the plan's own DoD).

## Explicitly out of scope
Gates 3-7 wiring · decide_exit / constitution / broker changes · auto-resume ·
event emission from backtest.py or ceiling.py (harness stays pure replay) ·
013 reconciliation of the append-vs-broker window · anything in the carry lane.

## Verification
1. `python3 -m pytest daytrade/ -q` — all green (154 + new).
2. `python3 daytrade/mutation_check_gate2_wiring.py` — all rows RED→GREEN;
   then the other nine drivers sequentially, unchanged counts.
3. `python3 daytrade/stockfish_exit.py` + full `ceiling.py` diffs vs baseline —
   byte-identical (wiring is runner I/O only).
4. One realistic scripted session via `--replay` producing both logs; manual
   eyeball that the event stream reads as a coherent trade history.
5. Adversarial review (fresh context: spec 012's wiring obligations + the diff
   + test output) before the status-line update commits.
