"""Mutation-oriented unit tests for FunderProExecutor's cTrader preflight.

DEFECT (2026-08-26): ``FunderProExecutor.__init__`` imported
``sovereign.execution.ctrader_bridge.CTraderBridge`` only inside
``_init_ctrader``, wrapped in a broad ``except Exception`` that logged a
warning and left ``self._ctrader`` as ``None``. Since
``sovereign/execution/`` contains only ``forex_exit_manager.py`` (no
``ctrader_bridge.py``), a DEMO/LIVE executor constructed "successfully" and
looked idle-but-connected until the first real order submission, at which
point ``_send_ctrader_order`` raised at the worst possible moment.

The fix: ``_preflight_ctrader_dependencies`` runs unconditionally in
``__init__`` for any non-OFF routing mode, before ``_init_ctrader``, and
raises ``CTraderBridgeMissing`` (an unguarded exception, not caught anywhere
on this path) naming the missing module.

Each test states the fault it exists to catch.
"""
import importlib

import pytest

from execution.funderpro_executor import (
    CTraderBridgeMissing,
    FunderProExecutor,
    _preflight_ctrader_dependencies,
)


class TestPreflightRaisesWhenBridgeMissing:
    """Fault: preflight removed, made non-fatal, or silently swallowed."""

    def test_preflight_raises_for_demo(self):
        with pytest.raises(CTraderBridgeMissing):
            _preflight_ctrader_dependencies('DEMO')

    def test_preflight_raises_for_live(self):
        with pytest.raises(CTraderBridgeMissing):
            _preflight_ctrader_dependencies('LIVE')

    def test_preflight_error_names_ctrader_bridge(self):
        with pytest.raises(CTraderBridgeMissing) as exc_info:
            _preflight_ctrader_dependencies('DEMO')
        assert 'ctrader_bridge' in str(exc_info.value)

    def test_preflight_is_noop_for_off(self):
        # OFF (log-only / simulated fill) never touches ctrader_bridge —
        # the preflight must not block it.
        _preflight_ctrader_dependencies('OFF')  # must not raise


class TestExecutorConstructionHonorsPreflight:
    """Fault: the executor can be constructed in a live-order-capable mode
    while ctrader_bridge is missing — i.e. the preflight is wired in name
    only and never actually called from __init__."""

    def test_off_mode_constructs_fine(self, monkeypatch):
        monkeypatch.delenv('FUNDERPRO_LIVE', raising=False)
        executor = FunderProExecutor(account_size=10_000.0)
        status = executor.get_status()
        assert status.routing == 'OFF'
        assert status.connected is False

    def test_demo_mode_construction_refused_while_bridge_missing(self, monkeypatch):
        monkeypatch.setenv('FUNDERPRO_LIVE', 'demo')
        with pytest.raises(CTraderBridgeMissing) as exc_info:
            FunderProExecutor(account_size=10_000.0)
        assert 'ctrader_bridge' in str(exc_info.value)

    def test_live_mode_construction_refused_while_bridge_missing(
        self, monkeypatch, tmp_path
    ):
        # Even if the pipeline GO guard were satisfied, the ctrader_bridge
        # preflight must independently refuse construction. We don't bother
        # satisfying the GO guard here — LIVE must fail either way, and if
        # the GO guard fires first that's still a correct refusal. What this
        # test guards against is a code path where GO-guard-pass +
        # bridge-missing somehow constructs successfully.
        monkeypatch.setenv('FUNDERPRO_LIVE', 'live')
        with pytest.raises((CTraderBridgeMissing, RuntimeError)) as exc_info:
            FunderProExecutor(account_size=10_000.0)
        # If the pipeline GO guard is what fired, this test doesn't prove
        # the bridge preflight — assert directly that the preflight itself
        # (independent of the GO guard) refuses LIVE.
        _ = exc_info
        with pytest.raises(CTraderBridgeMissing):
            _preflight_ctrader_dependencies('LIVE')


class TestBridgeReallyAbsentOnDisk:
    """Sanity: this whole test file is only meaningful while the bridge is
    genuinely absent. If it starts importing, the preflight's find_spec
    check would stop raising and TestPreflightRaisesWhenBridgeMissing above
    would fail loudly (correctly) — this test just documents the assumption
    explicitly rather than relying on that indirect signal alone."""

    def test_ctrader_bridge_module_not_importable(self):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module('sovereign.execution.ctrader_bridge')
