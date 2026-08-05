#!/usr/bin/env python3
"""CEILING — spec 008. What is regime knowledge WORTH, before we build it?

    Z = best single exit config applied to every day
    W = best config chosen PER DAY with perfect hindsight
    W - Z = the entire prize a regime classifier could ever win

Measured over two action spaces, because a small prize is ambiguous and the
ambiguity is fatal if ignored:

    prize_narrow  — reachable through the three shipped policies (spec 003)
    prize_wide    — reachable through any lever the engine COULD have

`prize_wide` big with `prize_narrow` small does not mean "don't build the
classifier". It means the knowledge is valuable and the exit engine's vocabulary
is the bottleneck — redesign the action space, not the classifier. A perfect
classifier handed three trail settings is a grandmaster allowed three moves.

THE ORACLE IS NOT ACHIEVABLE. It is an upper bound computed with perfect
hindsight and it is labelled as such on every line it prints. No component may
ever cite it as an expected result.

BOTH Z AND W ARE IN-SAMPLE, deliberately. Z is the best fixed config in
hindsight; W is the best per-day config in hindsight. Each is optimistic on its
own — but their DIFFERENCE is exactly the incremental value of day-level
discrimination, which is the quantity we want, and the shared optimism cancels.

Runs on the TUNE split only (spec 008): measuring a ceiling on the holdout
contaminates the holdout.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bars import load_sessions                                    # noqa: E402
from splits import tune_sessions, describe                        # noqa: E402
from stockfish_exit import (TradeState, decide_exit, apply_action,  # noqa: E402
                            policy_params, POLICY_PARAMS)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "daytrade"

# --- the mechanical entry, stated up front and identical across every arm -----
# Adapted from THE_SHOT.md: opening range 09:30-10:00, then the first 5-minute
# bar that CLOSES outside it. Direction comes from the break, never from opinion.
# The comparison here is EXIT-ONLY; if entries varied between arms the whole
# measurement would be meaningless.
OR_START, OR_END = "09:30", "10:00"
TRIGGER_END = "11:00"
TP1_R, TP2_R = 1.0, 2.14          # TP2 = THE_SHOT's 2.14R day-goal geometry
TRAIL_R = 0.5                     # base trail distance, in R; policies scale it
COST_PER_SHARE = 0.02             # round-trip, pessimistic-ish for NVDA NBBO


@dataclass(frozen=True)
class Entry:
    day: str
    ts: str
    time_block: str
    direction: int
    entry: float
    stop: float
    risk: float                    # dollars per share, the R unit
    tp1: float
    tp2: float
    trail_dist: float


def time_block(hhmm: str) -> str:
    if hhmm < "09:30": return "PREOPEN"
    if hhmm < "10:30": return "OPEN_DRIVE"
    if hhmm < "11:30": return "MORNING"
    if hhmm < "14:00": return "MIDDAY"
    if hhmm < "15:30": return "AFTERNOON"
    return "CLOSE"


def find_entry(session) -> Entry | None:
    """First 5m close outside the opening range, inside the trigger window."""
    or_bars = session.between(OR_START, OR_END)
    if len(or_bars) < 6:
        return None
    or_high, or_low = float(or_bars["High"].max()), float(or_bars["Low"].min())

    window = session.between(OR_END, TRIGGER_END)
    for ts, bar in window.iterrows():
        c = float(bar["Close"])
        if c > or_high:
            direction, stop = +1, float(bar["Low"])
        elif c < or_low:
            direction, stop = -1, float(bar["High"])
        else:
            continue
        risk = abs(c - stop)
        if risk <= 0:
            continue                       # degenerate bar, no risk geometry
        hhmm = ts.strftime("%H:%M")
        return Entry(str(session.day), ts.isoformat(), time_block(hhmm), direction,
                     c, stop, risk,
                     c + direction * TP1_R * risk,
                     c + direction * TP2_R * risk,
                     TRAIL_R * risk)
    return None


def _fill(action, st: TradeState, e: Entry, bar_open: float, bar_close: float) -> float:
    """Where an EXIT_ALL actually fills. Pessimistic, but not fictional.

    A resting stop fills AT the stop when the bar trades through it — NOT at the
    bar's worst tick. A 5-minute bar that wicks a dollar past the stop and
    recovers still takes you out at your price. Charging the full intrabar
    excursion to every stop-out is not conservatism, it is a made-up loss, and it
    made every fixed policy look worse than a full -1R.

    The genuine exception is a GAP: if the bar OPENED beyond the stop there was
    never a chance to fill at it, so the fill is the open. That is the real
    pessimistic case and it is the one worth modelling.

    Time-flatten and TP2-exit are market orders at a moment, not at an extreme,
    so they fill at the bar close.
    """
    if not action.reason.startswith("stop hit"):
        return bar_close
    gapped = bar_open < st.sl if e.direction > 0 else bar_open > st.sl
    return bar_open if gapped else st.sl


def simulate(session, e: Entry, cfg: dict) -> float:
    """Replay the rest of the session under one exit config. Returns R.

    Bars are presented to the engine in PESSIMISTIC order — adverse extreme
    first, then favourable, then close — so when a bar could have hit both the
    stop and the target, the stop wins. Intrabar path is unknowable at 5m
    resolution; assuming the good outcome is how backtests lie.
    """
    st = TradeState(direction=e.direction, entry=e.entry, qty=1.0, price=e.entry,
                    sl=e.stop, tp1=e.tp1, tp2=e.tp2, trail_dist=e.trail_dist,
                    goal_fraction=cfg["partial_frac"],
                    trail_mult=cfg["trail_mult"], be_arm_frac=cfg["be_arm_frac"],
                    hold_past_tp2=cfg["hold_past_tp2"],
                    flatten_at_et=cfg["flatten_et"])

    realized, held = 0.0, 1.0
    bars = session.after(e.ts[11:16])

    for ts, bar in bars.iterrows():
        o, c = float(bar["Open"]), float(bar["Close"])
        adverse = float(bar["Low"] if e.direction > 0 else bar["High"])
        favour = float(bar["High"] if e.direction > 0 else bar["Low"])
        st.now_et = ts.strftime("%H:%M")

        for px in (adverse, favour, c):
            st.price = px
            for a in decide_exit(st):
                if a.kind == "TAKE_PARTIAL":
                    realized += held * a.fraction * (st.tp2 - e.entry) * e.direction
                    held -= held * a.fraction
                elif a.kind == "EXIT_ALL":
                    realized += held * (_fill(a, st, e, o, c) - e.entry) * e.direction
                    return (realized - COST_PER_SHARE) / e.risk
                apply_action(st, a)

    # never resolved — flat at the last print
    realized += held * (float(bars["Close"].iloc[-1]) - e.entry) * e.direction
    return (realized - COST_PER_SHARE) / e.risk


# --- action spaces -----------------------------------------------------------

def narrow_space() -> dict[str, dict]:
    """The three policies as spec 003 shipped them.

    DELIBERATELY still the legacy names (STATIC / TRAIL_WIDE / TRAIL_TIGHT) even
    though v3 renamed the vocabulary to DEFEND / HARVEST / RIDE / EVENT /
    SALVAGE. `prize_narrow` means "what was reachable through the policies as
    designed at the time of measurement"; swapping in a different, larger set
    would silently redefine the quantity and make the 2026-08-03 number
    incomparable with every later one. Re-cut it deliberately, with a new
    baseline, or not at all.
    """
    out = {}
    for name in ("STATIC", "TRAIL_WIDE", "TRAIL_TIGHT"):
        p = policy_params(name)
        out[name] = {"trail_mult": p["trail_mult"], "be_arm_frac": p["be_arm_frac"],
                     "hold_past_tp2": p["hold_past_tp2"], "partial_frac": 0.5,
                     "flatten_et": None}
    return out


def wide_space() -> dict[str, dict]:
    """Deliberately larger than the ship set — every lever the engine could have.

    Configs that are behaviourally identical are collapsed: with
    hold_past_tp2=False the position is flat at TP2, so partial_frac and
    trail_mult can no longer matter. Leaving the duplicates in would inflate the
    oracle's apparent choice diversity without adding a single reachable outcome.
    """
    grid, seen = {}, set()
    for tm, be, pf, fl, hold in itertools.product(
            [None, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0],
            [0.25, 0.5, 1.0],
            [0.0, 0.25, 0.5, 0.75],
            ["11:00", "12:00", "15:45", None],
            [True, False]):
        key = (be, fl, hold) if not hold else (tm, be, pf, fl, hold)
        if key in seen:
            continue
        seen.add(key)
        name = (f"tm{tm}_be{be}_pf{pf}_fl{fl}_hold{int(hold)}" if hold
                else f"be{be}_fl{fl}_exitTP2")
        grid[name] = {"trail_mult": tm, "be_arm_frac": be, "partial_frac": pf,
                      "flatten_et": fl, "hold_past_tp2": hold}
    return grid


# --- the measurement ---------------------------------------------------------

def policy_ceiling(sessions, space: dict[str, dict], label: str) -> dict:
    entries = [(s, e) for s in sessions if (e := find_entry(s))]
    per_cfg = {name: {} for name in space}

    for s, e in entries:
        for name, cfg in space.items():
            per_cfg[name][e.day] = simulate(s, e, cfg)

    totals = {n: sum(d.values()) for n, d in per_cfg.items()}
    best_fixed = max(totals, key=totals.get)

    # Baselines. The oracle beats any fixed policy BY CONSTRUCTION (a max is never
    # below a mean), so "prize > 0" is not evidence of anything on its own. The
    # random picker is the honest floor: it is what a classifier with no skill
    # whatsoever collects, and any real classifier has to beat it.
    n_days = len(entries)
    rand_expected = sum(sum(per_cfg[c][e.day] for c in space) / len(space)
                        for _, e in entries)
    anti = sum(min(per_cfg[c][e.day] for c in space) for _, e in entries)
    # How many days had ANY winning configuration at all? If this is small, the
    # ceiling is set by the ENTRY, and no exit policy can raise it.
    live_days = sum(1 for _, e in entries
                    if max(per_cfg[c][e.day] for c in space) > 0.5)

    oracle_total, oracle_picks = 0.0, Counter()
    by_block = defaultdict(lambda: {"oracle": 0.0, "fixed": 0.0, "n": 0})
    for s, e in entries:
        pick = max(space, key=lambda n: per_cfg[n][e.day])
        oracle_total += per_cfg[pick][e.day]
        oracle_picks[pick] += 1
        b = by_block[e.time_block]
        b["oracle"] += per_cfg[pick][e.day]
        b["fixed"] += per_cfg[best_fixed][e.day]
        b["n"] += 1

    return {
        "label": label, "n_configs": len(space), "n_entries": len(entries),
        "per_config_total_R": totals,
        "best_fixed_policy": best_fixed,
        "Z_best_fixed_R": totals[best_fixed],
        "W_oracle_R": oracle_total,
        "prize_R": oracle_total - totals[best_fixed],
        # R PER TRADE is the load-bearing normalisation. Spec 008 pre-registered
        # the decision rule as "% of Z", which is degenerate here: Z came out near
        # zero, so the percentage divides by noise and explodes. Both are reported;
        # the percentage is kept only so the pre-registered rule can be evaluated
        # honestly and seen to be inapplicable, not quietly replaced.
        "prize_R_per_trade": (oracle_total - totals[best_fixed]) / max(n_days, 1),
        "Z_R_per_trade": totals[best_fixed] / max(n_days, 1),
        "W_R_per_trade": oracle_total / max(n_days, 1),
        "random_picker_R": rand_expected,
        "random_picker_R_per_trade": rand_expected / max(n_days, 1),
        "anti_oracle_R": anti,
        "days_with_any_winning_config": live_days,
        "prize_pct_of_Z": (100 * (oracle_total - totals[best_fixed]) / abs(totals[best_fixed])
                           if abs(totals[best_fixed]) > 1e-9 else float("inf")),
        "pct_denominator_degenerate": abs(totals[best_fixed]) / max(n_days, 1) < 0.15,
        "oracle_policy_hist": dict(oracle_picks.most_common(10)),
        "prize_by_time_block": {k: {"n": v["n"], "prize_R": v["oracle"] - v["fixed"]}
                                for k, v in by_block.items()},
        "_entries": [asdict(e) for _, e in entries],
        "_per_config": per_cfg,
    }


def report(narrow: dict, wide: dict) -> dict:
    nz, wz = narrow["Z_best_fixed_R"], wide["Z_best_fixed_R"]
    verdict, action = _verdict(narrow, wide)
    return {
        "n_sessions_tune": narrow["n_entries"],
        "always_static_R": narrow["per_config_total_R"]["STATIC"],
        "always_trail_wide_R": narrow["per_config_total_R"]["TRAIL_WIDE"],
        "always_trail_tight_R": narrow["per_config_total_R"]["TRAIL_TIGHT"],
        "Z_narrow": nz, "W_narrow": narrow["W_oracle_R"],
        "prize_narrow_R": narrow["prize_R"], "prize_narrow_pct": narrow["prize_pct_of_Z"],
        "Z_wide": wz, "W_wide": wide["W_oracle_R"],
        "prize_wide_R": wide["prize_R"], "prize_wide_pct": wide["prize_pct_of_Z"],
        "best_fixed_narrow": narrow["best_fixed_policy"],
        "best_fixed_wide": wide["best_fixed_policy"],
        "oracle_policy_hist_wide": wide["oracle_policy_hist"],
        "prize_by_time_block_wide": wide["prize_by_time_block"],
        "verdict": verdict, "prescribed_action": action,
        "prize_narrow_R_per_trade": narrow["prize_R_per_trade"],
        "prize_wide_R_per_trade": wide["prize_R_per_trade"],
        "random_picker_R": wide["random_picker_R"],
        "anti_oracle_R": wide["anti_oracle_R"],
        "days_with_any_winning_config": wide["days_with_any_winning_config"],
        "n_entries": wide["n_entries"],
    }


def _verdict(narrow: dict, wide: dict) -> tuple[str, str]:
    """Evaluate spec 008's PRE-REGISTERED rule — and refuse to replace it.

    The rule was written in "% of Z" before any number existed, which is exactly
    the right discipline. It just turns out not to be evaluable on this data: Z
    landed near zero, so the percentage divides by noise and reports 172% for a
    prize of 0.14R per trade.

    Substituting a threshold now, after seeing the numbers, is precisely the
    laundering spec 008 exists to prevent. So this returns NO_VERDICT and hands
    the re-registration back to a human. That is the correct output, not a
    failure of the measurement.
    """
    if wide["pct_denominator_degenerate"]:
        return ("NO_VERDICT — pre-registered rule is not evaluable",
                f"The rule thresholds on prize as a %% of Z, but Z = "
                f"{wide['Z_R_per_trade']:+.3f} R/trade is indistinguishable from zero, so "
                f"the percentage ({wide['prize_pct_of_Z']:.0f}%%) is an artefact of the "
                f"denominator. Absolute figures: prize_wide "
                f"{wide['prize_R_per_trade']:+.3f} R/trade, prize_narrow "
                f"{narrow['prize_R_per_trade']:+.3f} R/trade, ratio "
                f"{wide['prize_R_per_trade']/narrow['prize_R_per_trade']:.1f}x. "
                "Re-register a rule in R/trade BEFORE reading these again.")
    wide_pct, narrow_pct = wide["prize_pct_of_Z"], narrow["prize_pct_of_Z"]
    if wide_pct < 10:
        return ("NO_BUILD", "prize_wide < 10% of Z: regime knowledge has little value at "
                            "any lever. Ship the best fixed policy. This is a real, "
                            "money-saving outcome, not a failure.")
    ratio = wide_pct / narrow_pct if narrow_pct > 0 else float("inf")
    if ratio >= 2.0:
        return ("REDESIGN_ACTION_SPACE",
                f"prize_wide is {ratio:.1f}x prize_narrow: the information is valuable and "
                "the exit engine's vocabulary is the bottleneck. Redesign the action space "
                "FIRST, using oracle_policy_hist to see which levers the oracle reaches for.")
    if wide_pct < 25:
        return ("BUILD_TARGETED", "prize_wide 10-25%: build the classifier ONLY where "
                                  "prize_by_time_block says the value lives, not across "
                                  "the whole session.")
    return ("BUILD_FULLY", "prize_wide > 25%: build it. oracle_policy_hist becomes spec "
                           "003's real policy table.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="spec 008 — measure the ceiling")
    ap.add_argument("--symbol", default="NVDA")
    ap.add_argument("--json", action="store_true", help="dump full result json")
    a = ap.parse_args(argv)

    sessions = load_sessions(a.symbol, "5m")
    print(describe(sessions))
    tune = tune_sessions(sessions)
    print(f"\nmeasuring on the TUNE split only ({len(tune)} sessions) — a ceiling "
          f"measured on the holdout contaminates the holdout\n")

    narrow = policy_ceiling(tune, narrow_space(), "narrow")
    wide = policy_ceiling(tune, wide_space(), "wide")
    r = report(narrow, wide)

    bar = "=" * 72
    print(bar)
    print(f"  CEILING — {a.symbol}, {r['n_sessions_tune']} entries from {len(tune)} tune sessions")
    print(f"  ORACLE FIGURES USE PERFECT HINDSIGHT AND ARE NOT ACHIEVABLE")
    print(f"  costs: ${COST_PER_SHARE}/share round trip · entry: OR({OR_START}-{OR_END}) "
          f"break by {TRIGGER_END} · TP2 {TP2_R}R")
    print(bar)
    print(f"  always STATIC        {r['always_static_R']:+8.2f} R")
    print(f"  always TRAIL_WIDE    {r['always_trail_wide_R']:+8.2f} R")
    print(f"  always TRAIL_TIGHT   {r['always_trail_tight_R']:+8.2f} R")
    print()
    print(f"  Z  best fixed (narrow, {r['best_fixed_narrow']:<11s}) {r['Z_narrow']:+8.2f} R")
    print(f"  W  oracle     (narrow, {narrow['n_configs']:>3d} configs)     {r['W_narrow']:+8.2f} R")
    print(f"  >> PRIZE_NARROW      {r['prize_narrow_R']:+8.2f} R   ({r['prize_narrow_pct']:.1f}% of Z)")
    print()
    print(f"  Z  best fixed (wide)                  {r['Z_wide']:+8.2f} R   [{r['best_fixed_wide']}]")
    print(f"  W  oracle     (wide, {wide['n_configs']:>3d} configs)     {r['W_wide']:+8.2f} R")
    print(f"  >> PRIZE_WIDE        {r['prize_wide_R']:+8.2f} R   ({r['prize_wide_pct']:.1f}% of Z)")
    print(bar)
    print(f"  PER TRADE (n={r['n_entries']}) — the normalisation that does not divide by ~0:")
    print(f"    anti-oracle (worst pick)   {r['anti_oracle_R']/r['n_entries']:+7.3f} R/trade")
    print(f"    random config picker       {r['random_picker_R']/r['n_entries']:+7.3f} R/trade  <- no-skill floor")
    print(f"    Z best fixed               {r['Z_wide']/r['n_entries']:+7.3f} R/trade")
    print(f"    W oracle (hindsight)       {r['W_wide']/r['n_entries']:+7.3f} R/trade")
    print(f"    prize narrow               {r['prize_narrow_R_per_trade']:+7.3f} R/trade")
    print(f"    prize wide                 {r['prize_wide_R_per_trade']:+7.3f} R/trade")
    print()
    print(f"  !! days where ANY of {wide['n_configs']} configs cleared +0.5R: "
          f"{r['days_with_any_winning_config']}/{r['n_entries']}")
    print(f"     if that count is low, the ceiling is set by the ENTRY, not the exit —")
    print(f"     and no exit policy, however perfect, can raise it.")
    print(bar)
    print(f"  VERDICT: {r['verdict']}")
    print(f"  {r['prescribed_action']}")
    print(bar)
    print("\n  where the prize lives (wide):")
    for blk, v in sorted(r["prize_by_time_block_wide"].items(),
                         key=lambda kv: -kv[1]["prize_R"]):
        print(f"    {blk:12s} n={v['n']:3d}  prize {v['prize_R']:+7.2f} R")
    print("\n  what the oracle actually reaches for (top configs):")
    for cfg, n in list(r["oracle_policy_hist_wide"].items())[:8]:
        print(f"    {n:3d}x  {cfg}")

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "ceiling_report.json"
    path.write_text(json.dumps({k: v for k, v in r.items()}, indent=1, default=str))
    print(f"\n  written: {path}")
    if a.json:
        print(json.dumps(r, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
