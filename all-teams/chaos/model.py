from card_utils import Card
from game_state import GameStateView
from player_action import PlayerAction


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None

    def seed(self, hand: list[Card], player_id: int):
        self.hand = hand
        self.player_id = player_id

    def run(
        self,
        actions: list[PlayerAction],
        game_state: GameStateView
    ) -> PlayerAction:

        try:
            return PlayerAction.call(self.player_id)
        except Exception:
            return actions[0]

    def end_game(
        self,
        actions: list[PlayerAction],
        game_state: GameStateView
    ):
        pass