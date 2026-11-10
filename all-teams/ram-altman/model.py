import random

from card_utils import Card, evaluate, full_deck
from game_state import GameStateView
from player_action import PlayerAction

SIMULATIONS = 200  # equity samples per decision; more = slower but steadier
RAISE_EQUITY = 0.65  # raise when we win at least this often
CALL_MARGIN = 0.05  # call only when equity beats the pot odds by this much


def equity(hand, board, opponents, simulations=SIMULATIONS):
    """Share of pots won (ties split) against `opponents` random hands,
    dealing out the rest of the board at random."""
    known = {str(c) for c in hand + board}
    deck = [c for c in full_deck() if str(c) not in known]
    need = 5 - len(board)
    won = 0.0
    for _ in range(simulations):
        cards = random.sample(deck, need + 2 * opponents)
        full_board = board + cards[:need]
        mine = evaluate(hand + full_board)
        best = max(
            evaluate(cards[need + 2 * i : need + 2 * i + 2] + full_board)
            for i in range(opponents)
        )
        if mine > best:
            won += 1
        elif mine == best:
            won += 0.5
    return won / simulations


class Model:
    """Basic bot: estimate the chance of winning by simulation, then raise
    when strong, call when the price is right, otherwise check or fold."""

    def __init__(self):
        self.hand = []
        self.player_id = None

    def seed(self, hand: list[Card], player_id: int):
        self.hand = hand
        self.player_id = player_id

    def run(
        self, actions: list[PlayerAction], game_state: GameStateView
    ) -> PlayerAction:
        me = self.player_id
        opponents = max(1, len(game_state.players_in_hand) - 1)
        eq = equity(list(self.hand), list(game_state.board), opponents)

        if eq >= RAISE_EQUITY and game_state.can_raise:
            low, high = game_state.raise_range
            # About a pot-sized raise, kept within the legal range.
            target = game_state.current_bet + game_state.pot
            return PlayerAction.raise_to(me, min(high, max(low, target)))
        if game_state.call_amount == 0 or eq >= game_state.pot_odds + CALL_MARGIN:
            return PlayerAction.call(me)  # check, or a call worth paying
        return PlayerAction.fold(me)

    def end_game(
        self, actions: list[PlayerAction], game_state: GameStateView
    ):
        pass
