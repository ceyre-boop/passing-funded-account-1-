"""Pure Kelly-sizing math — Andrew Lo / AFML fractional Kelly, plus the Hoeffding
confidence correction fed into it.

Split out of kelly_engine.py on 2026-08-26. kelly_engine.py's module-level imports
(`layer2.risk_engine`, `layer2.dynamic_rr_engine`, `contracts.types`, `config.loader`)
belong to `SovereignRiskEngine`, a wrapper class copied from a different repo whose
dependency tree does not exist here (see sovereign/risk/kelly_engine.py's docstring
and test_kelly_engine.py). That made the whole module unimportable, including these
three pure functions, which have no relationship to `layer2`/`contracts`/`config` at
all — they are ordinary math. `sovereign/risk/layers/kelly.py` (Layer 2 — KELLY
CEILING, the thing that actually sits on risk_engine.decide()'s sizing path) now
imports directly from this module instead of from kelly_engine.py, so the real
control CLAUDE.md non-negotiable #4 describes can actually load.

kelly_engine.py re-exports these three names for backward compatibility, but
kelly_engine.py itself remains unimportable outside a test harness that stubs
`layer2`/`config` — that is correct and honest: `SovereignRiskEngine` really does
depend on foreign code that does not exist in this repo, and nothing calls it.

f* = (p·b - q) / b, fractional (quarter-)Kelly, clamped to [floor, ceiling].
"""

import math


# ── Hoeffding Confidence Interval (CS229 Lecture 09) ─────────────────── #

def hoeffding_win_rate(
    observed_win_rate: float,
    n_trades: int,
    confidence: float = 0.90,
    mode: str = 'lower',
) -> float:
    """
    Apply Hoeffding's inequality to get a conservative win_rate for Kelly.

    CS229 Ng (Lecture 09):
      "The Hoeffding inequality says P(|φ̂ - φ| > γ) ≤ 2·exp(-2γ²m).
       With probability ≥ 1-δ: |φ̂ - φ| ≤ sqrt(log(2/δ) / (2m)).
       The bound decays exponentially in m — with enough trades, the
       estimate becomes tight."

    Applied to Kelly sizing:
      - Observed win_rate φ̂ from m trades is an ESTIMATE of the true φ.
      - The true φ could be as low as φ̂ - γ (lower confidence bound).
      - Feeding the LOWER bound into Kelly = conservative sizing.
      - As m grows, γ → 0 and Kelly uses the full observed win_rate.

    This prevents the "winner's curse" of Kelly: fitting 20 trades with
    65% win rate and sizing as if the true rate IS 65%.

    Args:
        observed_win_rate: φ̂ from historical trade window
        n_trades: number of trades the estimate is based on
        confidence: 1 - δ (default 90% confidence)
        mode: 'lower' (conservative Kelly) or 'upper' (aggressive bound)

    Returns:
        Hoeffding-corrected win_rate, clamped to [0.1, 0.95]
    """
    if n_trades < 1:
        return 0.50  # uninformative prior

    delta = 1.0 - confidence
    # Hoeffding bound: γ = sqrt(log(2/δ) / (2m))
    gamma = math.sqrt(math.log(2.0 / max(delta, 1e-9)) / (2.0 * n_trades))

    if mode == 'lower':
        corrected = observed_win_rate - gamma
    else:
        corrected = observed_win_rate + gamma

    return float(max(0.10, min(0.95, corrected)))


def sample_complexity_confidence(n_trades: int, n_features: int = 6,
                                 delta: float = 0.05, gamma: float = 0.10) -> float:
    """
    Return confidence in PredictNow model based on CS229 sample complexity.

    CS229 Ng (Lecture 09):
      "To guarantee ε(ĥ) ≤ ε(h*) + 2γ with probability ≥ 1-δ,
       it suffices that m ≥ (1/2γ²)·log(2K/δ)"

    K here is the effective hypothesis class size — approximated as 2^n_features
    for a binary logistic regression over n_features binary-ish features.

    Returns float in [0, 1]: 0.0 = no trust, 1.0 = full trust.
    When < 0.5, the model has insufficient data → prefer priors.
    """
    K = 2 ** n_features  # effective hypothesis class size
    m_needed = math.log(2.0 * K / delta) / (2.0 * gamma ** 2)
    return float(min(1.0, n_trades / m_needed))


# ── Kelly Formula (Lo / AFML) ──────────────────────────────────────────── #

def fractional_kelly(
    win_rate: float,
    avg_win_r: float,
    avg_loss_r: float,
    fraction: float = 0.25,
    floor: float = 0.005,
    ceiling: float = 0.04,
) -> float:
    """
    Compute fractional Kelly bet size as % of equity.

    f* = (p·b - q) / b
    where:
        p = win_rate
        q = 1 - p
        b = avg_win_r / avg_loss_r   (reward/risk ratio of past trades)

    Returns fraction × f*, clamped to [floor, ceiling].

    Args:
        win_rate:   historical win rate (0–1)
        avg_win_r:  average R multiple on winning trades (positive)
        avg_loss_r: average R multiple on losing trades (positive, e.g. 1.0 = 1R loss)
        fraction:   Kelly fraction to apply (0.25 = quarter-Kelly)
        floor:      minimum bet size even when Kelly is small
        ceiling:    maximum bet size regardless of Kelly output

    NaN handling (fixed 2026-08-26): NaN in any input used to fail every guard
    below (NaN comparisons are always False in Python), so a NaN win_rate/
    avg_win_r/avg_loss_r fell through every early-return and reached
    `max(floor, min(ceiling, nan))`, which evaluates to `ceiling` — the MAXIMUM
    position size, on garbage input. That is the worst possible failure mode
    for a sizing function. NaN is now treated the same as the other malformed-
    input cases already handled below (avg_loss_r<=0, win_rate<=0/>=1): it
    returns `floor`, not `ceiling`. See test_kelly_math.py for the fault
    injection that would catch a regression here.
    """
    if math.isnan(win_rate) or math.isnan(avg_win_r) or math.isnan(avg_loss_r):
        return floor

    if avg_loss_r <= 0 or win_rate <= 0 or win_rate >= 1:
        return floor

    b = avg_win_r / avg_loss_r
    q = 1.0 - win_rate
    f_star = (win_rate * b - q) / b

    if f_star <= 0:
        # Negative Kelly: expected value is negative → don't bet
        return 0.0

    practical = f_star * fraction
    return float(max(floor, min(ceiling, practical)))
