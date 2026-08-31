"""body/alphazero_actor.py — AlphaZero rides as a Nautilus Actor.

WHY Actor AND NOT Strategy
    `nautilus_trader.common.actor.Actor` has no `submit_order`, no
    `cancel_order`, no `modify_order`, no `close_position`, no `order_factory`.
    Forty-seven methods exist on `Strategy` that do not exist here. That absence
    IS the enforcement of `CLAUDE.md`'s rule that AlphaZero must not place
    orders, size exits, or move stops -- there is no method to call.

    Do not "fix" this by handing the actor a reference to the strategy. The
    static guard in body/test_boundary.py fails the suite if any Actor subclass
    in this package so much as names an order verb.

WHAT IT PUBLISHES
    An `EntryDirective` and nothing else: direction, reason, confidence, and its
    own expiry. No quantity, no price, no stop.

THE DEFAULT POLICY ABSTAINS
    `abstain_always` is the shipped default because that is the honest state of
    this repo: every detection-floor survivor is closed on economics, the entry
    family is closed, and ODD.md §0 resolves its scope line to "never". A
    skeleton that emitted plausible directives would be a lie told in code.
"""
from __future__ import annotations

from nautilus_trader.common.actor import Actor
from nautilus_trader.model.data import DataType

from body.directive import EntryDirective

DIRECTIVE_TYPE = DataType(EntryDirective)


def abstain_always(bar) -> None:
    """The only policy this repo has evidence for. Returns None = stay silent.

    Silence is not a flat signal. AlphaZero abstains by saying nothing, which is
    why EntryDirective has no zero direction to abuse."""
    return None


class AlphaZeroActor(Actor):
    """Reads bars, forms meaning, publishes a directive. Cannot trade.

    `policy` maps a bar to an EntryDirective or to None. It is injected rather
    than hardcoded so that a policy can never be adopted implicitly -- swapping
    it is a visible act, and the shipped default abstains.
    """

    def __init__(self, config=None, *, bar_type=None, policy=abstain_always):
        super().__init__(config)
        self._bar_type = bar_type
        self._policy = policy
        self.published = 0
        self.abstained = 0

    def on_start(self) -> None:
        if self._bar_type is not None:
            self.subscribe_bars(self._bar_type)

    def on_bar(self, bar) -> None:
        directive = self._policy(bar)
        if directive is None:
            self.abstained += 1
            return
        if not isinstance(directive, EntryDirective):
            raise TypeError(
                f"policy returned {type(directive).__name__}; AlphaZero may only "
                "emit an EntryDirective — anything else is an attempt to send "
                "mechanics through the meaning channel")
        self.publish_data(DIRECTIVE_TYPE, directive)
        self.published += 1
