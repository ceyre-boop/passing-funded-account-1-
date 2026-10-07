#!/usr/bin/env python3
"""REGIME CLASSIFIER — spec 001. The heart of ALPHAZERO.

Answers one question about the tape as it stands: **what kind of day is this
right now?** Never what price will do next. A forecast is a different object
with a different scoring rule; this is a description of the present.

WHY THIS WAS BLOCKED, AND WHAT UNBLOCKED IT
-------------------------------------------
This file carried a `raise NotImplementedError` from 2026-08-03 to 2026-10-06
with three named blockers. All three are now cleared, which is the only reason
it is being written:

  1. "A rule in R/trade must be re-registered by a human BEFORE this gets
     built." Done: `specs/026_SUPERHUMAN_TARGET.md`, pre-registered 2026-10-06
     with gates on net R/trade, before any model was fitted and before the
     deepened cache was pulled.
  2. "The oracle reaches for flatten_et and be_arm_frac, essentially never for
     trail_mult. The thing worth classifying is 'should I still be in this
     after 11:00?'" Honoured: the POLICY table below moves the breakeven arm and
     the hold/flatten decision. It does not reach for trail width.
  3. "n=24 is far too few to trust any of this." Cleared: the Alpaca/SIP cache
     deepening (commit 431fb94) took the TRAIN split from 24 entries to 5,126
     across 19 symbols and ~650 session-days.

WHAT THIS FILE IS NOT
---------------------
v1 is RULES, not ML, on purpose — spec 001 is explicit about why. A hand-scored
table can be argued with line by line when it disagrees with Colin's eye; a
fitted model cannot, and there is no labelled data yet because `scorecard.py` is
what creates it. Learned weights are the actual walk-forward ALPHAZERO and they
do not start until the scorecard has months of labels. Writing them earlier
would be fitting noise, which is the specific failure `SANITY_AUDIT.md` exists
to prevent.

NO LOOKAHEAD, STRUCTURALLY
--------------------------
`classify` reads `bars_5m` and nothing else about the future. The caller passes
the frame ENDING at the bar being classified; this module asserts that the last
bar's timestamp is not after `ctx.now_et` and raises if it is. Grading against
what happened next lives in `scorecard.py` and never feeds back into a call.

UNAVAILABLE IS NOT ZERO
-----------------------
Overnight pools (ONH/ONL) need extended-hours bars. The Alpaca source is clipped
to regular hours by design, so those pools are genuinely unavailable and are
carried as `None` with a reason — never as 0.0, and no scoring rule reads them.
CLAUDE.md rule 3: never silently default an unavailable value to numeric zero.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from typing import Literal, Optional, Sequence

Regime = Literal["CONTINUATION", "MANIPULATION", "CONSOLIDATION"]
TimeBlock = Literal["PREOPEN", "OPEN_DRIVE", "MORNING", "MIDDAY",
                    "AFTERNOON", "CLOSE"]

RULE_VERSION = "regime-v1"

# --------------------------------------------------------------- thresholds
# Every one of these is a number Colin's screen time turned into a constant.
# They are tunable on the TUNE split only (splits.py) and MUST NOT be hand-edited
# after a losing day — that is rule-changing-after-a-loss, forbidden by
# specs/README.md rule 4.
N_RECLAIM = 3           # bars within which a sweep must be reclaimed
WICK_THRESH = 0.60      # wick / total range above this is wick-dominated
RANGE_QUIET = 0.60      # block range / atr5 below this is quiet
CONF_FLOOR = 0.45       # below this, fall back to the safe regime
ATR_EXPANSION_HOT = 1.10
VOL_RATIO_OK = 1.00
VOL_RATIO_SPIKE = 1.50
VOL_RATIO_THIN = 0.80
RANGE_NO_FOLLOW = 0.50  # volume spike with range under this = no follow-through
ATR5_N = 5              # bars in the fast ATR
ATR_DAILY_N = 14        # sessions in the daily ATR
EMA_PERIODS = (9, 21, 50, 200)

# Time-of-day priors. Doctrine, tunable, never a verdict on their own. The
# scorecard re-fits these FIRST once ~200 scored blocks exist; they are not
# re-fitted here because no scored block exists yet.
TIME_PRIORS: dict[str, dict[str, float]] = {
    "PREOPEN":    {"CONTINUATION": 1.0, "MANIPULATION": 1.0, "CONSOLIDATION": 1.0},
    "OPEN_DRIVE": {"CONTINUATION": 1.3, "MANIPULATION": 1.0, "CONSOLIDATION": 0.8},
    "MORNING":    {"CONTINUATION": 0.9, "MANIPULATION": 1.3, "CONSOLIDATION": 1.0},
    "MIDDAY":     {"CONTINUATION": 0.8, "MANIPULATION": 0.9, "CONSOLIDATION": 1.3},
    "AFTERNOON":  {"CONTINUATION": 1.1, "MANIPULATION": 1.1, "CONSOLIDATION": 0.9},
    "CLOSE":      {"CONTINUATION": 1.0, "MANIPULATION": 1.2, "CONSOLIDATION": 0.9},
}

# Regime -> exit intent. THE ENTIRE PAYOFF.
#
# These are the v3 names from stockfish_exit.INTENT, not spec 001's original
# STATIC / TRAIL_WIDE / TRAIL_TIGHT — the vocabulary was renamed and a RegimeRead
# must emit the current one. The mapping deliberately moves be_arm_frac and the
# hold/flatten decision, the two levers the 2026-08-03 ceiling oracle actually
# reached for, and leaves trail width alone, which it essentially never used.
#
# SALVAGE is NOT emitted here. It sits in stockfish_exit.NOT_AUTO_EMITTABLE
# because it asserts "the thesis has weakened" about an OPEN position, which is a
# claim about a specific trade rather than about the day. Spec 003 marks it
# [SKETCH]; emitting it from a day-level read would be the second exit brain the
# architecture forbids.
POLICY: dict[str, str] = {
    "CONTINUATION": "RIDE",      # let it run; participation is the point
    "MANIPULATION": "DEFEND",    # assume the sweep is coming for us; de-risk
    "CONSOLIDATION": "HARVEST",  # take the goal and be done; don't churn
}

SAFE_REGIME: Regime = "CONSOLIDATION"


class RegimeError(RuntimeError):
    """Malformed input to the classifier. Never downgraded to a warning."""


@dataclass(frozen=True)
class Context:
    """Everything the classifier needs that is not in the 5m frame.

    `now_et` is DATA, exactly like price — supplying a fact is not deciding
    (specs/000 Ruling 1). The classifier never reads a clock itself.
    """
    symbol: str
    now_et: str                       # 'HH:MM', the ET time of the bar close
    session_day: _date
    pdh: Optional[float] = None       # prior session high
    pdl: Optional[float] = None       # prior session low
    onh: Optional[float] = None       # overnight high — None when unavailable
    onl: Optional[float] = None       # overnight low  — None when unavailable
    unavailable: tuple[str, ...] = ()  # names carried as None, with a reason below
    unavailable_reason: str = ""


@dataclass(frozen=True)
class RegimeRead:
    ts: str
    symbol: str
    regime: Regime
    confidence: float
    time_block: TimeBlock
    direction_of_flow: int            # +1 / 0 / -1 — where momentum IS
    exit_policy: str                  # the ONLY thing downstream acts on
    evidence: dict = field(default_factory=dict)
    rule_version: str = RULE_VERSION


# ------------------------------------------------------------- time blocks

def time_block(now_et: str) -> TimeBlock:
    """ET 'HH:MM' -> the block it falls in. Boundaries are half-open [start, end).

    PREOPEN covers anything before the bell, including the 04:00-09:30 window and
    anything after 16:00 — a bar outside RTH is not a trading block and must not
    silently borrow AFTERNOON's priors.
    """
    m = _hhmm_minutes(now_et)
    if m < _hhmm_minutes("09:30"):
        return "PREOPEN"
    if m < _hhmm_minutes("10:30"):
        return "OPEN_DRIVE"
    if m < _hhmm_minutes("11:30"):
        return "MORNING"
    if m < _hhmm_minutes("14:00"):
        return "MIDDAY"
    if m < _hhmm_minutes("15:30"):
        return "AFTERNOON"
    if m < _hhmm_minutes("16:00"):
        return "CLOSE"
    return "PREOPEN"


def _hhmm_minutes(hhmm: str) -> int:
    try:
        h, mi = hhmm.split(":")
        h, mi = int(h), int(mi)
    except Exception as e:
        raise RegimeError(f"bad ET time {hhmm!r}: {e}") from e
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise RegimeError(f"ET time out of range: {hhmm!r}")
    return h * 60 + mi


def _sign(x: float) -> int:
    return 0 if x == 0 else (1 if x > 0 else -1)


def _stack_sign(stack: str) -> int:
    return {"aligned_up": 1, "aligned_down": -1, "tangled": 0}[stack]


# ---------------------------------------------------------------- evidence

def build_evidence(bars_5m, bars_1m, ctx: Context) -> dict:
    """Every number that moved the decision. Pure; no side effects, no I/O.

    `bars_5m` is a pandas DataFrame, ET-indexed, Open/High/Low/Close/Volume,
    CONTINUOUS and ENDING at the bar being classified. It may span several
    sessions — EMA200 on 5m needs 200 bars, which is more than one session.

    `bars_1m` is accepted for contract compatibility with spec 001 and may be
    None. No v1 rule reads it: every signal below is computable at 5m, and the
    cache is 5m. It is NOT silently treated as present.
    """
    if bars_5m is None or len(bars_5m) == 0:
        raise RegimeError("no 5m bars supplied — a classification needs a tape")
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in bars_5m.columns:
            raise RegimeError(f"5m frame is missing column {col!r}")
    if bars_5m[["Open", "High", "Low", "Close"]].isna().any().any():
        raise RegimeError("5m frame contains NaN OHLC — a hole is corruption, "
                          "never forward-filled (bars.py doctrine)")

    idx = bars_5m.index
    # STRUCTURAL integrity first. This check used to sit AFTER the now_et match
    # below, which made it unreachable for the case it exists to catch: a
    # reversed frame's last bar is its EARLIEST, so the now_et comparison fired
    # with a misleading message and the ordering bug was never named. Order
    # matters — validate the shape of the data before interpreting it.
    if not idx.is_monotonic_increasing:
        raise RegimeError("5m frame is not time-ordered")
    last_ts = idx[-1]
    # NO LOOKAHEAD, asserted: the frame must not reach past the bar being read.
    if last_ts.strftime("%H:%M") != ctx.now_et:
        raise RegimeError(
            f"frame ends at {last_ts.strftime('%H:%M')} but ctx.now_et is "
            f"{ctx.now_et} — classify() must be handed the tape as it stood at "
            f"now_et, not a frame containing later bars")

    today = bars_5m[idx.date == ctx.session_day]
    if len(today) == 0:
        raise RegimeError(f"no bars for session_day {ctx.session_day}")

    t = today.index.strftime("%H:%M")
    price = float(today["Close"].iloc[-1])

    # --- opening range. Incomplete before 10:00 is a FACT, not a failure.
    or_bars = today[(t >= "09:30") & (t <= "10:00")]
    or_complete = len(or_bars) >= 6
    or_high = float(or_bars["High"].max()) if len(or_bars) else None
    or_low = float(or_bars["Low"].min()) if len(or_bars) else None
    or_range = (or_high - or_low) if or_complete else None

    # --- session VWAP and how often price has crossed it today
    tp = (today["High"] + today["Low"] + today["Close"]) / 3.0
    cum_v = today["Volume"].cumsum()
    vwap = float((tp * today["Volume"]).cumsum().iloc[-1] / cum_v.iloc[-1]) \
        if float(cum_v.iloc[-1]) > 0 else None
    vwap_side = 0 if vwap is None else _sign(price - vwap)
    if vwap is None:
        vwap_crosses = None
    else:
        running = ((tp * today["Volume"]).cumsum() / cum_v.replace(0, float("nan")))
        side = (today["Close"] - running).apply(_sign)
        nz = side[side != 0]
        vwap_crosses = int((nz.diff().fillna(0) != 0).sum() - (1 if len(nz) else 0))
        vwap_crosses = max(vwap_crosses, 0)

    # --- EMA stack over the continuous series
    emas: dict[str, Optional[float]] = {}
    for p in EMA_PERIODS:
        emas[f"ema{p}"] = (float(bars_5m["Close"].ewm(span=p, adjust=False).mean().iloc[-1])
                           if len(bars_5m) >= p else None)
    have = [emas[f"ema{p}"] for p in EMA_PERIODS]
    if any(v is None for v in have):
        ema_stack = "tangled"       # not enough history to claim alignment
        ema_stack_known = False
    else:
        ema_stack_known = True
        if have[0] > have[1] > have[2] > have[3]:
            ema_stack = "aligned_up"
        elif have[0] < have[1] < have[2] < have[3]:
            ema_stack = "aligned_down"
        else:
            ema_stack = "tangled"

    # --- ATR, fast and daily
    atr_ser = _atr_series(bars_5m, ATR5_N)
    atr5 = float(atr_ser.iloc[-1]) if atr_ser.iloc[-1] == atr_ser.iloc[-1] else None
    if atr5 is None or atr5 <= 0:
        raise RegimeError(
            f"atr5 is {atr5!r} — every threshold here is in ATR units, so a zero "
            f"or missing ATR would silently turn each comparison into a "
            f"coin flip. Refusing to classify.")
    atr_daily = _daily_atr(bars_5m, ctx.session_day, ATR_DAILY_N)

    # atr_expansion: fast ATR now vs its median over THIS block so far today
    blk = time_block(ctx.now_et)
    same_block = today[[time_block(x) == blk for x in t]]
    med = _median_atr_at(atr_ser, same_block.index)
    atr_expansion = (atr5 / med) if (med and med > 0) else None

    block_range = float(same_block["High"].max() - same_block["Low"].min()) \
        if len(same_block) else None
    range_pct_of_atr = (block_range / atr5) if block_range is not None else None

    # --- volume: this bar vs the same minute-of-day across prior sessions
    volume = float(today["Volume"].iloc[-1])
    vol_avg = _avg_volume_at_minute(bars_5m, ctx.now_et, ctx.session_day)
    volume_ratio = (volume / vol_avg) if (vol_avg and vol_avg > 0) else None

    # --- wick dominance on the latest bar
    bar = today.iloc[-1]
    rng = float(bar["High"]) - float(bar["Low"])
    body = abs(float(bar["Close"]) - float(bar["Open"]))
    wick_dominance = ((rng - body) / rng) if rng > 0 else None

    # --- pools and sweeps. ONH/ONL are unavailable, carried as None.
    pools = {"pdh": ctx.pdh, "pdl": ctx.pdl, "onh": ctx.onh, "onl": ctx.onl,
             "orh": or_high if or_complete else None,
             "orl": or_low if or_complete else None}
    swept, bars_since_sweep, reclaimed = _sweeps(today, pools)

    return {
        "symbol": ctx.symbol, "ts": last_ts.isoformat(), "now_et": ctx.now_et,
        "or_high": or_high, "or_low": or_low, "or_range": or_range,
        "or_complete": or_complete,
        "price": price, "vwap": vwap, "vwap_side": vwap_side,
        "vwap_crosses": vwap_crosses,
        **emas, "ema_stack": ema_stack, "ema_stack_known": ema_stack_known,
        "atr5": atr5, "atr_daily": atr_daily, "atr_expansion": atr_expansion,
        "block_range": block_range, "range_pct_of_atr": range_pct_of_atr,
        "volume": volume, "vol_avg_at_this_minute": vol_avg,
        "volume_ratio": volume_ratio,
        "wick_dominance": wick_dominance,
        "pools": pools, "swept": swept, "reclaimed": reclaimed,
        "bars_since_sweep": bars_since_sweep,
        "unavailable": list(ctx.unavailable),
        "unavailable_reason": ctx.unavailable_reason,
        "rule_version": RULE_VERSION,
    }


def _tr_series(bars):
    """True range per bar, vectorised. Computed ONCE per frame, never per bar.

    The first version of this module recomputed the whole TR series inside a loop
    over timestamps (`_atr(bars.iloc[:i+1])` per stamp), which is O(n^2) in the
    frame length. Against the deepened 54k-bar cache that turned one measurement
    run into hours. The arithmetic is identical; only the number of times it is
    performed changed.
    """
    h, l, c = bars["High"], bars["Low"], bars["Close"]
    pc = c.shift(1)
    return (h - l).combine((h - pc).abs(), max).combine((l - pc).abs(), max)


def _atr_series(bars, n: int):
    """Rolling n-bar mean true range, aligned to `bars.index`."""
    return _tr_series(bars).rolling(n).mean()


def _atr(bars, n: int) -> Optional[float]:
    """Simple mean true range over the last n bars. None when too short."""
    if len(bars) < n + 1:
        return None
    v = float(_tr_series(bars).iloc[-n:].mean())
    return v if v == v else None


def _median_atr_at(atr_ser, stamps) -> Optional[float]:
    """Median of a precomputed ATR series at the given timestamps."""
    sub = atr_ser.reindex(stamps).dropna()
    if not len(sub):
        return None
    return float(sub.median())


def _daily_atr(bars, day: _date, n: int) -> Optional[float]:
    """Mean true range over the n sessions STRICTLY BEFORE `day`."""
    idx = bars.index
    prior = sorted({d for d in idx.date if d < day})[-(n + 1):]
    if len(prior) < 2:
        return None
    highs, lows, closes = [], [], []
    for d in prior:
        s = bars[idx.date == d]
        highs.append(float(s["High"].max()))
        lows.append(float(s["Low"].min()))
        closes.append(float(s["Close"].iloc[-1]))
    trs = []
    for i in range(1, len(prior)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    return (sum(trs) / len(trs)) if trs else None


def _avg_volume_at_minute(bars, now_et: str, day: _date) -> Optional[float]:
    """Mean volume at this minute-of-day over PRIOR sessions only."""
    idx = bars.index
    mask = (idx.strftime("%H:%M") == now_et) & (idx.date < day)
    v = bars[mask]["Volume"]
    return float(v.mean()) if len(v) else None


def _sweeps(today, pools: dict) -> tuple[list, Optional[int], bool]:
    """Which pools today's tape took out, and whether price came back inside.

    A pool whose level is None is SKIPPED, never compared against 0.0.
    """
    swept, last_i = [], None
    highs, lows, closes = today["High"], today["Low"], today["Close"]
    for name in ("pdh", "onh", "orh"):
        lvl = pools.get(name)
        if lvl is None:
            continue
        hit = [i for i, v in enumerate(highs) if float(v) > lvl]
        if hit:
            swept.append(name.upper())
            last_i = hit[-1] if last_i is None else max(last_i, hit[-1])
    for name in ("pdl", "onl", "orl"):
        lvl = pools.get(name)
        if lvl is None:
            continue
        hit = [i for i, v in enumerate(lows) if float(v) < lvl]
        if hit:
            swept.append(name.upper())
            last_i = hit[-1] if last_i is None else max(last_i, hit[-1])
    if last_i is None:
        return [], None, False
    bars_since = len(today) - 1 - last_i
    # reclaimed: price is back inside the swept level's side
    reclaimed = False
    px = float(closes.iloc[-1])
    for name in swept:
        lvl = pools.get(name.lower())
        if lvl is None:
            continue
        if name in ("PDH", "ONH", "ORH") and px < lvl:
            reclaimed = True
        if name in ("PDL", "ONL", "ORL") and px > lvl:
            reclaimed = True
    return swept, bars_since, reclaimed


# -------------------------------------------------------------- classify

def classify(bars_5m, bars_1m, ctx: Context) -> RegimeRead:
    """What kind of day is this right now? Pure, arguable, versioned.

    Each `+=` below is a line Colin can point at and call wrong. That is the
    whole design: when the read disagrees with his eye, the failing rule is
    identifiable, which a fitted model would not be.

    A rule whose input is None does NOT fire. It is not scored as False and the
    None is not coerced to 0 — an absent input is an absent opinion.
    """
    ev = build_evidence(bars_5m, bars_1m, ctx)
    tb = time_block(ctx.now_et)
    prior = TIME_PRIORS[tb]
    score = {"CONTINUATION": 0.0, "MANIPULATION": 0.0, "CONSOLIDATION": 0.0}

    # --- CONTINUATION: range extending, one side of VWAP, stack aligned, vol OK
    if ev["or_complete"] and (ev["price"] > ev["or_high"] or ev["price"] < ev["or_low"]):
        score["CONTINUATION"] += 2
    if (ev["vwap_side"] != 0 and ev["ema_stack"] != "tangled"
            and ev["vwap_side"] == _stack_sign(ev["ema_stack"])):
        score["CONTINUATION"] += 2
    if ev["atr_expansion"] is not None and ev["atr_expansion"] > ATR_EXPANSION_HOT:
        score["CONTINUATION"] += 1
    if ev["volume_ratio"] is not None and ev["volume_ratio"] >= VOL_RATIO_OK:
        score["CONTINUATION"] += 1

    # --- MANIPULATION: a pool got swept and price came back. stops die here.
    if (ev["swept"] and ev["reclaimed"]
            and ev["bars_since_sweep"] is not None
            and ev["bars_since_sweep"] <= N_RECLAIM):
        score["MANIPULATION"] += 4                 # strongest single signal
    if ev["wick_dominance"] is not None and ev["wick_dominance"] > WICK_THRESH:
        score["MANIPULATION"] += 2
    if (ev["volume_ratio"] is not None and ev["volume_ratio"] > VOL_RATIO_SPIKE
            and ev["range_pct_of_atr"] is not None
            and ev["range_pct_of_atr"] < RANGE_NO_FOLLOW):
        score["MANIPULATION"] += 2

    # --- CONSOLIDATION: small range, chopping VWAP, tangled stack, thin volume
    if ev["range_pct_of_atr"] is not None and ev["range_pct_of_atr"] < RANGE_QUIET:
        score["CONSOLIDATION"] += 2
    if ev["vwap_crosses"] is not None and ev["vwap_crosses"] >= 2:
        score["CONSOLIDATION"] += 2
    if ev["ema_stack"] == "tangled" and ev["ema_stack_known"]:
        score["CONSOLIDATION"] += 1
    if ev["volume_ratio"] is not None and ev["volume_ratio"] < VOL_RATIO_THIN:
        score["CONSOLIDATION"] += 1

    raw = dict(score)
    for r in score:
        score[r] *= prior[r]

    total = sum(score.values())
    if total <= 0:
        # Nothing fired. That is an absence of evidence, not a consolidation
        # signal — emit the safe regime at zero confidence and say so.
        regime, conf = SAFE_REGIME, 0.0
    else:
        regime = max(score, key=lambda k: score[k])
        conf = score[regime] / total

    floored = False
    if conf < CONF_FLOOR:
        # SAFE DEFAULT — never act on a guess (doctrine + APEX #2).
        regime, floored = SAFE_REGIME, True

    flow = 0
    if ev["vwap_side"] != 0 and ev["ema_stack"] != "tangled":
        if ev["vwap_side"] == _stack_sign(ev["ema_stack"]):
            flow = ev["vwap_side"]

    ev = {**ev, "score_raw": raw, "score_weighted": score,
          "time_prior": prior, "conf_floored": floored,
          "conf_floor": CONF_FLOOR}

    return RegimeRead(ts=ev["ts"], symbol=ctx.symbol, regime=regime,
                      confidence=round(conf, 4), time_block=tb,
                      direction_of_flow=flow, exit_policy=POLICY[regime],
                      evidence=ev, rule_version=RULE_VERSION)
