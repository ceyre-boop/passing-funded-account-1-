# Make the car drive — T_SIM, a null policy, and a full-loop session run

## Context

The success criterion changed, out loud: **completions and disengagements, not R.**
An autonomous system that reliably loses money is still an engineering achievement,
and unlike an edge it is testable today. AlphaZero at ~65% and Stockfish in early
beta are no longer blockers, because none of the three steps below needs the 35%
that isn't built.

The thing standing in the way is not the engines. It is the safety artifact: every
tier in `ODD.md` §4 is about **live** risk, so there is no way to drive on a closed
track. `StockfishStrategy` sits at `T2_DEFENSIVE` and refuses everything, which
means the full loop — perceive, decide, hand off, execute, log, degrade — cannot be
exercised end to end without an edge. The new goal is blocked by our own gate.

`T_SIM` is a **narrowing**, not a widening, so §6 change control does not gate it.
But it is a deliberate door in a wall we built on purpose, and the whole risk of
this session is that the door turns out to be openable from the wrong side.

## The blocker is two blockers, not one

Adding a tier unblocks nothing on its own. `evaluate_gate()` refuses independently
of tier:

```
gate.passed = False
FALSE  : ('t0_unseal',)
UNKNOWN: ('envelope','heartbeat','reconciliation','slippage','exit_domain',
          'checkpoint_hash','policy_version','prereg_current','holdout_sealed')
```

So `T_SIM` needs its **own explicit, reduced precondition list** — never a gate
bypass. A bypass is exactly the dangerous door; a short auditable list is not.

## Design

### 1. `T_SIM` in `az/odd.py` — and it must stay stdlib-only

`az/odd.py` imports only `enum` and `dataclasses`, which is why `gate/` + `az/`
run under 3.14 where nautilus is absent. **That must not change.** All
nautilus-specific detection lives in `body/`.

- `Tier.T_SIM = -1` (below `T3_HALT`, satisfying "below T2"). The existing order
  invariant `T3 < T2 < T1 < T0` is untouched, so the `min()`/`max()` conservatism
  logic and its test still hold.
- `may_open_risk` stays **False** for `T_SIM` — it never opens real risk. A new
  `may_open_simulated_risk` is True for `T_SIM` alone.
- New `RunEnvironment` enum: `UNKNOWN` (default — fails closed), `BACKTEST`,
  `SANDBOX`, `LIVE`.

### 2. `T_SIM` is enterable only at construction

`degrade()` currently permits a slide to any lower tier, so a live system could
slide *into* simulation while holding real risk. Close it:

- `FORBIDDEN_TRANSITIONS` gains `(t, Tier.T_SIM)` for every live tier.
- `recover(T_SIM)` → `T3_HALT`: the only way off the closed track is into the
  safest live state, then climb the normal ladder.

### 3. The hard fault — a kernel fact, not a config flag

`authorize_entry()` gains a required `environment`:

- `tier is T_SIM` and `environment is not BACKTEST` → **raise `OddError`**. A fault,
  not a `False`, because a sim tier outside a backtest is a corrupted system, not a
  refused trade.
- `tier is T_SIM` and `environment is BACKTEST` → evaluate the sim gate.
- Any live tier with `environment is UNKNOWN` → refuse.

`body/runtime.py::detect_environment(clock)` derives it structurally:
`isinstance(clock, TestClock)` → `BACKTEST`, else `LIVE`. Verified: a
`BacktestEngine` provably injects a `TestClock`, a `TradingNode` a `LiveClock`. The
kernel injects it — **there is no config path to override it.**

### 4. `SIM_PRECONDITIONS` — four things that hold even on a closed track

`evaluate_gate(tier=...)` selects the list. Not a bypass:

| key | why it survives into sim |
|---|---|
| `no_live_venue` | structural: environment proven `BACKTEST` |
| `holdout_sealed` | sealed data stays sealed in backtest too |
| `checkpoint_hash` | the point is exercising the **real** frozen exit core |
| `policy_declares_no_edge` | the calibration arm must label itself |

Dropped as live-only and meaningless on a closed track: `envelope`, `heartbeat`,
`reconciliation`, `slippage`, `exit_domain`, `policy_version`, `prereg_current`,
`t0_unseal`.

### 5. `NullEntryPolicy` — the calibration arm

`body/null_policy.py`, matching `bar -> EntryDirective | None`. Same discipline as
the degraded-candidate SPRT arm: **a loop that only works with a good policy is not
a loop we have tested.**

- Emits on a **fixed schedule** (every Nth bar, declared constant) — deterministic,
  so a run is reproducible.
- `confidence` pinned to a constant.
- Direction alternates deterministically, so no directional edge is even implied.
- `reason = "CALIBRATION ARM — no edge claimed; fixed-schedule emitter"`.
- `CALIBRATION_ARM = True`, which is what the `policy_declares_no_edge` precondition
  reads. No edge claimed, none implied.

### 6. `execute()` at `T_SIM` — real simulated fills

Currently raises `NotImplementedError`. At `T_SIM` it submits a bracket order to the
SIM venue and lets it fill, because an orphan counter is meaningless without
positions.

- Geometry reuses `az/candidates.py::geometry` (already declares
  `stop = entry − direction·k_stop·ATR14`, tp1/tp2 at 1R/2R, and **refuses** a
  missing `k_stop`) with `K_STOP` from the frozen `az/floor_params.py`.
- ATR14 reuses `az/state.py::_atr` — no second implementation.
- Fixed quantity: sizing is not what is under test.
- Live tiers keep raising `NotImplementedError`. The order path exists **only**
  behind the `T_SIM` + `BACKTEST` fault.

### 7. The session runner

`scripts/sim_session_run.py` — loads real SPY 5m bars from
`data/daytrade/bars_premarket/SPY_5m.parquet` (491,644 rows, OHLCV, ET-indexed,
2,678 days, 2016–2026), slices RTH sessions, runs `--sessions N` (default 20)
through `BacktestEngine` at `T_SIM`.

Reports, and **nothing else**:

```
sessions completed / attempted
directives published
directives refused          (broken out by reason)
orders submitted / filled
positions opened / closed
positions orphaned
exceptions raised
```

**No R. No P&L.** The scoreboard is completions and disengagements.

## Files

| file | change |
|---|---|
| `az/odd.py` | `T_SIM`, `RunEnvironment`, `SIM_PRECONDITIONS`, transition guards, `authorize_entry(environment=...)` |
| `az/test_odd.py` | extend — hard fault, unreachability, sim gate is a list not a bypass |
| `body/runtime.py` | new — `detect_environment(clock)` |
| `body/null_policy.py` | new — `NullEntryPolicy` |
| `body/stockfish_strategy.py` | `execute()` at `T_SIM`; thread environment through `authorize()` |
| `body/test_sim_tier.py` | new — the door cannot be opened from the wrong side |
| `scripts/sim_session_run.py` | new — the runner |
| `artifacts/SIM_DRIVE_RESULT.md` | the report |

## Verification

```bash
python3 -m pytest gate/ az/ -q            # 3.14, must stay green (92 now)
.venv-v1/bin/python -m pytest body/ -q    # 3.13, 59 now
.venv-v1/bin/python scripts/sim_session_run.py --sessions 20
```

Mutation pass on the new guards — each must fail the suite when reverted:
`T_SIM` authorizing outside `BACKTEST`; `detect_environment` returning `BACKTEST`
unconditionally; `degrade()` permitting a slide into `T_SIM`; the sim gate
downgraded to a bypass; `execute()` reachable at a live tier.

## Out of scope

The magnitude-conditioned policy (explicitly deferred). Any live venue or `[ib]`
extra. Any R or P&L figure. Migrating `daytrade/` onto Nautilus. Widening the ODD
toward risk — this change only narrows.
