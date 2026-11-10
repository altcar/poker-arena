"""Plan B - preflop ranges plus Monte-Carlo equity.

The workhorse: a 25 bb heads-up opening range, equity against random hands
postflop, pot-odds calls and a single value size.
"""

from policy import PokerBot


class Model(PokerBot):
    TIER = 2

    OPEN = 0.80
    THREE_BET = 0.12
    DEFEND = 1.0
    FOUR_BET = 0.045
    CALL_FOUR = 1.0

    VALUE = 0.58
    RAISE = 0.58
    MARGIN = 0.0
    BLUFF = 0.0
    VALUE_SIZE = 1.25
    ITERATIONS = 200
