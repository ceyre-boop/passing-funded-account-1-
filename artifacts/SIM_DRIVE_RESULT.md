# The car drives — T_SIM full-loop session run

**Date:** 2026-09-01 · **Runner:** `scripts/sim_session_run.py`
· **Tier:** `T_SIM` · **Policy:** `NullEntryPolicy` (calibration arm, no edge claimed)

**The metric changed, out loud: completions and disengagements, not R.** No P&L or R
multiple appears in this document or in the runner's output, by construction.

---

## Result

**20 of 20 sessions completed. 163 fills. 0 exceptions. 1 orphan.**

```
sessions completed         20 / 20        2026-07-30 .. 2026-08-26, real SPY 5m bars
exceptions raised          0
directives published       120
directives received        120            message bus dropped nothing
directives authorized      100
directives refused          20
    warmup                  20            1 per session — correct, see below
orders submitted           100
orders filled              163
positions opened            82
positions closed            81
positions ORPHANED           1
```

The full loop — **perceive → decide → hand off → execute → log** — ran end to end on
real historical bars, with an entry policy that claims no edge and implies none.

### The 20 refusals are the loop working, not failing

Exactly one per session. `NullEntryPolicy` emits its first directive at bar 12, but
ATR14 needs 14 bars, so `execute()` refuses for warmup rather than computing a stop
from an ATR that does not exist yet. That is dev rule 5 — explicit failure over
fallback — firing 20 times on the real path.

It is counted as a refusal with a named reason rather than vanishing between the
authorize and execute stages. An earlier version of this run reported `refused 0`
alongside `warmup 20`, which was an accounting inconsistency, not a different
behaviour; it is fixed and pinned by a test.

### The 1 orphan is a real finding

82 positions opened, 81 closed. One position was still open when its session's bars
ran out — an orphan under `ODD.md` §1b, which the counter exists to surface rather
than assume away. Nothing flattens at the close in this configuration, so this is
expected behaviour and is reported rather than explained away. **If a flatten-at-close
rule is added later, this counter is how you would know it works.**

---

## Why a deliberately worthless policy came first

`NullEntryPolicy` emits on a **fixed schedule**, never on any property of the bar,
with `confidence` pinned to a constant and direction **alternating deterministically**
so that not even a directional prior is smuggled in. It declares itself via
`CALIBRATION_ARM = True`, which is what the sim gate's `policy_declares_no_edge`
precondition reads. A policy that will not declare itself cannot pass.

Same discipline as the degraded-candidate arm in the exit work: **a loop that only
works when the policy is good is not a loop that has been tested.** When the real
entry policy lands it drops into the identical `bar -> EntryDirective | None`
signature, and the harness is already known to work.

---

## The blocker was two blockers

`ODD.md` §4 defines every tier in terms of **live** risk, so there was no way to drive
on a closed track — the engineering goal was blocked by the safety artifact. But
adding a tier alone would have unblocked nothing:

```
evaluate_gate()  ->  passed = False
                     FALSE  : t0_unseal
                     UNKNOWN: envelope, heartbeat, reconciliation, slippage,
                              exit_domain, checkpoint_hash, policy_version,
                              prereg_current, holdout_sealed
```

The gate refuses independently of tier. So `T_SIM` runs **its own four-line
checklist** — a shorter list, never a bypass. The distinction is the whole safety
argument: a bypass is unauditable and grows silently; four named lines can be read
and argued with.

| sim precondition | how the runner verified it |
|---|---|
| `checkpoint_hash` | recomputed sha256 of `daytrade/stockfish_exit.py`, compared to `SF-FROZEN-004`'s pinned `engine_sha256` — **matches** |
| `holdout_sealed` | confirmed the bar source sits outside `data/proof/` |
| `policy_declares_no_edge` | asked the policy for `CALIBRATION_ARM` |
| `no_live_venue` | `BacktestEngine` only, and independently re-derived at authorize time |

**Attested, not declared.** Anything unverifiable stays `UNKNOWN`, and `UNKNOWN` fails
the gate closed.

---

## The door is locked from three sides

`T_SIM` is a door in a wall built on purpose. The risk worth fearing is not that it
fails to open — it is that it opens from the wrong side.

1. **Environment is a kernel fact, not a config flag.** Nautilus injects a `TestClock`
   in a `BacktestEngine` and a `LiveClock` in a live node.
   `body/runtime.py::detect_environment` reads that and nothing else — it takes no
   override parameter, no env var, no config hook, and a test asserts its signature is
   exactly `(clock)`. Anything unidentifiable reads **LIVE**, the dangerous reading, on
   purpose. A test passes the literal string `"BACKTEST"` and asserts it reads LIVE.

2. **Outside a backtest, `T_SIM` is a FAULT, not a refusal.** `authorize_entry` raises
   `OddError` rather than returning `False`, because a simulation tier in a live
   process is a corrupted system, not a declined trade. `execute()` re-checks
   independently — two locks, not one.

3. **`T_SIM` is unreachable by transition.** `FORBIDDEN_TRANSITIONS` now contains
   every `(live_tier → T_SIM)` pair, so `degrade()` refuses a slide into simulation: a
   live system doing that would silently lose its execution path while holding a
   position. `recover(T_SIM)` lands in `T3_HALT` — the only way off the closed track
   is into the safest live state, then the normal climb.

`T_SIM.may_open_risk` remains **False**. It never opens real risk; the separate
`may_open_simulated_risk` is true for `T_SIM` alone. This is a **narrowing**, so
`ODD.md` §6 does not gate it — §6 gates widening toward risk.

---

## Verification

```bash
python3 -m pytest gate/ az/ -q          # 111 passed  (was 92)
python3 -m pytest daytrade/ scripts/ -q # 668 passed, 1 skipped
.venv-v1/bin/python -m pytest body/ -q  # 83 passed   (was 59)
.venv-v1/bin/python scripts/sim_session_run.py --sessions 20
```

**10 of 10 deliberate mutations caught**, each restoring the source byte-identically:
`T_SIM` authorizing outside `BACKTEST`; the sim gate downgraded to a bypass; a slide
into `T_SIM` permitted; `T_SIM.may_open_risk` turned on; `detect_environment` always
returning `BACKTEST`; an unknown clock reading as `BACKTEST`; `execute()` reachable at
a live tier; `execute()`'s environment check removed; the policy no longer declaring
itself; the policy emitting on every bar.

## What this does not license

No edge exists, and nothing here suggests one does. The policy is a fixed-schedule
emitter chosen precisely so that no result can be mistaken for a signal. Live tiers
still refuse everything, `execute()` still raises at every live tier, and the ODD's
T0 remains sealed.

The magnitude-conditioned policy was **not touched** this session, deliberately. It
drops into the same signature when it is ready.
