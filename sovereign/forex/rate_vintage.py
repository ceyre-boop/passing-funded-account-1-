"""Rate/CPI input vintage selection — spec 039.

ONE switch, read by both the data fetcher (which cache tree to read) and the
signal engine (what to do when a value is not yet knowable). Two switches would
drift apart; this module exists so they cannot.

Modes
-----
sealed       (default) the legacy ``data/cache/macro/`` tree, read with the
             historical ``.asof(nominal date)`` convention and the historical
             FALLBACK_RATES/FALLBACK_CPI behaviour. Byte-for-byte the behaviour
             that produced every existing artifact. Nothing about this mode
             changes.

nominal      ``data/cache/macro_nominal/`` — full-history latest-revision values
             indexed by NOMINAL observation date. This is the sealed generator's
             information set, reconstructed without the offline rig's
             2019/2020 macro truncation. It contains the look-ahead described in
             spec 039 and exists only as the control arm of the A/B.

publication  ``data/cache/macro_pub/`` — the same values indexed by the date
             each first became available (ALFRED realtime). No look-ahead.

In the two non-sealed modes a value that is not yet knowable is NaN, and NaN is
never turned into a number: the signal engine excludes that date and counts the
exclusion. Spec 039's invariant I56.
"""
from __future__ import annotations

import os
from pathlib import Path

SEALED = "sealed"
NOMINAL = "nominal"
PUBLICATION = "publication"
VALID_MODES = (SEALED, NOMINAL, PUBLICATION)

ENV_VAR = "CARRY_RATE_VINTAGE"

_MACRO_ROOT = Path(__file__).parents[2] / "data" / "cache"
_DIRS = {
    SEALED: _MACRO_ROOT / "macro",
    NOMINAL: _MACRO_ROOT / "macro_nominal",
    PUBLICATION: _MACRO_ROOT / "macro_pub",
}


class VintageUnavailable(Exception):
    """A vintage cache the selected mode requires is missing.

    Raised rather than falling back, because a silent fall back to the sealed
    tree would make a publication-vintage run report look-ahead numbers.
    """


def vintage_mode() -> str:
    mode = os.environ.get(ENV_VAR, SEALED).strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(
            f"{ENV_VAR}={mode!r} is not one of {VALID_MODES}. "
            "Refusing to guess which information set was intended."
        )
    return mode


def macro_cache_dir(mode: str | None = None) -> Path:
    return _DIRS[mode or vintage_mode()]


def is_sealed(mode: str | None = None) -> bool:
    return (mode or vintage_mode()) == SEALED


def require_cache(path: Path) -> Path:
    if not path.exists():
        raise VintageUnavailable(
            f"{path} is missing. Run scripts/build_rate_vintages.py before "
            f"running in {vintage_mode()!r} mode. A missing vintage is never "
            "filled in and never falls back to another tree."
        )
    return path
