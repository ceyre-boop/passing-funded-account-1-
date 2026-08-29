"""I70 — the bench is reproducible and a one-parameter mutation changes it; parity is checked on every row."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import carry_bench  # noqa: E402


def test_bench_reproducible():
    a, rows_a = carry_bench.bench()
    b, rows_b = carry_bench.bench()
    assert a == b and rows_a == rows_b
    # every decision row up to and including the incumbent's exit is parity-checked
    import pandas as pd
    paths = pd.read_parquet(ROOT / "artifacts" / "carry_paths.parquet")
    trades = pd.read_parquet(ROOT / "artifacts" / "carry_trades.parquet").set_index("trade_id")
    expected_rows = int(sum(min(int(trades.loc[t, "incumbent_hold"]), int(g.t.max())) for t, g in paths.groupby("trade_id")))
    assert rows_a == expected_rows == 1727
    assert round(a, 2) == 34.41


def test_bench_matches_committed_number():
    a, _ = carry_bench.bench()
    assert f"{a:.10f}" == (ROOT / "artifacts" / "bench.txt").read_text().strip()


def test_bench_mutation_changes_number():
    a, _ = carry_bench.bench()
    m, _ = carry_bench.bench(trailing_mult_scale=0.5, verify_inputs=False)   # halve every trail multiple
    assert m != a
    tiny, _ = carry_bench.bench(trailing_mult_scale=1.0 + 1e-4, verify_inputs=False)
    assert tiny == a  # a 0.01% change flips no exit on this workload — recorded, not hidden


def test_bench_cli_check_passes():
    r = subprocess.run([sys.executable, "scripts/carry_bench.py", "--check"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("bench ok ")


def test_bench_refuses_unhashed_inputs(monkeypatch, tmp_path):
    from sovereign.forex import inventory as inv
    monkeypatch.setattr(inv, "load", lambda: {"hashes": {}, "notes": {}})
    with pytest.raises(inv.InventoryError):
        carry_bench.bench()
