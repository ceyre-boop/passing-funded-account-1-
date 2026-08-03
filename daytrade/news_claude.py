#!/usr/bin/env python3
"""CLAUDE AS THE NEWS BRAIN — spec 009. NOT BUILT.

Replaces alphazero_bias.py's keyword-valence placeholder with a real judgment.
Two clocks, and the distinction matters: STOCKFISH is gated on being in a trade
(manual, correctly — it is gated on a human act). ALPHAZERO runs open-to-close
regardless, so it needs a launchd job AND a heartbeat. A silently dead collector
is worse than no collector: the gap is invisible until you go looking for data
that is not there.

STRUCTURED OUTPUT, never prose: bias, conviction, one-sentence thesis, analyst
consensus EPS alongside Claude's own projected EPS, and a `watch_for` list. The
5-minute delta check tests against `watch_for` — did anything on it happen? —
and only fires when the headline set actually changes, so it costs nearly nothing.

THE RULE WITH NO EXEMPTION: Claude's bias calls get scored exactly like the
regime classifier, against dumb baselines — always-flat, yesterday's direction,
coin flip. An LLM producing a confident-sounding paragraph is the easiest way in
this whole repo to smuggle an unvalidated edge past the gate, and that is the
mistake SANITY_AUDIT.md was written about. Prompt changes ARE rule changes:
versioned, committed before the session, never quietly edited after a bad day.
"""
raise NotImplementedError("news_claude.py is not built — see spec 009.")
