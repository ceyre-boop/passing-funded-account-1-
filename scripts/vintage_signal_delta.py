#!/usr/bin/env python3
"""Spec 039 — how much does the macro signal itself change under an honest vintage?

The trade-level A/B (scripts/run_vintage_ab.py) runs on the offline rig, which
reproduces only ~72% of the sealed 411 and is macro-signal-poor in 2015-2019.
This script measures the thing the rig cannot understate: the macro layer's own
monthly sign, computed directly, on every business-month-start date 2015-2024,
under both vintages. Population-independent.

Calls ForexSignalEngine._macro_signal_for_date with exactly the sealed config
(irp_weight 0.50, rate_weight 0.50, threshold 0.15, momentum filter on). Nothing
about the strategy is varied — only which vintage of the inputs it reads.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

PAIRS = {
    "EURUSD=X": ("EU", "US"),
    "GBPUSD=X": ("UK", "US"),
    "USDJPY=X": ("US", "JP"),
    "AUDUSD=X": ("AU", "US"),
}
START, END = "2015-01-01", "2024-12-31"
OUT = ROOT / "data" / "vintage_ab" / "signal_delta.json"


def signals_for(mode: str) -> dict[str, pd.Series]:
    os.environ["CARRY_RATE_VINTAGE"] = mode
    for m in list(sys.modules):
        if m.startswith("sovereign.forex") or m == "oos_campaign_test":
            del sys.modules[m]
    import oos_campaign_test as rig  # noqa: F401  (installs the offline yf patch)
    from sovereign.forex.data_fetcher import ForexDataFetcher
    from sovereign.forex.entry_engine import CBEventTrigger
    from sovereign.forex.signal_engine import ForexSignalEngine, SignalConfig
    import yfinance as yf

    fetcher = ForexDataFetcher()
    engine = ForexSignalEngine(fetcher=fetcher, cb_trigger=CBEventTrigger(),
                               config=SignalConfig())
    out = {}
    for pair, (base, quote) in PAIRS.items():
        px = yf.download(pair, start="2014-01-01", end=END)
        close = px["Close"] if "Close" in px.columns else px.iloc[:, 0]
        close.index = pd.to_datetime(close.index).tz_localize(None)
        br = fetcher.get_rate_history(base)
        qr = fetcher.get_rate_history(quote)
        bc = fetcher.get_cpi_history(base)
        qc = fetcher.get_cpi_history(quote)
        dates = [d for d in close.resample("BMS").first().index
                 if pd.Timestamp(START) <= d <= pd.Timestamp(END)]
        out[pair] = pd.Series(
            [engine._macro_signal_for_date(
                close=close, date=d, base_country=base, quote_country=quote,
                base_rates=br, quote_rates=qr, base_cpi_h=bc, quote_cpi_h=qc)
             for d in dates],
            index=pd.DatetimeIndex(dates),
        )
    out["__exclusions__"] = list(engine.vintage_exclusions)
    return out


def main() -> int:
    nom = signals_for("nominal")
    pub = signals_for("publication")
    report = {"per_pair": {}, "publication_exclusions": len(pub["__exclusions__"])}
    tot = dict(dates=0, same=0, flip=0, lost=0, gained=0, nom_nonzero=0, pub_nonzero=0)
    for pair in PAIRS:
        a, b = nom[pair], pub[pair]
        idx = a.index.intersection(b.index)
        a, b = a.loc[idx], b.loc[idx]
        flip = int(((a != 0) & (b != 0) & (a != b)).sum())
        lost = int(((a != 0) & (b == 0)).sum())
        gained = int(((a == 0) & (b != 0)).sum())
        same = int((a == b).sum())
        report["per_pair"][pair] = {
            "dates": len(idx), "identical": same, "sign_flip": flip,
            "lost": lost, "gained": gained,
            "nominal_nonzero": int((a != 0).sum()), "publication_nonzero": int((b != 0).sum()),
            "agreement_pct": round(100 * same / len(idx), 1),
        }
        tot["dates"] += len(idx); tot["same"] += same; tot["flip"] += flip
        tot["lost"] += lost; tot["gained"] += gained
        tot["nom_nonzero"] += int((a != 0).sum()); tot["pub_nonzero"] += int((b != 0).sum())
    tot["agreement_pct"] = round(100 * tot["same"] / tot["dates"], 1)
    report["total"] = tot
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
