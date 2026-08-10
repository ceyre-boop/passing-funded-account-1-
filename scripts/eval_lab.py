#!/usr/bin/env python3
"""SUPERSEDED by scripts/carry_buy_gate.py (spec 021) — kept for archaeology, do not run.
EVAL LAB — strategy search optimized purely for passing the Instant Pro $10k.

PRE-REGISTERED PROTOCOL (fixed before first run; see EVAL_LAB.md):
  Rules: $10k, 3% daily loss (intraday, vs day-start equity), 6% max loss
         (headline: TRAILING intraday off high-water equity; static also run),
         1:50 leverage, MT5 CFDs. No stated profit target/time limit (Instant).
         PASS := close equity >= +6% before bust, within 365d. TIMEOUT = alive, no pass.
  Split: TRAIN 2015-01-01..2021-12-31 | HOLDOUT 2022-01-01..2026-07-03 (never tuned on).
  Families/grids (K = 60 combos total):
    A Donchian breakout: N in {20,55} x ATRstop k in {2,3}            -> 4 signal cfgs
    B MA trend:          SMA in {50,100,200} x ATRstop k in {2,3}     -> 6
    C Mean-reversion:    RSI2 10/90, SMA200 filter {on,off} x k {1.5,2.5} -> 4
    D Carry (sealed 411 trades, no signal tuning)                     -> 1
    Risk/trade r in {0.25,0.5,0.75,1.0}%  => (4+6+4+1)*4 = 60 combos.
  Costs (pessimistic): 0.030% round trip + 0.012% extra on stop fills,
    swap -0.006%/day held (A/B/C), carry haircut -0.003%/day (D, on top of sealed pnl).
  Pessimistic mechanics: stops checked before favorable exits in-bar; gap-through
    fills at open; intraday worst of all open trades assumed co-timed; daily loss
    and trailing floor both breach on intraday equity.
  Selection: best train p(pass, trailing) per family -> single champion on train.
  Correction: entire 60-combo selection re-run on mean-centered per-pair log
    returns (zero drift/edge). Best-of-60 zero-edge OOS p(pass) = luck baseline.
  MC: weekly eval start dates (every 5 trading days) + stationary block bootstrap
    (20d blocks, 2000 paths, seed 42) on champion OOS daily R-series.
Limitations (stated, not hidden): daily bars only — intraday styles untestable
here; intrabar path unknown (bounded by OHLC, pessimistic ordering assumed);
carry trades book R on exit day (concentrates hits vs daily limit: pessimistic).
"""
import numpy as np, pandas as pd, csv, sys
from datetime import datetime

RNG = np.random.default_rng(42)
BASE = "/home/claude/passing-funded-account-1-"
PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]
TRAIN = ("2015-01-01", "2021-12-31")
OOS = ("2022-01-01", "2026-07-03")
RT_COST, STOP_SLIP, SWAP = 0.00030, 0.00012, 0.00006  # frac of price
RISKS = [0.0025, 0.005, 0.0075, 0.010]
HORIZON = 365

def load(zero_edge=False):
    data = {}
    for p in PAIRS:
        df = pd.read_parquet(f"{BASE}/data/research/spot_cache/{p}_ohlc.parquet").sort_index()
        df = df[~df.index.duplicated()]
        if zero_edge:
            lr = np.log(df.Close / df.Close.shift(1)).fillna(0.0)
            lr_c = lr - lr.mean()  # kill drift => zero-edge close path
            c = df.Close.iloc[0] * np.exp(lr_c.cumsum())
            ratio = c / df.Close
            for col in ("Open", "High", "Low", "Close"):
                df[col] = df[col] * ratio  # preserve intrabar shape, remove drift
        data[p] = df
    return data

def atr(df, n=14):
    tr = np.maximum(df.High - df.Low, np.maximum(abs(df.High - df.Close.shift(1)), abs(df.Low - df.Close.shift(1))))
    return tr.rolling(n).mean()

def rsi(close, n=2):
    d = close.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))

def gen_trades(df, fam, prm):
    """Return trade list: dicts(entry_i, exit_i, dir, entry_px, stop_dist, exit_px, stop_exit)."""
    C, H, L, O = df.Close.values, df.High.values, df.Low.values, df.Open.values
    A = atr(df).values
    n = len(df)
    sig = np.zeros(n)
    if fam == "A":
        N, k = prm["N"], prm["k"]
        hh = df.High.rolling(N).max().shift(1).values
        ll = df.Low.rolling(N).min().shift(1).values
        xh = df.High.rolling(N // 2).max().shift(1).values
        xl = df.Low.rolling(N // 2).min().shift(1).values
    elif fam == "B":
        N, k = prm["N"], prm["k"]
        sma = df.Close.rolling(N).mean().values
    elif fam == "C":
        k = prm["k"]
        r2 = rsi(df.Close).values
        s200 = df.Close.rolling(200).mean().values
    trades, pos = [], None
    start = 210
    for i in range(start, n - 1):
        if pos:
            d, ep, sd, ei = pos["dir"], pos["entry_px"], pos["stop_dist"], pos["entry_i"]
            stop = ep - d * sd
            hit = (L[i] <= stop) if d > 0 else (H[i] >= stop)
            if hit:  # pessimistic: stop first; gap fills at open
                fill = O[i] if (d > 0 and O[i] < stop) or (d < 0 and O[i] > stop) else stop
                trades.append(dict(entry_i=ei, exit_i=i, dir=d, entry_px=ep, stop_dist=sd, exit_px=fill, stop_exit=True))
                pos = None
            else:
                ex = False
                if fam == "A":
                    ex = (d > 0 and L[i] <= xl[i]) or (d < 0 and H[i] >= xh[i])
                    xpx = xl[i] if d > 0 else xh[i]
                elif fam == "B":
                    ex = (d > 0 and C[i] < sma[i]) or (d < 0 and C[i] > sma[i])
                    xpx = C[i]
                elif fam == "C":
                    ex = (d > 0 and r2[i] > 50) or (d < 0 and r2[i] < 50) or (i - ei >= 5)
                    xpx = C[i]
                if ex:
                    trades.append(dict(entry_i=ei, exit_i=i, dir=d, entry_px=ep, stop_dist=sd, exit_px=xpx, stop_exit=False))
                    pos = None
        if pos is None and not np.isnan(A[i]) and A[i] > 0:
            d = 0
            if fam == "A":
                if H[i] >= hh[i]: d, epx = 1, max(hh[i], O[i])
                elif L[i] <= ll[i]: d, epx = -1, min(ll[i], O[i])
            elif fam == "B":
                if C[i - 1] <= sma[i - 1] and C[i] > sma[i]: d, epx = 1, C[i]
                elif C[i - 1] >= sma[i - 1] and C[i] < sma[i]: d, epx = -1, C[i]
            elif fam == "C":
                filt_l = (not prm["filt"]) or C[i] > s200[i]
                filt_s = (not prm["filt"]) or C[i] < s200[i]
                if r2[i] < 10 and filt_l: d, epx = 1, C[i]
                elif r2[i] > 90 and filt_s: d, epx = -1, C[i]
            if d != 0:
                pos = dict(dir=d, entry_px=epx, stop_dist=prm["k"] * A[i], entry_i=i)
    return trades

def daily_R(df, trades):
    """Per-day (sum R increments, sum intraday-worst R increments) incl costs/swap."""
    n = len(df)
    C, H, L = df.Close.values, df.High.values, df.Low.values
    inc = np.zeros(n); worst = np.zeros(n)
    for t in trades:
        d, ep, sd, ei, xi, xp = t["dir"], t["entry_px"], t["stop_dist"], t["entry_i"], t["exit_i"], t["exit_px"]
        sdp = sd / ep  # stop distance as frac of price
        cost_R = RT_COST / sdp + (STOP_SLIP / sdp if t["stop_exit"] else 0.0)
        for i in range(ei, xi + 1):
            prev = ep if i == ei else C[i - 1]
            end = xp if i == xi else C[i]
            inc[i] += d * (end - prev) / sd - SWAP / sdp
            wpx = L[i] if d > 0 else H[i]
            worst[i] += min(d * (wpx - prev) / sd, d * (end - prev) / sd) - SWAP / sdp
        inc[xi] -= cost_R; worst[xi] -= cost_R
    return inc, worst

def portfolio_series(data, fam, prm):
    idx = data["EURUSD"].index
    inc = np.zeros(len(idx)); worst = np.zeros(len(idx))
    for p in PAIRS:
        df = data[p].reindex(idx).ffill()
        i2, w2 = daily_R(df, gen_trades(df, fam, prm))
        inc += i2; worst += w2
    return pd.Series(inc, idx), pd.Series(worst, idx)

def carry_series(idx):
    inc = pd.Series(0.0, index=idx); worst = pd.Series(0.0, index=idx)
    with open(f"{BASE}/data/proof/backtest_trades_v015_2015_2024.csv") as f:
        for row in csv.DictReader(f):
            xd = pd.Timestamp(row["exit_date"][:10])
            if xd in inc.index:
                r = float(row["pnl_pct"]) / float(row["risk_pct"])
                r -= 0.00003 * int(row["hold_days"]) / float(row["risk_pct"]) * 100 / 100  # swap haircut in R
                r -= 0.00003 * int(row["hold_days"]) / (float(row["risk_pct"]))
                inc[xd] += r; worst[xd] += min(r, 0.0)
    return inc, worst

def eval_run(inc, worst, risk, i0, trailing=True):
    bal = 1.0; hwm = 1.0; t = 0
    v_inc, v_wst = inc.values, worst.values
    for i in range(i0, len(v_inc)):
        t += 1
        if t > HORIZON * 5 // 7 + 1: return "TIMEOUT"
        day_w = bal * (1 + risk * v_wst[i])
        day_c = bal * (1 + risk * v_inc[i])
        if day_w < bal * 0.97 or day_c < bal * 0.97: return "BUST_DAILY"
        floor = (max(hwm, day_c) - 0.06) if trailing else 0.94
        if day_w <= floor or day_c <= floor: return "BUST_MAX"
        bal = day_c; hwm = max(hwm, bal)
        if bal >= 1.06: return "PASS"
    return "TIMEOUT"

def sweep(inc, worst, period, risk, trailing=True):
    lo = inc.index.searchsorted(pd.Timestamp(period[0]))
    hi = inc.index.searchsorted(pd.Timestamp(period[1] )) - HORIZON * 5 // 7
    starts = range(lo, max(hi, lo + 1), 5)
    res = [eval_run(inc, worst, risk, i, trailing) for i in starts]
    n = len(res)
    return dict(n=n, PASS=res.count("PASS") / n, BUST_DAILY=res.count("BUST_DAILY") / n,
                BUST_MAX=res.count("BUST_MAX") / n, TIMEOUT=res.count("TIMEOUT") / n)

def bootstrap_ci(inc, worst, period, risk, npaths=2000, block=20):
    lo = inc.index.searchsorted(pd.Timestamp(period[0]))
    hi = inc.index.searchsorted(pd.Timestamp(period[1]))
    vi, vw = inc.values[lo:hi], worst.values[lo:hi]
    L = HORIZON * 5 // 7
    passes = 0
    for _ in range(npaths):
        path_i, path_w = [], []
        while len(path_i) < L:
            s = RNG.integers(0, len(vi) - block)
            path_i.extend(vi[s:s + block]); path_w.extend(vw[s:s + block])
        pi = pd.Series(path_i[:L]); pw = pd.Series(path_w[:L])
        passes += eval_run(pi, pw, risk, 0) == "PASS"
    p = passes / npaths
    se = (p * (1 - p) / npaths) ** 0.5
    return p, p - 1.96 * se, p + 1.96 * se

GRIDS = {"A": [dict(N=N, k=k) for N in (20, 55) for k in (2, 3)],
         "B": [dict(N=N, k=k) for N in (50, 100, 200) for k in (2, 3)],
         "C": [dict(filt=f, k=k) for f in (True, False) for k in (1.5, 2.5)]}

def full_search(data, label):
    idx = data["EURUSD"].index
    combos = 0; rows = []
    series_cache = {}
    for fam, grid in GRIDS.items():
        for prm in grid:
            inc, worst = portfolio_series(data, fam, prm)
            series_cache[(fam, str(prm))] = (inc, worst)
            for r in RISKS:
                combos += 1
                tr = sweep(inc, worst, TRAIN, r)
                rows.append(dict(fam=fam, prm=str(prm), risk=r, **{f"train_{k}": v for k, v in tr.items()}))
    ci, cw = carry_series(idx)
    series_cache[("D", "sealed")] = (ci, cw)
    for r in RISKS:
        combos += 1
        tr = sweep(ci, cw, TRAIN, r)
        rows.append(dict(fam="D", prm="sealed", risk=r, **{f"train_{k}": v for k, v in tr.items()}))
    df = pd.DataFrame(rows).sort_values("train_PASS", ascending=False)
    print(f"\n=== {label}: K={combos} combos, train selection (top 10, trailing rules) ===")
    print(df.head(10).to_string(index=False))
    champ = df.iloc[0]
    inc, worst = series_cache[(champ.fam, champ.prm)]
    oos = sweep(inc, worst, OOS, champ.risk)
    oos_static = sweep(inc, worst, OOS, champ.risk, trailing=False)
    bs = bootstrap_ci(inc, worst, OOS, champ.risk)
    print(f"\nCHAMPION [{label}]: fam={champ.fam} {champ.prm} risk={champ.risk*100:.2f}%")
    print(f"  train p(pass)={champ.train_PASS:.1%} (n={champ.train_n})")
    print(f"  OOS  p(pass)={oos['PASS']:.1%} (n={oos['n']})  daily-bust={oos['BUST_DAILY']:.1%} maxDD-bust={oos['BUST_MAX']:.1%} timeout={oos['TIMEOUT']:.1%}")
    print(f"  OOS static-floor p(pass)={oos_static['PASS']:.1%}")
    print(f"  OOS block-bootstrap p(pass)={bs[0]:.1%} [95% CI {bs[1]:.1%}..{bs[2]:.1%}]")
    # per-family champions OOS (secondary, disclosed)
    print(f"\nPer-family champions OOS [{label}]:")
    for fam in ("A", "B", "C", "D"):
        sub = df[df.fam == fam]
        if not len(sub): continue
        c = sub.iloc[0]
        i2, w2 = series_cache[(c.fam, c.prm)]
        o2 = sweep(i2, w2, OOS, c.risk)
        print(f"  {fam} {c.prm} r={c.risk*100:.2f}%: train {c.train_PASS:.1%} -> OOS {o2['PASS']:.1%} (daily {o2['BUST_DAILY']:.1%}, maxDD {o2['BUST_MAX']:.1%}, timeout {o2['TIMEOUT']:.1%})")
    return df, champ

print("Loading real data...")
real = load(zero_edge=False)
df_real, champ_real = full_search(real, "REAL")
print("\nLoading ZERO-EDGE (mean-centered) data — same 60-combo search, pure luck baseline...")
zed = load(zero_edge=True)
df_zed, champ_zed = full_search(zed, "ZERO-EDGE")
