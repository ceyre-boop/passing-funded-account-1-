"""TRUE out-of-sample test: run the sealed v015 engine on 2025-01 -> 2026-07 data
it has never seen. Fully offline: yfinance is monkeypatched to serve the repo's
parquet caches. Step 1 reproduces 2015-2024 in-sample to prove the offline rig
matches the sealed CSV; only then does the OOS window count.
"""
import sys, warnings, statistics
from pathlib import Path
import pandas as pd
warnings.filterwarnings("ignore")
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
CACHE=ROOT/"data/research/spot_cache"

import yfinance as yf
_missing=set()
def offline_download(ticker,*a,**k):
    start=k.get('start'); end=k.get('end')
    name={'SPY':'SPY','^VIX':'VIX'}.get(ticker, str(ticker).replace('=X',''))
    p=CACHE/f"{name}_ohlc.parquet"; q=CACHE/f"{name}.parquet"
    f = p if p.exists() else (q if q.exists() else None)
    if f is None:
        _missing.add(ticker); return pd.DataFrame()
    df=pd.read_parquet(f)
    if start: df=df[df.index>=pd.Timestamp(start)]
    if end: df=df[df.index<=pd.Timestamp(end)]
    return df
yf.download=offline_download

from sovereign.forex.forex_backtester import ForexBacktester
from sovereign.forex.pair_universe import PAIR_CONFIG

PAIRS=['EURUSD=X','GBPUSD=X','USDJPY=X','AUDUSD=X']   # v015 universe (AUDNZD excluded, HYP-045)

def run(start,end,label):
    bt=ForexBacktester(start=start,end=end)
    allR=[]; n=0
    print(f"\n== {label} ({start} -> {end}) ==")
    for pair in PAIRS:
        res=bt.backtest_pair(pair)
        if res is None: print(f" {pair}: no result"); continue
        tr=getattr(res,'trades',None) or []
        Rs=[t.get('pnl_pct',0)/0.0075 if isinstance(t,dict) else 0 for t in tr]
        print(f" {pair}: n={res.total_trades} WR={res.win_rate:.1%} sharpe={getattr(res,'sharpe',float('nan')):.2f}")
        n+=res.total_trades
    print(f" TOTAL trades: {n}")
    if _missing: print(f" (tickers served empty: {_missing})")
    return n

if __name__=="__main__":
    run('2015-01-01','2024-12-31','IN-SAMPLE REPRODUCTION (target: ~411 trades, WR~49%)')
    run('2025-01-01','2026-07-03','OUT-OF-SAMPLE — data the seal has never seen')
