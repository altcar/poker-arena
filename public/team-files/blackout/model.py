import random

from card_utils import Card, evaluate, full_deck
from game_state import GameStateView
from player_action import PlayerAction


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None
        self.rng = random.Random()

    def seed(self, hand: list[Card], player_id: int):
        self.hand = hand
        self.player_id = player_id

    def run(
        self, actions: list[PlayerAction], game_state: GameStateView
    ) -> PlayerAction:
        opponents = len(game_state.players_in_hand) - 1
        equity = self._equity(list(game_state.board), opponents)
        opponent_raised = any(
            action.kind == "raise" and action.player_id != self.player_id
            for action in actions
        )

        if game_state.call_amount:
            pressure = 0.10 if opponent_raised else 0.0
            call_threshold = game_state.pot_odds + 0.035 + pressure
            raise_threshold = max(0.70, game_state.pot_odds + 0.24)
            if opponent_raised:
                raise_threshold += 0.08
            if game_state.can_raise and equity >= raise_threshold:
                return self._raise(game_state, equity)
            if equity >= call_threshold:
                return PlayerAction.call(self.player_id)
            return PlayerAction.fold(self.player_id)

        threshold = 0.57 if game_state.street == "preflop" else 0.64
        if game_state.can_raise and equity >= threshold:
            return self._raise(game_state, equity)
        return PlayerAction.call(self.player_id)

    def _raise(self, game_state: GameStateView, equity: float) -> PlayerAction:
        low, high = game_state.raise_range
        unopened_preflop = (
            game_state.street == "preflop"
            and game_state.current_bet <= game_state.big_blind
        )
        if unopened_preflop:
            target = int(2.5 * game_state.big_blind)
        elif game_state.call_amount:
            target = game_state.current_bet + game_state.pot + game_state.call_amount
        else:
            target = max(game_state.big_blind, int(game_state.pot * 0.65))

        if equity >= 0.85:
            target = max(target, game_state.current_bet + game_state.pot)
        return PlayerAction.raise_to(
            self.player_id, min(high, max(low, int(target)))
        )

    def _equity(self, board: list[Card], opponents: int) -> float:
        if opponents <= 0:
            return 1.0
        unseen = [card for card in full_deck() if card not in self.hand + board]
        missing = 5 - len(board)
        samples = 240 if opponents == 1 else 120
        share = 0.0

        for _ in range(samples):
            dealt = self.rng.sample(unseen, missing + 2 * opponents)
            final_board = board + dealt[:missing]
            my_rank = evaluate(self.hand + final_board)
            opponent_cards = dealt[missing:]
            ranks = [
                evaluate(opponent_cards[i:i + 2] + final_board)
                for i in range(0, 2 * opponents, 2)
            ]
            best = max(ranks)
            if my_rank > best:
                share += 1
            elif my_rank == best:
                share += 1 / (1 + ranks.count(best))

        return share / samples

    def end_game(
        self, actions: list[PlayerAction], game_state: GameStateView
    ):
        pass
