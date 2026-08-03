# 000 — RULINGS & BUILD ORDER
Read this first. It settles the two open questions Claude Code raised and fixes
the sequence. Everything in `specs/` is a WRITTEN SPEC, not built code, unless a
file says otherwise.

---

## RULING 1 — time-flatten stays in `decide_exit`. Claude Code was right, I was wrong.

SPEC_CHECK.md line 21 said session policy belongs in the runner. That was wrong,
and the reasoning against it is the stronger one: **any exit decision living in
the runner is the second implementation the Stockfish contract forbids.** If the
runner can flatten a position on its own authority, then the backtest harness
must reimplement that same clock rule to match — and now there are two exit
brains that have to agree forever. That is exactly the failure mode this
architecture exists to prevent.

**Ruling:** every rule that can close, reduce, or move a stop on a position lives
in `decide_exit`, full stop. The runner's job is narrow and mechanical: fetch
price, fetch the clock, fetch urgency, build the state, call the engine, express
the returned Actions as orders, log. It decides nothing.

The clock is *data* — `now_et` is an input the runner supplies, exactly like
price. Supplying a fact is not deciding. Current implementation is correct as
built; SPEC_CHECK.md line 21 is struck.

**General principle, applies to every future component:** the runner and the
harness are I/O. If a component needs to make a judgment about a position, it
makes it inside an engine both of them import.

---

## RULING 2 — sequencing: BACKTEST HARNESS FIRST, then regime.py.

BUILD_PLAN.md says regime.py is the thing "nothing else matters until."
SPEC_CHECK.md says the harness is the DoD for everything already built. Both are
true, and there is a synthesis that isn't splitting the difference:

**The harness is the bench that makes regime.py tunable.** Component 1's
definition of done is "classify every 5-minute block of the last 60 sessions and
compare against Colin's eye." That IS a backtest run. Building regime.py first
means hand-rolling a throwaway replay loop to test it, then throwing that away
when the real harness lands — or worse, keeping it, and now there are two replay
paths that drift.

So: harness first, for two reasons at once. It closes the Definition of Done on
the runner + engine that already exist (byte-identical diff), and it is the tool
regime.py needs on day one to be tuned over 60 sessions instead of guessed at.

**Order (each its own commit):**
1. **005_BACKTEST** — replay harness, same engine, byte-identical JSONL. Closes DoD on existing work, becomes the bench for everything after.
2. **001_REGIME** — the classifier, tuned on the bench against Colin's eye.
3. **004_SCORECARD** — regime-call scoring. Starts collecting immediately because it needs weeks of samples to mean anything; the earlier it starts, the sooner it's worth something.
4. **002_SURVIVAL** — pure arithmetic, no dependencies, useful the morning it lands.
5. **003_STOCKFISH_V2** — one field, four policies, four replay variants.
6. **006_ALPHAZERO_V2** — wire regime + news into the frozen bias.json contract.
7. **007_BRIEF** — the automated morning read. Convenience, so it goes last.

---

## The long game, written down so we stop re-deriving it

The opponent is the general market. There is no ELO because there are no rules
and no clean win condition — that is the honest disanalogy with chess, and it is
why "AlphaZero self-play" was rescoped to walk-forward learning early on.

What we DO have: a reward pathway that is measurable fast. Not P&L (too noisy,
50 trades to say anything), but **regime-call accuracy** — roughly 78 five-minute
blocks per session, hundreds of samples per week, each one scorable against what
the tape actually did next. That is the closest thing to a win condition this
game offers, and it is what turns pattern recognition from a claim into a number.

AlphaZero won 78/100 and drew the rest. The goal here is the same shape: not
being right every day, being right enough over thousands of trials, with the
downside bounded every single time by the survival planner and the ladder.

Target for now: **NVDA only.** One name, deep data, live every day. Breadth later.

---

## Status legend used across these specs
- `[BUILT]` — exists, tested, in the repo.
- `[SPEC]` — fully specified here, not written yet. Safe to build from.
- `[SKETCH]` — direction is right, details deliberately unfinished. **Do not
  build from a SKETCH without a planning pass first** — building it now would
  bake in a guess we'd have to tear out.
