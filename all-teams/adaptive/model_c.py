"""Plan C - B plus a live opponent model.

Counters per team (VPIP/PFR/aggression/big-bet frequency) drive the range
filter used by the equity calculation and switch the strategy archetype.
"""

from policy import PokerBot


class Model(PokerBot):
    TIER = 3

    OPEN = 0.58
    THREE_BET = 0.13
    DEFEND = 0.88
    FOUR_BET = 0.05
    CALL_FOUR = 0.11

    VALUE = 0.63
    RAISE = 0.63
    MARGIN = 0.0
    BLUFF = 0.10
    ITERATIONS = 240
