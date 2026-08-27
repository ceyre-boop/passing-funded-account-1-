"""Characterisation tests for sovereign/risk/kelly_engine.py.

CONTEXT (2026-08-26, updated): CLAUDE.md non-negotiable #4 states
"sovereign/risk/kelly_engine.py computes proper quarter-Kelly ... then
sovereign/risk/layers/prop.py applies the funded-account ceiling on top." This file
originally found that sentence false in every part: kelly_engine.py could not even
be imported (`ModuleNotFoundError: No module named 'layer2'`), and nothing outside
sovereign/ called any of it.

FIX (2026-08-26): the three pure math functions this module used to define inline
(`fractional_kelly`, `hoeffding_win_rate`, `sample_complexity_confidence`) had no
actual dependency on this module's broken imports (`layer2.risk_engine`,
`layer2.dynamic_rr_engine`, `config.loader` — see below) — only `SovereignRiskEngine`
does. They were split out to sovereign/risk/kelly_math.py, which has zero external
dependencies and is imported directly by sovereign/risk/layers/kelly.py, the real
Layer 2 ceiling risk_engine.decide() actually calls. See test_kelly_math.py for their
characterisation (including the NaN-hazard fix) and test_risk_layers.py for proof
that decide() now genuinely uses a working, computed Kelly ceiling.

This file now covers what's left: `kelly_engine.py` still exists as a thin
backward-compatible re-export of kelly_math's three functions, plus
`SovereignRiskEngine` — a wrapper class copied from a different repo (~/quant /
~/quant-master-wt) that composes `layer2.risk_engine.RiskEngine` and
`layer2.dynamic_rr_engine.DynamicRREngine`, neither of which exists here, and reads
config via a top-level `config.loader` package that also doesn't exist in this repo
(only the unrelated `sovereign/risk/config` package does). `contracts.types` DOES
resolve (top-level ./contracts/types.py) — so 3 of 4 of kelly_engine.py's remaining
top-level imports are genuinely dead in this repo.

This is intentionally left broken rather than faked: `layer2`/`config.loader` are not
vendored, stubbed, or shimmed here (see HARD CONSTRAINTS — a fake risk layer is worse
than a loud absence). kelly_engine.py therefore remains unimportable as shipped, and
`SovereignRiskEngine` remains untestable and uncallable — correctly, since nothing in
this repo calls it (`rg kelly_engine|risk_engine` outside sovereign/ returns nothing)
and it depends on real foreign code this repo does not have.
"""
import subprocess
import sys

import pytest


def test_module_is_unimportable_as_shipped():
    """FINDING (still true post-fix, and correctly so): kelly_engine.py cannot be
    imported anywhere in this repo without stubbing `layer2` (missing entirely) and
    `config.loader` (top-level `config` package does not exist; only the unrelated
    sovereign/risk/config package does). Run in a subprocess so this repo's real
    sys.path is used, uncontaminated by any stubbing elsewhere in the test suite."""
    result = subprocess.run(
        [sys.executable, "-c", "import sovereign.risk.kelly_engine"],
        cwd=__file__.rsplit("/sovereign/", 1)[0],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "ModuleNotFoundError" in result.stderr
    assert "layer2" in result.stderr


def test_kelly_engine_re_exports_kelly_math_would_work_if_layer2_existed():
    """The re-export lines (`from sovereign.risk.kelly_math import fractional_kelly,
    hoeffding_win_rate, sample_complexity_confidence`) are correct and would resolve
    fine on their own — it's purely the module-level `from layer2...` /
    `from config.loader...` imports ABOVE them in file order that kill the whole
    module's importability. Verify by stubbing only the two genuinely-missing
    packages (not faking their behaviour — empty placeholders, isolated to this
    test process) and confirming the re-exported names then match kelly_math's
    originals exactly."""
    import types

    if "layer2" not in sys.modules:
        layer2 = types.ModuleType("layer2")
        layer2_risk_engine = types.ModuleType("layer2.risk_engine")
        layer2_risk_engine.RiskEngine = type("RiskEngine", (), {})
        layer2_dynamic_rr = types.ModuleType("layer2.dynamic_rr_engine")
        layer2_dynamic_rr.DynamicRREngine = type("DynamicRREngine", (), {})
        sys.modules["layer2"] = layer2
        sys.modules["layer2.risk_engine"] = layer2_risk_engine
        sys.modules["layer2.dynamic_rr_engine"] = layer2_dynamic_rr
    if "config" not in sys.modules:
        config_pkg = types.ModuleType("config")
        config_loader = types.ModuleType("config.loader")
        config_loader.params = {}
        sys.modules["config"] = config_pkg
        sys.modules["config.loader"] = config_loader

    from sovereign.risk import kelly_engine
    from sovereign.risk import kelly_math

    assert kelly_engine.fractional_kelly is kelly_math.fractional_kelly
    assert kelly_engine.hoeffding_win_rate is kelly_math.hoeffding_win_rate
    assert kelly_engine.sample_complexity_confidence is kelly_math.sample_complexity_confidence

    for mod in ("layer2", "layer2.risk_engine", "layer2.dynamic_rr_engine",
                "config", "config.loader", "sovereign.risk.kelly_engine"):
        sys.modules.pop(mod, None)


def test_sovereign_risk_engine_has_zero_callers_outside_this_file():
    """Documents why SovereignRiskEngine being permanently broken is acceptable:
    nothing calls it. If this ever stops being true, whoever wires a caller to it
    is also responsible for making it importable for real (a real layer2/config
    dependency, not a stub) — this test is a tripwire, not a design endorsement."""
    import subprocess as sp

    result = sp.run(
        ["grep", "-rn", "-E", "SovereignRiskEngine|kelly_engine\\.compute",
         "--include=*.py", "scripts/", "daytrade/", "execution/"],
        cwd=__file__.rsplit("/sovereign/", 1)[0],
        capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", (
        "SovereignRiskEngine now has a caller outside sovereign/ — it must be made "
        "genuinely importable (real layer2/config deps), not left broken:\n"
        + result.stdout
    )
