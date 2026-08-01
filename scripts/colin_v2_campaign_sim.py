# COLIN_V2 campaign simulator: calendar-clock, immediate rebuy on bust/expiry.
# Question answered: P(funded within H calendar days of starting the campaign)?
import csv
from datetime import datetime, timedelta

rows=list(csv.DictReader(open('data/proof/backtest_trades_v015_2015_2024.csv')))
for r in rows:
    r['ed']=datetime.fromisoformat(r['entry_date']); r['xd']=datetime.fromisoformat(r['exit_date'])
    r['R']=float(r['pnl_pct'])/float(r['risk_pct'])
rows.sort(key=lambda r:r['ed'])
T0,T1=rows[0]['ed'],rows[-1]['xd']
ACCT=100_000.0; TARGET=0.08; MAXDD=0.08; ATTEMPT_DEADLINE=90

def campaign(t0,H,policy):
    """Run rebuy-on-bust campaign from t0 with calendar horizon H days.
       Returns (funded_day or None, attempts_used)."""
    horizon=t0+timedelta(days=H)
    attempts=1
    bal=ACCT; peak=ACCT; floor=ACCT*(1-MAXDD)
    att_start=t0; open_pos=[]  # (exit_date, risk$, R)
    trades=[r for r in rows if r['ed']>=t0 and r['ed']<=horizon]
    # event walk day by day
    day=t0
    ti=0
    while day<=horizon:
        # attempt expiry (timed eval clock) -> rebuy fresh
        if (day-att_start).days>=ATTEMPT_DEADLINE:
            attempts+=1; att_start=day; bal=ACCT; peak=ACCT; floor=ACCT*(1-MAXDD); open_pos=[]
        # exits today
        for p in [p for p in open_pos if p[0].date()==day.date()]:
            bal+=p[1]*p[2]; open_pos.remove(p)
            if bal<=floor:  # BUST -> rebuy immediately (fee later), positions die with account
                attempts+=1; att_start=day; bal=ACCT; peak=ACCT; floor=ACCT*(1-MAXDD); open_pos=[]
                break
            if bal>=ACCT*(1+TARGET) and (day-att_start).days>=2:
                return (day-t0).days, attempts
        # entries today (only if exit lands before both horizon and attempt deadline)
        while ti<len(trades) and trades[ti]['ed'].date()==day.date():
            tr=trades[ti]; ti+=1
            if tr['xd']>horizon or (tr['xd']-att_start).days>=ATTEMPT_DEADLINE: continue
            buf=bal-floor
            r=policy(bal=bal,floor=floor,buf=buf,start=ACCT,
                     gap=ACCT*(1+TARGET)-bal,days_left=(min(horizon,att_start+timedelta(days=ATTEMPT_DEADLINE))-tr['ed']).days)
            open_pos.append((tr['xd'], bal*max(0.0,r), tr['R']))
        # EOD floor roll
        if bal>peak: peak=bal
        nf=peak-ACCT*MAXDD
        floor=max(floor, min(nf,ACCT*(1-MAXDD)) if peak>=ACCT else nf)
        day+=timedelta(days=1)
    return None, attempts

def sweep(H,policy,step=3):
    starts=[]; t=T0
    while t+timedelta(days=H)<=T1: starts.append(t); t+=timedelta(days=step)
    hits=[]; atts=[]
    for t0 in starts:
        d,a=campaign(t0,H,policy)
        atts.append(a)
        if d is not None: hits.append(d)
    n=len(starts)
    return dict(n=n,P=len(hits)/n,med_day=sorted(hits)[len(hits)//2] if hits else None,
                mean_attempts=sum(atts)/n,max_attempts=max(atts))

# ---- policies ----
def static(r,cap=1.0):
    return lambda bal,buf,**k: min(r, cap*buf/bal)
def gapguard(r,g=3.5):
    return lambda bal,buf,**k: min(r, buf/(g*bal))            # survive a -g R trade
def pace(rlo,rhi,g=3.5):
    def pol(bal,buf,gap,days_left,**k):
        etl=max(days_left/9.0,0.5)                            # expected completed trades left
        need=(gap/bal)/ (0.356*etl)                           # risk needed to close gap at avg R
        return max(rlo,min(rhi,need,buf/(g*bal)))
    return pol
def sprint(rbase,cush,rhi,g=3.5):
    def pol(bal,buf,start,**k):
        r = rhi if bal>=start*(1+cush) else rbase
        return min(r, buf/(g*bal))
    return pol

grids=[]
for r in (0.02,0.03,0.04,0.05,0.06): grids.append((f"static {r:.0%}",static(r)))
for r in (0.03,0.04,0.05,0.06):      grids.append((f"gapguard {r:.0%}",gapguard(r)))
for rlo,rhi in ((0.01,0.04),(0.015,0.05),(0.02,0.06)): grids.append((f"pace {rlo:.1%}->{rhi:.0%}",pace(rlo,rhi)))
for rb,c,rh in ((0.03,0.03,0.05),(0.02,0.04,0.06),(0.04,0.03,0.02)): grids.append((f"sprint {rb:.0%}+{c:.0%}->{rh:.0%}",sprint(rb,c,rh)))

for H in (30,90):
    print(f"\n== CAMPAIGN horizon {H}d (rebuy on bust, 90d eval clock, step=3d starts) ==")
    print(f"{'policy':24} | {'P(funded)':>9} {'med day':>7} {'E[attempts]':>11} {'max':>4}")
    best=None
    for name,pol in grids:
        e=sweep(H,pol)
        print(f"{name:24} | {e['P']:9.1%} {str(e['med_day']):>7} {e['mean_attempts']:11.2f} {e['max_attempts']:4d}")

# ── Reproduce the COLIN_V2 headline numbers ──────────────────────────────────
# P(funded ≤ H) with rebuy-on-bust campaigns, static eval-mode risk:
#   for H in (30,60,90,120,150,180): print(H, sweep(H, static(0.05)))
# Findings sealed 2026-08-01 (see COLIN_V2.md). Data source: the sealed
# data/proof/backtest_trades_v015_2015_2024.csv — run from repo root.
