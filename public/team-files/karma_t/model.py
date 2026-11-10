import random

from card_utils import Card
from game_state import GameStateView
from player_action import PlayerAction


class Model:
    """
    Your bot: change anything in this file, and add files next to it.

    This object is what gets pickled, so load weights and data in __init__
    and keep them in plain attributes.
    """

    def __init__(self):
        self.hand: list[Card] = []
        self.player_id: int | None = None

    def seed(self, hand: list[Card], player_id: int):
        """
        Called once before the hand: your two hole cards and your id.

        Store them; run() is not given them again.
        """
        self.hand = hand
        self.player_id = player_id

    def _is_trash_hand() -> bool:
        pass

    def run(
        self, actions: list[PlayerAction], game_state: GameStateView
    ) -> PlayerAction:
        """
        Called on your turn.
        """
        # 1. Check if our hole cards are unsuited 2-7 or unsuited 3-8
        is_trash = False
        if len(self.hand) == 2:
            c1, c2 = self.hand
            # c1.rank and c2.rank are integers (e.g., 2-14), c1.suit are suits
            ranks = {c1.rank, c2.rank}
            is_unsuited = (c1.suit != c2.suit)

            # Standard ranks: 2=2, 3=3, 7=7, 8=8
            if is_unsuited and (ranks == {2, 7} or ranks == {3, 8}):
                is_trash = True

        # 2. If it is a trash hand (unsuited 2-7 or 3-8), fold (or check if free)
        if is_trash:
            # If call amount is 0, we can check for free instead of folding
            if game_state.call_amount == 0:
                return PlayerAction.call(self.player_id)
            return PlayerAction.fold(self.player_id)

        if random.random() < 0.2:
            return PlayerAction.fold(self.player_id)

        # 3. Otherwise, go All-In (raise to the maximum allowed amount)
        max_raise = getattr(game_state, "max_raise_to", None)
        if game_state.can_raise and max_raise is not None and max_raise > 0:
            return PlayerAction.raise_to(self.player_id, max_raise)
        
        # Fallback if raising isn't available
        if game_state.call_amount > 0:
            return PlayerAction.call(self.player_id)
        return PlayerAction.call(self.player_id) # Acts as check when 0

    def end_game(
        self, actions: list[PlayerAction], game_state: GameStateView
    ):
        """
        Called after every hand, whether or not you were still in it.
        """
        pass