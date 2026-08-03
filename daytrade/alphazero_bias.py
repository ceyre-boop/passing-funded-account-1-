#!/usr/bin/env python3
"""ALPHAZERO — the rolling bias/sentiment layer. Runs every 5-10 min ON COLIN'S
MACHINE (cloud sessions can't hold a 5-minute loop while the market trades).

Job: read fresh headlines for the watchlist, score a daily bias in [-1, +1],
detect URGENT shifts (the Trump-tweet case), and write data/daytrade/bias.json.
STOCKFISH reads only two fields from that file: bias and urgency. The contract
between the two engines is that file, nothing else.

HONEST LABEL: v1 scoring is a keyword-valence table — a placeholder, NOT a
trained model and NOT a validated edge. It exists so the plumbing is real and
the interface is frozen; the model gets smarter later without touching
STOCKFISH. Fail loud: no headlines fetched -> urgency='stale', never a guess.

Usage:  python3 alphazero_bias.py --once            # single reading
        python3 alphazero_bias.py --loop 300        # every 5 min (local machine)
"""
import json, time, argparse, sys
from datetime import datetime, timezone
from pathlib import Path

WATCHLIST = ["NVDA", "SPY", "QQQ"]
OUT = Path(__file__).resolve().parents[1] / "data" / "daytrade" / "bias.json"

POS = {"beat", "beats", "surge", "rally", "record", "upgrade", "strong", "growth",
       "bullish", "soars", "tops", "raises", "buyback", "expands"}
NEG = {"miss", "misses", "cut", "cuts", "tariff", "probe", "lawsuit", "downgrade",
       "weak", "bearish", "plunge", "sinks", "recall", "ban", "warning", "fears"}
URGENT_NEG = {"halt", "halted", "crash", "investigation", "fraud", "war", "emergency"}

def fetch_headlines():
    """yfinance news per ticker. On Colin's machine this has live network."""
    try:
        import yfinance as yf
        heads = []
        for t in WATCHLIST:
            for item in (yf.Ticker(t).news or [])[:8]:
                title = (item.get("content", item) or {}).get("title") or item.get("title", "")
                if title: heads.append((t, title))
        return heads
    except Exception as e:
        print(f"ALPHAZERO: headline fetch failed loudly: {e}", file=sys.stderr)
        return []

def score(headlines):
    if not headlines: return None, "stale", []
    tot, urgent, seen = 0, "none", []
    for tkr, h in headlines:
        words = set(h.lower().replace(",", " ").split())
        s = len(words & POS) - len(words & NEG)
        if words & URGENT_NEG: urgent = "exit"
        tot += s; seen.append({"ticker": tkr, "headline": h, "score": s})
    bias = max(-1.0, min(1.0, tot / max(len(headlines), 1) / 2))
    return bias, urgent, seen

def run_once(prev_bias=None):
    heads = fetch_headlines()
    bias, urgency, seen = score(heads)
    if bias is not None and prev_bias is not None and urgency == "none":
        if abs(bias - prev_bias) >= 0.5:          # regime lurch -> tighten the trail
            urgency = "tighten"
    out = {"ts": datetime.now(timezone.utc).isoformat(), "bias": bias,
           "urgency": urgency, "n_headlines": len(heads), "detail": seen[:20],
           "model": "keyword-valence v1 (placeholder, unvalidated)"}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1))
    print(f"ALPHAZERO {out['ts']} bias={bias} urgency={urgency} n={len(heads)}")
    return bias

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", type=int, metavar="SECONDS")
    a = ap.parse_args()
    if a.loop:
        prev = None
        while True:
            prev = run_once(prev) or prev
            time.sleep(a.loop)
    else:
        run_once()
