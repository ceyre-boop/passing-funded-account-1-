"""Step 2 of the OOS test: trade-level stats + V2 eval-campaign pass rate on 2025-2026,
run with the SAME offline rig on both periods so the comparison is apples-to-apples."""
import sys, warnings, statistics
from pathlib import Path
from datetime import timedelta
import pandas as pd
warnings.filterwarnings("ignore")
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
CACHE=ROOT/"data/research/spot_cache"
import yfinance as yf
def offline_download(ticker,*a,**k):
    name={'SPY':'SPY','^VIX':'VIX'}.get(ticker,str(ticker).replace('=X',''))
    for suff in ('_ohlc',''):
        f=CACHE/f"{name}{suff}.parquet"
        if f.exists():
            df=pd.read_parquet(f)
            if k.get('start'): df=df[df.index>=pd.Timestamp(k['start'])]
            if k.get('end'): df=df[df.index<=pd.Timestamp(k['end'])]
            return df
    return pd.DataFrame()
yf.download=offline_download
from sovereign.forex.forex_backtester import ForexBacktester
from sovereign.forex.pair_universe import PAIR_CONFIG, CB_TO_COUNTRY
PAIRS=['EURUSD=X','GBPUSD=X','USDJPY=X','AUDUSD=X']

def get_trades(start,end):
    bt=ForexBacktester(start=start,end=end)
    out=[]
    for pair in PAIRS:
        cfg=PAIR_CONFIG[pair]
        res,tr=bt.run_pair_with_trades(pair, CB_TO_COUNTRY[cfg.base_central_bank], CB_TO_COUNTRY[cfg.quote_central_bank])
        for t in tr:
            t['pair']=pair; out.append(t)
    out.sort(key=lambda t:t['entry_date'])
    return out

def stats(tr,label):
    R=[t['pnl_pct']/0.0075 for t in tr]  # same R convention as the sealed CSV (risk_pct 0.0075)
    wr=sum(1 for x in R if x>0)/len(R)
    print(f"{label}: n={len(R)} WR={wr:.1%} avgR={statistics.mean(R):+.3f} medR={statistics.median(R):+.3f} worst={min(R):.2f}")
    return R

def campaign(trades,t0,H,risk):
    horizon=t0+timedelta(days=H); att=1
    bal=100_000.0;peak=bal;floor=92_000.0
    win=[t for t in trades if t['entry_date']>=t0 and t['exit_date']<=horizon]
    events=sorted([(t['exit_date'],i,t) for i,t in enumerate(win)])
    open_r={}
    day=t0
    ei=0; entries=sorted([(t['entry_date'],i,t) for i,t in enumerate(win)])
    ci=0
    while day<=horizon:
        # exits
        while ei<len(events) and events[ei][0].date()==day.date():
            t=events[ei][2]; ei+=1
            risk_d=open_r.pop(id(t),None)
            if risk_d is None: continue
            bal+=risk_d*(t['pnl_pct']/0.0075)
            if bal<=floor: att+=1;bal,peak,floor=100_000.0,100_000.0,92_000.0;open_r={}
            elif bal>=108_000: return True,att,(day-t0).days
        while ci<len(entries) and entries[ci][0].date()==day.date():
            t=entries[ci][2]; ci+=1
            open_r[id(t)]=bal*risk
        if bal>peak:peak=bal
        nf=peak-8000.0
        floor=max(floor,min(nf,92_000.0) if peak>=100_000 else nf)
        day+=timedelta(days=1)
    return False,att,None

def camp_sweep(trades,H,risk,label):
    t0=min(t['entry_date'] for t in trades); t1=max(t['exit_date'] for t in trades)
    wins=0;n=0;atts=[]
    t=t0
    while t+timedelta(days=H)<=t1:
        ok,a,_=campaign(trades,t,H,risk); wins+=ok;atts.append(a);n+=1;t+=timedelta(days=3)
    print(f"{label} V2-campaign H={H}d risk={risk:.0%}: P={wins/n:.1%} (n={n} starts) E[evals]={statistics.mean(atts):.2f}")

ins=get_trades('2015-01-01','2024-12-31')
oos=get_trades('2025-01-01','2026-07-03')
Ri=stats(ins,"RIG in-sample 2015-24 ")
Ro=stats(oos,"RIG OOS 2025-26      ")
camp_sweep(ins,90,0.05,"RIG in-sample")
camp_sweep(oos,90,0.05,"RIG OOS      ")
camp_sweep(oos,30,0.05,"RIG OOS      ")
