"""
Phase 6 — Risk Engine Wrapper

Andrew Lo / AFML fix applied:
  Old: kelly_fraction = risk_pct  (grade % masquerading as Kelly — wrong)
  New: f* = (p·b - q) / b  where p = win rate, b = avg win / avg loss, q = 1-p
       Fractional Kelly at 25% applied (half-Kelly is theoretical optimum under
       uncertainty; 25% is the practical bound for live trading — Lo 2017).
       Grade-based cap still enforced: Kelly is bounded above by the grade %.

  Kelly formula derivation:
    Expected log-wealth is maximised by f* = (p·b - q) / b
    At f*, E[log(W)] = p·log(1 + f*·b) + q·log(1 - f*)
    Practical Kelly = f* × 0.25  (quarter-Kelly)
    This limits ruin probability under parameter uncertainty while still
    using the information content of the win-rate/R:R estimate.

  Grade cap: grade_risk_pct acts as a ceiling. If Kelly says 3% but
    the model grade (confidence) is 'C' (grade = 0.5%), we use 0.5%.
    This enforces that low-conviction signals never get oversized even
    if the historical win rate happened to be high on a small sample.
"""

from layer2.risk_engine import RiskEngine
from layer2.dynamic_rr_engine import DynamicRREngine
from contracts.types import RiskOutput, BiasOutput, RouterOutput
from config.loader import params

# The three pure math functions below (hoeffding_win_rate, sample_complexity_confidence,
# fractional_kelly) used to be defined inline here. They have no dependency on layer2/
# contracts/config — only SovereignRiskEngine below does — so they were split out to
# sovereign/risk/kelly_math.py on 2026-08-26 so that sovereign/risk/layers/kelly.py (the
# real Layer 2 ceiling on risk_engine.decide()'s sizing path) can import them without
# pulling in this module's broken layer2/config imports. Re-exported here for backward
# compatibility with anything that already imports them from kelly_engine.
from sovereign.risk.kelly_math import (  # noqa: F401
    fractional_kelly,
    hoeffding_win_rate,
    sample_complexity_confidence,
)


class SovereignRiskEngine:
    """
    Grade-based sizing with fractional Kelly as the computed optimum.
    Kelly is used as the sizing reference; grade cap is the ceiling.
    """

    def __init__(self):
        self.base_engine = RiskEngine()
        self.rr_engine = DynamicRREngine()

    def _grade_risk_pct(self, confidence: float, library_insight=None) -> float:
        """
        Grade-based ceiling on position size, scaled by Library convergence.

        Base caps from config (A+=4%, A=3%, B=2%, C=1.5%).
        Library convergence reduces the cap dynamically:
          7+ volumes → ×0.50  (extreme late-cycle stress)
          5-6 volumes → ×0.625
          3+ volumes  → ×0.75
        PTJ: "Defence before offence." Smaller bets until macro regime clarifies.
        """
        g = params['risk']['grade_risk']
        if confidence >= 0.92:   base_cap = g['A_plus']
        elif confidence >= 0.78: base_cap = g['A']
        elif confidence >= 0.65: base_cap = g['B']
        else:                    base_cap = g['C']

        if library_insight is None:
            return base_cap

        # Count converging volumes
        converging = 0
        max_sim = 0.0
        try:
            for vm in library_insight.volume_matches:
                if vm.similarity >= 0.60:
                    converging += 1
                if vm.similarity > max_sim:
                    max_sim = vm.similarity
        except Exception:
            return base_cap

        if converging >= 7 and max_sim > 0.90:
            return base_cap * 0.50
        elif converging >= 5:
            return base_cap * 0.625
        elif converging >= 3:
            return base_cap * 0.75
        return base_cap

    def _get_stop_mult(self, regime: str) -> float:
        regime_params = params.get("regime_params", {})
        regime_block = regime_params.get(regime, {}) if isinstance(regime_params, dict) else {}
        if regime_block and "stop_atr_mult" in regime_block:
            return float(regime_block["stop_atr_mult"])
        return float(params["risk"].get("atr_stop_multiplier", 1.5))

    def _get_tp_rr(self, regime: str, symbol: str = "") -> float:
        if symbol:
            asset_params = params.get("asset_params", {})
            asset_block = asset_params.get(symbol, {}) if isinstance(asset_params, dict) else {}
            if asset_block and "atr_target_multiplier" in asset_block:
                return float(asset_block["atr_target_multiplier"])
        regime_params = params.get("regime_params", {})
        regime_block = regime_params.get(regime, {}) if isinstance(regime_params, dict) else {}
        if regime_block and "tp_rr" in regime_block:
            return float(regime_block["tp_rr"])
        return float(params["risk"].get("atr_target_multiplier", 3.0))

    def compute(self, bias: BiasOutput, router: RouterOutput,
                account_equity: float, atr: float, entry_price: float,
                win_rate: float = 0.55,
                avg_win_r: float = 2.0,
                avg_loss_r: float = 1.0,
                predict_now_prob: float = None,
                n_trades: int = 20,
                library_insight=None,
                pegasus_params=None) -> RiskOutput:
        """
        Final trade parameters with fractional Kelly sizing.

        Ernest Chan (second lecture, 52:00):
          "In classical statistics you get a static probability — 5% every
          day. In machine learning you get a DIFFERENT probability each day
          based on current market conditions. That is a much more nuanced
          understanding of the market."

        predict_now_prob: if supplied, OVERRIDES the default win_rate prior
          with PredictNow's dynamic per-trade probability. This is the correct
          wiring: PredictNow outputs P(this trade is profitable) conditioned
          on today's regime — that IS the win_rate for Kelly.

          Without this, Kelly uses a static 55% prior regardless of whether
          the current regime has historically been 40% or 70% for this strategy.

        win_rate / avg_win_r / avg_loss_r: used when predict_now_prob is None.
          Defaults are conservative priors (55% WR, 2:1 R:R).

        Sizing logic:
            kelly_pct  = fractional_kelly(win_rate, avg_win_r, avg_loss_r)
            grade_cap  = _grade_risk_pct(confidence)
            risk_pct   = min(kelly_pct, grade_cap)
        """
        # Override static win_rate with PredictNow's dynamic probability
        if predict_now_prob is not None and 0.0 < predict_now_prob < 1.0:
            win_rate = predict_now_prob

        narrative_modifier = float(getattr(bias, "narrative_modifier", 0.0) or 0.0)
        effective_confidence = max(0.0, min(1.0, bias.confidence + narrative_modifier))

        # ── Hoeffding confidence interval on win_rate (CS229 Lecture 09) ── #
        # Ng: "P(|φ̂ - φ| > γ) ≤ 2·exp(-2γ²m). With probability ≥ 1-δ,
        # |φ̂ - φ| ≤ sqrt(log(2/δ)/(2m)). Feed the LOWER bound into Kelly —
        # conservative sizing until the estimate has enough data behind it."
        # As n_trades → ∞, γ → 0 and the bound converges to the point estimate.
        hoeffding_wr = hoeffding_win_rate(
            observed_win_rate=win_rate,
            n_trades=n_trades,
            confidence=0.90,
            mode='lower',
        )
        # Scale correction towards full win_rate as sample complexity is satisfied
        sc_conf = sample_complexity_confidence(n_trades, n_features=6)
        win_rate = sc_conf * win_rate + (1.0 - sc_conf) * hoeffding_wr

        # 1. Grade cap (Library convergence scales this dynamically)
        grade_cap = self._grade_risk_pct(effective_confidence, library_insight=library_insight)

        # Pegasus-learned Kelly fraction cap (REINFORCE — takes most conservative)
        if pegasus_params is not None:
            grade_cap = min(grade_cap, pegasus_params.kelly_fraction_cap)

        # 2. ATR safety gate
        symbol = router.symbol
        atr_pct = (atr / entry_price) * 100
        atr_limit = params['atr_gate'].get(symbol, 4.0)

        if atr_pct > atr_limit:
            return RiskOutput(
                position_size=0.0,
                kelly_fraction=0.0,
                stop_price=0.0,
                stop_method='ATR_GATE_BLOCKED',
                tp1_price=0.0,
                tp2_price=0.0,
                trail_config={'reason': f'ATR {atr_pct:.2f}% > limit {atr_limit}%'},
                expected_value=-1.0,
                ev_positive=False,
                size_breakdown={
                    'block_reason': 'Volatility Hemorrhage Threshold Exceeded',
                    'narrative_modifier': narrative_modifier,
                    'effective_confidence': effective_confidence,
                },
            )

        # 3. Fractional Kelly (AFML / Andrew Lo)
        kelly_pct = fractional_kelly(
            win_rate=win_rate,
            avg_win_r=avg_win_r,
            avg_loss_r=avg_loss_r,
            fraction=0.25,            # quarter-Kelly
            floor=0.005,
            ceiling=0.04,
        )

        if kelly_pct <= 0:
            # Kelly says don't bet — negative expected log-wealth
            return RiskOutput(
                position_size=0.0,
                kelly_fraction=0.0,
                stop_price=0.0,
                stop_method='KELLY_NEGATIVE_EV',
                tp1_price=0.0,
                tp2_price=0.0,
                trail_config={'reason': f'Kelly f*≤0: win_rate={win_rate:.2f} R={avg_win_r:.1f}:{avg_loss_r:.1f}'},
                expected_value=-1.0,
                ev_positive=False,
                size_breakdown={
                    'kelly_pct': 0.0,
                    'grade_cap': grade_cap,
                    'narrative_modifier': narrative_modifier,
                    'effective_confidence': effective_confidence,
                },
            )

        # 4. Final risk %: Kelly floored by grade cap
        risk_pct = min(kelly_pct, grade_cap)

        # 5. Position structure
        regime_label = router.regime
        stop_mult = self._get_stop_mult(regime_label)
        tp_rr = self._get_tp_rr(regime_label, symbol=symbol)

        # Pegasus REINFORCE overrides (trusted after ≥20 updates — applied proportionally)
        if pegasus_params is not None:
            stop_mult = pegasus_params.stop_atr_mult
            tp_rr = pegasus_params.tp_rr_ratio

        dollar_risk = account_equity * risk_pct
        stop_distance = stop_mult * atr
        position_size = dollar_risk / stop_distance if stop_distance > 0 else 0.0

        direction = 1 if bias.direction.value == 1 else -1
        stop_price = entry_price - direction * stop_distance
        tp1_price = entry_price + direction * stop_distance * tp_rr
        tp2_price = entry_price + direction * stop_distance * tp_rr * 1.5

        # Expected value using actual win_rate and R:R
        ev = win_rate * avg_win_r - (1 - win_rate) * avg_loss_r
        ev_positive = ev > 0 and position_size > 0

        return RiskOutput(
            position_size=position_size,
            kelly_fraction=risk_pct,
            stop_price=stop_price,
            stop_method=f"regime_atr_{stop_mult}x",
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            trail_config={},
            expected_value=ev,
            ev_positive=ev_positive,
            size_breakdown={
                'dollar_risk': dollar_risk,
                'stop_distance': stop_distance,
                'risk_pct': risk_pct,
                'kelly_pct': kelly_pct,
                'grade_cap': grade_cap,
                'narrative_modifier': narrative_modifier,
                'effective_confidence': effective_confidence,
                'kelly_f_star': (win_rate * (avg_win_r / avg_loss_r) - (1 - win_rate)) / (avg_win_r / avg_loss_r),
                'stop_mult': stop_mult,
                'tp_rr': tp_rr,
                'win_rate_input': win_rate,
                'avg_win_r': avg_win_r,
                'avg_loss_r': avg_loss_r,
            },
        )
