"""body/test_boundary.py — proof that AlphaZero cannot reach an order path.

Two independent guards, because either alone is defeatable:

  RUNTIME  -- the Actor subclass genuinely has no order methods.
  STATIC   -- no Actor subclass in this package NAMES an order verb, which is
              what catches the real attack: handing the actor a reference to
              the strategy and calling through it. `hasattr` would never see
              that; the AST scan does.

Tests marked FAULT INJECTION construct the violation on purpose and assert the
guard fires, per CLAUDE.md's definition of VERIFIED.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# nautilus_trader v1 needs Python <=3.13 and lives in .venv-v1, while the rest of
# the repo runs on 3.14. Skip cleanly there rather than erroring the root run:
#     .venv-v1/bin/python -m pytest body/ -q
pytest.importorskip("nautilus_trader",
                    reason="run body/ under .venv-v1 (py3.13); see "
                           "artifacts/NAUTILUS_BODY_ASSESSMENT.md")

from nautilus_trader.common.actor import Actor  # noqa: E402
from nautilus_trader.trading.strategy import Strategy  # noqa: E402

from body.alphazero_actor import AlphaZeroActor, abstain_always  # noqa: E402
from body.directive import LONG, SHORT, DirectiveError, EntryDirective  # noqa: E402
from body.stockfish_strategy import StockfishStrategy  # noqa: E402

ORDER_VERBS = ("submit_order", "submit_order_list", "cancel_order",
               "cancel_all_orders", "cancel_orders", "modify_order",
               "close_position", "close_all_positions", "order_factory",
               "query_order")

BODY = Path(__file__).resolve().parent


def directive(**kw):
    base = dict(instrument_id="SPY.ARCA", direction=LONG, reason="macro splash",
                confidence=0.4, ts_event=1_000, valid_for_ns=500)
    base.update(kw)
    return EntryDirective(**base)


# ------------------------------------------------------- runtime guard

@pytest.mark.parametrize("verb", ORDER_VERBS)
def test_actor_base_has_no_order_authority(verb):
    assert not hasattr(Actor, verb), f"Actor gained {verb} — the boundary is gone"


@pytest.mark.parametrize("verb", ORDER_VERBS)
def test_alphazero_has_no_order_authority(verb):
    assert not hasattr(AlphaZeroActor, verb)


@pytest.mark.parametrize("verb", ORDER_VERBS)
def test_stockfish_holds_the_order_authority(verb):
    """The other half: authority must actually live somewhere."""
    assert hasattr(StockfishStrategy, verb), f"Strategy lost {verb}"


def test_the_two_engines_do_not_share_a_base_with_order_methods():
    assert issubclass(AlphaZeroActor, Actor) and not issubclass(AlphaZeroActor, Strategy)
    assert issubclass(StockfishStrategy, Strategy)


# -------------------------------------------------------- static guard

def _actor_subclass_nodes(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
                getattr(b, "id", getattr(b, "attr", None)) == "Actor" for b in node.bases):
            yield node


def _order_verb_references(node: ast.AST) -> list[str]:
    hits = []
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and n.attr in ORDER_VERBS:
            hits.append(n.attr)
        if isinstance(n, ast.Name) and n.id in ORDER_VERBS:
            hits.append(n.id)
    return hits


def test_no_actor_subclass_in_body_names_an_order_verb():
    """The guard that catches calling through a smuggled strategy reference."""
    offenders = {}
    for path in BODY.glob("*.py"):
        if path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text())
        for cls in _actor_subclass_nodes(tree):
            hits = _order_verb_references(cls)
            if hits:
                offenders[f"{path.name}::{cls.name}"] = sorted(set(hits))
    assert not offenders, f"Actor subclass reaching for order methods: {offenders}"


def test_static_guard_actually_fires():
    """FAULT INJECTION: the scan must fail on a smuggled call it cannot see via
    hasattr. If this passes, the guard above is decorative."""
    smuggled = ast.parse(
        "class Sneaky(Actor):\n"
        "    def on_bar(self, bar):\n"
        "        self._strategy.submit_order(bar)\n")
    found = [_order_verb_references(c) for c in _actor_subclass_nodes(smuggled)]
    assert found and "submit_order" in found[0]


def test_static_guard_ignores_non_actor_classes():
    """The Strategy is SUPPOSED to name these — the scan must not flag it."""
    tree = ast.parse((BODY / "stockfish_strategy.py").read_text())
    assert not list(_actor_subclass_nodes(tree))


# ------------------------------------------------- the directive's inertness

@pytest.mark.parametrize("field", ("quantity", "qty", "size", "stop", "stop_price",
                                   "target", "price", "notional"))
def test_directive_carries_no_executable_quantity(field):
    assert not hasattr(directive(), field), (
        f"EntryDirective grew {field!r} — AlphaZero must not size or price anything")


def test_there_is_no_zero_direction():
    """Silence is how AlphaZero abstains; a zero direction would let a missing
    signal masquerade as a flat one."""
    with pytest.raises(DirectiveError):
        directive(direction=0)


@pytest.mark.parametrize("bad", [dict(reason="   "), dict(confidence=1.5),
                                 dict(confidence=-0.1), dict(valid_for_ns=0)])
def test_directive_refuses_malformed_meaning(bad):
    with pytest.raises(DirectiveError):
        directive(**bad)


def test_direction_accepts_both_sides():
    assert directive(direction=SHORT).direction == SHORT


# ------------------------------------------------------ the actor's behaviour

def test_shipped_policy_abstains():
    assert abstain_always(object()) is None


def test_actor_rejects_a_policy_returning_mechanics():
    """FAULT INJECTION: an attempt to push non-meaning through the channel."""
    a = AlphaZeroActor(policy=lambda bar: {"submit": "market", "qty": 100})
    with pytest.raises(TypeError):
        a.on_bar(object())


def test_actor_counts_abstentions_without_publishing():
    a = AlphaZeroActor()
    a.on_bar(object())
    assert a.abstained == 1 and a.published == 0


# ------------------------------------------------- the strategy's refusals

def test_stale_directive_is_refused():
    s = StockfishStrategy()
    d = directive()
    ok, why = s.authorize(d, now_ns=d.valid_until_ns + 1)
    assert not ok and why.startswith("stale")


def test_fresh_directive_still_refused_because_the_odd_gate_is_shut():
    """Freshness is necessary, not sufficient. v0.1 cannot authorize anything."""
    s = StockfishStrategy()
    d = directive()
    ok, why = s.authorize(d, now_ns=d.ts_event)
    assert not ok and not why.startswith("stale")


def test_execution_path_refuses_rather_than_pretending():
    with pytest.raises(NotImplementedError):
        StockfishStrategy().execute(directive())


def test_strategy_never_reads_the_reason_string():
    """Stockfish must not infer meaning. `reason` is audit trail, not input."""
    src = (BODY / "stockfish_strategy.py").read_text()
    body_only = src.split('"""', 2)[-1]          # drop the module docstring
    assert ".reason" not in body_only, "Stockfish is reading AlphaZero's reason"
