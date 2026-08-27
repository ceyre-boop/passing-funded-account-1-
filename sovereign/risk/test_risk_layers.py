"""Characterisation + invariant tests for the risk-layer cascade:
sovereign/risk/risk_engine.py, risk_state.py, layers/base_size.py,
layers/gates.py, layers/prop.py, and the risk_engine.py <-> layers/kelly.py wiring.

CONTEXT (2026-08-26): CLAUDE.md non-negotiable #4 claims
"sovereign/risk/kelly_engine.py computes proper quarter-Kelly ... then
sovereign/risk/layers/prop.py applies the funded-account ceiling on top."
`layers/prop.py` DOES exist (confirmed below by test_prop_module_exists). The
Kelly half used to be false: `sovereign/risk/layers/kelly.py` imported
`fractional_kelly`/`hoeffding_win_rate` from `sovereign/risk/kelly_engine.py`, whose
OWN module-level imports (`layer2.risk_engine`, `layer2.dynamic_rr_engine` — see
test_kelly_engine.py) don't exist in this repo, so `import
sovereign.risk.layers.kelly` raised `ModuleNotFoundError` and `risk_engine.py`'s
`_ceiling()`/`_modulator()` fix (still in place, see
TestLayerExistenceGuardDistinguishesAbsentFromBroken below) made `decide()` refuse
loudly rather than silently treat the broken layer as no constraint.

FIX (2026-08-26): the pure math (`fractional_kelly`, `hoeffding_win_rate`) was split
out to sovereign/risk/kelly_math.py, which has no dependency on `layer2`/`config` at
all. `sovereign/risk/layers/kelly.py` now imports from kelly_math.py directly, so it
imports cleanly, and `risk_engine.decide()` genuinely uses a computed Kelly ceiling —
see TestKellyLayerIsWired below for both the fix verification and, separately,
continuing coverage that a *hypothetically* broken ceiling layer would still make
decide() refuse rather than silently widen (kelly_engine.py's SovereignRiskEngine
class remains genuinely broken/unused — see test_kelly_engine.py — but it is no
longer on the path anything needs).

`rg kelly_engine|risk_engine` outside sovereign/ still returns nothing — this whole
cascade is fully wired to itself but has zero callers anywhere else in the repo. It
is not on any live sizing path today; fixing the wiring makes CLAUDE.md's sentence
true of the code that exists, not of anything actually placing an order.
"""
import math
import sys
import types

import pytest

from sovereign.risk import risk_engine
from sovereign.risk.config.loader import load_risk_config
from sovereign.risk.layers.base_size import base_size
from sovereign.risk.layers.gates import run_gates, unarmed_gates
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
    """Install a WORKING sovereign.risk.layers.kelly stand-in with a FIXED ceiling
    value (default math.inf = no constraint).

    Tests below that exercise gates/prop/the final-risk invariant care about
    layers other than Kelly. The real kelly.py now imports cleanly and computes a
    genuine, edge_stats-dependent ceiling (see TestKellyLayerIsWired) — good for
    Kelly's own tests, but a moving target for tests that just want to isolate
    some OTHER layer's behaviour. This stand-in pins Kelly to a known, fixed value
    (math.inf by default = no constraint) so those tests aren't coupled to Kelly's
    config/formula.
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


class TestKellyLayerIsWired:
    """THE central finding, now fixed. CLAUDE.md #4 implies Kelly is an active
    control on the sizing path. `sovereign/risk/layers/kelly.py` used to import
    `fractional_kelly`/`hoeffding_win_rate` from `sovereign/risk/kelly_engine.py`,
    whose own module-level imports (`layer2.risk_engine`, `layer2.dynamic_rr_engine`
    — see test_kelly_engine.py) do not exist in this repo, so
    `import sovereign.risk.layers.kelly` raised ModuleNotFoundError and (after the
    earlier `_ceiling()` fix — see TestLayerExistenceGuardDistinguishesAbsentFromBroken)
    `decide()` refused outright rather than silently treating Kelly as unconstrained.

    FIX (2026-08-26): `layers/kelly.py` now imports `fractional_kelly`/
    `hoeffding_win_rate` from `sovereign/risk/kelly_math.py`, which has no
    `layer2`/`contracts`/`config` dependency at all. The import chain works, so
    Kelly is now a genuinely computed ceiling on `decide()`'s sizing path — not a
    stand-in, not math.inf.
    """

    def test_kelly_layer_imports_cleanly_now(self):
        for mod in ("sovereign.risk.layers.kelly", "sovereign.risk.kelly_math"):
            sys.modules.pop(mod, None)
        mod = __import__("sovereign.risk.layers.kelly", fromlist=["ceiling"])
        assert hasattr(mod, "ceiling")

    def test_kelly_layer_file_exists_on_disk_and_is_now_importable(self):
        assert risk_engine._layer_module_exists("kelly") is True
        for mod in ("sovereign.risk.layers.kelly", "sovereign.risk.kelly_math"):
            sys.modules.pop(mod, None)
        __import__("sovereign.risk.layers.kelly")  # must not raise

    def test_decide_no_longer_raises_on_kelly_and_uses_a_real_ceiling(self):
        """FIX VERIFICATION: decide() used to raise ImportError here. It now
        succeeds and reports a genuinely computed (not math.inf, not a stand-in)
        Kelly ceiling."""
        for mod in ("sovereign.risk.layers.kelly", "sovereign.risk.kelly_math"):
            sys.modules.pop(mod, None)
        sig = _signal(grade="A")
        state = _state()  # no edge_stats -> thin-data floor path
        decision = risk_engine.decide(sig, state, _cfg())
        cfg = _cfg()
        assert decision.layer_budgets["kelly_ceiling"] == pytest.approx(
            cfg["kelly"]["fixed_fractional_floor"]
        )
        assert decision.layer_budgets["kelly_ceiling"] != math.inf

    def test_decide_computes_quarter_kelly_from_real_edge_stats(self):
        """With enough trades in state.edge_stats, the Kelly ceiling is the actual
        quarter-Kelly formula output (via kelly_math.fractional_kelly), not the
        thin-data floor and not math.inf — and it can bind as the tightest
        constraint, exactly as CLAUDE.md #4 describes."""
        from sovereign.risk.kelly_math import fractional_kelly, hoeffding_win_rate

        cfg = _cfg()
        n, p, b = 200, 0.60, 2.0
        state = _state(edge_stats={"forex_macro": {"n_trades": n, "win_rate": p, "payoff": b}})
        decision = risk_engine.decide(_signal(grade="A"), state, cfg)

        p_adj = hoeffding_win_rate(p, n)
        expected = fractional_kelly(
            p_adj, b, 1.0, fraction=cfg["kelly"]["fraction"], floor=0.0, ceiling=cfg["kelly"]["hard_cap"],
        )
        assert decision.layer_budgets["kelly_ceiling"] == pytest.approx(max(0.0, expected))
        assert decision.layer_budgets["kelly_ceiling"] != math.inf

    def test_decide_still_honors_a_stand_in_kelly_layer(self, monkeypatch):
        """Unchanged behaviour, still worth asserting: risk_engine.py's cascade
        logic honors whatever `layers/kelly.py` reports, real or stand-in."""
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

    def test_present_but_broken_ceiling_raises_not_math_inf(self, monkeypatch):
        """Fails if a present-but-broken ceiling layer still yields math.inf.

        Uses a synthetic broken layer name (like the modulator test below) rather
        than kelly: as of 2026-08-26 `layers/kelly.py` genuinely imports (see
        TestKellyLayerIsWired), so it can no longer stand in for 'present but
        broken' — this guard must be proven independently of which real layer
        happens to be broken today."""
        broken_name = "portfolio"
        real_spec_find = risk_engine.importlib_util.find_spec

        def _fake_find_spec(name, *a, **kw):
            if name == f"sovereign.risk.layers.{broken_name}":
                return object()  # "the file exists" — any non-None sentinel
            return real_spec_find(name, *a, **kw)

        def _fake_import(name, *a, **kw):
            if name == f"sovereign.risk.layers.{broken_name}":
                raise ImportError("simulated broken import for portfolio")
            raise ImportError(name)

        monkeypatch.setattr(risk_engine.importlib_util, "find_spec", _fake_find_spec)
        monkeypatch.setattr(risk_engine, "import_module", _fake_import)
        with pytest.raises(ImportError, match="portfolio"):
            risk_engine._ceiling("portfolio", _signal(), _state(), _cfg())

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
        _install_working_kelly_stand_in(monkeypatch)  # isolate from real Kelly's edge_stats-dependent value; test gates in isolation
        state = _state(health_ok=False)
        decision = risk_engine.decide(_signal(), state, _cfg())
        assert decision.halt is True
        assert decision.final_risk_pct == 0.0
        assert decision.final_size == 0.0
        assert decision.binding_constraint.startswith("halt:")


class TestMcBreachGateInertnessIsVisible:
    """mc_breach_halt is armed in risk_config.yaml, but no production code ever
    supplies state.mc_breach_prob (only test_mc_breach_prob_halts above sets it
    by hand) — the gate can never fire on a real decision. Fault injection: if
    unarmed_gates() stops reporting this, or decide() stops attaching it to
    RiskDecision, these tests fail. The gate itself must keep working exactly
    as before (test_mc_breach_prob_halts, unchanged) — this is a visibility
    fix, not a behavior fix."""

    def test_unarmed_gates_reports_mc_breach_when_input_absent(self):
        reported = unarmed_gates(_state(), _cfg())
        assert any("mc_breach_prob" in u for u in reported)

    def test_unarmed_gates_silent_once_mc_breach_prob_is_supplied(self):
        """The moment a real supplier sets mc_breach_prob (even to a
        non-halting value), the gate is armed again and must not be reported
        as unarmed."""
        reported = unarmed_gates(_state(mc_breach_prob=0.01), _cfg())
        assert not any("mc_breach_prob" in u for u in reported)

    def test_unarmed_gates_does_not_mask_other_absent_inputs_as_armed(self):
        """Regression guard: this must not become a hardcoded single-field
        check that silently stops covering mc_breach_prob if the config key
        is renamed. Removing 'mc_breach_halt' from gate config must also
        remove the corresponding unarmed report."""
        cfg = _cfg()
        cfg["gates"] = dict(cfg["gates"])
        del cfg["gates"]["mc_breach_halt"]
        reported = unarmed_gates(_state(), cfg)
        assert not any("mc_breach_prob" in u for u in reported)

    def test_decide_surfaces_unarmed_gates_on_a_normal_decision(self, monkeypatch):
        """The inertness must reach the actual decision object every caller
        sees — not just the low-level gates.py function — so it shows up in
        the audit log on every ordinary sizing decision, halt or not."""
        _install_working_kelly_stand_in(monkeypatch)
        decision = risk_engine.decide(_signal(), _state(), _cfg())
        assert decision.halt is False
        assert any("mc_breach_prob" in u for u in decision.unarmed_gates)
        assert "UNARMED GATES" in decision.reasoning
        assert "mc_breach_prob" in decision.reasoning

    def test_decide_surfaces_unarmed_gates_even_when_another_gate_halts(self, monkeypatch):
        """Inertness reporting must not silently disappear just because a
        different, working gate happened to halt this particular decision."""
        _install_working_kelly_stand_in(monkeypatch)
        state = _state(health_ok=False)
        decision = risk_engine.decide(_signal(), state, _cfg())
        assert decision.halt is True
        assert any("mc_breach_prob" in u for u in decision.unarmed_gates)


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

    def test_decide_still_refuses_before_prop_can_bind_when_a_ceiling_layer_is_broken(self, monkeypatch):
        """The general guard, no longer demonstrable via kelly (fixed 2026-08-26 —
        see TestKellyLayerIsWired): if ANY wired ceiling layer is present-but-broken,
        decide() must still refuse outright rather than silently letting prop paper
        over it. Synthesizes brokenness on portfolio instead."""
        broken_name = "portfolio"
        real_spec_find = risk_engine.importlib_util.find_spec
        real_import = risk_engine.import_module

        def _fake_find_spec(name, *a, **kw):
            if name == f"sovereign.risk.layers.{broken_name}":
                return object()
            return real_spec_find(name, *a, **kw)

        def _fake_import(name, *a, **kw):
            if name == f"sovereign.risk.layers.{broken_name}":
                raise ImportError("simulated broken import for portfolio")
            return real_import(name, *a, **kw)  # other layers (volatility, drawdown,
            # regime, kelly) must still import for real — decide() runs the whole
            # cascade, not just the layer under test.

        monkeypatch.setattr(risk_engine.importlib_util, "find_spec", _fake_find_spec)
        monkeypatch.setattr(risk_engine, "import_module", _fake_import)

        state = _state(equity=98_800.0, daily_realized_pnl=-1200.0)
        with pytest.raises(ImportError, match="portfolio"):
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
        _install_working_kelly_stand_in(monkeypatch)  # isolate from real Kelly's edge_stats-dependent value; test the invariant in isolation
        for grade in ("A+", "A", "B", "C"):
            state = _state()
            decision = risk_engine.decide(_signal(grade=grade), state, _cfg())
            lb = decision.layer_budgets
            assert decision.final_risk_pct <= lb["base"] + 1e-12
            assert decision.final_risk_pct <= lb["kelly_ceiling"] + 1e-12
            assert decision.final_risk_pct <= lb["portfolio_ceiling"] + 1e-12
            assert decision.final_risk_pct <= lb["prop_ceiling"] + 1e-12

    def test_final_is_zero_and_never_negative_at_the_edge(self, monkeypatch):
        _install_working_kelly_stand_in(monkeypatch)  # isolate from real Kelly's edge_stats-dependent value; test the invariant in isolation
        state = _state(equity=91_000.0, daily_realized_pnl=-9000.0)
        decision = risk_engine.decide(_signal(), state, _cfg())
        assert decision.final_risk_pct == 0.0
        assert decision.final_size == 0.0
