#!/usr/bin/env python3
"""scripts/sim_session_run.py — does the car drive?

THE METRIC CHANGED, OUT LOUD
    The scoreboard here is COMPLETIONS AND DISENGAGEMENTS, never R. An autonomous
    system that reliably loses money is still an engineering achievement, and
    unlike an edge it is testable today. This script therefore reports sessions
    completed, directives published, directives refused with reason, orders
    filled, positions orphaned, and exceptions raised.

    It never prints a P&L or an R multiple. If you find yourself wanting one,
    that is a different question and needs its own pre-registration.

WHAT IS BEING TESTED
    The full loop, on real historical bars, with a policy that claims no edge:

        perceive -> decide -> hand off -> execute -> log -> degrade

    The entry policy is `NullEntryPolicy`, a fixed-schedule emitter. That is the
    point: a loop that only works when the policy is good is not a loop that has
    been tested. Same discipline as the degraded-candidate arm in the exit work.

THE FOUR ATTESTATIONS ARE CHECKED, NOT DECLARED
    T_SIM runs its own four-line precondition list, and this runner attests each
    one only after actually verifying it -- the frozen exit-core hash is
    recomputed from disk and compared to the pinned value, the bar source is
    confirmed to sit outside the sealed proof directory, and the policy is asked
    whether it declares itself a calibration arm. An attestation that cannot be
    verified is left UNKNOWN, and UNKNOWN fails the gate closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT / "az"), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig  # noqa: E402
from nautilus_trader.config import LoggingConfig  # noqa: E402
from nautilus_trader.model.currencies import USD  # noqa: E402
from nautilus_trader.model.data import Bar, BarType  # noqa: E402
from nautilus_trader.model.enums import AccountType, OmsType  # noqa: E402
from nautilus_trader.model.identifiers import Venue  # noqa: E402
from nautilus_trader.model.objects import Money, Price, Quantity  # noqa: E402
from nautilus_trader.test_kit.providers import TestInstrumentProvider  # noqa: E402

from odd import Tier, Truth  # noqa: E402

from body.alphazero_actor import AlphaZeroActor  # noqa: E402
from body.entry_policy import VetoEntryPolicy  # noqa: E402
from body.null_policy import NullEntryPolicy  # noqa: E402
from body.runtime import assert_no_live_execution_client  # noqa: E402
from body.stockfish_strategy import StockfishStrategy  # noqa: E402

POLICIES = {"null": NullEntryPolicy, "veto": VetoEntryPolicy}

BARS = ROOT / "data" / "daytrade" / "bars_premarket" / "SPY_5m.parquet"
FROZEN = ROOT / "data" / "daytrade" / "SF_FROZEN_004.json"
SEALED = ROOT / "data" / "proof"
RTH_OPEN, RTH_CLOSE = "09:30", "15:55"
MIN_BARS = 40


# ------------------------------------------------------------- attestations

def attest(policy_cls) -> tuple[dict, list[str]]:
    """Verify each sim precondition. Anything unverifiable stays UNKNOWN."""
    notes, out = [], {}

    # checkpoint_hash — recompute, do not trust the file's own claim
    try:
        doc = json.loads(FROZEN.read_text())
        engine_path = ROOT / doc["engine"]
        actual = hashlib.sha256(engine_path.read_bytes()).hexdigest()
        match = actual == doc["engine_sha256"]
        out["checkpoint_hash"] = Truth.TRUE if match else Truth.FALSE
        notes.append(f"checkpoint_hash: {doc['checkpoint_id']} engine sha256 "
                     f"{'matches' if match else 'DRIFTED FROM'} pinned value")
    except Exception as e:                                    # noqa: BLE001
        out["checkpoint_hash"] = Truth.UNKNOWN
        notes.append(f"checkpoint_hash: UNKNOWN — {e}")

    # holdout_sealed — the bar source must not live under the sealed proof dir
    try:
        inside = SEALED.resolve() in BARS.resolve().parents
        out["holdout_sealed"] = Truth.FALSE if inside else Truth.TRUE
        notes.append(f"holdout_sealed: bar source is "
                     f"{'INSIDE' if inside else 'outside'} {SEALED.name}/ — "
                     f"{'refusing' if inside else 'nothing unsealed'}")
    except Exception as e:                                    # noqa: BLE001
        out["holdout_sealed"] = Truth.UNKNOWN
        notes.append(f"holdout_sealed: UNKNOWN — {e}")

    # policy_declares_no_edge — ask the policy ACTUALLY IN USE.
    # This used to hardcode NullEntryPolicy, which meant the attestation was
    # true no matter which policy drove: a real policy could have run while the
    # gate certified a different class. Same defect shape as a guard that tests
    # a helper instead of the path.
    declares = getattr(policy_cls, "DECLARES_NO_EDGE", False) is True
    out["policy_declares_no_edge"] = Truth.TRUE if declares else Truth.UNKNOWN
    notes.append(f"policy_declares_no_edge: {policy_cls.__name__}."
                 f"DECLARES_NO_EDGE={declares}")

    # no_live_venue — CORRECTED 2026-09-01. This used to be a hardcoded
    # Truth.TRUE beside a comment claiming it was "independently re-checked from
    # the injected clock". It was not re-checked, and the clock is what
    # authorize_entry already reads — so it was one sensor counted twice, and
    # two locks with one sensor is one lock. (The claim also went out in an
    # artifact; both are wrong and both are corrected.)
    #
    # The real second sensor is a DIFFERENT object: which ExecutionEngine class
    # the kernel constructed. A live TradingNode builds LiveExecutionEngine; a
    # BacktestEngine builds the plain one. Probed here on a throwaway engine
    # identical to the ones the run will use, and re-asserted per session on the
    # actual engine before it is allowed to run.
    try:
        probe = _probe_engine()
        assert_no_live_execution_client(probe.kernel.exec_engine)
        cls = type(probe.kernel.exec_engine).__name__
        probe.dispose()
        out["no_live_venue"] = Truth.TRUE
        notes.append(f"no_live_venue: kernel builds {cls} (not LiveExecutionEngine); "
                     "re-asserted per session on the real engine")
    except Exception as e:                                    # noqa: BLE001
        out["no_live_venue"] = Truth.UNKNOWN
        notes.append(f"no_live_venue: UNKNOWN — {e}")
    return out, notes


# ------------------------------------------------------------------- data

def load_sessions(limit: int) -> list[tuple[str, pd.DataFrame]]:
    df = pd.read_parquet(BARS)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert("America/New_York")
    hh = df.index.strftime("%H:%M")
    rth = df[(hh >= RTH_OPEN) & (hh <= RTH_CLOSE)]
    out = []
    for day, chunk in rth.groupby(rth.index.date):
        if len(chunk) >= MIN_BARS:
            out.append((day.isoformat(), chunk))
    return out[-limit:]


def _probe_engine() -> BacktestEngine:
    """An engine identical in kind to the ones the run will build, so the
    attestation inspects the real thing rather than asserting about it."""
    e = BacktestEngine(BacktestEngineConfig(
        trader_id="SIMPROBE-001", logging=LoggingConfig(bypass_logging=True)))
    e.add_venue(venue=Venue("SIM"), oms_type=OmsType.NETTING,
                account_type=AccountType.MARGIN, base_currency=USD,
                starting_balances=[Money(1_000_000, USD)])
    return e


def run_session(day: str, chunk: pd.DataFrame, attestations: dict,
                policy_cls=NullEntryPolicy) -> dict:
    engine = BacktestEngine(BacktestEngineConfig(
        trader_id="SIMDRIVE-001", logging=LoggingConfig(bypass_logging=True)))
    venue = Venue("SIM")
    engine.add_venue(venue=venue, oms_type=OmsType.NETTING,
                     account_type=AccountType.MARGIN, base_currency=USD,
                     starting_balances=[Money(1_000_000, USD)])
    inst = TestInstrumentProvider.equity(symbol="SPY", venue="SIM")
    engine.add_instrument(inst)

    bar_type = BarType.from_str(f"{inst.id}-5-MINUTE-LAST-EXTERNAL")
    bars = []
    for ts, row in chunk.iterrows():
        t = int(ts.value)
        bars.append(Bar(
            bar_type=bar_type,
            open=inst.make_price(float(row["Open"])),
            high=inst.make_price(float(row["High"])),
            low=inst.make_price(float(row["Low"])),
            close=inst.make_price(float(row["Close"])),
            volume=Quantity.from_int(int(row["Volume"])),
            ts_event=t, ts_init=t))
    engine.add_data(bars)

    policy = policy_cls()
    actor = AlphaZeroActor(bar_type=bar_type, policy=policy)
    strat = StockfishStrategy(tier=Tier.T_SIM, sim_attestations=attestations,
                              bar_type=bar_type)
    engine.add_actor(actor)
    engine.add_strategy(strat)
    # second sensor, on the ACTUAL engine, before a single bar is processed
    assert_no_live_execution_client(engine.kernel.exec_engine)
    engine.run()

    res = {
        "day": day, "bars": len(bars), "completed": True, "exception": None,
        "published": actor.published, "abstained": actor.abstained,
        "received": strat.received, "authorized": strat.authorized,
        "refused": (strat.rejected_stale + strat.rejected_unauthorized
                    + strat.rejected_warmup),
        "refusal_reasons": dict(strat.refusal_reasons),
        "orders_submitted": strat.orders_submitted,
        "orders_filled": strat.orders_filled,
        "positions_opened": strat.positions_opened,
        "positions_closed": strat.positions_closed,
        "orphans": strat.orphans,
        "vetoes": policy.veto_summary() if hasattr(policy, "veto_summary") else {},
        "bars_seen": getattr(policy, "seen", 0),
        # Per-bar ledgers carried out for the ODD §5 disengagement backfill.
        # CAPTURE ONLY — nothing here changes a decision; the policy has
        # already run by the time this is read.
        "ledgers": [vars(l) for l in getattr(policy, "ledgers", [])],
        "strategy_authorized": strat.authorized,
        "strategy_refused": (strat.rejected_stale + strat.rejected_unauthorized
                             + strat.rejected_warmup),
    }
    engine.dispose()
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="T_SIM full-loop session run")
    ap.add_argument("--sessions", type=int, default=20)
    ap.add_argument("--policy", choices=sorted(POLICIES), default="null",
                    help="which entry policy drives the loop")
    a = ap.parse_args(argv)

    attestations, notes = attest(POLICIES[a.policy])
    print("T_SIM SESSION RUN — does the car drive?")
    print(f"policy: {POLICIES[a.policy].__name__}")
    print("scoreboard: completions and disengagements. NO R, NO P&L.\n")
    print("attestations (verified, not declared):")
    for n in notes:
        print(f"  {n}")
    unverified = [k for k, v in attestations.items() if v is not Truth.TRUE]
    if unverified:
        print(f"\n  REFUSING: {unverified} not verified TRUE — the sim gate fails "
              "closed rather than running on an unchecked claim.")

    sessions = load_sessions(a.sessions)
    print(f"\nsessions: {len(sessions)} "
          f"({sessions[0][0]} .. {sessions[-1][0]})\n")

    results, exceptions = [], []
    for day, chunk in sessions:
        try:
            results.append(run_session(day, chunk, attestations,
                                       POLICIES[a.policy]))
        except Exception as e:                                # noqa: BLE001
            exceptions.append((day, repr(e)))
            results.append({"day": day, "completed": False, "exception": repr(e)})
            traceback.print_exc(limit=2)

    done = [r for r in results if r.get("completed")]
    agg = Counter()
    reasons = Counter()
    for r in done:
        for k in ("published", "received", "authorized", "refused",
                  "orders_submitted", "orders_filled", "positions_opened",
                  "positions_closed", "orphans"):
            agg[k] += r.get(k, 0)
        reasons.update(r.get("refusal_reasons", {}))

    w = 26
    print("=" * 58)
    print(f"{'sessions completed':<{w}} {len(done)} / {len(results)}")
    print(f"{'exceptions raised':<{w}} {len(exceptions)}")
    print(f"{'directives published':<{w}} {agg['published']}")
    print(f"{'directives received':<{w}} {agg['received']}")
    print(f"{'directives authorized':<{w}} {agg['authorized']}")
    print(f"{'directives refused':<{w}} {agg['refused']}")
    for k, v in reasons.most_common():
        print(f"{'    ' + k:<{w}} {v}")
    print(f"{'orders submitted':<{w}} {agg['orders_submitted']}")
    print(f"{'orders filled':<{w}} {agg['orders_filled']}")
    print(f"{'positions opened':<{w}} {agg['positions_opened']}")
    print(f"{'positions closed':<{w}} {agg['positions_closed']}")
    print(f"{'positions ORPHANED':<{w}} {agg['orphans']}")
    print("=" * 58)

    # The veto ledger. Not a result -- the instrument MECH-006 needs, which is
    # the one hypothesis about this layer still standing. A veto with no logged
    # ledger cannot be tested against a rate-matched control, and an untestable
    # veto is indistinguishable from timidity.
    vetoes = Counter()
    bars_seen = 0
    for r in done:
        vetoes.update(r.get("vetoes", {}))
        bars_seen += r.get("bars_seen", 0)
    if vetoes:
        print(f"\nVETO LEDGER — {bars_seen} bars evaluated, "
              f"{agg['published']} entries taken")
        for k, v in vetoes.most_common():
            print(f"  {k:<28} {v:>6}  ({v / max(1, bars_seen) * 100:>5.1f}% of bars)")
        print("  (a ledger, not a result — MECH-006 needs a rate-matched control)")
    for day, err in exceptions:
        print(f"  EXCEPTION {day}: {err}")
    drove = len(done) == len(results) and agg["orders_filled"] > 0
    print(f"\nVERDICT: {'THE CAR DRIVES' if drove else 'NOT YET'} — "
          f"{len(done)}/{len(results)} sessions completed, "
          f"{agg['orders_filled']} fills, {agg['orphans']} orphans.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
