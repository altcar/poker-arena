"""Plan D - C plus mixed strategies, board texture and MDF defence.

Adds blocker-aware bluffing, texture-driven sizing, small-bet defence and
mixed frequencies keyed to the hand, which makes the bot both harder to read
and harder to exploit.
"""

from policy import PokerBot


class Model(PokerBot):
    TIER = 4

    OPEN = 0.62
    THREE_BET = 0.15
    DEFEND = 0.90
    FOUR_BET = 0.055
    CALL_FOUR = 0.13

    VALUE = 0.62
    RAISE = 0.62
    MARGIN = 0.0
    BLUFF = 0.16
    ITERATIONS = 300
