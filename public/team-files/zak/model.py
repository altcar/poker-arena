import random

from card_utils import Card
from cards import card_id
from decision import Params, choose
from game_state import GameStateView
from hand_history import HandHistory
from opponents import OpponentModel
from player_action import PlayerAction
from policy_net import PolicyNet
from strength import StrengthCache


try:  # evolved by scripts/evolve.py --export; absent until then
    from policy_weights import GENES, WEIGHTS
except ImportError:
    GENES, WEIGHTS = {}, None


class Model:
    """
    Heads-up bot. Learns a generative model of each opponent after every
    hand (opponents.py) and picks the move with the most expected chips
    against it (decision.py); preflop it falls back on the push/fold
    equilibrium (tables.py) while it knows little about the opponent.

    This object is what gets pickled, so everything here is plain data.
    """

    def __init__(self):
        self.hand = []
        self.player_id = None
        self.history = HandHistory()
        self.opponents = OpponentModel()  # persists for the whole event
        self.strengths = StrengthCache()  # per hand
        self.params = Params(**GENES)
        self.policy = PolicyNet.zero_output()
        if WEIGHTS is not None:
            self.policy.weights = list(WEIGHTS)
        self.rng = random.Random(0)
        self.hands_played = 0
        # Hands left in the event, ours included: set before pickling. It
        # counts down after every hand and sets how much busting costs.
        self.hands_remaining = 3000
        self.hands_per_opponent = 200  # hands against each team: set too
        self.net_chips = 0  # our total won (+) or lost (-) so far
        # Per team: hands played, and our net chips in hands that ended
        # without a showdown (blinds and pots given up when we fold, less
        # those it gives up): what playing safe against it does to us.
        self.team_hands: dict[str, int] = {}
        self.team_fold_net: dict[str, float] = {}
        self.debug = False  # True: errors in run() raise instead of check/fold

    def seed(self, hand: list[Card], player_id: int):
        self.hand = hand
        self.player_id = player_id
        self.history.start([card_id(c) for c in hand], player_id)
        self.strengths = StrengthCache()

    def run(
        self, actions: list[PlayerAction], game_state: GameStateView
    ) -> PlayerAction:
        try:
            self.history.observe(actions, game_state)
            return choose(game_state, self.player_id, self.history,
                          self.opponents, self.strengths, self.params,
                          self.rng, self.future_value(game_state),
                          self.policy)
        except Exception:
            if self.debug:
                raise
            # Never worse than a fault: check if free, else fold.
            if game_state.call_amount == 0:
                return PlayerAction.call(self.player_id)
            return PlayerAction.fold(self.player_id)

    def future_value(self, state) -> float:
        """
        Chips we expect to win in the rest of the event, which busting
        loses: our edge per hand (results so far, shrunk towards a prior,
        never below 0) times the hands left, capped.

        Discounted by how far playing safe would take us towards busting
        against this opponent: our net chips per hand in hands nobody showed
        down (shrunk towards 0), if negative, over the hands left against
        it. Against one that keeps stealing our blinds, folding busts us
        too, so survival is worth nothing extra.
        """
        p = self.params
        prior = p.edge_prior_bb * state.big_blind * p.edge_prior_hands
        edge = (self.net_chips + prior) / (self.hands_played + p.edge_prior_hands)
        value = min(p.future_cap_chips, max(0.0, edge) * self.hands_remaining)
        team = state.teams[1 - self.player_id]
        played = self.team_hands.get(team, 0)
        rate = self.team_fold_net.get(team, 0.0) / (played + p.bleed_prior_hands)
        bleed = max(0.0, -rate)
        left = max(0, min(self.hands_per_opponent - played, self.hands_remaining))
        stack = state.stacks[self.player_id] + state.bets[self.player_id]
        return value * max(0.0, 1.0 - bleed * left / max(1, stack))

    def end_game(
        self, actions: list[PlayerAction], game_state: GameStateView
    ):
        self.history.finish(actions, game_state)
        self.hands_played += 1
        self.hands_remaining = max(0, self.hands_remaining - 1)
        me = self.player_id
        net = game_state.stacks[me] - self.history.start_stacks[me]
        self.net_chips += net
        team = self.history.teams[self.history.opponent()]
        self.team_hands[team] = self.team_hands.get(team, 0) + 1
        if any(r.kind == "fold" for r in self.history.records):
            self.team_fold_net[team] = self.team_fold_net.get(team, 0.0) + net
        try:
            self.opponents.update(self.history, self.strengths)
        except Exception:
            if self.debug:
                raise
        self.strengths = StrengthCache()  # keep the pickle small
