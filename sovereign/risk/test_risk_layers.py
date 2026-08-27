"""Characterisation + invariant tests for the risk-layer cascade:
sovereign/risk/risk_engine.py, risk_state.py, layers/base_size.py,
layers/gates.py, layers/prop.py, and the risk_engine.py <-> layers/kelly.py wiring.

CONTEXT (2026-08-26): CLAUDE.md non-negotiable #4 claims
"sovereign/risk/kelly_engine.py computes proper quarter-Kelly ... then
sovereign/risk/layers/prop.py applies the funded-account ceiling on top."
`layers/prop.py` DOES exist (confirmed below by test_prop_module_exists). The
"Kelly ... then prop ceiling on top" framing is accurate only for the config/math
design — see TestKellyCeilingBugContradictsCLAUDEmd below for the load-bearing
finding that the Kelly ceiling was not actually reaching decide() (silently
becoming math.inf on any ImportError, whether or not the layer was genuinely
absent).

FIX (2026-08-26): `_ceiling()`/`_modulator()` in risk_engine.py now distinguish
"layer file genuinely absent" (permissive default, unchanged) from "layer file
exists but its own imports are broken" (a hard, loud ImportError — never a
silent no-constraint default). `sovereign/risk/layers/kelly.py` DOES exist on
disk, so with the real (broken) import chain in this repo, calling
risk_engine.decide() now RAISES instead of silently returning kelly_ceiling ==
math.inf. See TestKellyCeilingBugContradictsCLAUDEmd for both the diagnosis and
the fix's effect.

Nothing in scripts/, daytrade/, or execution/ calls sovereign.risk.risk_engine.decide
or SovereignRiskEngine.compute (confirmed by `rg kelly_engine|risk_engine` outside
sovereign/ returning nothing) — this whole cascade is fully wired to itself but has
zero callers anywhere else in the repo. It is not on any live sizing path today.
"""
import math
import sys
import types

import pytest

from sovereign.risk import risk_engine
from sovereign.risk.config.loader import load_risk_config
from sovereign.risk.layers.base_size import base_size
from sovereign.risk.layers.gates import run_gates
from sovereign.risk.layers.prop import prop_ceiling
from sovereign.risk.risk_state import Position, RiskState, Signal


def _cfg():
    """Real risk_config.yaml, with audit logging disabled so tests don't write to
    data/risk/risk_decisions.jsonl."""
    cfg = dict(load_risk_config())
    cfg["audit"] = dict(cfg["audit"])
    cfg["audit"]["enabled"] = False
    return cfg


def _signal(grade="A", instrument="EURUSD", entry=1.10, stop=1.09):
    return Signal(instrument=instrument, direction=1, entry=entry, stop=stop, grade=grade)


def _state(**overrides):
    base = dict(
        equity=100_000.0, peak_equity=100_000.0, starting_balance=100_000.0,
        daily_realized_pnl=0.0, daily_open_pnl=0.0,
    )
    base.update(overrides)
    return RiskState(**base)


def _install_working_kelly_stand_in(monkeypatch, ceiling_value=math.inf):
    """Install a WORKING (not broken) sovereign.risk.layers.kelly stand-in.

    Tests below that exercise gates/prop/the final-risk invariant care about
    layers other than Kelly. Now that a present-but-broken kelly.py raises
    (by design — see TestKellyCeilingBugContradictsCLAUDEmd), those tests need
    a kelly layer that actually imports so they can isolate their own target
    behaviour instead of tripping over the unrelated, already-documented Kelly
    import bug. `ceiling_value=math.inf` reproduces "no constraint" the honest
    way: via a real layer that says so, not via a broken one masquerading as
    absent.
    """
    fake_kelly = types.ModuleType("sovereign.risk.layers.kelly")
    fake_kelly.ceiling = lambda signal, state, cfg: ceiling_value
    monkeypatch.setitem(sys.modules, "sovereign.risk.layers.kelly", fake_kelly)


class TestLayersPropExists:
    def test_prop_module_and_ceiling_function_exist(self):
        """CLAUDE.md names sovereign/risk/layers/prop.py explicitly — verify it
        exists and exposes the ceiling function risk_engine.py actually calls."""
        import sovereign.risk.layers.prop as prop_mod
        assert hasattr(prop_mod, "prop_ceiling")


class TestKellyCeilingBugContradictsCLAUDEmd:
    """THE central finding. CLAUDE.md #4 implies Kelly is an active control on the
    sizing path. `sovereign/risk/layers/kelly.py` imports `fractional_kelly` and
    `hoeffding_win_rate` from `sovereign/risk/kelly_engine.py`, whose own
    module-level imports (`layer2.risk_engine`, `layer2.dynamic_rr_engine` — see
    test_kelly_engine.py) do not exist in this repo. So
    `import sovereign.risk.layers.kelly` raises ModuleNotFoundError.

    Before the fix, risk_engine.py's `_ceiling()` helper treated ANY ImportError on
    a layer module as "not built yet -> no constraint" (returned math.inf). That
    fallback is designed for layers genuinely not written yet. It could not
    distinguish that case from a layer module that exists, is wired into
    `_CEILINGS`, and is completely broken — so the Kelly ceiling silently became
    infinite (no constraint at all), not the quarter-Kelly cap CLAUDE.md describes.

    AFTER the fix, `_ceiling()` checks whether the layer file exists on disk
    (`importlib.util.find_spec`) before treating an ImportError as "not built yet".
    `layers/kelly.py` DOES exist, so the same ImportError now propagates as a loud
    failure out of `decide()` instead of being swallowed into math.inf.
    """

    def test_kelly_layer_is_unreachable_in_this_repo(self):
        # Reproduces the exact ModuleNotFoundError chain risk_engine.py catches.
        for mod in ("sovereign.risk.layers.kelly", "sovereign.risk.kelly_engine",
                    "layer2", "layer2.risk_engine", "layer2.dynamic_rr_engine"):
            sys.modules.pop(mod, None)
        with pytest.raises(ModuleNotFoundError, match="layer2"):
            __import__("sovereign.risk.layers.kelly")

    def test_kelly_layer_file_exists_on_disk_despite_being_unimportable(self):
        """The load-bearing precondition for the fix: this is a present-but-broken
        layer, not a genuinely-absent one, so the guard in _ceiling() must trigger
        against the real case, not just a synthetic one."""
        assert risk_engine._layer_module_exists("kelly") is True

    def test_decide_now_refuses_instead_of_treating_broken_kelly_as_no_constraint(self):
        """FIX VERIFICATION: decide() no longer silently returns
        kelly_ceiling == math.inf for a present-but-broken layer. It raises,
        surfacing the real ModuleNotFoundError chain, so the engine refuses to
        produce a sizing decision rather than silently sizing unbounded."""
        for mod in ("sovereign.risk.layers.kelly", "sovereign.risk.kelly_engine"):
            sys.modules.pop(mod, None)
        sig = _signal(grade="A")
        state = _state()
        with pytest.raises(ImportError, match="kelly"):
            risk_engine.decide(sig, state, _cfg())

    def test_decide_WOULD_honor_a_kelly_ceiling_if_the_import_actually_worked(self, monkeypatch):
        """Contrast test proving the math.inf above is purely an artifact of the
        broken import chain, not a deliberate design choice: if layers/kelly is
        replaced with a working stand-in, decide() immediately uses it and it can
        become the binding constraint. This isolates the bug to the import, not to
        risk_engine.py's cascade logic (which is correct)."""
        fake_kelly = types.ModuleType("sovereign.risk.layers.kelly")
        fake_kelly.ceiling = lambda signal, state, cfg: 0.001  # deliberately tiny
        monkeypatch.setitem(sys.modules, "sovereign.risk.layers.kelly", fake_kelly)

        sig = _signal(grade="A")
        state = _state()
        decision = risk_engine.decide(sig, state, _cfg())
        assert decision.layer_budgets["kelly_ceiling"] == 0.001
        assert decision.binding_constraint == "kelly"
        assert decision.final_risk_pct == pytest.approx(0.001)


class TestLayerExistenceGuardDistinguishesAbsentFromBroken:
    """Direct fault-injection coverage of the fix's core distinction:
    'genuinely not built yet' (permissive default preserved) vs 'file exists,
    import is broken' (loud failure, never silently permissive)."""

    def test_present_but_broken_ceiling_raises_not_math_inf(self):
        """Fails if a present-but-broken layer still yields math.inf."""
        for mod in ("sovereign.risk.layers.kelly", "sovereign.risk.kelly_engine"):
            sys.modules.pop(mod, None)
        with pytest.raises(ImportError):
            risk_engine._ceiling("kelly", _signal(), _state(), _cfg())

    def test_present_but_broken_modulator_raises_not_identity(self, monkeypatch):
        """Same guard, exercised on the _modulator() path (factor 1.0 identity)
        using a synthetic present-but-broken module so the modulator side of the
        fix is covered independently of which real layers happen to be broken
        today."""
        broken_name = "volatility"
        real_spec_find = risk_engine.importlib_util.find_spec

        def _fake_find_spec(name, *a, **kw):
            if name == f"sovereign.risk.layers.{broken_name}":
                return object()  # "the file exists" — any non-None sentinel
            return real_spec_find(name, *a, **kw)

        def _fake_import(name, *a, **kw):
            if name == f"sovereign.risk.layers.{broken_name}":
                raise ImportError("simulated broken import for volatility")
            raise ImportError(name)

        monkeypatch.setattr(risk_engine.importlib_util, "find_spec", _fake_find_spec)
        monkeypatch.setattr(risk_engine, "import_module", _fake_import)
        with pytest.raises(ImportError, match="volatility"):
            risk_engine._modulator("volatility", _signal(), _state(), _cfg())

    def test_genuinely_absent_ceiling_layer_stays_permissive(self, monkeypatch):
        """Fails if a genuinely-absent layer stops being permissive (do not
        over-correct). Uses a layer name that has no corresponding file on disk
        at all."""
        monkeypatch.setattr(
            risk_engine, "import_module",
            lambda name, *a, **kw: (_ for _ in ()).throw(ImportError(name)),
        )
        assert risk_engine._layer_module_exists("definitely_not_a_real_layer") is False
        result = risk_engine._ceiling("definitely_not_a_real_layer", _signal(), _state(), _cfg())
        assert result == math.inf

    def test_genuinely_absent_modulator_layer_stays_permissive(self, monkeypatch):
        monkeypatch.setattr(
            risk_engine, "import_module",
            lambda name, *a, **kw: (_ for _ in ()).throw(ImportError(name)),
        )
        assert risk_engine._layer_module_exists("definitely_not_a_real_layer") is False
        result = risk_engine._modulator("definitely_not_a_real_layer", _signal(), _state(), _cfg())
        assert result == 1.0


class TestBaseSizeFailsLoudOnUnknownGrade:
    """Layer 1 base_size: does NOT silently default to a size on an unrecognised
    grade — this is the one layer in this cascade that already matches CLAUDE.md
    rule #3 ('never silently default unavailable values to numeric zero')."""

    def test_known_grades_return_configured_caps(self):
        cfg = _cfg()
        assert base_size(_signal(grade="A+"), cfg) == cfg["base"]["grade_risk"]["A+"]
        assert base_size(_signal(grade="C"), cfg) == cfg["base"]["grade_risk"]["C"]

    def test_unknown_grade_raises_instead_of_defaulting(self):
        with pytest.raises(ValueError, match="unknown grade"):
            base_size(_signal(grade="Z"), _cfg())

    def test_hard_ceiling_clamps_even_a_misconfigured_grade_cap(self):
        cfg = _cfg()
        cfg = dict(cfg)
        cfg["base"] = dict(cfg["base"])
        cfg["base"]["grade_risk"] = dict(cfg["base"]["grade_risk"])
        cfg["base"]["grade_risk"]["A+"] = 0.50  # way above the 1% hard ceiling
        result = base_size(_signal(grade="A+"), cfg)
        assert result == cfg["base"]["ceiling"]


class TestGatesHaltOnEveryConfiguredCondition:
    """Layer 0 hard gates: fault-injection per gate, confirming each actually fires
    and produces a halt reason (not a silent pass-through)."""

    def test_daily_loss_limit_halts(self):
        state = _state(daily_realized_pnl=-3000.0)
        reason = run_gates(_signal(), state, _cfg())
        assert reason is not None and "daily_loss_limit" in reason

    def test_max_drawdown_buffer_halts(self):
        state = _state(equity=91_500.0, drawdown_trailing=0.085)
        reason = run_gates(_signal(), state, _cfg())
        assert reason is not None and "max_drawdown_buffer" in reason

    def test_health_not_ok_halts(self):
        state = _state(health_ok=False)
        reason = run_gates(_signal(), state, _cfg())
        assert reason is not None and "health_not_ok" in reason

    def test_threat_critical_halts(self):
        state = _state(threat_score=0.90)
        reason = run_gates(_signal(), state, _cfg())
        assert reason is not None and "threat_critical" in reason

    def test_mc_breach_prob_halts(self):
        state = _state(mc_breach_prob=0.35)
        reason = run_gates(_signal(), state, _cfg())
        assert reason is not None and "mc_breach_prob" in reason

    def test_no_gate_fires_on_healthy_state(self):
        assert run_gates(_signal(), _state(), _cfg()) is None

    def test_decide_zeroes_size_and_risk_when_a_gate_fires(self, monkeypatch):
        _install_working_kelly_stand_in(monkeypatch)  # isolate: test gates, not the Kelly bug
        state = _state(health_ok=False)
        decision = risk_engine.decide(_signal(), state, _cfg())
        assert decision.halt is True
        assert decision.final_risk_pct == 0.0
        assert decision.final_size == 0.0
        assert decision.binding_constraint.startswith("halt:")


class TestPropCeilingAppliesOnTopOfWhateverElseBinds:
    """Layer 7 prop ceiling: this IS a real, working ceiling regardless of the Kelly
    bug above — it is computed independently and combined via min() with every
    other ceiling, so it always applies 'on top' of whatever the other ceilings are
    (including a broken/infinite Kelly ceiling). That part of CLAUDE.md #4 holds up;
    it's the Kelly half of the sentence that doesn't."""

    def test_healthy_account_gets_nonzero_prop_headroom(self):
        result = prop_ceiling(_signal(), _state(), _cfg())
        assert result > 0.0

    def test_prop_ceiling_zero_when_already_at_the_drawdown_floor(self):
        # equity already at/below the binding floor -> zero new risk permitted.
        state = _state(equity=91_000.0, daily_realized_pnl=-9000.0)
        assert prop_ceiling(_signal(), state, _cfg()) == 0.0

    def test_prop_ceiling_zero_when_open_positions_consume_the_whole_budget(self):
        state = _state(open_positions=[
            Position(instrument="GBPUSD", direction=1, size=1, entry=1.30, stop=1.29,
                     risk_pct_at_entry=0.02),
        ])
        assert prop_ceiling(_signal(), state, _cfg()) == 0.0

    def test_decide_now_refuses_before_prop_can_bind_when_kelly_is_broken(self):
        """Post-fix: with the real (broken) kelly layer import in play, decide()
        raises before it ever gets to combine prop with the other ceilings — the
        engine refuses outright rather than silently letting prop paper over a
        broken Kelly layer. This replaces the pre-fix test that asserted prop
        quietly bound anyway while kelly_ceiling was math.inf; that was exactly
        the silent-widening failure mode this fix closes."""
        for mod in ("sovereign.risk.layers.kelly", "sovereign.risk.kelly_engine"):
            sys.modules.pop(mod, None)
        state = _state(equity=98_800.0, daily_realized_pnl=-1200.0)
        with pytest.raises(ImportError, match="kelly"):
            risk_engine.decide(_signal(grade="A+"), state, _cfg())

    def test_prop_ceiling_binds_on_its_own_once_kelly_is_a_working_layer(self, monkeypatch):
        """With a genuinely working (not broken) kelly stand-in returning no
        constraint, prop is still the effective ceiling once the account is near
        its floor — i.e. prop's protection is not contingent on Kelly being the
        binding layer. Isolates prop's own behaviour from the separate,
        already-documented Kelly import bug."""
        _install_working_kelly_stand_in(monkeypatch)
        # Daily loss deep enough to shrink prop's headroom below the A+ base cap
        # (1%), but short of the -2% halt threshold, so no gate fires and prop is
        # free to bind on its own.
        state = _state(equity=98_800.0, daily_realized_pnl=-1200.0)
        decision = risk_engine.decide(_signal(grade="A+"), state, _cfg())
        assert decision.halt is False
        assert decision.layer_budgets["kelly_ceiling"] == math.inf
        assert decision.binding_constraint == "prop"
        assert decision.final_risk_pct == pytest.approx(decision.layer_budgets["prop_ceiling"])


class TestFinalRiskInvariant:
    """risk_engine.py's own stated invariant: final_risk <= base_risk and
    final_risk <= every ceiling, always."""

    def test_final_never_exceeds_base_or_any_ceiling(self, monkeypatch):
        _install_working_kelly_stand_in(monkeypatch)  # isolate: test the invariant, not the Kelly bug
        for grade in ("A+", "A", "B", "C"):
            state = _state()
            decision = risk_engine.decide(_signal(grade=grade), state, _cfg())
            lb = decision.layer_budgets
            assert decision.final_risk_pct <= lb["base"] + 1e-12
            assert decision.final_risk_pct <= lb["kelly_ceiling"] + 1e-12
            assert decision.final_risk_pct <= lb["portfolio_ceiling"] + 1e-12
            assert decision.final_risk_pct <= lb["prop_ceiling"] + 1e-12

    def test_final_is_zero_and_never_negative_at_the_edge(self, monkeypatch):
        _install_working_kelly_stand_in(monkeypatch)  # isolate: test the invariant, not the Kelly bug
        state = _state(equity=91_000.0, daily_realized_pnl=-9000.0)
        decision = risk_engine.decide(_signal(), state, _cfg())
        assert decision.final_risk_pct == 0.0
        assert decision.final_size == 0.0
