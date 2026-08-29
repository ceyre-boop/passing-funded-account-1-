#!/usr/bin/env python3
"""Preflight — the macro calendar's freshness gate. Same discipline as
scripts/carry_scan.py's macro staleness preflight: an event_risk of 0.0 is
only informative if the sources behind it are current, so this refuses (exit
1) rather than let a silently-empty calendar be read as "no events today".

Usage:
  python3 scripts/check_macro_calendar_freshness.py            # exits 1 if stale
  python3 scripts/check_macro_calendar_freshness.py --quiet    # exit code only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "daytrade"))
import macro_calendar as mc  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="macro calendar freshness preflight")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    f = mc.freshness()
    if not a.quiet:
        print(f"  fred_age_days: {f['fred_age_days']}  (limit {mc.FRED_STALE_DAYS})")
        print(f"  fomc_age_days: {f['fomc_age_days']}  (limit {mc.FOMC_STALE_DAYS})")

    if f["stale"]:
        if not a.quiet:
            print("  STALE:")
            for r in f["reasons"]:
                print(f"    - {r}")
        return 1

    if not a.quiet:
        print("  FRESH")
    return 0


if __name__ == "__main__":
    sys.exit(main())
