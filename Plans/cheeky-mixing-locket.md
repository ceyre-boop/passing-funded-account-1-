# Council review — verify, then close the four real holes

## Context

A three-model review argued one structural thesis: **the rigour is concentrated in
the decision layer, and the invariants proved there are treated as properties of a
live trading system.** That thesis is correct, and one confirmed finding proves it
concretely. But three of the review's specific claims — including its
"highest-confidence" one — do not reproduce, and one of those was seeded by my own
retracted miscount. Both halves go in the record.

Every claim below was tested before being accepted or rejected.

## Refuted by test — this goes in the record, or the review becomes received wisdom

| Claim | Test | Result |
|---|---|---|
| Short-side `max()` inverts the stop (all 3 models, "highest confidence") | mirror-image long vs short, identical distances | **Exact mirror.** `max(level*direction)` and `entry − direction·k·ATR` are both side-aware. Stop-distance paths identical to 1e-9. |
| A persisted ratchet is needed when TIGHTEN lifts | drive RIDE → tighten → lift | **Stop held at 103.925.** `state.sl` + the `tighter` gate already *is* the proposed ratchet. |
| 44 written / 13 resolved = 70% file drawer | recount by row kind | **17 written, 13 resolved, 4 unresolvable, 0 open.** My earlier miscount, retracted, propagated into the review. |
| No model identity versioning | `forecast.py:224` | Scoring **does** partition on `model_version`. Only `prompt_version` is missing — the narrower claim is real. |

## Confirmed — four holes, priority ordered by evidence

### 1. Quantization loosens the stop at the venue boundary  `[the thesis, made concrete]`

`make_price(103.925) → 103.92` — rounds to **nearest**, so a long stop can move
*down* by up to half a tick. The `max()` never-loosen proof is a property of the
decision function; the order that reaches the venue is a different object.

Fix at the boundary in `body/`, **not** in `daytrade/stockfish_exit.py` — that file
is pinned by `SF-FROZEN-004` and quantization is a venue property, not a decision
property. Add a side-aware `quantize_protective()`: a long stop rounds **up**, a
short stop rounds **down**, always toward tighter, with a post-condition asserting
the quantized level is no looser than the computed one. Single call site,
`body/stockfish_strategy.py::execute`.

### 2. T_SIM has one sensor, not two

`scripts/sim_session_run.py:109` is `out["no_live_venue"] = Truth.TRUE` —
unconditional. The comment beside it (and my own artifact) claim it is
"independently re-checked from the injected clock." It is not re-checked; the clock
is the only real sensor, read once. **I wrote that overstatement and it should be
corrected in the same commit.**

Second sensor, genuinely independent of the clock: the **execution-client
inventory**. Verified reachable at `engine.kernel.exec_engine.registered_clients`.

> **The naive version of this check is vacuous and I hit it in 30 seconds.**
> `registered_clients` returns `ClientId`s, not client objects, so
> `isinstance(c, LiveExecutionClient)` is trivially false for every element and the
> sensor "passes" while measuring nothing. The implementation must assert on the
> concrete client objects or the exec-engine class, and **a mutation test must prove
> the sensor fails when a live client is present.** An assertion that cannot fail is
> the exact failure this whole gate exists to prevent.

### 3. The promotion gate has no discrimination requirement

`forecast.py::baseline_brier` is **uniform only**. A forecaster emitting
unconditional empirical base rates beats uniform forever with zero information, is
perfectly calibrated by construction, has constant per-regime score, and never says
EXIT — so it passes calibration, regime stability, and tail regret. Confirmed: no
`climatolog|resolution|discriminat|skill` anywhere in the file.

Three additions to `daytrade/forecast.py`:
- a **climatology baseline** (empirical base rates over resolved outcomes) alongside uniform
- a **discrimination gate**: Brier skill vs climatology > 0, bootstrap CI excluding zero
- `prompt_version` joins `model_version` in the scoring partition

Plus one guard against a failure the review correctly forecasts: when 49 of 50 are in
and the tail gate still reads `REGRET_DATA_MISSING`, the pressure to define
`policy_regret_r = 0.0` for capped directives will be enormous and technically
defensible — and it silently converts the tail gate into a tautology, because a
capped directive cannot cause regret. **A test must assert that a suppressed or
capped directive never imputes a zero regret.**

### 4. Cost sensitivity — a column, not a verdict

`SLIP_BPS = 2.0` is **15.5× the SPY half-spread** (0.13 bps), and slippage is **66%**
of the modelled `$0.1176`/share. Since costs, not the effect, set both economic
floors, the register's primary output carries an error bar it does not show.

**Ruling (yours): sensitivity only. No verdict changes.** `scripts/floor_test.py`
gains `--sweep` emitting floor-vs-cost across `$0.015 … $0.118`, and
`artifacts/FLOOR_TEST_RESULT.md` gains a sensitivity column plus a banner: verdicts
stand, and reopening any closure requires a **new pre-registration written before the
numbers are looked at again**. The 15.5× figure is recorded as a known defect in the
*pre-registration*, not as a reason to rerun it — that distinction is the freeze
doing its job.

## Files

| file | change |
|---|---|
| `body/stockfish_strategy.py` | side-aware `quantize_protective()` at the one order site |
| `body/runtime.py` | `assert_no_live_execution_client()` — the second sensor |
| `scripts/sim_session_run.py` | attest `no_live_venue` from the inventory; fix the false comment |
| `daytrade/forecast.py` | climatology baseline, discrimination gate, `prompt_version` partition |
| `body/test_sim_tier.py`, `daytrade/test_alpha_operator.py` | tests + mutations for each guard |
| `scripts/floor_test.py` | `--sweep` |
| `artifacts/COUNCIL_REVIEW_2026-09-01.md` | what reproduced, what didn't, with the mirror test as evidence |
| `artifacts/FLOOR_TEST_RESULT.md` | sensitivity column + freeze banner |

## Verification

```bash
python3 -m pytest daytrade/ scripts/ gate/ az/ -q     # 785 now
.venv-v1/bin/python -m pytest body/ -q                # 83 now
.venv-v1/bin/python scripts/sim_session_run.py --sessions 20
python3 scripts/floor_test.py --sweep
```

Mutation pass, each restoring byte-identically — **and each must fail the suite**:
quantization reverted to `make_price`; the live-client sensor made vacuous (the
`ClientId` trap above); climatology baseline swapped back to uniform; a capped
directive imputing `policy_regret_r = 0.0`; `prompt_version` dropped from the
partition.

## Out of scope

Any change to `daytrade/stockfish_exit.py` — pinned by `SF-FROZEN-004`; a fix there
needs a new checkpoint and is not what this session is for. Any re-run of the floor
tests for a **verdict**. Re-opening the entry-null or the 0/396 exit sweep — the
review's confounding argument is interesting and is a separate pre-registration, not
a fix. The magnitude policy. Live venue or the `[ib]` extra.
