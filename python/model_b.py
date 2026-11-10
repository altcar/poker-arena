"""Plan B - preflop ranges plus Monte-Carlo equity.

The workhorse: a 25 bb heads-up opening range, equity against random hands
postflop, pot-odds calls and a single value size.
"""

from policy import PokerBot


class Model(PokerBot):
    TIER = 2

    OPEN = 0.55
    THREE_BET = 0.12
    DEFEND = 0.85
    FOUR_BET = 0.045
    CALL_FOUR = 0.10

    VALUE = 0.64
    RAISE = 0.64
    MARGIN = 0.0
    BLUFF = 0.06
    ITERATIONS = 200
