#!/usr/bin/env python3
"""scripts/carry_exit_sprt.py — spec 045's gate, run once, everything required.

Order of operations (each a halt on failure, never a warning):
  1. hash guardrail — every input verified against artifacts/inventory.json; CARRY-FROZEN-001 verifies
  2. registry — the state keys must be as-of computable (feature_registry.require_as_of)
  3. the spec's incumbent-derived numbers are RECOMPUTED from the frozen artifacts and must equal
     the declared values (n_units 121, n_units_oof 97, sigma 0.7580, delta 0.1914) — if the data
     moved under the spec, stop
  4. anchored walk-forward → out-of-fold per-unit ΔR, base fill
  5. self-exclusion asserted on the result (I63): no OOF trade was in its own block's train set
  6. SPRT (base) · walk-forward again with the candidate-pessimistic fill → SPRT (pessimistic)
     · sign-flip permutation on the base unit ΔR
  7. the pre-registered decision rule → artifacts/freeze_decision.json; never loosened

Every parameter is a required CLI argument. The declared values are in spec 045 §7.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "daytrade"))

from daytrade.measure import MeasurementError  # noqa: E402
from daytrade.mechanisms import mde  # noqa: E402
from sovereign.forex import carry_checkpoint, inventory  # noqa: E402
from sovereign.forex import exit_tablebase as tb  # noqa: E402
from sovereign.forex.feature_registry import require_as_of  # noqa: E402
from sovereign.forex.fill_model import BaseFill, PessimisticFill  # noqa: E402
from sovereign.forex.permutation import sign_flip_test  # noqa: E402
from sovereign.forex.sprt import Decision, sprt  # noqa: E402

PATHS, TRADES, UNITS = "artifacts/carry_paths.parquet", "artifacts/carry_trades.parquet", "artifacts/carry_units.json"
CHECKPOINT = "data/carry/CARRY_FROZEN_001.json"
STATE_KEYS = ("unrealized_r_net", "t", "weekend_next")
DECLARED = {"n_units": 121, "n_units_oof": 97, "sigma": 0.7580, "delta": 0.1914}   # spec 045 §7, filled 76461f2


class DriverError(RuntimeError):
    pass


def _args(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    req = p.add_argument_group("declared parameters (spec 045 §7) — all required")
    for name, typ in (("--h-max", int), ("--n-min", int), ("--n-bins", int), ("--seed", int), ("--alpha", float),
                      ("--beta", float), ("--spread-mult", float), ("--slip-mult", float), ("--delay-bars", int),
                      ("--draws", int), ("--perm-seed", int)):
        req.add_argument(name, type=typ, required=True)
    req.add_argument("--blocks", required=True, help="comma-separated block start dates")
    req.add_argument("--t-buckets", required=True, help='e.g. "1-1,2-3,4-5,6-8,9-10"')
    return p.parse_args(argv)


def _units_frame(trades: pd.DataFrame, units: dict) -> pd.DataFrame:
    rows = []
    for uid, tids in units["units"].items():
        sub = trades[trades.trade_id.isin(tids)]
        rows.append({"unit_id": int(uid), "first_entry": pd.to_datetime(sub.entry_date).min(),
                     "incumbent_r": float(sub.incumbent_r_net.mean()), "n_trades": int(len(sub))})
    return pd.DataFrame(rows).sort_values(["first_entry", "unit_id"]).reset_index(drop=True)


def _check_declared(uf: pd.DataFrame, blocks: list[pd.Timestamp]) -> dict:
    sigma = float(uf.incumbent_r.std(ddof=1))
    n_units = int(len(uf))
    n_oof = int((uf.first_entry >= blocks[1]).sum())
    delta = float(mde(sigma, n_oof))
    got = {"n_units": n_units, "n_units_oof": n_oof, "sigma": round(sigma, 4), "delta": round(delta, 4)}
    bad = {k: (got[k], DECLARED[k]) for k in DECLARED if got[k] != DECLARED[k]}
    if bad:
        raise DriverError(f"the frozen data no longer yields spec 045's declared numbers: {bad} — halting, not re-deriving")
    return {"n_units": n_units, "n_units_oof": n_oof, "sigma": sigma, "delta": delta}


def _assert_self_excluded(result) -> int:
    """I63: every OOF trade was evaluated exactly once, and never sat in the train set of its own block."""
    seen: set[int] = set()
    for blk in result.blocks:
        train = set(blk.train_trade_ids)
        test = list(blk.test_trade_ids)
        if train & set(test):
            raise MeasurementError(f"block {blk.k}: {len(train & set(test))} trades are in both train and test")
        dup = seen & set(test)
        if dup:
            raise MeasurementError(f"block {blk.k}: {len(dup)} test trades already evaluated in an earlier block")
        seen |= set(test)
    if not seen:
        raise MeasurementError("no out-of-fold trades — self-exclusion asserted on an empty set is meaningless")
    return len(seen)


def _unit_deltas(oof: pd.DataFrame, uf: pd.DataFrame) -> pd.DataFrame:
    g = oof.groupby("unit_id").agg(delta_mean=("delta_r", "mean"), delta_sum=("delta_r", "sum"),
                                   n_trades=("trade_id", "size"), deviated=("deviated", "max"), block=("block", "first"))
    g = g.join(uf.set_index("unit_id")[["first_entry"]], how="left")
    if g.first_entry.isna().any():
        raise DriverError("an OOF unit is missing from the unit frame")
    return g.sort_values(["block", "first_entry"]).reset_index()


def _entry_value_function(res, paths: pd.DataFrame) -> dict:
    """spec 045 §10: V(s_entry) per block — the table's own (cross-fit, in-sample-optimistic) value at the
    t=1 cells — beside the OOF-realized mean R of the incumbent and the candidate for test trades that
    started in that cell. The handoff to ~/quant; labelled, never a gate input here."""
    t1 = paths[paths.t == 1].set_index("trade_id")
    out = {"label": "IN-SAMPLE-OPTIMISTIC table value at t=1, with OOF-realized means beside it; NOT an input to any gate",
           "blocks": {}}
    for b in res.blocks:
        cells = {}
        keys = {tid: tb.state_key(t1.loc[tid], b.disc) for tid in b.test_trade_ids if tid in t1.index}
        app = b.applied.set_index("trade_id") if "trade_id" in b.applied.columns else b.applied
        for key, cell in sorted(b.table.cells.items()):
            if key[1] != 1:
                continue
            tids = [tid for tid, k in keys.items() if k == key]
            sub = app.loc[[t for t in tids if t in app.index]] if tids else None
            cells[str(key)] = {"n_train": cell.n, "action": cell.action, "q_exit": cell.q_exit, "q_hold": cell.q_hold,
                               "value_cross_fit": cell.value,
                               "oof_n": int(len(sub)) if sub is not None else 0,
                               "oof_incumbent_mean_r": float(sub.incumbent_r_net.mean()) if sub is not None and len(sub) else None,
                               "oof_candidate_mean_r": float(sub.candidate_r.mean()) if sub is not None and len(sub) else None}
        out["blocks"][int(b.k)] = {"r_edges": list(b.disc.r_edges), "t1_cells": cells}
    return out


def _run_arm(name, fill, paths, trades, units, a, blocks, t_buckets, uf) -> tuple[dict, pd.DataFrame]:
    res = tb.walk_forward(paths, trades, units, blocks=tuple(b.strftime("%Y-%m-%d") for b in blocks), h_max=a.h_max,
                          n_min=a.n_min, n_bins=a.n_bins, t_buckets=t_buckets, seed=a.seed, fill=fill,
                          bins_out_dir=ROOT / "artifacts")
    n_oof_trades = _assert_self_excluded(res)
    ud = _unit_deltas(res.oof, uf)
    return res, ud, n_oof_trades


def main(argv=None) -> int:
    a = _args(sys.argv[1:] if argv is None else argv)
    blocks = [pd.Timestamp(x) for x in a.blocks.split(",")]
    t_buckets = tuple(tuple(int(v) for v in seg.split("-")) for seg in a.t_buckets.split(","))
    # 1. guardrail
    hashes = inventory.require_hashes([PATHS, TRADES, UNITS, CHECKPOINT])
    if carry_checkpoint.verify(quiet=True) != 0:
        raise DriverError("CARRY-FROZEN-001 does not verify")
    # 2. registry
    require_as_of(STATE_KEYS, context="tablebase state")
    paths = pd.read_parquet(ROOT / PATHS)
    trades = pd.read_parquet(ROOT / TRADES)
    with (ROOT / UNITS).open() as fh:
        units = json.load(fh)
    uf = _units_frame(trades, units)
    # 3. declared numbers recomputed
    dec = _check_declared(uf, blocks)
    sigma, delta = dec["sigma"], dec["delta"]

    out = {"spec": "045", "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "inputs": hashes,
           "params": {k: getattr(a, k) for k in vars(a)}, "declared": DECLARED, "recomputed": dec, "arms": {}}
    arms = {"base": BaseFill(),
            "pessimistic": PessimisticFill(spread_mult=a.spread_mult, slip_mult=a.slip_mult, delay_bars=a.delay_bars)}
    unit_deltas = {}
    for name, fill in arms.items():
        res, ud, n_oof_trades = _run_arm(name, fill, paths, trades, units, a, blocks, t_buckets, uf)
        d = ud.delta_mean.to_numpy()
        s = sprt(d.tolist(), delta=delta, sigma=sigma, alpha=a.alpha, beta=a.beta)
        unit_deltas[name] = ud
        per_block = {int(k): {"n_units": int(len(g)), "mean_delta": float(g.delta_mean.mean()), "sum_delta": float(g.delta_sum.sum()),
                              "deviation_rate": float(g.deviated.mean())} for k, g in ud.groupby("block")}
        out["arms"][name] = {
            "sprt": {"decision": s.decision.value, "stop_index": s.stop_index, "llr_final": s.llr_final,
                     "n_consumed": s.n_consumed, "n_available": s.n_available, "upper_bound": s.upper_bound,
                     "lower_bound": s.lower_bound, "llr_trace": list(s.llr_trace)},
            "n_oof_units": int(len(ud)), "n_oof_trades": n_oof_trades,
            "deviation_rate_units": float(ud.deviated.mean()), "mean_unit_delta": float(d.mean()),
            "sum_trade_delta": float(ud.delta_sum.sum()), "sigma_delta_empirical": float(d.std(ddof=1)) if len(d) > 1 else None,
            "per_block": per_block,
            "coverage": {int(b.k): b.coverage for b in res.blocks},
            "bins": {int(b.k): list(b.disc.r_edges) for b in res.blocks},
            "decided_exits": int(res.oof.decided.sum()),
            "fill": {"kind": name, **({"spread_mult": a.spread_mult, "slip_mult": a.slip_mult, "delay_bars": a.delay_bars} if name == "pessimistic" else {})},
        }
        ud.to_csv(ROOT / "artifacts" / f"unit_deltas_{name}.csv", index=False)
        if name == "base":
            (ROOT / "artifacts" / "entry_value_function.json").write_text(json.dumps(_entry_value_function(res, paths), indent=2, default=str) + "\n")
    perm = sign_flip_test(unit_deltas["base"].delta_mean.to_numpy(), draws=a.draws, seed=a.perm_seed)
    out["permutation_base"] = perm.__dict__
    out["secondary_411"] = "NOT RUN — no frozen paths exist for the contaminated 411; would need its own extraction. NO_INFERENTIAL_WEIGHT either way."
    b, pz = out["arms"]["base"]["sprt"]["decision"], out["arms"]["pessimistic"]["sprt"]["decision"]
    passes = (b == Decision.ACCEPT_H1.value) and (pz == Decision.ACCEPT_H1.value) and (perm.p_one_sided < a.alpha)
    dev = out["arms"]["base"]["deviation_rate_units"]
    reading = ("REPLACE — all three criteria met" if passes else
               ("NO DEVIATION — the table agreed with the incumbent on >90% of units; nothing was tested" if dev < 0.10 else
                f"INCUMBENT STAYS — base {b}, pessimistic {pz}, permutation p={perm.p_one_sided:.4f}"))
    out["decision"] = {"carry_exit_v1_replaces_incumbent": bool(passes), "reading": reading,
                       "rule": "ACCEPT_H1 on both arms AND permutation p < alpha; otherwise CARRY-FROZEN-001 stays"}
    (ROOT / "artifacts" / "sprt_result.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    (ROOT / "artifacts" / "freeze_decision.json").write_text(json.dumps(out["decision"] | {"run_at": out["run_at"], "spec": "045"}, indent=2) + "\n")
    inventory.record({"artifacts/sprt_result.json": "spec 045 gate output", "artifacts/freeze_decision.json": "spec 045 decision",
                      "artifacts/entry_value_function.json": "spec 045 §10 handoff — in-sample-optimistic V(s_entry) with OOF-realized means beside it"})

    print(f"spec 045 gate — n_units {dec['n_units']} oof {dec['n_units_oof']} sigma {sigma:.4f} delta {delta:.4f} alpha {a.alpha} beta {a.beta}")
    for name in arms:
        r = out["arms"][name]
        print(f"[{name}] SPRT {r['sprt']['decision']} at {r['sprt']['stop_index']} of {r['sprt']['n_available']} units, LLR {r['sprt']['llr_final']:+.3f} "
              f"(A {r['sprt']['upper_bound']:.3f} B {r['sprt']['lower_bound']:.3f}) | mean unit ΔR {r['mean_unit_delta']:+.4f} sum trade ΔR {r['sum_trade_delta']:+.3f} "
              f"| deviation {r['deviation_rate_units']:.0%} | decided exits {r['decided_exits']} | σ_ΔR {r['sigma_delta_empirical']:.3f}")
        for k, pb in r["per_block"].items():
            print(f"   block {k}: n {pb['n_units']} meanΔ {pb['mean_delta']:+.4f} sumΔ {pb['sum_delta']:+.3f} dev {pb['deviation_rate']:.0%} coverage {r['coverage'][k]:.0%} bins {[round(x,3) for x in r['bins'][k]]}")
    print(f"[permutation base] p={perm.p_one_sided:.4f} obs mean {perm.observed_mean:+.4f} null 5-95% [{perm.null_q05:+.4f}, {perm.null_q95:+.4f}] nonzero {perm.n_nonzero}/{perm.n}")
    print(f"DECISION: {reading}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (DriverError, MeasurementError, inventory.InventoryError) as e:
        print(f"HALT: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
