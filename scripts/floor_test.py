#!/usr/bin/env python3
"""scripts/floor_test.py — the economic-floor tests declared in
artifacts/ECONOMIC_FLOOR_PREREG.md, run under the constants frozen in
az/floor_params.py.

ORDER IS FIXED BY THE PRE-REGISTRATION (§5)
    Condition (b), falsifiability, is checked FIRST:

        FLOOR >= MDE(sigma_R, N_events)

    If the declared floor sits below what the sample can detect, no outcome is
    informative -- a pass would be indistinguishable from noise and a fail would
    prove nothing. The study is then CLOSED WITHOUT BEING RUN, and this script
    does not report an E[R] for it.

    Only if (b) holds is condition (a) evaluated:

        E[R_net per event] >= FLOOR = max(0.10 R, 2 x cost drag in R)

TWO FAMILIES, NOT THREE (§3)
    spy_macro_decay and spy_premarket share the same six FRED releases, the same
    day universe (1009 vs 1008 event days), and nested windows -- 08:30-08:35 is
    a strict subset of 08:30-09:00. They are one finding at two resolutions. The
    pre-declared rule "the shorter window governs" selects macro_decay.
    spy_fomc_double_splash is a separate event universe and stands alone.
    Benjamini-Hochberg applies across the two, not three.

NO LOOKAHEAD
    ATR14 and the reference price are taken from bars strictly BEFORE the
    measurement window -- the 08:30 bar IS the macro_decay event window and the
    14:00 bar IS the FOMC window, so including either would price the trade with
    the outcome it is trying to predict. `_asof` enforces the strict cutoff.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "az"), str(ROOT / "daytrade"), str(ROOT)]

import floor_params as fp                                  # noqa: E402
import mechanisms                                          # noqa: E402
from ceiling import COST_PER_SHARE                         # noqa: E402

ES = ROOT / "data" / "daytrade" / "event_study"
ET = "America/New_York"


def load_bars(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(ET)
    return df


def _asof(bars: pd.DataFrame, cutoff_hhmm: str) -> pd.DataFrame:
    """Bars STRICTLY before the measurement window opens."""
    return bars[bars.index.strftime("%H:%M") < cutoff_hhmm]


def per_day_context(bars: pd.DataFrame, window_open: str) -> pd.DataFrame:
    """One row per day: reference price and ATR14, both as-of strictly before
    `window_open`. ATR14 is az/state.py::_atr -- mean(High-Low) over 14 bars."""
    rows = []
    for day, chunk in bars.groupby(bars.index.date):
        hist = _asof(chunk, window_open)
        if len(hist) < 14:
            continue
        w = hist.tail(14)
        atr14 = float((w["High"].astype(float) - w["Low"].astype(float)).mean())
        if not (atr14 > 0):
            continue
        rows.append({"date": day.isoformat(),
                     "price": float(hist["Close"].iloc[-1]),
                     "atr14": atr14})
    return pd.DataFrame(rows)


def evaluate(name: str, ctx: pd.DataFrame, abs_ret: pd.Series,
             diff_abs_ret: float, n_events_recorded: int,
             cost_override: float | None = None) -> dict:
    """The declared conversion, per event day. Costs are charged once against
    the differential, exactly as prereg §1 writes the identity."""
    price, atr = ctx["price"].to_numpy(), ctx["atr14"].to_numpy()
    risk = fp.K_STOP * atr

    # pessimistic fill: doubled per-share cost + adverse slippage on entry
    # cost_override exists ONLY for the sensitivity sweep and defaults to None,
    # so the pre-registered path cannot be altered by accident.
    if cost_override is not None:
        cost_per_share = price * 0.0 + float(cost_override)
    else:
        cost_per_share = COST_PER_SHARE * fp.COST_MULT + price * fp.SLIP_BPS / 10_000.0
    cost_drag_r = cost_per_share / risk

    # E[R] uses the study-level DIFFERENTIAL (prereg §1), not the event mean.
    r_edge = (fp.KAPPA * diff_abs_ret * price - cost_per_share) / risk
    # sigma_R for the power check comes from the per-day REALIZED spread.
    r_realized = (fp.KAPPA * abs_ret.to_numpy() * price - cost_per_share) / risk

    n = len(ctx)
    sigma_r = float(pd.Series(r_realized).std(ddof=1))
    mde = float(mechanisms.mde(sigma_r, n))
    floor = max(fp.FLOOR_ABS_R, fp.FLOOR_COST_MULTIPLE * float(cost_drag_r.mean()))
    return {
        "name": name, "n_events": n, "n_events_recorded": n_events_recorded,
        "diff_abs_ret": diff_abs_ret,
        "mean_price": float(price.mean()), "mean_atr14": float(atr.mean()),
        "mean_cost_per_share": float(cost_per_share.mean()),
        "mean_cost_drag_r": float(cost_drag_r.mean()),
        "floor": floor, "sigma_r": sigma_r, "mde": mde,
        "_ctx": ctx, "_abs_ret": abs_ret,
        "falsifiable": floor >= mde,
        "expected_r": float(pd.Series(r_edge).mean()),
    }


def report(res: dict) -> bool:
    print(f"\n{'='*72}\n{res['name']}   N_events = {res['n_events']}"
          f"  (recorded {res['n_events_recorded']})")
    print(f"{'='*72}")
    print(f"  inputs   diff|ret| {res['diff_abs_ret']:.6f}   mean price "
          f"{res['mean_price']:.2f}   mean ATR14 {res['mean_atr14']:.4f}")
    print(f"  costs    {res['mean_cost_per_share']:.4f}/share  ->  "
          f"{res['mean_cost_drag_r']:.4f} R of drag per event")
    print(f"  FLOOR    max({fp.FLOOR_ABS_R}, {fp.FLOOR_COST_MULTIPLE} x "
          f"{res['mean_cost_drag_r']:.4f}) = {res['floor']:.4f} R")
    print(f"\n  (b) FALSIFIABILITY -- checked first, per prereg §5")
    print(f"      sigma_R {res['sigma_r']:.4f}   N {res['n_events']}   "
          f"MDE = {res['mde']:.4f} R")
    print(f"      FLOOR {res['floor']:.4f} >= MDE {res['mde']:.4f} ?  "
          f"{'YES' if res['falsifiable'] else 'NO'}")
    if not res["falsifiable"]:
        print(f"\n  VERDICT: CLOSED WITHOUT BEING RUN.")
        print(f"      The declared floor is {res['mde']/res['floor']:.2f}x below what "
              f"{res['n_events']} events can detect.")
        print(f"      No E[R] is reported: at this N neither a pass nor a fail "
              f"would be informative.")
        return False
    print(f"\n  (a) ECONOMIC FLOOR")
    print(f"      E[R_net per event] = {res['expected_r']:+.4f} R   vs FLOOR "
          f"{res['floor']:.4f} R")
    ok = res["expected_r"] >= res["floor"]
    print(f"\n  VERDICT: {'CLEARS THE FLOOR' if ok else 'FAILS THE FLOOR — CLOSED'}")
    return ok


# --------------------------------------------------------------- sensitivity

# The pre-registered cost is $0.1176/share on SPY, of which SLIP_BPS=2.0
# contributes $0.0776 -- 66% of the total, and 15.5x the ~0.13bp half-spread of
# a 1-cent-wide SPY quote. Since COSTS, NOT THE EFFECT, set both floors, the
# closed register carries an error bar it does not currently show.
#
# THIS REPORTS NO VERDICTS. k_stop and the fill model were frozen at 3b10e8b
# BEFORE the computation, precisely so they could not be retuned after seeing
# FOMC land at 93% of its floor. The 15.5x figure is a defect in the
# PRE-REGISTRATION, not a licence to rerun it. Reopening any closure requires a
# NEW pre-registration written before these numbers are looked at again.
# None = the pre-registered path itself, NOT a flat 0.1176. The real model is
# COST_PER_SHARE*COST_MULT + price*SLIP_BPS/1e4, which varies per day with the
# price, and mean-of-ratios != ratio-of-means. Substituting the average as a
# constant would print a "pre-registered" row that does not reproduce the
# pre-registered result — a small lie in the one row that has to be exact.
COST_GRID = (0.015, 0.02, 0.03, 0.05, 0.08, None)


def sweep(results: list) -> int:
    print("\n" + "=" * 72)
    print("COST SENSITIVITY — NO VERDICTS. Every closure still stands.")
    print("=" * 72)
    print("SLIP_BPS=2.0 is 15.5x the SPY half-spread and 66% of the modelled cost.")
    print("This shows where the floor WOULD sit across a plausible range. It does")
    print("not reopen anything: the fill model was frozen before the computation so")
    print("it could not be retuned after the answer was visible.")
    print()
    print("BOTH prereg conditions are shown. Lowering cost lowers the FLOOR, which")
    print("can push it BELOW the MDE — and a floor under the detection limit means")
    print("the study is untestable at that N and closes UNRUN. Cheaper costs do not")
    print("monotonically help; they can move a family from one closure to another.\n")
    for r in results:
        print(f"{r['name']}   N={r['n_events']}")
        print(f"  {'cost/share':>11} {'FLOOR':>9} {'MDE':>8} {'(b)':>5} "
              f"{'E[R]':>10} {'(a)':>5}  verdict")
        for c in COST_GRID:
            alt = evaluate(r["name"], r["_ctx"], r["_abs_ret"], r["diff_abs_ret"],
                           r["n_events_recorded"], cost_override=c)
            # BOTH prereg conditions, in the order §5 fixes. Showing only (a)
            # was a defect in the first version of this sweep: it implied a
            # family "clears" at low cost when the falsifiability condition had
            # already failed, which is a DIFFERENT closure, not a pass.
            b_ok = alt["floor"] >= alt["mde"]
            a_ok = alt["expected_r"] >= alt["floor"]
            verdict = ("CLEARS BOTH" if (b_ok and a_ok)
                       else "UNTESTABLE — closed unrun" if not b_ok
                       else "fails economics")
            label = f"{c:>11.4f}" if c is not None else f"{'prereg':>11}"
            mark = "  <- as frozen" if c is None else ""
            print(f"  {label} {alt['floor']:>9.4f} {alt['mde']:>8.4f} "
                  f"{('yes' if b_ok else 'NO'):>5} {alt['expected_r']:>+10.4f} "
                  f"{('yes' if a_ok else 'no'):>5}  {verdict}{mark}")
        # the frozen row must reproduce the headline result exactly
        assert abs(alt["floor"] - r["floor"]) < 1e-9, (
            f"sweep's frozen row {alt['floor']} != the pre-registered floor "
            f"{r['floor']} — the sensitivity is misrepresenting the result it "
            "is a sensitivity of")
        print()
    print("Recorded as a known defect in the pre-registration. Verdicts unchanged.")
    return 0


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="economic-floor tests")
    ap.add_argument("--sweep", action="store_true",
                    help="cost-sensitivity table; reports NO verdicts")
    a = ap.parse_args(argv)
    print("ECONOMIC-FLOOR TESTS — artifacts/ECONOMIC_FLOOR_PREREG.md")
    print(f"frozen: kappa={fp.KAPPA}  k_stop={fp.K_STOP}  cost_mult={fp.COST_MULT}"
          f"  slip_bps={fp.SLIP_BPS}  (az/floor_params.py, commit 3b10e8b)")

    results = []

    # ---- family A: macro_decay (governs the collapsed macro_decay/premarket pair)
    split = pd.read_csv(ES / "spy_macro_decay_primary_days.csv")
    ev = split[split["arm"] == "event"]
    summ = json.loads((ES / "spy_macro_decay_study_summary.json").read_text())["PRIMARY"]
    ctx = per_day_context(load_bars(ROOT / "data/daytrade/bars_premarket/SPY_5m.parquet"),
                          "08:30")
    m = ev.merge(ctx, on="date", how="inner")
    results.append(evaluate("spy_macro_decay  (08:30-08:35; governs premarket too)",
                            m[["price", "atr14"]], m["abs_ret"],
                            summ["diff_event_minus_control"], summ["event_n"]))

    # ---- family B: fomc_double_splash
    cal = json.loads((ROOT / "data/daytrade/fomc_calendar.json").read_text())
    # `events`, not `meetings`; 2 entries carry unscheduled: true (COVID 2020)
    sched = {e["date"] for e in cal["events"] if not e.get("unscheduled")}
    fd = pd.read_csv(ES / "spy_fomc_days.csv")
    fev = fd[fd["date"].isin(sched)].dropna(subset=["stmt_abs_ret"])
    fsumm = json.loads(
        (ES / "spy_fomc_double_splash_event_study_summary.json").read_text())["PRIMARY"]
    fctx = per_day_context(load_bars(ROOT / "data/daytrade/bars_premarket/SPY_5m.parquet"), "14:00")
    fm = fev.merge(fctx, on="date", how="inner")
    results.append(evaluate("spy_fomc_double_splash  (14:00-14:05)",
                            fm[["price", "atr14"]], fm["stmt_abs_ret"],
                            fsumm["diff_event_minus_control"], fsumm["event_n"]))

    if a.sweep:
        return sweep(results)

    survivors = [r for r in results if report(r)]
    print(f"\n{'='*72}")
    print(f"{len(survivors)} of {len(results)} families clear both conditions.")
    if not survivors:
        print("Benjamini-Hochberg across the two families is moot: nothing reached (a).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
