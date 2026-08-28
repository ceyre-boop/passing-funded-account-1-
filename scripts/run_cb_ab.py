#!/usr/bin/env python3
"""Decisive experiment: does the quarantined CB-surprise layer carry the v015 edge?

SAME engine (sovereign.forex.forex_backtester.ForexBacktester via
scripts/oos_campaign_test.get_trades), SAME data, SAME window
(2015-01-01 -> 2024-12-31), SAME rate vintage ("nominal" -- the mode that
produced the 08-26 anchor run), with exactly ONE variable flipped between
the two arms: sovereign.forex.entry_engine.CB_LAYER_DISABLED.

  cb_on   CB_LAYER_DISABLED = False, CBEventTrigger reads the quarantined
          data/cache/cb_decisions.json.FABRICATED file. Should reproduce the
          08-26 nominal run: 288 trades, avg_r ~= +0.3226.
  cb_off  CB_LAYER_DISABLED = True (the current, live default). No CB events.

Both arms are driven in the same process, back to back, via the exact same
call path -- entry_engine.CB_LAYER_DISABLED and .CB_DECISIONS_PATH are patched
as module attributes immediately before each get_trades() call. Nothing in
sovereign/forex/entry_engine.py is edited, and data/cache/cb_decisions.json.FABRICATED
is read in place -- never renamed back.

Reuses scripts/run_vintage_ab.py's trade-collection call path and
scripts/vintage_ab_stats.py's stats/control machinery (arm(), perm_null(),
matched()) rather than re-deriving either. Writes to data/cb_ab/ only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from vintage_ab_stats import arm as arm_stats, matched, perm_null  # noqa: E402

OUT_DIR = ROOT / "data" / "cb_ab"
START, END = "2015-01-01", "2024-12-31"
RATE_MODE = "nominal"  # the mode that produced the 08-26 anchor (288 trades, avg_r +0.3226)
CB_FABRICATED_PATH = ROOT / "data" / "cache" / "cb_decisions.json.FABRICATED"

# data/cache/macro_nominal/ (built by scripts/build_rate_vintages.py) is not
# present in this working tree right now -- it is a rebuildable cache, not
# sealed evidence, but building it under data/cache/ would violate the
# read-only constraint on that directory for this task. An identical copy
# (byte-for-byte, verified by md5) exists in a sibling worktree from the same
# 08-26 vintage-AB run; it is copied to the session scratchpad (never under
# data/cache/) and sovereign.forex.rate_vintage._DIRS['nominal'] is patched
# in-process to point at it -- same technique as the CB_DECISIONS_PATH patch
# below, and equally reversible / non-invasive.
SCRATCH_NOMINAL_DIR = Path(
    "/private/tmp/claude-501/-Users-taboost-passing-funded-account-1-/"
    "42802b2d-5476-426f-b67f-631a5679f292/scratchpad/macro_nominal"
)
ANCHOR_N, ANCHOR_AVG_R = 288, 0.3226
RNG = np.random.default_rng(20260827)
DRAWS = 10000


def _r(trades: list[dict]) -> list[float]:
    return [t["pnl_pct"] / t["risk_pct"] for t in trades]


def run_arm(cb_on: bool) -> list[dict]:
    """Run get_trades() once with CB_LAYER_DISABLED patched to the desired state.

    No sys.modules reimport: CARRY_RATE_VINTAGE is read live (os.environ.get)
    on every call by sovereign/forex/rate_vintage.py, and CBEventTrigger is
    constructed fresh inside ForexBacktester.__init__ on every get_trades()
    call, so patching the entry_engine module attributes immediately before
    the call is sufficient -- and keeps everything else byte-identical
    between arms, which is the point of a one-variable A/B.
    """
    os.environ["CARRY_RATE_VINTAGE"] = RATE_MODE

    for m in list(sys.modules):
        if m.startswith("sovereign.forex") or m == "oos_campaign_test":
            del sys.modules[m]
    import oos_campaign_test as rig  # noqa: E402  (fresh import per call, on purpose)
    from sovereign.forex import entry_engine  # noqa: E402
    from sovereign.forex import rate_vintage  # noqa: E402

    if SCRATCH_NOMINAL_DIR.exists():
        rate_vintage._DIRS[rate_vintage.NOMINAL] = SCRATCH_NOMINAL_DIR

    entry_engine.CB_LAYER_DISABLED = not cb_on
    if cb_on:
        entry_engine.CB_DECISIONS_PATH = CB_FABRICATED_PATH
    assert entry_engine.CB_LAYER_DISABLED == (not cb_on), (
        f"CB_LAYER_DISABLED patch did not take: expected {not cb_on}, "
        f"got {entry_engine.CB_LAYER_DISABLED}"
    )
    print(
        f"[run_arm] cb_on={cb_on} "
        f"CB_LAYER_DISABLED={entry_engine.CB_LAYER_DISABLED} "
        f"CB_DECISIONS_PATH={entry_engine.CB_DECISIONS_PATH} "
        f"nominal_dir={rate_vintage.macro_cache_dir('nominal')}"
    )

    trades = rig.get_trades(START, END)
    for t in trades:
        t.setdefault("risk_pct", 0.0075)
    return trades


def trades_to_df(trades: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(
        [
            {
                "pair": t["pair"],
                "entry_date": t["entry_date"],
                "exit_date": t["exit_date"],
                "direction": t.get("direction"),
                "pnl_pct": t["pnl_pct"],
                "risk_pct": t["risk_pct"],
                "hold_days": t["hold_days"],
                "exit_reason": t.get("exit_reason"),
            }
            for t in trades
        ]
    )
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df["R"] = df["pnl_pct"] / df["risk_pct"]
    return df


def exact_match(a: pd.DataFrame, b: pd.DataFrame):
    """Match on (pair, entry_date) exactly -- the CB layer only fills or
    overrides an existing signal slot on the SAME entry-date grid; it never
    shifts an entry date. Fuzzy day-tolerance matching (vintage_ab_stats.matched)
    is the wrong tool here."""
    key_a = list(zip(a["pair"], a["entry_date"]))
    key_b = list(zip(b["pair"], b["entry_date"]))
    set_a, set_b = set(key_a), set(key_b)
    both = set_a & set_b
    a_idx = [i for i, k in enumerate(key_a) if k in both]
    b_idx = [i for i, k in enumerate(key_b) if k in both]
    a_only = a[[k not in set_b for k in key_a]].reset_index(drop=True)
    b_only = b[[k not in set_a for k in key_b]].reset_index(drop=True)
    ma = a.iloc[a_idx].sort_values(["pair", "entry_date"]).reset_index(drop=True)
    mb = b.iloc[b_idx].sort_values(["pair", "entry_date"]).reset_index(drop=True)
    return ma, mb, a_only, b_only


def paired_stats(cb_on_df: pd.DataFrame, cb_off_df: pd.DataFrame) -> dict:
    matched_on, matched_off, on_only, off_only = exact_match(cb_on_df, cb_off_df)
    assert len(matched_on) == len(matched_off)

    n_dir_changed = int((matched_on["direction"] != matched_off["direction"]).sum())
    n_hold_changed = int((matched_on["hold_days"] != matched_off["hold_days"]).sum())
    n_exit_changed = int((matched_on["exit_date"] != matched_off["exit_date"]).sum())

    delta = (matched_on["R"] - matched_off["R"]).to_numpy()
    if len(delta):
        boot = np.array(
            [delta[RNG.integers(0, len(delta), len(delta))].mean() for _ in range(DRAWS)]
        )
        ci = [round(float(np.quantile(boot, 0.025)), 4), round(float(np.quantile(boot, 0.975)), 4)]
        mean_delta = round(float(delta.mean()), 4)
    else:
        ci = [None, None]
        mean_delta = None

    on_only_r = on_only["R"] if len(on_only) else pd.Series(dtype=float)
    cb_on_only_block = {
        "n": int(len(on_only)),
        "avg_r": round(float(on_only_r.mean()), 4) if len(on_only_r) else None,
        "win_rate": round(float((on_only_r > 0).mean()), 4) if len(on_only_r) else None,
        "total_r": round(float(on_only_r.sum()), 2) if len(on_only_r) else None,
    }

    per_pair = {}
    for pair in sorted(set(cb_on_df["pair"]) | set(cb_off_df["pair"])):
        on_p = cb_on_df[cb_on_df["pair"] == pair]["R"]
        off_p = cb_off_df[cb_off_df["pair"] == pair]["R"]
        per_pair[pair] = {
            "cb_on": {"n": int(len(on_p)), "avg_r": round(float(on_p.mean()), 4) if len(on_p) else None},
            "cb_off": {"n": int(len(off_p)), "avg_r": round(float(off_p.mean()), 4) if len(off_p) else None},
        }

    return {
        "n_matched": int(len(matched_on)),
        "n_cb_on_only": int(len(on_only)),
        "n_cb_off_only": int(len(off_only)),
        "matched_n_direction_changed": n_dir_changed,
        "matched_n_exit_date_changed": n_exit_changed,
        "matched_n_hold_days_changed": n_hold_changed,
        "matched_mean_delta_r_on_minus_off": mean_delta,
        "matched_delta_r_95ci_bootstrap": ci,
        "bootstrap_draws": DRAWS,
        "bootstrap_seed": 20260827,
        "cb_on_only": cb_on_only_block,
        "per_pair": per_pair,
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cb_on_trades = run_arm(cb_on=True)
    cb_off_trades = run_arm(cb_on=False)

    cb_on_df = trades_to_df(cb_on_trades)
    cb_off_df = trades_to_df(cb_off_trades)

    cb_on_df.drop(columns=["R"]).to_csv(OUT_DIR / "cb_on_trades.csv", index=False)
    cb_off_df.drop(columns=["R"]).to_csv(OUT_DIR / "cb_off_trades.csv", index=False)

    anchor_ok = (
        abs(len(cb_on_df) - ANCHOR_N) <= 2
        and abs(float(cb_on_df["R"].mean()) - ANCHOR_AVG_R) <= 0.01
    )

    stats = {
        "anchor_check": {
            "expected_n": ANCHOR_N,
            "expected_avg_r": ANCHOR_AVG_R,
            "observed_n": int(len(cb_on_df)),
            "observed_avg_r": round(float(cb_on_df["R"].mean()), 4),
            "match": anchor_ok,
        },
        "cb_on": arm_stats(cb_on_df, "cb_on (CB_LAYER_DISABLED=False)"),
        "cb_off": arm_stats(cb_off_df, "cb_off (CB_LAYER_DISABLED=True, current default)"),
        "paired": paired_stats(cb_on_df, cb_off_df),
        "cb_off_permutation_control": perm_null(cb_off_df),
    }

    (OUT_DIR / "stats.json").write_text(json.dumps(stats, indent=2, default=str))

    if not anchor_ok:
        print(
            "\n*** ANCHOR MISMATCH *** expected n=288 avg_r=+0.3226, "
            f"got n={len(cb_on_df)} avg_r={cb_on_df['R'].mean():+.4f}. "
            "A/B is INVALID -- something besides CB_LAYER_DISABLED differs. "
            "Not adjusting anything; reporting as-is.\n"
        )
    else:
        print(f"\nAnchor check OK: n={len(cb_on_df)}, avg_r={cb_on_df['R'].mean():+.4f}\n")

    print(
        f"{'arm':<8} {'n':>5} {'avg_r':>8} {'WR':>7} {'sharpe':>8} {'total_r':>9}"
    )
    for label, df in (("cb_on", cb_on_df), ("cb_off", cb_off_df)):
        r = df["R"]
        print(
            f"{label:<8} {len(df):>5} {r.mean():>8.4f} {r.gt(0).mean():>7.1%} "
            f"{stats[label]['sharpe']:>8.4f} {r.sum():>9.2f}"
        )

    print("\ncb_on-only trades (the layer's own P&L contribution):")
    print(json.dumps(stats["paired"]["cb_on_only"], indent=2))

    print("\nper-pair (n, avg_r) cb_on vs cb_off:")
    for pair, d in stats["paired"]["per_pair"].items():
        print(f"  {pair}: cb_on n={d['cb_on']['n']} avg_r={d['cb_on']['avg_r']}  "
              f"cb_off n={d['cb_off']['n']} avg_r={d['cb_off']['avg_r']}")

    print(
        f"\ncb_off direction-permutation control: "
        f"p_avg_r_ge_observed={stats['cb_off_permutation_control']['p_avg_r_ge_observed']}"
    )

    print(f"\nwrote {OUT_DIR / 'stats.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
