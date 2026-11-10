"""Plan E - maximum aggression: D plus population exploits.

Tighter value thresholds against stations, wide pressure against nits,
traps against maniacs, overbet shoves with the nuts and thin-hero-call
defence.  This is the variant to submit if it passes the fault suite.
"""

from policy import PokerBot


class Model(PokerBot):
    TIER = 5

    OPEN = 0.66
    THREE_BET = 0.16
    DEFEND = 0.92
    FOUR_BET = 0.06
    CALL_FOUR = 0.14

    VALUE = 0.60
    RAISE = 0.61
    MARGIN = 0.0
    BLUFF = 0.22
    ITERATIONS = 320

    def _value_fraction(self, game_state, eq, board):
        from botcore import board_texture
        style = self._style(game_state)
        paired, suited, connected = board_texture(board)
        if style == "station":
            return 1.0 if eq > 0.70 else 0.8
        if style == "nit":
            return 0.55
        if style == "maniac":
            return 0.5 if eq > 0.8 else 1.15
        if eq > 0.88 and game_state.street == "river":
            return 1.35
        if paired or connected:
            return 0.8
        return 0.66
