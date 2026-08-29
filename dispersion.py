"""
dispersion.py — magnitude conditioner. Move 3, computed half.

Forecasts conditional dispersion of the remainder of the session from an
as-of-computable state. No direction, no sign, no classifier. Arithmetic over
bars, so it is backtestable on the full SPY series today rather than accruing
live-forward.

Consumed by the Stockfish core as expected range: hold / tighten / exit resolve
against a distribution width without ever knowing which way price goes.

THE SPLIT IS DECLARED HERE, BEFORE ANY SWEEP RUNS.
Importing this module fixes the holdout. Changing SPLIT below is an unseal and
requires the same ceremony as sealed_sessions(): reason, rule_version, commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
import hashlib

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Pre-registered split. Written before the 2,678-session run, not after.
# ---------------------------------------------------------------------------

SPLIT = {
    "tune":    ("2015-01-01", "2021-12-31"),   # ~1,760 sessions
    "holdout": ("2022-01-01", "2026-08-29"),   # ~1,170 sessions, DO NOT TOUCH
}
SPLIT_VERSION = "disp-split-001"
EMBARGO_DAYS = 5   # purge across the boundary; session features look back 20 bars


def _split_hash() -> str:
    payload = f"{SPLIT_VERSION}|{SPLIT}|{EMBARGO_DAYS}".encode()
    return hashlib.sha256(payload).hexdigest()[:16]


SPLIT_SHA = _split_hash()


class SealBreak(RuntimeError):
    pass


def sessions(df: pd.DataFrame, lane: str, *, unseal_reason: str | None = None) -> pd.DataFrame:
    """lane in {'tune','holdout'}. Holdout requires an explicit unseal_reason."""
    if lane not in SPLIT:
        raise ValueError(f"lane must be one of {list(SPLIT)}")
    if lane == "holdout" and not unseal_reason:
        raise SealBreak(
            "holdout requires unseal_reason + a frozen commit. "
            f"split={SPLIT_VERSION} sha={SPLIT_SHA}"
        )
    lo, hi = SPLIT[lane]
    lo, hi = pd.Timestamp(lo), pd.Timestamp(hi)
    if lane == "tune":
        hi = hi - pd.Timedelta(days=EMBARGO_DAYS)
    else:
        lo = lo + pd.Timedelta(days=EMBARGO_DAYS)
    d = df.index.get_level_values("session_date")
    return df[(d >= lo) & (d <= hi)]


# ---------------------------------------------------------------------------
# Features. Every one carries as_of_computable=True by construction — each is a
# function of bars strictly at or before the decision bar.
# ---------------------------------------------------------------------------

FEATURES = (
    "rv_short",          # realized vol, trailing 12 bars (1h on 5m)
    "rv_long",           # realized vol, trailing 78 bars (1 session)
    "vol_expansion",     # rv_short / rv_long
    "or_width_atr",      # opening range width / ATR20
    "compression",       # tr5 / tr20_median
    "gap_atr",           # overnight gap / ATR20
    "minutes_elapsed",   # session phase, monotone
)


def build_features(bars: pd.DataFrame, decision_bar: int = 12) -> pd.DataFrame:
    """
    bars: 5m OHLCV, MultiIndex (session_date, bar_idx).
    decision_bar: bars after the open at which the forecast is made.
    """
    out = []
    for date, sess in bars.groupby(level="session_date"):
        if len(sess) < decision_bar + 12:
            continue
        px = sess["close"].to_numpy()
        hi, lo = sess["high"].to_numpy(), sess["low"].to_numpy()
        ret = np.diff(np.log(px), prepend=np.log(px[0]))

        d = decision_bar
        rv_s = ret[max(0, d - 12):d].std() * np.sqrt(78)
        rv_l = ret[:d].std() * np.sqrt(78) if d > 2 else np.nan
        tr = hi - lo
        atr20 = np.nanmedian(tr[:d]) if d > 0 else np.nan

        out.append({
            "session_date": date,
            "rv_short": rv_s,
            "rv_long": rv_l,
            "vol_expansion": rv_s / rv_l if rv_l else np.nan,
            "or_width_atr": (hi[:d].max() - lo[:d].min()) / atr20 if atr20 else np.nan,
            "compression": np.nanmedian(tr[max(0, d - 5):d]) / atr20 if atr20 else np.nan,
            "gap_atr": (px[0] - sess["prev_close"].iloc[0]) / atr20 if atr20 else np.nan,
            "minutes_elapsed": d * 5,
            # TARGET: dispersion of the remainder, sign-free
            "y_dispersion": (hi[d:].max() - lo[d:].min()) / atr20 if atr20 else np.nan,
        })
    return pd.DataFrame(out).set_index("session_date").dropna()


# ---------------------------------------------------------------------------
# Skill scoring. Same posture as the Brier-vs-0.800 check on the other side:
# a forecaster that cannot beat the unconditional baseline is worse than silence.
# ---------------------------------------------------------------------------

@dataclass
class Skill:
    quantile: float
    pinball_model: float
    pinball_baseline: float      # unconditional empirical quantile
    n: int

    @property
    def skill_score(self) -> float:
        if self.pinball_baseline == 0:
            return float("nan")
        return 1.0 - self.pinball_model / self.pinball_baseline

    def verdict(self) -> str:
        s = self.skill_score
        if s <= 0:
            return "REJECT — no better than the unconditional distribution"
        if s < 0.05:
            return "NO_SUPERSEDE — improvement inside noise"
        return "CANDIDATE"


def pinball(y: np.ndarray, pred: np.ndarray, q: float) -> float:
    e = y - pred
    return float(np.mean(np.maximum(q * e, (q - 1) * e)))


def score(y: np.ndarray, pred: np.ndarray, q: float) -> Skill:
    baseline = np.full_like(y, np.quantile(y, q))
    return Skill(
        quantile=q,
        pinball_model=pinball(y, pred, q),
        pinball_baseline=pinball(y, baseline, q),
        n=len(y),
    )


def fit_quantiles(
    train: pd.DataFrame,
    test: pd.DataFrame,
    quantiles: Sequence[float] = (0.25, 0.5, 0.75, 0.9),
) -> dict[float, Skill]:
    """Gradient-boosted quantile regression. Deliberately small — the point is
    the harness, not the model. If a 3-feature GBM can't beat unconditional,
    a bigger model is fitting the tune lane."""
    from sklearn.ensemble import GradientBoostingRegressor

    X_tr, y_tr = train[list(FEATURES)].to_numpy(), train["y_dispersion"].to_numpy()
    X_te, y_te = test[list(FEATURES)].to_numpy(), test["y_dispersion"].to_numpy()

    results = {}
    for q in quantiles:
        m = GradientBoostingRegressor(
            loss="quantile", alpha=q,
            n_estimators=200, max_depth=3, learning_rate=0.05,
            subsample=0.8, random_state=0,
        ).fit(X_tr, y_tr)
        results[q] = score(y_te, m.predict(X_te), q)
    return results


if __name__ == "__main__":
    print(f"split={SPLIT_VERSION} sha={SPLIT_SHA} embargo={EMBARGO_DAYS}d")
    print(f"tune   : {SPLIT['tune']}")
    print(f"holdout: {SPLIT['holdout']}  (sealed)")