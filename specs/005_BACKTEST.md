# 005 — BACKTEST HARNESS  `daytrade/backtest.py`   `[SPEC]`  ← BUILD THIS FIRST
Two jobs in one file: close the Definition of Done on everything already built,
and become the bench that makes the regime classifier tunable.

## Job 1 — the DoD diff
```python
# replay a recorded session's quotes through the SAME engine and prove the
# harness and the live runner produce identical action logs.
python3 daytrade/backtest.py --replay-session data/daytrade/session_2026-08-03.jsonl
# -> writes backtest_2026-08-03.jsonl, then:
diff <(jq -c '{ts,price,actions}' session_2026-08-03.jsonl) \
     <(jq -c '{ts,price,actions}' backtest_2026-08-03.jsonl)   # must be EMPTY
```
If that diff is non-empty, something in the runner is deciding on its own
authority and must be moved into the engine (ruling 1). **This is the test that
proves the whole architecture, so it runs in CI-style on every engine change.**

Fields compared: ts, price, and the full action list (kind, sl, fraction,
reason). Fields NOT compared: quote source, latency, broker responses — those
legitimately differ between live and replay.

## Job 2 — the bench
```python
python3 daytrade/backtest.py --symbol NVDA --days 60 --label-regimes
# -> data/regime_labels.csv        one row per 5m block, full evidence dict
# -> data/regime_scorecard.csv     graded via spec 004's grade()
# -> prints report(): accuracy, both baselines, lift, calibration buckets
```
This is how spec 001 gets tuned without hand-rolling a throwaway replay loop —
and it is why this file is first in the order.

## Structure
```python
def load_bars(symbol, days, tf) -> Bars:
    """yfinance 1m only goes back ~30d; 5m goes back ~60d. For longer history
    use the quant repo's parquet caches — do NOT re-fetch and re-cache here,
    that repo already solved it. Fail loud on gaps; never forward-fill a bar."""

def replay_session(session_jsonl) -> list[dict]:
    """Job 1. Reconstruct TradeState from the recorded plan + quotes, step
    through decide_exit/apply_action, emit the same JSONL shape."""

def label_history(symbol, days) -> None:
    """Job 2. For each 5m block: classify(), then grade() against forward bars."""

def counterfactual(session, policies=("STATIC","TRAIL_WIDE","TRAIL_TIGHT")) -> dict:
    """Same day, same entry, each exit policy. What WOULD each have made?
    This is how policy choices earn their place instead of being assumed."""
```

## Non-negotiables
- Imports `decide_exit`, `apply_action`, `classify`, `grade`. Implements ZERO
  decision logic of its own. If the harness needs a rule the engine doesn't
  have, the rule goes in the engine.
- Bar gaps, holidays, half-days, and the 09:30 open are explicit cases. A missing
  bar raises; it is never silently skipped or interpolated.
- Look-ahead is the cardinal sin. `classify()` at block N sees bars ≤ N. `grade()`
  sees N+1..N+K and is only ever called for scoring, never fed back into a
  classification. **Write an assertion that enforces this at the boundary** —
  SANITY_AUDIT.md exists because this exact mistake already cost us once.
- Costs modeled at the pessimistic end when replaying fills, same convention as
  EVAL_LAB.md: spread crossed, stops fill through, gaps fill at the open.

## Definition of done
1. Byte-identical diff on the 2026-08-03 session. Empty output, printed.
2. 60 sessions of NVDA labeled, scorecard report printed with both baselines.
3. The four expected replay logs from spec 003 reproduce exactly.

## `[SKETCH]` — later, needs real thought
- **Intraday tick/sub-minute replay.** Everything here is 1m/5m bars. Real fills
  happen inside bars, and the 1-5 minute continuation bet lives at that
  resolution. This needs a real data source (Databento/Polygon — still the open
  blocker from NEXT.md) and a fill model that isn't a guess. Large, expensive,
  and NOT needed to tune the regime classifier, which is why it waits.
- **Walk-forward parameter fitting** over `TIME_PRIORS` and rule weights. The
  bench makes it mechanically easy, which is exactly the danger. Gated behind
  spec 004's prerequisites and a pre-registered protocol, no exceptions.
