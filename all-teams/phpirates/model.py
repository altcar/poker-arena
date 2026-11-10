"""
Heads-up no-limit hold'em bot (see BRIEF.md).

Everything the model keeps is plain data: ints, floats, strings, tuples,
lists, dicts and one random.Random. Cards are ints 0..51 (pk_cards); no
Card object is ever stored.
"""

import random
import time

import pk_cards
import pk_opponent
import pk_postflop
import pk_preflop
from player_action import PlayerAction

DEALER = -1
STREETS = {0: "preflop", 3: "flop", 4: "turn", 5: "river"}

# Every strategy number, in one place. Tune one at a time with
# tools/sweep.py --set key=value; a model keeps its own copy (self.params).
PARAMS = {
    # Preflop (BRIEF 5.1).
    "open_pct": 0.70,  # button: open-raise this share of hands, fold rest
    "open_size": 2.2,  # open raise, big blinds
    "bb_shove_eq": 0.58,  # BB jam vs an open of 3 bb or less, when equity
    # against their opening range is at least this
    "bb_call_pct": 0.65,  # BB defend: call with hands up to this percentile
    "bb_call_margin": 0.06,  # ... when equity > price + this
    "big_raise_bb": 8.0,  # a raise above this is treated like an all-in
    "limp_raise_pct": 0.30,  # BB vs a limp: raise this share of hands
    "limp_raise_size": 3.5,  # big blinds
    "allin_margin": 0.02,  # call an all-in when equity > price + this
    "call_margin": 0.05,  # pot-odds calls of small re-raises
    "station_call_allin": 0.60,  # they call all-ins this often: shove
    "station_shove_ev": 0.5,  # for value when it wins this many bb
    "station_limp_pct": 0.85,  # and limp these hands otherwise
    # Postflop (BRIEF 5.2).
    "post_sizes": (0.33, 0.66, 1.0),  # bet sizes, share of the pot
    "post_value_margin": 0.02,  # taken off eq_called for bets
    "post_allin_margin": 0.02,  # calling an all-in: equity > price + this
    "post_spr_allin": 1.5,  # SPR at or below this, with eq >= post_spr_eq:
    "post_spr_eq": 0.55,  # all in
    "post_bluff_eq": 0.35,  # a bet below this equity is a bluff, made only
    "post_bluff_fold": 0.50,  # when they fold to it at least this often
    "post_maniac_check_eq": 0.75,  # vs maniacs: check, don't bet, below
    "mc_budget": 0.06,  # seconds of Monte Carlo per decision
    "mc_samples": 2000,  # Monte Carlo samples at most
    "allin_seen": 5,  # all-ins they faced before their fold rate counts
    "allin_fold_unseen": 0.30,  # until then: fold to our all-in at most
    # Our image (point 7): unless they are clearly folders.
    "allin_rate_cap": 0.25,  # our preflop all-ins stay under this share
    "vpip_floor": 0.50,  # BB vs limps: raise more while VPIP is lower
    # Opponent model (BRIEF 6).
    "shrink_k": 10.0,  # a statistic counts as k chances at its prior
    "lam_n": 30.0,  # exploits are used n / (n + lam_n) of the time
    "min_seen": 15,  # chances a label needs: strong evidence only
    "stat_decay": 0.99,  # every count is multiplied by this each hand
    "size_small": 0.45,  # bet-size buckets, share of the pot: small up to
    "size_medium": 0.85,  # this, medium up to this, large above
    "eq_top_min": 0.05,  # eq_called is against at least this top share
    "fold_cap": 0.95,  # fold chances are never counted above this
    "small_open_bb": 3.0,  # BB: an open up to this many bb is "small"
    "big_open_extra": 0.03,  # BB jam vs a bigger open needs this more eq
    "commit_div": 3,  # a call of 1 / commit_div of our stack commits us
    "station_guess_drop": 0.1,  # guessed call-all-in rate: 1 - their fold
    "station_guess_min": 0.15,  # to an open - drop, kept in min..max
    "station_guess_max": 0.9,
    "station_allin_seen": 8,  # or all-ins seen, before shoving for value
    "own_seen": 20,  # our own rates count from this many hands
    # Reading their range (BRIEF 5.3).
    "range_floor": 0.03,  # a range is never narrower than this share
    "trap_limp": 0.1,  # a limp range keeps this much of the raise range
    "trap_check": 0.15,  # BB check after our limp: likewise
    "trap_call": 0.1,  # BB call of our open: likewise
    "reraise_keep": 0.45,  # a later preflop re-raise keeps their best
    "reraise_rest": 0.15,  # share at full weight, the rest times this
    "recall_keep": 0.75,  # calling our re-raise: likewise
    "recall_rest": 0.4,
    "check_top": 0.12,  # a postflop check discounts the top share
    "check_discount": 0.7,  # of their range by this factor
    # Labels (strong evidence only).
    "label_folder": 0.75,  # folds to opens and bets at least this often
    "label_station_call": 0.70,  # calls bets at least this often and
    "label_station_fold": 0.20,  # folds to small/medium bets at most this
    "label_maniac": 0.5,  # re-raises, shoves or raises at least this
    "label_maniac_bet": 0.75,  # bets when checked to at least this often
    "label_random_seen": 10,  # showdowns needed, and the bluff rate,
    "label_random_bluff": 0.40,  # for "random"
    "weak_strength": 0.5,  # a shown hand below this river strength: weak
    "noise_slack": 0.05,  # a preflop raise this far outside the range
    # Priors: a sensible player's rates (fold to an open, fold to a bet...).
    "prior_btn_fold": 0.25,
    "prior_btn_limp": 0.10,
    "prior_btn_raise": 0.60,
    "prior_btn_allin": 0.05,
    "prior_bb_fold": 0.35,
    "prior_bb_call": 0.45,
    "prior_bb_raise": 0.15,
    "prior_bb_allin": 0.05,
    "prior_limp_raise": 0.30,
    "prior_vs3b_fold": 0.50,
    "prior_vs3b_call": 0.30,
    "prior_vs3b_raise": 0.20,
    "prior_call_allin": 0.30,
    "prior_bet": 0.40,
    "prior_fold_small": 0.35,
    "prior_fold_medium": 0.40,
    "prior_fold_large": 0.45,
    "prior_fold_raise": 0.35,
    "prior_raise_vs_bet": 0.10,
    "prior_call_vs_bet": 0.50,
    "prior_bluff": 0.25,
    "prior_call_weak": 0.25,
    "prior_pf_noise": 0.10,
}


class Model:
    def __init__(self):
        self.rng = random.Random(20261107)
        self.params = dict(PARAMS)
        self.debug = False  # True (local tests only): errors are raised
        self.opp = {}  # team name -> statistics (plain dicts)
        self.hole = None  # (card int, card int)
        self.player_id = 0
        self.history = []  # (player_id, kind, amount, street) this hand
        self.board = []  # card ints
        self.shown = {}  # player_id -> [card ints] shown at showdown
        self.won = {}  # player_id -> chips won this hand

    # -- the engine's calls --------------------------------------------------

    def seed(self, hand, player_id):
        self.player_id = player_id
        self.history = []
        self.board = []
        self.shown = {}
        self.won = {}
        try:
            self.hole = (pk_cards.to_int(hand[0]), pk_cards.to_int(hand[1]))
        except Exception:
            self.hole = None  # run() then only checks or folds

    def run(self, actions, game_state):
        start = time.perf_counter()
        me = self.player_id
        try:
            if game_state.call_amount == 0:
                safe = PlayerAction.call(me)
            else:
                safe = PlayerAction.fold(me)
        except Exception:
            safe = PlayerAction.fold(me)
        try:
            self._record(actions)
            self._ready()
            if self.hole is None:
                return safe
            opp = self._opponent(game_state, me)
            if game_state.street == "preflop":
                kind, total = pk_preflop.decide(self, game_state, me, opp)
            else:
                kind, total = pk_postflop.decide(
                    self, game_state, me, opp, start)
            action = to_action(me, game_state, kind, total)
            game_state.validate_action(action)
            return action
        except Exception:
            if self.debug:
                raise
            return safe

    def end_game(self, actions, game_state):
        try:
            self._record(actions)
            self._ready()
            if game_state.num_players == 2 and self.hole is not None:
                opp = 1 - self.player_id
                stats = self.opp_stats(game_state, opp)
                pk_opponent.update(stats, self, game_state, opp)
        except Exception:
            if self.debug:
                raise
        try:
            # Every count fades a little each hand, so the statistics (and
            # the labels, worked out from them at every decision) follow an
            # opponent who changes. Nothing kept grows with hands played.
            for stats in self.opp.values():
                pk_opponent.decay(stats, self.params["stat_decay"])
        except Exception:
            if self.debug:
                raise

    def _ready(self):
        """Fills in any setting a pickle made by older code lacks (so it
        never fails on a missing key), and hands the opponent model its
        numbers from self.params."""
        for key, value in PARAMS.items():
            if key not in self.params:
                self.params[key] = value
        pk_opponent.configure(self.params)

    def opp_stats(self, gs, opp):
        """The statistics kept on the opponent's team (made on first use)."""
        name = gs.teams[opp]
        stats = self.opp.get(name)
        if stats is None:
            stats = pk_opponent.new_stats()
            self.opp[name] = stats
        return stats

    # -- per-hand bookkeeping ------------------------------------------------

    def _record(self, actions):
        """Adds the actions since our last turn to this hand's history."""
        for action in actions:
            kind = action.kind
            pid = action.player_id
            if kind == "reveal_card":
                card = pk_cards.to_int(action.card)
                if pid == DEALER:
                    self.board.append(card)
                else:
                    self.shown.setdefault(pid, []).append(card)
            elif kind == "win":
                self.won[pid] = self.won.get(pid, 0) + action.amount
            else:
                street = STREETS.get(len(self.board), "preflop")
                self.history.append((pid, kind, action.amount, street))

    def _opponent(self, gs, me):
        """The other player still in the hand (heads-up: the other seat)."""
        for p in gs.players_in_hand:
            if p != me:
                return p
        return (me + 1) % gs.num_players


def to_action(me, gs, kind, total):
    """A legal PlayerAction for (kind, total): raises are clamped to the
    legal range (all in when little would be left), a raise that is not
    allowed becomes a call, and we never fold when checking is free."""
    if kind == "raise" and gs.can_raise:
        low, high = gs.raise_range
        total = int(max(low, min(high, total)))
        if total >= 0.9 * high:
            total = high
        return PlayerAction.raise_to(me, total)
    if kind == "fold" and gs.call_amount > 0:
        return PlayerAction.fold(me)
    return PlayerAction.call(me)
