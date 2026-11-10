"""Pokey: our heads-up no-limit hold'em bot.

The strategy lives in pokey_strategy.py (with features.py and poker_eval.py,
all plain standard-library Python). This file only translates between the
engine's API and the strategy: it keeps the hand's action history, builds the
view the strategy reads, and turns its decision into a legal PlayerAction.
"""
from card_utils import Card
from game_state import GameStateView
from player_action import DEALER, PlayerAction

from pokey_strategy import PokeyBot


class Rec:
    """One player action as the strategy reads it."""

    def __init__(self, player_id, action, street):
        self.player_id = player_id
        self.action = action
        self.street = street


class View:
    """The fields of the hand the strategy reads, filled from GameStateView."""

    def __init__(self, model, gs):
        me = model.player_id
        self.hole_cards = [str(c) for c in model.hand]
        self.community_cards = [str(c) for c in gs.board]
        self.call_amount = gs.call_amount
        self.pot_size = gs.pot
        self.my_bet = gs.bets[me]
        self.my_stack = gs.stacks[me]
        self.can_raise = gs.can_raise
        self.can_bet = False
        self.min_raise_to = gs.min_raise_to
        self.max_raise_to = gs.max_raise_to
        self.big_blind = gs.big_blind
        self.action_history = model.history
        self.my_player_id = me
        self.dealer_index = gs.button
        self.is_dealer = gs.button == me


STREETS = {3: "flop", 4: "turn", 5: "river"}


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None
        self.history = []
        self.board_seen = 0
        self.street = "preflop"
        self.bot = PokeyBot()

    def seed(self, hand: list[Card], player_id: int):
        self.hand = hand
        self.player_id = player_id
        self.history = []
        self.board_seen = 0
        self.street = "preflop"

    def _log(self, actions):
        for a in actions:
            if a.kind == "reveal_card":
                if a.player_id == DEALER:
                    self.board_seen += 1
                    self.street = STREETS.get(self.board_seen, self.street)
            elif a.kind in ("fold", "call", "raise"):
                self.history.append(Rec(a.player_id, a.kind, self.street))

    def run(self, actions: list[PlayerAction], game_state: GameStateView) -> PlayerAction:
        me = self.player_id
        try:
            self._log(actions)
            kind, amount = self.bot.act(View(self, game_state))
            if kind == "raise" and game_state.can_raise:
                total = max(game_state.min_raise_to, min(game_state.max_raise_to, int(amount)))
                return PlayerAction.raise_to(me, total)
            if kind == "fold" and game_state.call_amount > 0:
                return PlayerAction.fold(me)
            return PlayerAction.call(me)  # calls, or checks when nothing is owed
        except Exception:
            if game_state.call_amount == 0:
                return PlayerAction.call(me)
            return PlayerAction.fold(me)

    def end_game(self, actions: list[PlayerAction], game_state: GameStateView):
        pass
