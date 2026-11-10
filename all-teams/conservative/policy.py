"""Shared decision engine for the A-E models.

One implementation, five tiers.  Tier 1 is a hand-strength heuristic; tier 2
adds Monte-Carlo equity; tier 3 adds opponent modelling; tier 4 adds mixed
strategies, board texture and MDF; tier 5 adds population exploits and
aggressive sizing.

Everything is plain data so the object survives pickling.
"""

from card_utils import Card
from player_action import PlayerAction

from botcore import (
    OpponentModel,
    board_texture,
    clamp_raise,
    equity,
    eval7,
    has_ace,
    in_position,
    is_suited,
    preflop_percentile,
    rank_of,
    suit_of,
)


SAFE = ("call", "fold")


class PokerBot:
    TIER = 2

    # Ranges, by percentile (0 is the best hand).
    OPEN = 0.62
    THREE_BET = 0.12
    DEFEND = 0.48
    FOUR_BET = 0.045
    CALL_FOUR = 0.10
    JAM_BB = 12.0

    # Postflop thresholds.
    VALUE = 0.62
    RAISE = 0.72
    BLUFF = 0.0          # bluff frequency when checked to
    MARGIN = 0.02        # extra equity required to call
    VALUE_SIZE = 0.75    # fraction of the pot for a tier 2-3 value bet
    ITERATIONS = 220
    COMMIT = True        # stack off at low SPR
    SHOVE_CLOSE = True   # round a raise to all-in when it is close
    USE_MDF = True       # defend wider against small bets
    USE_RANGE_FILTER = True  # narrow the MC with the opponent's line

    def __init__(self):
        self.hole = []
        self.player_id = None
        self.opponents = OpponentModel()
        self._hand_actions = []
        self._hand_seed = 0
        self._opp_raised = False
        self._opp_three_bet = False
        self._our_raises = 0
        self._preflop_done = False

    # ------------------------------------------------------------- lifecycle

    def seed(self, hand, player_id):
        self.hole = [self._encode(c) for c in hand]
        self.player_id = player_id
        self._hand_actions = []
        self._opp_raised = False
        self._opp_three_bet = False
        self._our_raises = 0
        self._pre_raises = 0
        self._preflop_done = False
        # Deterministic per-hand mixing without storing an RNG object.
        a, b = self.hole
        self._hand_seed = (a * 2654435761 + b * 40503 + 17) & 0xFFFFFFFF

    def run(self, actions, game_state):
        try:
            self._remember(actions, game_state)
            return self._decide(game_state)
        except Exception:
            # A fault is worse than a bad fold: never let one escape.
            try:
                me = self.player_id
                if game_state.call_amount == 0:
                    return PlayerAction.call(me)
                return PlayerAction.fold(me)
            except Exception:
                return PlayerAction.call(self.player_id)

    def end_game(self, actions, game_state):
        try:
            self._remember(actions, game_state)
            self.opponents.observe(
                self._hand_actions, game_state, self.player_id,
                game_state.teams,
            )
        except Exception:
            pass

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _encode(card):
        from botcore import encode
        return encode(card)

    def _remember(self, actions, game_state):
        if actions:
            self._hand_actions.extend(actions)
        preflop = game_state.street == "preflop" and not game_state.board
        for action in actions:
            if action.kind != "raise":
                continue
            if action.player_id == self.player_id:
                self._our_raises += 1
                if preflop:
                    self._pre_raises += 1
            elif action.player_id >= 0 and preflop:
                self._pre_raises += 1
                self._opp_raised = True
                if self._pre_raises >= 2:
                    self._opp_three_bet = True
        if not preflop:
            self._preflop_done = True

    def _rand(self, salt=0):
        """Cheap deterministic 0..1 value, stable across processes."""
        x = (self._hand_seed + salt * 1013904223 + len(self._hand_actions) * 7919)
        x ^= (x >> 13)
        x = (x * 1274126177) & 0xFFFFFFFF
        return ((x ^ (x >> 16)) & 0xFFFF) / 65535.0

    def _opponent_team(self, game_state):
        me = self.player_id
        for seat, team in enumerate(game_state.teams):
            if seat != me:
                return team
        return ""

    def _style(self, game_state):
        if self.TIER < 3:
            return "unknown"
        return self.opponents.style(self._opponent_team(game_state))

    def _facing_raise_amount(self, game_state):
        return game_state.current_bet - game_state.big_blind

    def _opponent_filter(self, game_state):
        """A hole-card filter for the MC, from the opponent's preflop line."""
        if self.TIER < 3 or game_state.num_players > 2:
            return None
        if not self.USE_RANGE_FILTER:
            return None
        if self._opp_three_bet:
            limit = 0.14
        elif self._opp_raised:
            limit = 0.42
        else:
            limit = 0.75
        style = self._style(game_state)
        if style == "station":
            limit = min(0.95, limit + 0.18)
        elif style == "nit":
            limit = max(0.08, limit - 0.16)
        elif style == "maniac":
            limit = min(0.95, limit + 0.10)

        def keep(combo):
            return preflop_percentile(combo) <= limit

        return keep

    # -------------------------------------------------------------- decisions

    def _decide(self, game_state):
        if game_state.street == "preflop":
            return self._preflop(game_state)
        return self._postflop(game_state)

    # ---------------------------------------------------------------- preflop

    def _preflop(self, game_state):
        me = self.player_id
        bb = game_state.big_blind
        pct = preflop_percentile(self.hole)
        stack_bb = game_state.stacks[me] / float(bb)
        facing = game_state.current_bet > bb
        raises_by_opp = self._opp_raised
        three_bet = self._opp_three_bet
        style = self._style(game_state)

        open_r = self.OPEN
        three_r = self.THREE_BET
        defend = self.DEFEND
        if style == "nit":
            open_r = min(0.95, open_r + 0.12)
            three_r = min(0.30, three_r + 0.05)
        elif style == "station":
            open_r = max(0.25, open_r - 0.10)
            three_r = max(0.04, three_r - 0.03)
        elif style == "maniac":
            defend = min(0.85, defend + 0.12)

        # Shallow: push/fold, which is close to optimal below ~12 bb.
        if stack_bb <= self.JAM_BB and game_state.can_raise:
            jam_pct = self._jam_threshold(stack_bb)
            if pct <= jam_pct:
                return PlayerAction.raise_to(me, game_state.max_raise_to)
            if not facing:
                return PlayerAction.call(me)
            return PlayerAction.fold(me)

        if three_bet:
            if pct <= self.FOUR_BET and game_state.can_raise:
                total = self._raise_to_bb(game_state, 2.6 * game_state.current_bet)
                return PlayerAction.raise_to(me, total)
            if pct <= self.CALL_FOUR:
                return PlayerAction.call(me)
            return PlayerAction.fold(me)

        if facing:
            # One raise (or a limp-raise) in front.
            if pct <= three_r and game_state.can_raise:
                target = max(3.0 * game_state.current_bet,
                             game_state.current_bet + 3.0 * bb)
                return PlayerAction.raise_to(
                    me, self._raise_to_bb(game_state, target))
            if pct <= defend:
                return PlayerAction.call(me)
            # Small blind completion is cheap: take a flop with a wide range.
            if (game_state.num_players == 2
                    and game_state.call_amount <= bb
                    and pct <= min(0.9, defend + 0.25)):
                return PlayerAction.call(me)
            return PlayerAction.fold(me)

        # Unraised pot: open or check.
        if game_state.call_amount == 0:
            if pct <= max(self.THREE_BET, 0.10) and game_state.can_raise:
                return PlayerAction.raise_to(
                    me, self._raise_to_bb(game_state, 3.0 * bb))
            return PlayerAction.call(me)
        if pct <= open_r:
            return PlayerAction.raise_to(
                me, self._raise_to_bb(game_state, 2.2 * bb))
        if game_state.num_players == 2:
            # Button limps a lot at 25 bb; it is cheap and keeps the pot small.
            return PlayerAction.call(me)
        return PlayerAction.fold(me)

    def _jam_threshold(self, stack_bb):
        """Percentile that jams at this depth (wider as stacks shrink)."""
        if stack_bb <= 5:
            return 0.72
        if stack_bb <= 8:
            return 0.55
        if stack_bb <= 10:
            return 0.42
        return 0.30

    def _raise_to_bb(self, game_state, desired_total):
        """Clamp a desired street total to the legal raise range."""
        return clamp_raise(int(desired_total), game_state)

    # --------------------------------------------------------------- postflop

    def _postflop(self, game_state):
        me = self.player_id
        board = [self._encode(card) for card in game_state.board]
        opponents = max(1, len(game_state.players_in_hand) - 1)
        cat = eval7(self.hole + board)[0]
        style = self._style(game_state)
        iters = self.ITERATIONS
        if self.TIER >= 4:
            iters = int(iters * 1.4)
        if self.TIER <= 1:
            eq = self._heuristic_equity(cat, board)
        else:
            eq = equity(
                self.hole, board, opponents, self._rng_for(board), iters,
                self._opponent_filter(game_state),
            )

        value = self.VALUE
        raise_at = self.RAISE
        margin = self.MARGIN
        if style == "station":
            value -= 0.08          # thinner value, no bluffs
            raise_at -= 0.04
            margin -= 0.02
        elif style == "nit":
            value += 0.03
            raise_at -= 0.02
        elif style == "maniac":
            value += 0.02
            margin += 0.06

        call = game_state.call_amount
        pot_odds = game_state.pot_odds
        can_raise = game_state.can_raise

        # Commit thresholds: at low SPR, stack off with any real equity.
        spr = game_state.spr(me)
        commit = self.COMMIT and spr < 1.2 and eq > 0.45

        if call == 0:
            if can_raise and (eq >= value or cat >= 2 or commit):
                frac = self._value_fraction(game_state, eq, board)
                return PlayerAction.raise_to(
                    me, self._size(game_state, frac))
            if can_raise and self.BLUFF > 0 and self._bluff_spot(
                    game_state, board, eq, style):
                return PlayerAction.raise_to(
                    me, self._size(game_state, 0.55))
            return PlayerAction.call(me)

        # Facing a bet.
        if can_raise and (eq >= raise_at or commit) and cat >= 0:
            if eq >= raise_at or (self.TIER >= 4 and eq >= 0.60):
                frac = self._value_fraction(game_state, eq, board)
                return PlayerAction.raise_to(
                    me, self._size(game_state, frac))

        threshold = pot_odds + margin
        if self.USE_MDF and self.TIER >= 4 and pot_odds < 0.22:
            threshold = pot_odds + 0.005   # defend vs small bets (MDF)
        if eq >= threshold:
            return PlayerAction.call(me)
        if self.TIER >= 5 and eq >= pot_odds and cat >= 1:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)

    def _rng_for(self, board):
        import random
        return random.Random(self._hand_seed ^ (len(board) * 7919))

    def _heuristic_equity(self, cat, board):
        """Tier 1: hand category mapped to a rough win share."""
        if len(board) < 3:
            return 0.5
        base = {0: 0.22, 1: 0.55, 2: 0.72, 3: 0.82, 4: 0.86,
                5: 0.90, 6: 0.94, 7: 0.97, 8: 0.99}[max(0, min(8, cat))]
        if cat == 0:
            high = max(rank_of(c) for c in self.hole)
            base += (high - 4) * 0.012
        return min(0.99, base)

    def _value_fraction(self, game_state, eq, board):
        paired, suited, connected = board_texture(board)
        if self.TIER >= 4:
            if eq > 0.85 and game_state.street in ("turn", "river"):
                return 1.15          # overbet with the nuts
            if paired or connected:
                return 0.75
            return 0.66
        return self.VALUE_SIZE

    def _size(self, game_state, fraction):
        me = self.player_id
        call = game_state.call_amount
        pot_after = game_state.pot + call
        total = game_state.bets[me] + call + int(pot_after * fraction)
        total = max(total, game_state.bets[me] + call + game_state.big_blind)
        total = clamp_raise(total, game_state)
        # Shove when the raise is already most of the stack.
        if (self.SHOVE_CLOSE
                and game_state.max_raise_to - total <= game_state.big_blind * 2):
            total = game_state.max_raise_to
        return total

    def _bluff_spot(self, game_state, board, eq, style):
        if style == "station":
            return False
        if eq > 0.40:
            return False
        freq = self.BLUFF
        if style == "nit":
            freq = min(0.8, freq * 1.6)
        if len(board) < 4:
            freq *= 0.6
        if self._rand(len(board)) > freq:
            return False
        if self.TIER >= 4:
            # Blocker bluffs: an ace or the flush suit in our hand.
            if has_ace(self.hole) or any(
                    suit_of(c) == suit_of(self.hole[0]) for c in self.hole):
                return True
            return self._rand(3 + len(board)) < 0.35
        return True
