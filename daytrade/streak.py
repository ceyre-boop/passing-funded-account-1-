#!/usr/bin/env python3
"""STREAK TRACKER — spec 004, second half. NOT BUILT. No blockers.

Buildable today: it depends on nothing but the shot ledger and session results.
It is unbuilt only because 005/008 came first in the order.

THE CONTRACT (spec 004):
    StreakState(green_streak, longest_streak, days_since_red, rolling_20d_winrate,
                cumulative_R, day_pnl, distance_to_goal, consecutive_red_days,
                campaign_cushion, cooloff_until)
    update(day_result) -> None      # called at every session close
    -> data/streak.json

THE ONE RULE THAT CARRIES THE LADDER: two consecutive red days starts a 5-day
cooloff, enforced here and honoured by the runner. friction_ladder.py showed the
entire campaign's 93-98% pass probability depends on the cooloff being real. It
is not advice. The runner must refuse to arm before `cooloff_until`.
"""
raise NotImplementedError("streak.py is not built. No blockers — see spec 004.")
