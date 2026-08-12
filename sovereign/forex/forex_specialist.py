"""
ForexSpecialist — top-level orchestrator for the forex system.

Pipeline:
  1. ForexMacroEngine  → macro directional bias per pair
  2. ICTEngine         → price action structure (FVG, OB, sweeps, kill zones)
  3. ForexEntryEngine  → 6-point ICT checklist → entry/stop/target
  4. ForexPositionSizer → risk-based sizing with hard rules

Usage:
    specialist = ForexSpecialist(account_balance=10_000)
    report = specialist.run()
    for sig in report.tradeable:
        print(sig)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

from sovereign.forex.entry_engine import ForexEntryEngine, ForexEntrySignal
from sovereign.forex.position_sizer import ForexPositionSizer, PositionSize

logger = logging.getLogger(__name__)


@dataclass
class ForexTradeCandidate:
    entry_signal: ForexEntrySignal
    position: PositionSize

    def summary(self) -> str:
        s = self.entry_signal
        p = self.position
        lines = [
            f"{'─'*60}",
            f"  {s.pair:12s}  {s.direction:5s}  Score: {s.score}/6  Conviction: {s.macro_conviction:.2f}",
            f"  Entry: {s.entry_price:.5f}  Stop: {s.stop_price:.5f}  "
            f"Risk: {p.risk_pct:.1%} (${p.risk_usd:.0f})",
            f"  T1: {s.t1:.5f}  T2: {s.t2:.5f}  T3: {s.t3:.5f}  (R:R T1={s.rr_t1:.1f})",
            f"  Units: {p.units:,.0f}  Size modifier: {s.size_modifier:.0%}",
            f"  Kill Zone: {s.ict_analysis.kill_zone_name or 'N/A'}",
            f"  Macro driver: {s.macro_signal.primary_driver}",
            f"  Rationale:",
        ]
        for r in s.rationale:
            lines.append(f"    {r}")
        return '\n'.join(lines)


@dataclass
class ForexScanReport:
    as_of: pd.Timestamp
    tradeable: List[ForexTradeCandidate] = field(default_factory=list)
    skipped: List[ForexEntrySignal] = field(default_factory=list)

    def print(self) -> None:
        print(f"\n{'═'*60}")
        print(f"  FOREX SCAN  —  {self.as_of.strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"{'═'*60}")
        if not self.tradeable:
            print("  No tradeable setups at this time.")
        else:
            for c in self.tradeable:
                print(c.summary())
        print(f"\n  Evaluated: {len(self.tradeable) + len(self.skipped)} pairs  "
              f"|  Tradeable: {len(self.tradeable)}")
        print(f"{'═'*60}\n")


class ForexSpecialist:

    def __init__(self, account_balance: float = 10_000.0):
        self._entry = ForexEntryEngine()
        self._sizer = ForexPositionSizer(account_balance=account_balance)

    def run(self) -> ForexScanReport:
        """Full scan across all 11 pairs → returns ranked trade candidates."""
        from sovereign.forex.pair_universe import ALL_PAIRS
        report = ForexScanReport(as_of=pd.Timestamp.utcnow())

        # Single pass: evaluate all pairs, collect all signals
        entry_signals: list[ForexEntrySignal] = []
        for pair in ALL_PAIRS:
            try:
                sig = self._entry.evaluate(pair)
                if sig:
                    entry_signals.append(sig)
            except Exception as e:
                logger.warning(f"ForexSpecialist eval failed for {pair}: {type(e).__name__}: {e}")

        for sig in entry_signals:
            if not sig.is_tradeable:
                report.skipped.append(sig)
                continue

            pos = self._sizer.size(
                pair=sig.pair,
                entry=sig.entry_price,
                stop=sig.stop_price,
                t1=sig.t1,
                t2=sig.t2,
                t3=sig.t3,
                size_modifier=sig.size_modifier,
            )

            if pos.rejected:
                logger.info(f"{sig.pair}: position rejected — {pos.reject_reason}")
                report.skipped.append(sig)
                continue

            # A SCAN CANDIDATE IS NOT AN EXECUTED TRADE.
            #
            # This block used to call `log_forex_decision(...)`, which appends to
            # the executed-trade decision log — for every setup the scanner merely
            # SAW. Nothing here confirms an entry fill: `run()` ranks candidates
            # and returns them, and the human or the execution path decides
            # whether any of them is taken. The result was a decision log whose
            # rows were mostly trades nobody entered, which makes every win rate,
            # every R aggregate and every Oracle inference computed from it wrong
            # in the same direction.
            #
            # Candidates are still worth keeping — they are the forecast half of
            # the record, and scoring "what did the scanner rank highly" against
            # "what actually happened" is a real question. So they are recorded,
            # in their own directory, carrying `record_kind: "candidate"`, where
            # no executed-trade reader can pick them up.
            #
            # The executed-trade record is now written at the point an entry fill
            # is CONFIRMED — `execution_ledger.log_executed_trade()`, which is
            # idempotent on trade_id — not here.
            try:
                from sovereign.intelligence.execution_ledger import log_scan_candidate
                _macro = sig.macro_signal
                log_scan_candidate(
                    system="FOREX",
                    pair=sig.pair,
                    direction=sig.direction,
                    entry_level=sig.entry_price,
                    stop_loss=sig.stop_price,
                    rationale=list(sig.rationale[:6]),
                    extra={
                        "score": sig.score,
                        "macro_conviction": sig.macro_conviction,
                        "risk_pct": pos.risk_pct,
                        "size_modifier": sig.size_modifier,
                        "rate_diff_z": getattr(_macro, "irp_z", None),
                        "rate_differential": getattr(_macro, "rate_differential", None),
                        "ppp_z": getattr(_macro, "ppp_z", None),
                        "primary_driver": getattr(_macro, "primary_driver", None),
                        "hold_days_est": getattr(_macro, "hold_period_estimate", 60),
                        "below_proven_bar": bool(sig.macro_conviction < 0.35),
                    },
                )
            except Exception as e:
                # Scan-time telemetry must not break a scan. This is the research
                # lane, not the execution lane — the executed-trade ledger is
                # fail-loud precisely because it is the one that must not miss.
                logger.debug(f"candidate log failed for {sig.pair}: {type(e).__name__}: {e}")

            report.tradeable.append(ForexTradeCandidate(
                entry_signal=sig,
                position=pos,
            ))

        # Sort tradeable by score then macro conviction
        report.tradeable.sort(
            key=lambda c: (c.entry_signal.score, c.entry_signal.macro_conviction),
            reverse=True,
        )

        return report

    def evaluate_pair(self, pair: str) -> Optional[ForexTradeCandidate]:
        """Single-pair evaluation with position sizing."""
        sig = self._entry.evaluate(pair)
        if sig is None or not sig.is_tradeable:
            return None

        pos = self._sizer.size(
            pair=sig.pair,
            entry=sig.entry_price,
            stop=sig.stop_price,
            t1=sig.t1,
            t2=sig.t2,
            t3=sig.t3,
            size_modifier=sig.size_modifier,
        )

        if pos.rejected:
            return None

        return ForexTradeCandidate(entry_signal=sig, position=pos)
