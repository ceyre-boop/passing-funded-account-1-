"""JJ-SIM-001 tracker — log sim trades, enforce Lucid-style rules, regenerate the dashboard.

  python3 scripts/jj_sim_tracker.py --status
  python3 scripts/jj_sim_tracker.py --trade -1.0 --note "9:30 continuation, stopped"
  python3 scripts/jj_sim_tracker.py --trade 1.5 --note "reversion to open, TP"
  python3 scripts/jj_sim_tracker.py --eod          # end-of-day floor roll
Every call rewrites dashboard/jj_dashboard.html from state. R multiples: +1.5 win / -1.0 loss typical.
"""
import json, sys, argparse
from datetime import date
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/"data/propfirm/jj_sim_account.json"
DASH=ROOT/"dashboard/jj_dashboard.html"

def load(): return json.loads(STATE.read_text())
def save(s): STATE.write_text(json.dumps(s,indent=2))

def eod(s):
    if s["balance"]>s["peak_eod"]: s["peak_eod"]=s["balance"]
    nf=s["peak_eod"]-8000.0
    s["floor"]=max(s["floor"], min(nf,92000.0) if s["peak_eod"]>=100000 else nf)
    s["trading_days"]+=1
    check(s)

def trade(s,r,note=""):
    risk=s["balance"]*s["risk_per_trade_pct"]
    pnl=risk*r
    s["balance"]+=pnl
    s["trades"].append({"date":str(date.today()),"r":r,"risk":round(risk,2),"pnl":round(pnl,2),
                        "balance":round(s["balance"],2),"note":note})
    check(s)

def check(s):
    if s["balance"]<=s["floor"]: s["status"]="BUSTED — rebuy per COLIN_V2 (log it, restart state)"
    elif s["balance"]>=108000 and s["trading_days"]>=2: s["status"]="PASSED"

def render(s):
    n=len(s["trades"]); wins=sum(1 for t in s["trades"] if t["r"]>0)
    wr=wins/n if n else 0; buf=s["balance"]-s["floor"]
    prog=max(0,min(1,(s["balance"]-100000)/8000))
    pts=" ".join(f"{i},{100-(t['balance']-92000)/16000*100:.1f}" for i,t in enumerate([{"balance":100000}]+s["trades"]))
    rows="".join(f"<tr><td>{t['date']}</td><td>{t['r']:+.2f}R</td><td>${t['pnl']:+,.0f}</td><td>${t['balance']:,.0f}</td><td>{t.get('note','')}</td></tr>" for t in reversed(s["trades"][-30:]))
    status_color={"ACTIVE":"#4ade80","PASSED":"#22d3ee"}.get(s["status"].split(" ")[0],"#f87171")
    html=f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>JJ-SIM-001 — Funded Eval</title><style>
body{{background:#0f1117;color:#e2e8f0;font-family:-apple-system,Segoe UI,sans-serif;margin:0;padding:24px}}
.card{{background:#161a23;border:1px solid #1e2433;border-radius:12px;padding:18px;margin-bottom:14px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
.k{{font-size:11px;letter-spacing:.08em;color:#94a3b8;text-transform:uppercase}} .v{{font-size:26px;font-weight:700;margin-top:4px}}
.bar{{height:10px;background:#1e2433;border-radius:5px;overflow:hidden}} .fill{{height:100%;background:linear-gradient(90deg,#22d3ee,#4ade80)}}
table{{width:100%;border-collapse:collapse;font-size:13px}} td,th{{padding:6px 8px;border-bottom:1px solid #1e2433;text-align:left}}
.badge{{display:inline-block;padding:4px 12px;border-radius:99px;font-weight:700;background:#1e2433}}
small{{color:#64748b}}</style></head><body>
<div class="card"><div style="display:flex;justify-content:space-between;align-items:center">
<div><div style="font-size:20px;font-weight:800">JJ-SIM-001 · $100K Evaluation <span class="badge" style="color:{status_color}">{s['status'].split(' ')[0]}</span></div>
<small>{s['strategy']} · opened {s['opened']} · Lucid-style rules · SIM treated as real</small></div>
<div style="text-align:right"><div class="k">Projection (validated spec)</div><div class="v" style="font-size:18px">75% @30d · 94% @90d</div></div></div></div>
<div class="grid">
<div class="card"><div class="k">Balance</div><div class="v">${s['balance']:,.0f}</div></div>
<div class="card"><div class="k">Target $108,000</div><div class="v">${max(0,108000-s['balance']):,.0f} to go</div>
<div class="bar" style="margin-top:8px"><div class="fill" style="width:{prog*100:.0f}%"></div></div></div>
<div class="card"><div class="k">Trailing floor</div><div class="v">${s['floor']:,.0f}</div><small>buffer ${buf:,.0f}</small></div>
<div class="card"><div class="k">Day</div><div class="v">{s['trading_days']} / {s['deadline_days']}</div><small>30d checkpoint at day {s['checkpoint_days']}</small></div>
<div class="card"><div class="k">Trades</div><div class="v">{n}</div><small>WR {wr:.0%} · target ≥52% at 1:1.5</small></div></div>
<div class="card"><div class="k">Equity path (per trade)</div>
<svg viewBox="0 0 {max(n,1)} 100" preserveAspectRatio="none" style="width:100%;height:120px"><polyline fill="none" stroke="#22d3ee" stroke-width="1.5" points="{pts}"/></svg></div>
<div class="card"><div class="k">Trade log (latest 30)</div><table><tr><th>Date</th><th>R</th><th>P&L</th><th>Balance</th><th>Note</th></tr>{rows or '<tr><td colspan=5><small>No trades yet — validation sprint starts when NQ data lands.</small></td></tr>'}</table></div>
<div class="card"><small><b>The gate this account exists to pass:</b> live WR within CI of 55% at 1:1.5 over n≥80 trades. Pass → buy the real eval per COLIN_V2. Miss → no fees burned, back to the bench. Alta Investments · generated by scripts/jj_sim_tracker.py</small></div>
</body></html>"""
    DASH.write_text(html)

if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--trade",type=float); ap.add_argument("--note",default="")
    ap.add_argument("--eod",action="store_true"); ap.add_argument("--status",action="store_true")
    a=ap.parse_args(); s=load()
    if a.trade is not None: trade(s,a.trade,a.note)
    if a.eod: eod(s)
    save(s); render(s)
    print(f"{s['name']}: ${s['balance']:,.0f} | floor ${s['floor']:,.0f} | day {s['trading_days']} | {len(s['trades'])} trades | {s['status']}")
