#!/usr/bin/env python3
"""gate/discovery.py — MDE at discovery. A standing rule, enforced as code.

THE RULE
    Every candidate effect gets a minimum detectable effect computed at
    DISCOVERY, against the sample that produced it, BEFORE it proceeds to an
    adversarial pass or a pre-registration. If the observed effect is below its
    own study's detection threshold it is recorded as BELOW NOISE FLOOR and stops
    there. It does not become a lead.

WHY THIS RULE EXISTS
    C5_gap:high survived: monotone gradients on two conditioners, 68% of configs
    positive in the top cell, an even spread across eleven years, and a full
    adversarial pass. Every one of those is a ROBUSTNESS signal. None of them is
    an EFFECT SIZE.

    At n=705 — the sample that found it — the MDE was 0.1212 R against an observed
    0.0137 R. Nine times below the noise floor of the study that produced it.

    Robustness checks cannot detect this failure mode. A below-floor effect can be
    monotone, stable across regimes, and significant, because those properties are
    about consistency, not magnitude. Only the arithmetic catches it.

Lane-neutral by construction: this module knows nothing about carry, daytrade,
entries or exits. It takes three numbers.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "daytrade"))
from mechanisms import mde  # noqa: E402 — the repo's own MDE, never a second formula


class DiscoveryError(ValueError):
    pass


ABOVE = "ABOVE FLOOR"
BELOW = "BELOW NOISE FLOOR"


@dataclass(frozen=True)
class Finding:
    """A candidate effect and the detection threshold of the study that found it.

    `n_units` must be the INDEPENDENT unit — days, episodes, clusters — never
    rows. Passing a row count here inflates the sample and understates the MDE,
    which is the same unit error the N_days rule exists to prevent.
    """
    name: str
    observed_effect: float
    per_unit_sd: float
    n_units: int
    unit: str = "days"
    source: str = ""

    def __post_init__(self):
        if not math.isfinite(self.observed_effect):
            raise DiscoveryError(f"{self.name}: observed_effect is not finite")
        if not (self.per_unit_sd > 0 and math.isfinite(self.per_unit_sd)):
            raise DiscoveryError(f"{self.name}: per_unit_sd must be finite and > 0, "
                                 f"got {self.per_unit_sd!r}")
        if not (isinstance(self.n_units, int) and self.n_units > 0):
            raise DiscoveryError(f"{self.name}: n_units must be a positive int, got {self.n_units!r}")

    @property
    def mde(self) -> float:
        """Minimum detectable effect at one-sided 95% / 80% power."""
        return float(mde(self.per_unit_sd, self.n_units))

    @property
    def ratio(self) -> float:
        """How many times the MDE exceeds the observed effect. >1 means below floor."""
        e = abs(self.observed_effect)
        return float("inf") if e == 0 else self.mde / e

    @property
    def verdict(self) -> str:
        return BELOW if self.ratio > 1.0 else ABOVE

    @property
    def proceeds(self) -> bool:
        """Whether this may become a lead at all."""
        return self.verdict == ABOVE

    def header(self) -> str:
        """The ONE rendering. An effect is never emitted without its MDE."""
        r = "∞" if self.ratio == float("inf") else f"{self.ratio:.1f}x"
        return (f"{self.name}: effect {self.observed_effect:+.4f}  "
                f"MDE {self.mde:.4f} ({self.n_units:,} {self.unit})  "
                f"{r} — {self.verdict}")

    def __str__(self) -> str:
        return self.header()


def require_above_floor(f: Finding) -> Finding:
    """Gate a lead. Raises if the effect is below its own study's noise floor —
    the point at which a finding stops, rather than proceeding to an adversarial
    pass that cannot detect the problem."""
    if not f.proceeds:
        raise DiscoveryError(
            f"{f.name} is BELOW NOISE FLOOR and may not become a lead: "
            f"observed {f.observed_effect:+.4f} against an MDE of {f.mde:.4f} at "
            f"n={f.n_units:,} {f.unit} ({f.ratio:.1f}x). Robustness checks cannot "
            "detect this; only the arithmetic can.")
    return f
