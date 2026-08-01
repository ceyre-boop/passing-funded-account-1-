"""Requirements-map Monte Carlo — sealed 2026-08-01.
10,000 rebuy-on-bust campaigns per cell, trailing 8% DD (locks at start), best risk per cell.
IID model + regime-weather model calibrated to real carry block-replay (27% @30d / 67% @90d).

SEALED RESULTS:
  IID:      carry@1wk 55/93 | carry@3wk 94/100 | JJ 55%WR 1:1.5 @5wk 99.8/100 | scalper@3wk 92/100
  WEATHER:  JJ @5wk ~75/94  | scalper@3wk ~65/85 | carry@3wk ~64/85
ONLY the carry@0.8wk row is a strategy we own. JJ row = V3 build target (validation gate applies).
"""
import random
def campaign(H,tpw,wr,w,l,risk,weather=False,bad_arr=0.15,bad_wr=0.10,switch=1/180,p_bad=0.45,seed=None):
    if seed is not None: random.seed(seed)
    bal=100.0;peak=100.0;floor=92.0
    bad = weather and random.random()<p_bad
    for day in range(H):
        if weather and random.random()<switch: bad = not bad
        tpd=(tpw/7)*((bad_arr if bad else 1.4) if weather else 1.0)
        w_r=wr-(bad_wr if bad and weather else 0)
        if random.random()<min(tpd,0.95):
            r=w if random.random()<w_r else l
            bal+=100*risk*r
            if bal<=floor: bal,peak,floor=100.0,100.0,92.0
            if bal>=108: return True
        if bal>peak: peak=bal
        floor=max(floor,min(peak-8.0,92.0))
    return False
if __name__=="__main__":
    random.seed(99); T=10_000
    for name,tpw,wr,w,l in [("JJ-style 55% 1:1.5 @5/wk",5,.55,1.5,-1.0),
                            ("Scalper 60% 1:1 @3/wk",3,.60,1.0,-1.0),
                            ("Carry-like @3/wk",3,.487,1.43,-.85)]:
        for weather in (False,True):
            p30=max(sum(campaign(30,tpw,wr,w,l,r,weather) for _ in range(T))/T for r in (0.02,0.03,0.05))
            p90=max(sum(campaign(90,tpw,wr,w,l,r,weather) for _ in range(T))/T for r in (0.02,0.03,0.05))
            print(f"{name:28} {'weather' if weather else 'IID':7} | P(30d) {p30:6.1%}  P(90d) {p90:6.1%}")
