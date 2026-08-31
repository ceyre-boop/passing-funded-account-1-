# Nautilus Trader as the body — fit assessment

**Date:** 2026-08-31 · **Upstream:** `nautechsystems/nautilus_trader`
· **Clone:** `~/nautilus_trader` (403 MB, already present, HEAD `b608206601`)
· **Installed:** v1 `1.231.0` in `.venv-v1` (py3.13), v2 `2.0.0rc3` in `.venv` (py3.14)

**Verdict: yes, it is the right body — and build on v1, not v2.** The single
strongest reason is not performance or venue coverage. It is that Nautilus's
component model makes this repo's central architectural rule *structurally
unbreakable* instead of a convention enforced by review.

---

## The finding that decides it

`CLAUDE.md` states the boundary the whole cockpit rests on:

> AlphaZero communicates meaning. Stockfish controls mechanics.
> **AlphaZero MUST NOT:** place orders, calculate executable exit quantities,
> move stops directly, bypass Stockfish constitution rules.

Today that is a documentation rule. Nothing in the code stops an AlphaZero module
from importing an order path — it is held by discipline and by review.

In Nautilus v1 the `Actor` and `Strategy` classes split exactly along that line,
verified directly against the installed package:

| method | `Strategy` | `Actor` |
|---|---|---|
| `submit_order` | yes | **no** |
| `submit_order_list` | yes | **no** |
| `cancel_order` | yes | **no** |
| `cancel_all_orders` | yes | **no** |
| `modify_order` | yes | **no** |
| `close_position` | yes | **no** |
| `close_all_positions` | yes | **no** |
| `order_factory` | yes | **no** |

`Actor` has **zero** order-submission surface — 47 methods exist on `Strategy`
that do not exist on `Actor` at all. What `Actor` *can* do is exactly what
AlphaZero is supposed to do: `publish_signal`, `subscribe_signal`,
`publish_data`.

**So: AlphaZero becomes an `Actor`. Stockfish becomes the `Strategy`.** The rule
stops being a thing we must not do and becomes a thing that does not typecheck —
`AttributeError`, not a code review comment. That is development rule 4, "make
invalid states unrepresentable," applied to the most important invariant in the
repo.

---

## The mapping

| this repo | Nautilus seam | note |
|---|---|---|
| AlphaZero — meaning / entry | `Actor` + `publish_signal` | no order authority by construction |
| Stockfish — exit mechanics | `Strategy` | sole holder of submit / cancel / modify / close |
| ODD §4 tier ladder | `TradingState` + `ComponentState` | see below — near-exact |
| ODD §3 MRM enforcement | `RiskEngine.set_trading_state()` | one call, engine-level, not per-strategy |
| ODD §2 preconditions gate | `RiskEngine` pre-trade checks + a gating `Actor` | |
| `daytrade/bars.py`, the parquet caches | `ParquetDataCatalog` | replaces homegrown loading |
| `decision_logger` | message bus + event store | every event already persisted |
| `ceiling.simulate` vs live | `BacktestEngine` / `TradingNode`, same `Strategy` | the parity this repo lacks |

### The tier ladder is already there

`TradingState` ships as `ACTIVE / REDUCING / HALTED`. ODD.md §4 defines
T1 Restricted / T2 Defensive ("manage existing only, no new risk") / T3 Halt.
`REDUCING` *is* T2 — same semantics, already implemented and tested upstream.

`ComponentState` carries `RUNNING → DEGRADING → DEGRADED → FAULTING → FAULTED`,
and `Component` exposes `degrade()` and `fault()` with `on_degrade` / `on_fault`
hooks on every actor and strategy. ODD.md §4's "tiers degrade automatically and
instantly, recover slowly and manually" is that state machine.

`az/odd.py` — written earlier today — should therefore become a **thin
translation layer** onto `TradingState`, not a parallel implementation of it.
Keeping two ladders would violate the repo's own one-implementation rule. What
`az/odd.py` keeps that Nautilus does not have: the three-valued `Truth`, the
`UNSET` threshold sentinel, the §2 checklist, and the T0 seal. Those are ours.

---

## v1 or v2 — build on v1

| | v1 `1.231.0` | v2 `2.0.0rc3` |
|---|---|---|
| status | stable | release candidate |
| core | Cython | Rust + PyO3 |
| Python | ≤ 3.13 | `>=3.12,<3.15` (3.14 OK) |
| `Strategy` | yes | yes |
| **`Actor`** | **yes** | **no — not ported** |
| `ExecAlgorithm` | yes | no |

v2 is the future and it already runs on this machine's 3.14. But **v2 rc3 ships
`Strategy` only** — no `Actor`, no `ExecAlgorithm`. The Actor/Strategy split is
the entire reason to adopt this platform, so v2 cannot deliver the one thing we
came for. Build on v1 under Python 3.13; revisit v2 when it ports `Actor`.
Upstream is explicit that v1 goes to `develop_v1` for ~3 months of security
backports only after cutover, so this is a known-finite runway, not a permanent
home.

---

## What this does not fix, stated plainly

**There is no edge to put inside the body.** As of today all four detection-floor
survivors are closed on economics (`artifacts/FLOOR_TEST_RESULT.md`), the entry
family is closed, and ODD.md §0 resolves its own scope line to *"never."*
Nautilus replaces `runner.py`, the bar loading, the execution plumbing, and the
backtest/live gap. It replaces none of the search for an edge, and adopting it
does not move a single result.

The honest framing: this is chassis work, and it is the *right* chassis work —
it is where `runner.py:132-153` silently ignoring a typo'd plan key on the live
path stops being possible. But a better body around no edge is still no edge.

## Costs to accept before committing to it

1. **LGPL-3.0-only.** Importing it as a library for personal trading creates no
   obligation. Distributing anything that embeds it does. Fine today; a real
   constraint if this ever becomes a product.
2. **Size.** 403 MB source clone, 54 MB wheel, a Rust toolchain if ever built
   from source. `CLAUDE.md` says this repo is small on purpose — this is the
   largest single dependency it would ever take.
3. **Migration is a rewrite, not a wiring job.** `daytrade/` assumes it owns the
   loop. Nautilus owns the loop and calls you. Every component becomes an
   `on_bar` handler. That is a real project with its own gates.
4. **v1's runway is finite** (see above).

## Reproduce

```bash
uv venv --python 3.13 .venv-v1 && uv pip install --python .venv-v1/bin/python nautilus_trader
.venv-v1/bin/python -c "
from nautilus_trader.common.actor import Actor
from nautilus_trader.trading.strategy import Strategy
print('Actor can submit orders:', hasattr(Actor,'submit_order'))
print('Strategy can submit orders:', hasattr(Strategy,'submit_order'))"
```

Both venvs are gitignored. Nothing under `daytrade/`, `gate/`, `az/`, `specs/`
or `data/` was modified by this assessment.
