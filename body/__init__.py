"""body/ — the Nautilus chassis. AlphaZero rides as an Actor, Stockfish as a Strategy.

The whole point of this package is that `CLAUDE.md`'s central rule stops being a
convention and becomes an API fact:

    AlphaZero communicates meaning.  Stockfish controls mechanics.

`nautilus_trader.common.actor.Actor` has NO order-submission surface --
`submit_order`, `cancel_order`, `modify_order`, `close_position` and four more
simply do not exist on it. So an AlphaZero component built on `Actor` cannot
place an order, and the failure mode is `AttributeError` at import-adjacent
time rather than a review comment six months late.

See artifacts/NAUTILUS_BODY_ASSESSMENT.md for why this platform and why v1.
"""
