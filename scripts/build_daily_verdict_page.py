#!/usr/bin/env python3
"""
build_daily_verdict_page.py — generates daily_verdict.html

The ONE page that answers, in plain language: should we be trading live today,
and why or why not. Pulls real state from files that already exist — invents
nothing, fabricates no status. If a source file is missing or a field is null,
the page says so instead of guessing.

Run daily (wire into the existing scheduled pipeline alongside health_check.py).
Output: daily_verdict.html (repo root — served by both localhost and Render/Pages).
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from sovereign.risk.layers.prop import (  # noqa: E402
    MissingContractInput,
    eval_size,
    funded_size,
)

SIZING_FIRM = "cti_1step"  # the only firm with a ruin_engine frontier on disk today

def load(path, default=None):
    p = ROOT / path
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except Exception:
        return default

STALE_DAYS = 7

def main():
    # Spec 021: the ONLY gate source is the carry buy gate state. The old
    # data/agent/prop_challenge_state.json is ICT-lane legacy and is never read
    # again — it once rendered a misleading "4 of 6 green" from a stale snapshot.
    state = load('data/agent/carry_buy_gate_state.json')
    health = load('data/agent/system_health_verdict.json', {})
    numbers = load('data/research/colin_v1_window_backtest.json', {})

    # 2026-08-26 paper-loop dispatch: the RUNNING live record, read straight off
    # the same ledger scripts/paper_carry_daily.py fills and
    # scripts/carry_buy_gate.py's G5 gate reads — not the dead
    # data/agent/carry_paper_account.json placeholder (no writer ever existed).
    paper_ledger_path = ROOT / 'data' / 'trade_logs' / 'paper_carry_trades.jsonl'
    paper_closed = []
    if paper_ledger_path.exists():
        for line in paper_ledger_path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get('status') == 'closed' and rec.get('R') is not None:
                paper_closed.append(rec)
    paper_n = len(paper_closed)
    paper_win_rate = (sum(1 for r in paper_closed if r['R'] > 0) / paper_n) if paper_n else None
    paper_mean_r = (sum(r['R'] for r in paper_closed) / paper_n) if paper_n else None
    g5 = (state or {}).get('gates', {}).get('G5', {})

    # Plain-language translation of each gate (spec 021 P6)
    gate_plain = {
        'G1': 'Does our evaluator exactly reproduce the sealed 411-trade record?',
        'G2': 'Do we understand why the offline replay differs from the sealed record?',
        'G3': 'Does the strategy beat pure luck on data it has never seen?',
        'G4': 'Does the firm we want to buy actually allow every trade we make?',
        'G5': 'Have we proven it with 80+ practice trades at the same rules?',
    }

    # Red-by-default: missing file, missing keys, or stale state ⇒ NOT READY.
    stale_why = None
    gates = []
    verdict_word = None
    if state is None:
        stale_why = "No carry buy-gate state exists yet. Run scripts/carry_buy_gate.py --update-state."
    else:
        try:
            ts = datetime.fromisoformat(state['timestamp'])
        except (KeyError, ValueError):
            ts = None
        if ts is None:
            stale_why = "The gate state file has no readable timestamp — treating it as untrusted."
        elif (datetime.now() - ts).days >= STALE_DAYS:
            stale_why = (f"The gate state is {(datetime.now() - ts).days} days old "
                         f"(limit {STALE_DAYS}). Stale green is not green.")
        elif not isinstance(state.get('gates'), dict) or \
                set(state['gates']) != {'G1', 'G2', 'G3', 'G4', 'G5'}:
            stale_why = "The gate state is missing gate entries — treating it as untrusted."
        else:
            gates = [dict(id=k, status=v.get('status', 'RED'),
                          value=str(v.get('why', v.get('report', ''))or '')[:60])
                     for k, v in sorted(state['gates'].items())]
            verdict_word = state.get('verdict')

    green = sum(1 for g in gates if g.get('status') == 'GREEN')
    red = sum(1 for g in gates if g.get('status') == 'RED')

    if stale_why is not None:
        verdict = "NOT READY — NO TRUSTWORTHY GATE STATE"
        verdict_color = "#A32D2D"
        verdict_bg = "#FCEBEB"
        verdict_why = stale_why
    elif verdict_word == 'BUY' and red == 0 and green == 5:
        verdict = "READY — THE BUY GATE IS FULLY GREEN"
        verdict_color = "#0F6E56"
        verdict_bg = "#E1F5EE"
        verdict_why = "All five checks passed on fresh data. The purchase decision is unlocked."
    else:
        verdict = "NOT READY — DO NOT BUY AN EVALUATION YET"
        verdict_color = "#A32D2D"
        verdict_bg = "#FCEBEB"
        verdict_why = (f"{green} of 5 checks are green. Every check must pass before "
                       "any money goes to a prop firm.")

    strategies = health.get('strategies', {})
    strat_rows = ""
    for name, s in strategies.items():
        ks = s.get('kill_switch', 'UNKNOWN')
        reason = s.get('reason', '')
        color = {'OK': '#0F6E56', 'REDUCE': '#854F0B', 'HALT': '#A32D2D'}.get(ks, '#888')
        strat_rows += f"""
        <div class="strat-row">
          <span class="strat-name">{name.replace('_',' ').title()}</span>
          <span class="strat-pill" style="background:{color}22;color:{color}">{ks}</span>
          <span class="strat-reason">{reason}</span>
        </div>"""

    gate_rows = ""
    for g in gates:
        gid = g.get('id', '')
        status = g.get('status', 'UNKNOWN')
        color = {'GREEN': '#0F6E56', 'YELLOW': '#854F0B', 'RED': '#A32D2D'}.get(status, '#888')
        icon = {'GREEN': '✓', 'YELLOW': '…', 'RED': '✗'}.get(status, '?')
        plain_q = gate_plain.get(gid, gid)
        gate_rows += f"""
        <div class="gate-row">
          <span class="gate-icon" style="color:{color}">{icon}</span>
          <span class="gate-q">{plain_q}</span>
          <span class="gate-val" style="color:{color}">{g.get('value','')}</span>
        </div>"""

    def money_row(label, w):
        if not w or not isinstance(w, dict):
            return ""
        return f"""
        <tr><td>{label}</td>
        <td>{w.get('median',0):+.1%}</td>
        <td>{w.get('p5',0):+.1%}</td>
        <td>{w.get('p95',0):+.1%}</td>
        <td>{w.get('p_pos',0):.0%}</td></tr>"""

    numbers_rows = money_row('5-year test', numbers.get('5yr')) + money_row('10-year test', numbers.get('10yr'))

    # Eval vs funded sizing -- two opposite objective functions, surfaced
    # side by side (sovereign/risk/layers/prop.py: eval_size / funded_size).
    # Neither is wired into an order path; this is compute-and-display only,
    # per the unratified-sizing constraint in CLAUDE.md.
    try:
        eval_result = eval_size(SIZING_FIRM)
        eval_sizing_html = f"""
        <div class="strat-row" style="flex-direction:column;align-items:flex-start;gap:4px;">
          <strong>Eval — maximize P(pass), no rebuy</strong>
          <span>Plateau {eval_result['plateau_risk_lo_pct']:.2%}–{eval_result['plateau_risk_hi_pct']:.2%}
          per trade ({eval_result['plateau_n_cells']} risk levels, all inside one confidence
          interval around {eval_result['argmax_p_pass']:.0%} P(pass)). Not a single precise
          number on purpose -- the peak is a plateau, not a point.</span>
        </div>"""
    except (FileNotFoundError, ValueError) as e:
        eval_sizing_html = f"""
        <div class="strat-row"><span class="strat-reason">Eval sizing not available: {e}</span></div>"""

    try:
        funded_result = funded_size(SIZING_FIRM, profit_split=0.8, payout_interval_days=14)
        funded_sizing_html = f"""
        <div class="strat-row" style="flex-direction:column;align-items:flex-start;gap:4px;">
          <strong>Funded — maximize E[payout], free to lose</strong>
          <span>{funded_result['recommended_risk_pct']:.2%} per trade
          ({'drawdown ceiling binds' if funded_result['dd_ceiling_binds'] else 'growth-optimal Kelly binds'},
          full-Kelly point {funded_result['growth_optimal_risk_pct']:.2%}). ILLUSTRATIVE ONLY --
          computed with a placeholder profit_split=80% / payout every 14 days because
          data/propfirm/firm_contracts.yaml does not carry either field for any firm yet.</span>
        </div>"""
    except MissingContractInput as e:
        funded_sizing_html = f"""
        <div class="strat-row"><span class="strat-reason">Funded sizing not available: {e}</span></div>"""

    if paper_n == 0:
        paper_line = ("No closed paper trades yet. scripts/paper_carry_daily.py fills "
                      "this ledger forward, one real signal at a time -- most days are "
                      "empty by design (macro entries only fire monthly).")
    else:
        need = g5.get('need', 80)
        g5_status = g5.get('status', 'RED')
        paper_line = (
            f"{paper_n} closed paper trade{'s' if paper_n != 1 else ''} so far "
            f"(need {need} for G5). Win rate {paper_win_rate:.0%}, mean R "
            f"{paper_mean_r:+.3f} -- measured on what the system ACTUALLY did with "
            f"live data, not the 2015-2024 backtest. G5 (does the live sample still "
            f"look like the sealed edge?): {g5_status}."
            + ("" if paper_n >= need else
               " A full campaign P(pass) estimate (like G3's bootstrap) needs enough "
               "elapsed calendar days to complete an observation horizon (365/730d) -- "
               "not yet available at this sample size.")
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Should We Trade Live Today?</title>
<style>
  body {{ font-family: -apple-system, 'Segoe UI', sans-serif; background:#FCFCFB; color:#0B0B0B;
         max-width:760px; margin:0 auto; padding:24px 20px 60px; line-height:1.6; }}
  h1 {{ font-size:20px; font-weight:500; margin-bottom:4px; }}
  .updated {{ color:#898781; font-size:13px; margin-bottom:24px; }}
  .verdict-box {{ background:{verdict_bg}; border-radius:16px; padding:28px 24px; margin-bottom:28px; }}
  .verdict-text {{ font-size:28px; font-weight:600; color:{verdict_color}; margin-bottom:10px; }}
  .verdict-why {{ font-size:16px; color:#3a3a38; }}
  h2 {{ font-size:16px; font-weight:500; margin:32px 0 12px; border-bottom:1px solid #E1E0D9; padding-bottom:8px; }}
  .gate-row, .strat-row {{ display:flex; align-items:center; gap:12px; padding:10px 0;
                            border-bottom:1px solid #F0EFE8; font-size:14px; }}
  .gate-icon {{ font-size:18px; font-weight:700; width:20px; }}
  .gate-q {{ flex:1; }}
  .gate-val {{ font-size:13px; font-weight:500; text-align:right; }}
  .strat-name {{ width:130px; font-weight:500; }}
  .strat-pill {{ padding:2px 10px; border-radius:8px; font-size:11px; font-weight:600; }}
  .strat-reason {{ flex:1; color:#6B6A63; font-size:13px; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; margin-top:8px; }}
  th, td {{ text-align:right; padding:8px 6px; border-bottom:1px solid #F0EFE8; }}
  th:first-child, td:first-child {{ text-align:left; }}
  .paper-note {{ background:#F9F9F7; border-radius:12px; padding:16px; font-size:14px; color:#3a3a38; }}
  .footer {{ margin-top:40px; font-size:12px; color:#B4B3A8; }}
</style>
</head>
<body>

<h1>Should We Trade Live Today?</h1>
<p class="updated">Checked: {datetime.now(timezone.utc).strftime('%B %d, %Y at %H:%M UTC')}</p>

<div class="verdict-box">
  <div class="verdict-text">{verdict}</div>
  <div class="verdict-why">{verdict_why}</div>
</div>

<h2>The 5 questions we ask before buying an evaluation</h2>
{gate_rows if gate_rows else '<p>No gate data found yet.</p>'}

<h2>Each strategy, one line each</h2>
{strat_rows if strat_rows else '<p>No strategy health data found yet.</p>'}
<p style="font-size:12px;color:#898781;margin-top:8px;">"REDUCE" doesn't mean broken — it means we don't have enough real trades yet to be sure it's healthy, so size stays small until we do.</p>

<h2>What the carry strategy actually makes (real backtest, real math)</h2>
<table>
<tr><th>Test period</th><th>Typical year</th><th>Bad year</th><th>Great year</th><th>% of years green</th></tr>
{numbers_rows if numbers_rows else '<tr><td colspan="5">Run scripts/colin_v1_window_backtest.py to fill this in.</td></tr>'}
</table>
<p style="font-size:12px;color:#898781;margin-top:8px;">At 1% risk per trade, on the real sealed trade log. Not a guess — this is what actually would have happened.</p>

<h2>Eval sizing vs. funded sizing — opposite problems, not one ceiling</h2>
<p style="font-size:13px;color:#3a3a38;margin-bottom:8px;">Evaluation maximizes P(pass before drawdown) — a ruin problem with an interior optimum. Once funded, further losses cost nothing until the account is pulled, so the objective flips to maximizing E[payout] — a Kelly problem, structurally much larger. Both are size-only, compute-and-display — unratified, not wired into any order path.</p>
{eval_sizing_html}
{funded_sizing_html}

<div class="paper-note">
  <strong>Practice account right now:</strong> {paper_line}
</div>

<div class="footer">
  Auto-generated from real files in the repo — data/agent/carry_buy_gate_state.json
  (spec 021), data/agent/system_health_verdict.json,
  data/research/colin_v1_window_backtest.json, data/trade_logs/paper_carry_trades.jsonl.
  Nothing on this page is invented. A missing number means the underlying data
  doesn't exist yet, not that everything is fine.
</div>

</body>
</html>"""

    out = ROOT / 'daily_verdict.html'
    out.write_text(html)
    print(f"Wrote {out}")
    print(f"Verdict: {verdict}")

if __name__ == '__main__':
    main()
