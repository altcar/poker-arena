from card_utils import Card
from game_state import GameStateView
from player_action import PlayerAction
from evaluator import Evaluator
from tracker import OpponentTracker
from strategy import StrategyEngine


class Model:
    """
    Tournament Poker Bot.
    Combines GTO preflop tables, Monte Carlo equity rollout,
    draw potential analysis, and online opponent archetype exploitation.
    """

    def __init__(self):
        self.hand: list[Card] = []
        self.player_id: int | None = None
        self.evaluator = Evaluator(seed=1337)
        self.tracker = OpponentTracker()
        self.strategy = StrategyEngine(self.evaluator, self.tracker)

    def seed(self, hand: list[Card], player_id: int):
        """Called once before each hand: stores hole cards and seat index."""
        self.hand = hand
        self.player_id = player_id

    def run(
        self, actions: list[PlayerAction], game_state: GameStateView
    ) -> PlayerAction:
        """
        Called on our turn. Tracks opponent behavior and returns optimal action.
        """
        me = self.player_id
        try:
            # Update opponent tracking from recent actions
            opp_id = 1 - me if game_state.num_players == 2 else (me + 1) % game_state.num_players
            opp_name = game_state.teams[opp_id]

            for a in actions:
                if a.player_id == opp_id:
                    is_shove = (a.kind == "raise" and (a.amount or 0) >= 400)
                    if game_state.street == "preflop":
                        if opp_id == game_state.button:
                            self.tracker.record_preflop_sb(opp_name, a.kind, is_shove=is_shove)
                        else:
                            self.tracker.record_bb_vs_steal(opp_name, a.kind, is_shove=is_shove)
                    else:
                        if a.kind in ("fold", "call", "raise"):
                            self.tracker.record_postflop_action(opp_name, a.kind, is_shove=is_shove)

            # Decide best action
            return self.strategy.decide(self.hand, me, actions, game_state)

        except Exception:
            # Bulletproof fallback: check if free, else fold (0 faults guarantee)
            if game_state.call_amount == 0:
                return PlayerAction.call(me)
            return PlayerAction.fold(me)

    def end_game(
        self, actions: list[PlayerAction], game_state: GameStateView
    ):
        """Called after each hand: updates match tracking and stats."""
        try:
            me = self.player_id if self.player_id is not None else 0
            opp_id = 1 - me if game_state.num_players == 2 else (me + 1) % game_state.num_players
            opp_name = game_state.teams[opp_id]
            self.tracker.end_hand(opp_name)
        except Exception:
            pass
