"""Plan A - conservative, deterministic hand-strength heuristic.

No Monte-Carlo, no opponent modelling.  Very hard to fault, cheap on the
clock, and enough to punish the fold/call dummies.
"""

from policy import PokerBot


class Model(PokerBot):
    TIER = 1

    OPEN = 0.34
    THREE_BET = 0.09
    DEFEND = 0.32
    FOUR_BET = 0.03
    CALL_FOUR = 0.07

    VALUE = 0.62
    RAISE = 0.78
    MARGIN = 0.05
    BLUFF = 0.0
    ITERATIONS = 0
