"""
Equity-driven, opponent-adaptive no-limit hold'em bot.

Preflop: Chen-score ranges (open, call, 3-bet), and a Monte Carlo equity
check against a tight range when a big share of the stack is at stake.
Postflop: Monte Carlo equity against the opponents' likely range (narrower
when facing a big bet) compared with pot odds, value bets, rare bluffs.
Across hands (end_game / run) it counts each opponent team's raises, calls
and folds, and leans on them: bluffs folders more, calls aggressive players
wider.

Keep every attribute plain data: this object is pickled and submitted.
"""

import random

from equity import CHEN, cards_to_ints, equity
from player_action import PlayerAction

DEALER = -1


def _new_stats():
    return {"hands": 0, "raises": 0, "calls": 0, "folds": 0, "faced": 0,
            "fold_faced": 0}


class Model:
    def __init__(self):
        self.hand = []
        self.hole = []  # the hand as card ints
        self.player_id = None
        self.board_count = 0  # dealer cards seen this hand
        self.bet_open = False  # a raise this street that must be answered
        self.rng = random.Random(20261107)
        self.stats = {}  # team name -> counts, kept across hands
        self.samples = 1500  # Monte Carlo deals per decision
        self.max_seconds = 1.5  # hard cap for one equity estimate

    # -- the arena protocol --------------------------------------------------

    def seed(self, hand, player_id):
        self.hand = list(hand)
        self.hole = cards_to_ints(self.hand)
        self.player_id = player_id
        self.board_count = 0
        self.bet_open = False

    def run(self, actions, game_state):
        try:
            self._observe(actions, game_state)
        except Exception:
            pass  # learning must never cost us the move
        try:
            return self._decide(game_state)
        except Exception:
            return self._safe(game_state)

    def end_game(self, actions, game_state):
        try:
            self._observe(actions, game_state)
            for seat in range(game_state.num_players):
                if seat != self.player_id:
                    name = self._name(game_state, seat)
                    self.stats.setdefault(name, _new_stats())["hands"] += 1
        except Exception:
            pass

    # -- fallbacks and helpers -----------------------------------------------

    def _safe(self, gs):
        """Never fault: check when free, else fold."""
        try:
            if gs.call_amount == 0:
                return PlayerAction.call(self.player_id)
        except Exception:
            pass
        return PlayerAction.fold(self.player_id)

    def _raise(self, gs, total):
        """Raise to total, kept in the legal range; a call if we cannot."""
        me = self.player_id
        raise_range = gs.raise_range
        if not gs.can_raise or raise_range is None:
            return PlayerAction.call(me)
        low, high = raise_range
        return PlayerAction.raise_to(me, int(min(high, max(low, total))))

    def _name(self, gs, seat):
        """The team name at a seat (the seat number if there are none)."""
        teams = gs.teams
        if teams is None or seat >= len(teams) or teams[seat] is None:
            return str(seat)
        return teams[seat]

    def _equity(self, gs, cutoff, n_opp):
        board = cards_to_ints(list(gs.board))
        return equity(self.hole, board, n_opp, self.rng, self.samples,
                      cutoff, self.max_seconds)

    # -- learning the opponents ----------------------------------------------

    def _observe(self, actions, gs):
        me = self.player_id
        for a in actions:
            kind = a.kind
            if kind == "reveal_card":
                if a.player_id == DEALER:
                    self.board_count += 1
                    self.bet_open = False  # a new street
                continue
            if kind not in ("fold", "call", "raise"):
                continue
            if a.player_id != me:
                name = self._name(gs, a.player_id)
                st = self.stats.setdefault(name, _new_stats())
                if kind == "raise":
                    st["raises"] += 1
                elif kind == "call":
                    st["calls"] += 1
                else:
                    st["folds"] += 1
                if self.bet_open:
                    st["faced"] += 1
                    if kind == "fold":
                        st["fold_faced"] += 1
            if kind == "raise":
                self.bet_open = True

    def _profile(self, gs):
        """(raise rate, fold-to-bet rate) of the opponents still in, with a
        prior so a few observations do not swing it."""
        raises = calls = folds = faced = fold_faced = 0
        try:
            for seat in gs.players_in_hand:
                if seat == self.player_id:
                    continue
                st = self.stats.get(self._name(gs, seat))
                if st:
                    raises += st["raises"]
                    calls += st["calls"]
                    folds += st["folds"]
                    faced += st["faced"]
                    fold_faced += st["fold_faced"]
        except Exception:
            pass
        raise_rate = (raises + 3.0) / (raises + calls + folds + 12.0)
        fold_to_bet = (fold_faced + 4.0) / (faced + 12.0)
        return raise_rate, fold_to_bet

    def _bluff_prob(self, fold_to_bet):
        return min(0.40, max(0.03, 0.10 + 0.6 * (fold_to_bet - 0.35)))

    # -- decisions -----------------------------------------------------------

    def _decide(self, gs):
        if len(gs.board) == 0:
            return self._preflop(gs)
        return self._postflop(gs)

    def _preflop(self, gs):
        me = self.player_id
        bb = gs.big_blind
        score = CHEN[self.hole[0]][self.hole[1]]
        n_opp = max(1, len(gs.players_in_hand) - 1)
        owe = gs.call_amount
        behind = gs.stacks[me]
        total_stack = behind + gs.bets[me]
        raise_rate, fold_to_bet = self._profile(gs)
        facing_raise = gs.current_bet > bb
        ratio = gs.current_bet / bb

        if owe == 0:  # the big blind's option
            if score >= 8:
                return self._raise(gs, 3 * bb)
            return PlayerAction.call(me)

        # A big share of the stack is at stake: decide by equity against a
        # range that matches the size of the raise.
        if owe >= 0.30 * max(1, behind):
            cutoff = 3.0 if ratio <= 3 else (5.0 if ratio <= 10 else 7.0)
            if raise_rate > 0.40:
                cutoff -= 1.5
            elif raise_rate < 0.15:
                cutoff += 1.5
            e = self._equity(gs, cutoff, n_opp)
            if gs.can_raise and e >= 0.72:
                return self._raise(gs, gs.max_raise_to)
            if e >= gs.pot_odds + 0.03:
                return PlayerAction.call(me)
            return PlayerAction.fold(me)

        if not facing_raise:
            if total_stack <= 12 * bb:  # short: push or fold
                if score >= 6:
                    return self._raise(gs, gs.max_raise_to)
                return PlayerAction.fold(me)
            open_score = 4 if n_opp == 1 else 7
            if fold_to_bet > 0.5:
                open_score -= 1  # they fold a lot: steal wider
            if score >= open_score:
                size = 2.5 * bb if n_opp == 1 else 3 * bb
                return self._raise(gs, size)
            limp_score = 2 if n_opp == 1 else 5
            if score >= limp_score and owe <= bb:
                return PlayerAction.call(me)
            return PlayerAction.fold(me)

        # Facing a raise that is not a big share of the stack.
        if score >= 11 and gs.can_raise:
            return self._raise(gs, 3 * gs.current_bet)
        call_score = min(10.0, max(5.0, 5.0 + 0.6 * (ratio - 2)))
        if n_opp > 1:
            call_score += 1
        if raise_rate > 0.40:
            call_score -= 1  # a wide raiser
        if score >= call_score:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)

    def _postflop(self, gs):
        me = self.player_id
        n_opp = max(1, len(gs.players_in_hand) - 1)
        owe = gs.call_amount
        raise_rate, fold_to_bet = self._profile(gs)
        cutoff = None
        if owe > 0:  # a bet means a stronger range
            r = owe / max(1.0, gs.pot - owe)
            cutoff = 2.0 + 5.0 * min(r, 1.0)
            if raise_rate > 0.40:
                cutoff -= 1.5
            elif raise_rate < 0.15:
                cutoff += 1.5
        e = self._equity(gs, cutoff, n_opp)
        pot_odds = gs.pot_odds
        try:
            committed = gs.spr(me) <= 1.0
        except Exception:
            committed = False

        if committed and gs.can_raise and e >= 0.55:
            return self._raise(gs, gs.max_raise_to)

        if owe > 0:
            if (gs.can_raise and e >= 0.78 + 0.05 * (n_opp - 1)
                    and self.rng.random() < 0.85):
                size = int(0.75 * (gs.pot + owe))
                return self._raise(gs, gs.current_bet + size)
            margin = 0.0 if (committed or raise_rate > 0.40) else 0.04
            if e >= pot_odds + margin:
                return PlayerAction.call(me)
            return PlayerAction.fold(me)

        if gs.can_raise:
            if e >= 0.62 + 0.06 * (n_opp - 1):
                share = 0.75 if e >= 0.82 else 0.55
                return self._raise(gs, gs.bets[me] + int(share * gs.pot))
            if (n_opp == 1 and e < 0.40
                    and self.rng.random() < self._bluff_prob(fold_to_bet)):
                return self._raise(gs, gs.bets[me] + int(0.6 * gs.pot))
        return PlayerAction.call(me)
