#!/usr/bin/env python3
"""scripts/carry_bench.py — the carry engine's `bench`: a fixed workload, one number.

Stockfish's discipline: any refactor that changes this number without intending
to is a bug. The workload is the frozen path dataset (CARRY-FROZEN-001 datasets)
replayed bar-by-bar through the pinned `exit_machine.decide_exit`; the number is
the incumbent's total net R over the 350 trades, printed at full precision.

What it checks before printing anything (any failure → exit 1, nothing printed
on stdout): the inventory hashes of both parquets match, CARRY-FROZEN-001
verifies, and every replayed decision equals the frozen `incumbent_action` on
every row up to the incumbent's exit (parity — 2,940 rows).

The 20 trades whose incumbent exit lies past H_max=10 have no rows past 10; their
R is the frozen `incumbent_r_net` (an HMAX-inherit), so the replay contributes
their first ten decisions (all HOLD) to parity and the frozen number to the sum.

Usage:  python3 scripts/carry_bench.py            # prints the number
        python3 scripts/carry_bench.py --check    # compares against artifacts/bench.txt (pre-commit)
No rig import. No network. ~1 s.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from sovereign.forex import carry_checkpoint, inventory  # noqa: E402
from carry_tablebase_paths import replay_decisions, unrealized_r  # noqa: E402

PATHS = "artifacts/carry_paths.parquet"
TRADES = "artifacts/carry_trades.parquet"
BENCH_TXT = ROOT / "artifacts" / "bench.txt"
STRICT_MODE = False
ENABLE_CB_REFRESH = True
DECISION_TO_ACTION = {0: "HOLD", 1: "EXIT:stop", 2: "EXIT:reversal", 3: "EXIT:cb_refresh",
                      4: "EXIT:time", 5: "EXIT:trailing_stop", 6: "EXIT:donchian_exit"}


class BenchError(RuntimeError):
    pass


def bench(*, trailing_mult_scale: float = 1.0, verify_inputs: bool = True) -> tuple[float, int]:
    """Return (total_net_r, rows_checked). `trailing_mult_scale` exists ONLY for the
    fault-injection test (I70) — the bench proper always runs at 1.0."""
    if verify_inputs:
        inventory.require_hashes([PATHS, TRADES])
        if carry_checkpoint.verify(quiet=True) != 0:
            raise BenchError("CARRY-FROZEN-001 does not verify — refusing to bench a moved opponent")
    paths = pd.read_parquet(ROOT / PATHS)
    trades = pd.read_parquet(ROOT / TRADES).set_index("trade_id")
    total = 0.0
    checked = 0
    for tid, rows in paths.groupby("trade_id", sort=True):
        rows = rows.sort_values("t")
        tr = trades.loc[tid]
        max_t = int(rows.t.max())
        if not (rows.t.to_numpy() == np.arange(1, max_t + 1)).all():
            raise BenchError(f"trade {tid}: decision bars are not contiguous 1..{max_t}")
        decs = replay_decisions(
            closes=rows.close.to_numpy(), atr_pcts=rows.atr_pct.to_numpy(), signals=rows.signal.to_numpy(),
            hold_days_arr=rows.hold_today.to_numpy(), entry_bar=0, direction=int(tr.direction),
            entry_price=float(tr.entry_price), stop_price=float(tr.stop_price), hold_limit=int(tr.hold_limit),
            stop_atr_mult=float(tr.stop_atr_mult), trailing_atr_mult=float(tr.trailing_mult) * trailing_mult_scale,
            strict_mode=STRICT_MODE, enable_cb_refresh=ENABLE_CB_REFRESH, max_t=max_t)
        inc_hold = int(tr.incumbent_hold)
        exit_t = None
        for (t, _bar, d), action in zip(decs, rows.incumbent_action.to_numpy()):
            if t <= min(inc_hold, max_t) and trailing_mult_scale == 1.0:
                if DECISION_TO_ACTION[d] != action:
                    raise BenchError(f"parity: trade {tid} t={t} replay {DECISION_TO_ACTION[d]} != frozen {action}")
                checked += 1
            if d != 0 and exit_t is None:
                exit_t = t
        if exit_t is None:
            # incumbent still in at max_t: HMAX-inherit — the frozen realized R
            if trailing_mult_scale == 1.0 and inc_hold <= max_t:
                raise BenchError(f"trade {tid}: no exit replayed but incumbent exited at t={inc_hold}")
            total += float(tr.incumbent_r_net)
        else:
            close = float(rows.loc[rows.t == exit_t, "close"].iloc[0])
            _, net = unrealized_r(direction=int(tr.direction), close=close, entry_price=float(tr.entry_price),
                                  cost_frac=float(tr.cost_frac), risk_dist=float(tr.risk_dist), risk_pct=float(tr.risk_pct))
            total += net
    return total, checked


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        total, checked = bench()
    except Exception as e:  # noqa: BLE001 — any failure is a bench failure, reported on stderr
        print(f"BENCH FAIL: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    number = f"{total:.10f}"
    if "--check" in argv:
        if not BENCH_TXT.exists():
            print(f"BENCH FAIL: {BENCH_TXT.relative_to(ROOT)} missing — run without --check once and commit it", file=sys.stderr)
            return 1
        expected = BENCH_TXT.read_text().strip()
        if expected != number:
            print(f"BENCH FAIL: {number} != {expected} (artifacts/bench.txt); rows checked {checked}", file=sys.stderr)
            return 1
        print(f"bench ok {number} ({checked} rows parity)")
        return 0
    print(number)
    return 0


if __name__ == "__main__":
    sys.exit(main())
