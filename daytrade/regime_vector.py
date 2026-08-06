#!/usr/bin/env python3
"""REGIME VECTOR — the multidimensional state of the environment.

"Bullish / bearish / neutral" collapses a market into one axis it does not
actually live on. A tape can be directionally bullish, highly volatile,
liquidity-poor, event-sensitive and weak in breadth AT THE SAME TIME, and the
right exit policy depends on that whole shape rather than the direction alone.

TWELVE DIMENSIONS, and the important design decision: MOST OF THEM ARE
COMPUTED, NOT GUESSED. Trend strength, realized volatility, volatility
expansion, liquidity, gap risk, momentum persistence, mean-reversion pressure,
cross-asset correlation, index alignment and relative strength are all
arithmetic over bars we already have. Asking a language model to estimate a
number that a formula can produce exactly is slower, costlier, unrepeatable and
unscoreable. Only the dimensions that genuinely require judgment or data we do
not hold — market breadth, event risk — are left for a caller to supply.

Every dimension therefore carries its SOURCE. A consumer can always tell whether
a number was measured, judged, or is simply absent, and `unavailable` is a first
class state rather than a silent zero. A zero that means "no trend" and a zero
that means "we could not compute this" are different facts, and conflating them
is how a system quietly starts trading on nothing.

FAIL LOUD: malformed bars raise. A dimension whose inputs are missing is
returned `unavailable` with the reason attached — never defaulted.
"""
from __future__ import annotations

import math
import statistics as st
from dataclasses import dataclass, field, asdict
from typing import Literal, Optional

Source = Literal["computed", "judged", "unavailable"]

# name -> (low, high, what a HIGH value means). The range is part of the
# contract: a consumer must never have to guess whether 0 is neutral or empty.
SPEC: dict[str, tuple[float, float, str]] = {
    "trend_strength":          (-1.0, 1.0, "strong directional trend (sign = direction)"),
    "realized_volatility":     (0.0, 1.0, "today is volatile vs this symbol's own recent history"),
    "volatility_expansion":    (-1.0, 1.0, "volatility is expanding (negative = contracting)"),
    "liquidity_quality":       (0.0, 1.0, "deep, orderly tape — fills are trustworthy"),
    "market_breadth":          (0.0, 1.0, "the move is broad, not a handful of names"),
    "cross_asset_correlation": (-1.0, 1.0, "moving with the index (negative = against it)"),
    "gap_risk":                (0.0, 1.0, "overnight gaps have been large recently"),
    "event_risk":              (0.0, 1.0, "a scheduled catalyst can reprice this today"),
    "momentum_persistence":    (-1.0, 1.0, "moves continue (negative = they reverse)"),
    "mean_reversion_pressure": (0.0, 1.0, "stretched from VWAP, snap-back likely"),
    "index_alignment":         (-1.0, 1.0, "agreeing with the index bar for bar"),
    "symbol_relative_strength":(-1.0, 1.0, "outperforming the index today"),
}


class RegimeError(RuntimeError):
    """Bad inputs. Never downgraded to a default value."""


@dataclass(frozen=True)
class Dimension:
    name: str
    value: Optional[float]
    source: Source
    detail: str

    def __post_init__(self):
        if self.name not in SPEC:
            raise RegimeError(f"unknown dimension {self.name!r}; known: {sorted(SPEC)}")
        if self.source == "unavailable":
            if self.value is not None:
                raise RegimeError(f"{self.name}: unavailable dimensions carry no value")
            return
        if self.value is None:
            raise RegimeError(f"{self.name}: source={self.source} but no value")
        lo, hi = SPEC[self.name][:2]
        if not (lo - 1e-9 <= self.value <= hi + 1e-9):
            raise RegimeError(f"{self.name}={self.value} outside its declared range [{lo}, {hi}]")


@dataclass(frozen=True)
class RegimeVector:
    ts: str
    symbol: str
    dims: dict[str, Dimension]

    def __post_init__(self):
        missing = set(SPEC) - set(self.dims)
        if missing:
            raise RegimeError(
                f"incomplete vector, missing {sorted(missing)}. Every dimension must be "
                "present — an absent one must say 'unavailable' and why, so a consumer "
                "can never mistake silence for a measurement.")

    def value(self, name: str) -> Optional[float]:
        return self.dims[name].value

    def available(self) -> dict[str, float]:
        return {k: d.value for k, d in self.dims.items() if d.source != "unavailable"}

    def to_dict(self) -> dict:
        return {"ts": self.ts, "symbol": self.symbol,
                "dims": {k: asdict(v) for k, v in self.dims.items()}}

    def describe(self) -> str:
        out = []
        for k in SPEC:
            d = self.dims[k]
            mark = {"computed": "=", "judged": "~", "unavailable": "?"}[d.source]
            v = "     -" if d.value is None else f"{d.value:+6.2f}"
            out.append(f"  {mark} {k:26s} {v}   {d.detail}")
        return "\n".join(out)


# --------------------------------------------------------------- arithmetic

def _closes(df) -> list[float]:
    return [float(x) for x in df["Close"]]


def _returns(df) -> list[float]:
    c = _closes(df)
    return [(c[i] / c[i - 1] - 1.0) for i in range(1, len(c)) if c[i - 1]]


def _atr(df) -> float:
    """Mean true range over the frame. Simple and sufficient at 5m resolution."""
    h, l, c = [float(x) for x in df["High"]], [float(x) for x in df["Low"]], _closes(df)
    trs = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
           for i in range(1, len(c))]
    return st.mean(trs) if trs else 0.0


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _slope_per_atr(df) -> float:
    """Least-squares slope of closes across the frame, expressed in ATR units so
    it is comparable across symbols and price levels."""
    c = _closes(df)
    n = len(c)
    if n < 3:
        raise RegimeError(f"need >=3 bars for a slope, got {n}")
    xs = list(range(n))
    mx, my = st.mean(xs), st.mean(c)
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, c)) / denom
    atr = _atr(df)
    return 0.0 if atr == 0 else (slope * n) / atr


def _percentile(x: float, pop: list[float]) -> float:
    if not pop:
        raise RegimeError("empty population for percentile")
    return sum(1 for p in pop if p <= x) / len(pop)


def _vwap(df) -> float:
    num = sum(((float(h) + float(l) + float(c)) / 3.0) * float(v)
              for h, l, c, v in zip(df["High"], df["Low"], df["Close"], df["Volume"]))
    den = sum(float(v) for v in df["Volume"])
    if den == 0:
        raise RegimeError("zero volume — cannot compute VWAP")
    return num / den


def _autocorr1(xs: list[float]) -> float:
    if len(xs) < 4:
        raise RegimeError(f"need >=4 returns for autocorrelation, got {len(xs)}")
    m = st.mean(xs)
    num = sum((xs[i] - m) * (xs[i - 1] - m) for i in range(1, len(xs)))
    den = sum((x - m) ** 2 for x in xs)
    return 0.0 if den == 0 else _clip(num / den, -1.0, 1.0)


# ------------------------------------------------------------ the computation

def compute(symbol: str, session, history: list, *, ts: str,
            index_session=None, judged: dict[str, tuple[float, str]] | None = None,
            up_to: str | None = None) -> RegimeVector:
    """Build the vector. Deterministic given identical inputs — that is the whole
    point, and it is what makes the thing repeatable session after session.

    `session`  — today's Session (see bars.py)
    `history`  — prior Sessions, for the distributions today is scored against
    `up_to`    — 'HH:MM'; only bars at or before this are used. NO LOOK-AHEAD:
                 a vector computed for 10:30 must never see the 11:00 bar.
    `index_session` — same day for SPY/QQQ, enabling the three relative dims
    `judged`   — {dim: (value, detail)} for dimensions a formula cannot reach
    """
    judged = judged or {}
    df = session.between("00:00", up_to) if up_to else session.df
    if len(df) < 4:
        raise RegimeError(f"{symbol} {session.day}: only {len(df)} bars up to "
                          f"{up_to or 'session end'} — too few to describe a regime")

    dims: dict[str, Dimension] = {}

    def put(name, value, source, detail):
        dims[name] = Dimension(name, value, source, detail)

    # --- trend -------------------------------------------------------------
    slope = _slope_per_atr(df)
    put("trend_strength", _clip(slope / 4.0, -1, 1), "computed",
        f"regression slope {slope:+.2f} ATR across {len(df)} bars")

    # --- volatility, scored against this symbol's own history ---------------
    atr_today = _atr(df)
    px = st.mean(_closes(df))
    atr_pct = atr_today / px if px else 0.0
    hist_atr = [_atr(s.df) / st.mean(_closes(s.df)) for s in history if len(s.df) >= 4]
    if hist_atr:
        put("realized_volatility", _percentile(atr_pct, hist_atr), "computed",
            f"ATR {atr_pct:.3%} of price, {_percentile(atr_pct, hist_atr):.0%}ile of "
            f"{len(hist_atr)} prior sessions")
        med = st.median(hist_atr)
        ratio = math.log(atr_pct / med) if med > 0 and atr_pct > 0 else 0.0
        put("volatility_expansion", _clip(ratio, -1, 1), "computed",
            f"log(today/median) = {ratio:+.2f}")
    else:
        put("realized_volatility", None, "unavailable", "no prior sessions to score against")
        put("volatility_expansion", None, "unavailable", "no prior sessions to score against")

    # --- liquidity ---------------------------------------------------------
    vol_today = sum(float(v) for v in df["Volume"])
    hist_vol = [sum(float(v) for v in s.df["Volume"]) for s in history]
    if hist_vol:
        pctl = _percentile(vol_today, hist_vol)
        put("liquidity_quality", pctl, "computed",
            f"session volume {vol_today:,.0f}, {pctl:.0%}ile of {len(hist_vol)} sessions")
    else:
        put("liquidity_quality", None, "unavailable", "no prior sessions for a volume baseline")

    # --- gap risk ----------------------------------------------------------
    if len(history) >= 2:
        gaps = []
        for prev, cur in zip(history, history[1:]):
            pc, co = float(prev.df["Close"].iloc[-1]), float(cur.df["Open"].iloc[0])
            if pc:
                gaps.append(abs(co / pc - 1.0))
        if gaps:
            med_gap = st.median(gaps)
            put("gap_risk", _clip(med_gap / 0.03, 0, 1), "computed",
                f"median overnight gap {med_gap:.2%} over {len(gaps)} sessions "
                f"(1.0 == 3%+)")
        else:
            put("gap_risk", None, "unavailable", "could not pair sessions for gaps")
    else:
        put("gap_risk", None, "unavailable", "need >=2 prior sessions")

    # --- momentum vs mean reversion ---------------------------------------
    rets = _returns(df)
    put("momentum_persistence", _autocorr1(rets), "computed",
        f"lag-1 autocorrelation of {len(rets)} bar returns")

    vwap, last = _vwap(df), _closes(df)[-1]
    stretch = abs(last - vwap) / atr_today if atr_today else 0.0
    put("mean_reversion_pressure", _clip(stretch / 3.0, 0, 1), "computed",
        f"{stretch:.1f} ATR from VWAP {vwap:.2f} (1.0 == 3 ATR)")

    # --- the three that need an index -------------------------------------
    if index_session is not None:
        idf = index_session.between("00:00", up_to) if up_to else index_session.df
        a, b = _returns(df), _returns(idf)
        n = min(len(a), len(b))
        if n >= 4:
            a, b = a[-n:], b[-n:]
            try:
                corr = _clip(st.correlation(a, b), -1, 1)
            except st.StatisticsError:
                corr = 0.0
            put("cross_asset_correlation", corr, "computed",
                f"return correlation with index over {n} bars")
            agree = sum(1 for x, y in zip(a, b) if x * y > 0) / n
            put("index_alignment", _clip(2 * agree - 1, -1, 1), "computed",
                f"{agree:.0%} of bars moved the same direction as the index")
            sret = _closes(df)[-1] / _closes(df)[0] - 1.0
            iret = _closes(idf)[-1] / _closes(idf)[0] - 1.0
            put("symbol_relative_strength", _clip((sret - iret) * 25, -1, 1), "computed",
                f"symbol {sret:+.2%} vs index {iret:+.2%}")
        else:
            for k in ("cross_asset_correlation", "index_alignment", "symbol_relative_strength"):
                put(k, None, "unavailable", f"only {n} overlapping bars with the index")
    else:
        for k in ("cross_asset_correlation", "index_alignment", "symbol_relative_strength"):
            put(k, None, "unavailable", "no index session supplied")

    # --- the two that arithmetic cannot reach ------------------------------
    for k, why in (("market_breadth", "needs advance/decline data this repo does not hold"),
                   ("event_risk", "calendar-driven — supply via `judged` from macro_calendar.md")):
        if k in judged:
            v, detail = judged[k]
            put(k, v, "judged", detail)
        else:
            put(k, None, "unavailable", why)

    for k, (v, detail) in judged.items():
        if k not in dims:
            raise RegimeError(f"judged dimension {k!r} is not in the spec")
        if dims[k].source == "computed":
            raise RegimeError(
                f"{k!r} is COMPUTED from bars — refusing to overwrite arithmetic with a "
                "judgement. If the formula is wrong, fix the formula.")

    return RegimeVector(ts=ts, symbol=symbol, dims=dims)


if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from bars import load_sessions

    ap = argparse.ArgumentParser(description="compute the regime vector (no LLM, no cost)")
    ap.add_argument("--symbol", default="NVDA")
    ap.add_argument("--index", default=None, help="e.g. SPY, if cached")
    ap.add_argument("--up-to", default=None, help="HH:MM — no look-ahead past this")
    a = ap.parse_args()

    ss = load_sessions(a.symbol, "5m")
    idx = None
    if a.index:
        i = load_sessions(a.index, "5m")
        idx = next((x for x in i if x.day == ss[-1].day), None)
    v = compute(a.symbol, ss[-1], ss[:-1], ts=str(ss[-1].day),
                index_session=idx, up_to=a.up_to)
    print(f"{a.symbol} {ss[-1].day}" + (f" up to {a.up_to}" if a.up_to else "") + "\n")
    print(v.describe())
    print(f"\n  = computed   ~ judged   ? unavailable")
    print(f"  {len(v.available())}/{len(SPEC)} dimensions have values")
