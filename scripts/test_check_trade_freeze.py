"""Unit tests for the trade-evidence freeze — CLAUDE.md non-negotiable 6."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_trade_freeze as ctf  # noqa: E402


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def test_count_closed_trades_only_counts_closed_with_r(tmp_path):
    path = tmp_path / "trades.jsonl"
    _write_jsonl(path, [
        dict(status="closed", R=0.5),
        dict(status="closed", R=-0.2),
        dict(status="open", R=None),
        dict(status="closed", R=None),  # defensive: shouldn't happen, but not evidence
    ])
    assert ctf.count_closed_trades(path) == 2


def test_count_closed_trades_missing_file_is_zero(tmp_path):
    assert ctf.count_closed_trades(tmp_path / "does_not_exist.jsonl") == 0


def test_blocks_new_spec_file_below_threshold():
    blocked, offenders = ctf.evaluate(
        added_files=["specs/099_NEW_LAYER.md"], closed_count=0, commit_msg="")
    assert blocked is True
    assert offenders == ["specs/099_NEW_LAYER.md"]


def test_blocks_new_daytrade_file_below_threshold():
    blocked, offenders = ctf.evaluate(
        added_files=["daytrade/new_module.py"], closed_count=49, commit_msg="")
    assert blocked is True


def test_allows_new_file_at_threshold():
    blocked, offenders = ctf.evaluate(
        added_files=["specs/099_NEW_LAYER.md"], closed_count=50, commit_msg="")
    assert blocked is False
    assert offenders == []


def test_allows_new_file_above_threshold():
    blocked, _ = ctf.evaluate(
        added_files=["specs/099_NEW_LAYER.md"], closed_count=80, commit_msg="")
    assert blocked is False


def test_ignores_files_outside_gated_dirs():
    blocked, _ = ctf.evaluate(
        added_files=["scripts/new_tool.py", "README.md"], closed_count=0, commit_msg="")
    assert blocked is False


def test_allows_edits_to_existing_files_regardless_of_count():
    # An edit never appears in the "added" list (git diff-filter=A), so it's
    # never even offered to evaluate() — this documents that invariant.
    blocked, _ = ctf.evaluate(added_files=[], closed_count=0, commit_msg="")
    assert blocked is False


def test_test_files_are_exempt():
    blocked, _ = ctf.evaluate(
        added_files=["specs/test_new_thing.py", "daytrade/tests/test_x.py"],
        closed_count=0, commit_msg="")
    assert blocked is False


def test_grandfathered_paths_are_exempt():
    blocked, _ = ctf.evaluate(
        added_files=["daytrade/paper_carry_runner.py"], closed_count=0, commit_msg="")
    assert blocked is False


def test_override_phrase_unblocks():
    blocked, _ = ctf.evaluate(
        added_files=["specs/099_NEW_LAYER.md"], closed_count=0,
        commit_msg="Add new layer\n\nFREEZE OVERRIDE: unblocks trade generation, see spec 040\n")
    assert blocked is False


def test_override_phrase_must_be_present_not_just_mentioned():
    blocked, _ = ctf.evaluate(
        added_files=["specs/099_NEW_LAYER.md"], closed_count=0,
        commit_msg="talking about FREEZE OVERRIDE in prose but not as a real directive")
    # "FREEZE OVERRIDE:" must start a line (colon required) — this message
    # has no line starting with the exact phrase, so it still blocks.
    assert blocked is True


def test_mixed_added_files_reports_only_offenders():
    blocked, offenders = ctf.evaluate(
        added_files=["specs/099_NEW.md", "README.md", "scripts/tool.py"],
        closed_count=0, commit_msg="")
    assert blocked is True
    assert offenders == ["specs/099_NEW.md"]
