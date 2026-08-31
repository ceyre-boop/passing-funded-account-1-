"""az/floor_params.py — the two free terms of the economic-floor conversion,
declared before any R is computed.

WHY THIS FILE EXISTS SEPARATELY
    artifacts/ECONOMIC_FLOOR_PREREG.md §1 fixed every term of

                        κ · Δ|ret| · P  −  c_total
      E[R per event]  = ───────────────────────────
                              k_stop · ATR14

    except two: `κ` (capture fraction) and `k_stop` (stop width in ATR units).
    Its §6 requires those to land in their own commit, visible in git history
    ahead of any computed R. This is that commit. Nothing here computes an R,
    reads a price series, or imports a study.

    If these constants ever appear in the same commit as a result, the
    pre-registration is violated and that result is void.

WHY THESE VALUES
    k_stop = 1.0
        Risk = 1 × ATR14. Not a new empirical claim: it is the same R
        denominator the exit work already used when G1 (−0.0676) and G2
        hold-to-close (−0.0773) bracketed the geometry range with the
        denominator pinned at 1×ATR. Choosing anything else here would make
        this a different study than the one those numbers came from, which is
        exactly what az/candidates.geometry's refusal to default k_stop is
        guarding against.

    κ = 0.50
        A deliberately GENEROUS upper bound, chosen so that a failure is
        decisive rather than arguable.

        This is a screening test. The fill model is mandatory and hostile
        (2× costs, adverse slippage, delayed fill), so if the capture
        assumption were also pessimistic, a negative result could always be
        blamed on stacked conservatism. Pairing generous capture with hostile
        execution removes that defence: anything that fails here fails on the
        effect, not on the assumptions.

        0.50 is an upper bound because no direction-free structure is known to
        realize more than half of a window's absolute move. A bracket must pay
        its own width before it captures any excursion beyond it; a straddle
        pays two entries to collect one side. κ > 0.5 would require assuming
        the correct side is picked, which §0 of the pre-registration forbids --
        all three directional studies are below their detection floor.

        κ = 0.50 is therefore a CEILING ON THE RESULT, not an estimate of it.
        A pass at κ = 0.50 is not evidence the strategy earns that capture; it
        only means the study is not yet closed on economics.
"""
from __future__ import annotations

# --- the two terms §6 requires to be declared before any R -------------------
KAPPA: float = 0.50           # capture fraction, direction-free, generous bound
K_STOP: float = 1.0           # stop width in ATR14 units; risk = K_STOP * ATR14

# --- restated from the pre-registration so the test cannot drift from it -----
# az/fills.py::PessimisticFill parameters (prereg §4). PessimisticFill is
# instantiated nowhere else in committed code, so these are declared, not
# inherited.
COST_MULT: float = 2.0        # multiplies ceiling.COST_PER_SHARE (0.02/share)
SLIP_BPS: float = 2.0         # charged against the entry, always adverse
DELAY_BARS: int = 1           # fill pushed one bar later

# FLOOR = max(FLOOR_ABS_R, FLOOR_COST_MULTIPLE × cost drag in R)  (prereg §2a)
FLOOR_ABS_R: float = 0.10
FLOOR_COST_MULTIPLE: float = 2.0

# Frozen: these are pre-registered, not tunable. Anything that wants a
# different value is a different study and needs its own pre-registration.
__all__ = ["KAPPA", "K_STOP", "COST_MULT", "SLIP_BPS", "DELAY_BARS",
           "FLOOR_ABS_R", "FLOOR_COST_MULTIPLE"]
