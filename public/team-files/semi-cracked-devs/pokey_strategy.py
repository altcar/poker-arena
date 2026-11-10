"""Pokey strategy: heads-up NLHE decisions (stdlib only).

Layers (see the blueprint):
  safety wrapper -> opponent model -> features -> policy -> legal clamp
Policy: MODE = "classifier" (trained model), "baseline" (rules) or
"hybrid" (rules vs passive bots, model vs everyone else).

Engine facts (organisers' README): pot_size includes this round's bets;
raise_to is the street total; max_raise_to is all-in; Response.call()
checks when nothing is owed; cards are like "AS", "TD".
bot.py must hold exactly ONE PokerBot subclass: set MODE for A/B copies.
"""

from __future__ import annotations

import random
import time



class Action:
    """Decisions as plain tuples; model.py turns them into PlayerActions."""

    @staticmethod
    def fold():
        return ("fold", 0)

    @staticmethod
    def call():
        return ("call", 0)

    @staticmethod
    def raise_to(total):
        return ("raise", int(total))

from poker_eval import card
from features import extract, range_equity, UNKNOWN_OPP

SKIP_WORDS = ("blind", "post", "ante")
AGGRO_ACTIONS = ("bet", "raise", "raise_to", "all_in", "allin")  # exact names: "call" contains "all"!
RAISE_FRACS = {"RAISE_H": 0.5, "RAISE_P": 1.0, "RAISE_O": 1.5}
NORMAL_BUDGET = 0.020   # seconds of Monte Carlo per decision
LOW_BUDGET = 0.004      # when we are using too much of the time bank
AVG_LIMIT = 0.030       # average seconds per decision before going low
PASSIVE_AGGR = 0.25     # below this the opponent bets only strong hands
STEAL_FOLD = 0.65       # steal preflop once they fold this often to our raises
STEAL_BB = 4.25         # steal size: above the ~3bb a call-small bot will pay
STEAL_CONTINUE = 0.60   # preflop strength needed to call a re-raise of our steal
TIGHT_RAISE = 0.35      # opponent raises (not limps) less often than this: a tight raiser
TIGHT_CALL = 0.62       # preflop strength needed to call a tight raiser
REREISE_JAM = 0.80      # preflop strength to go all-in over a re-raise of our raise
OPEN_MIN = 0.30         # button: raise first in with at least this preflop strength
LIMP_MIN = 0.22         # button: limp with at least this (below OPEN_MIN); limps lose vs good bots
BB_DEFEND = 0.0         # big blind: minimum strength to call an open
LOOSE_RERAISE = 0.35    # re-raise rate above which their re-raises are not respected
VALUE_BET = 0.55        # equity vs their calling range needed to bet for value
VALUE_BIG = 0.70        # ...to bet big
RANGE_WIDTH = 1.0       # their betting range share = 0.08 + this x postflop aggression
RANGE_SAMPLES = 120     # Monte Carlo samples for equity vs their betting range
RANGE_MARGIN = 0.02     # range equity must beat pot odds by this to continue
RANGE_RAISE = 0.65      # range equity needed to raise rather than call


def to_int(c):
    c = str(c).strip()
    if c.startswith("10"):
        c = "T" + c[2:]
    return card(c[0] + c[-1])


class OpponentModel:
    """Shrunk-to-prior stats; never trusts a handful of hands."""

    def __init__(self):
        self.tested = 0      # our non-all-in bets/raises before the river
        self.folds = 0       # ...after which we never acted again: they folded
        self.acts = 0        # opponent non-fold actions
        self.aggr = 0        # ...that were bets/raises
        self.pre_tested = 0  # our non-all-in preflop raises
        self.pre_folds = 0   # ...that ended the hand: they folded preflop
        self.post_acts = 0   # their non-fold actions after the flop
        self.post_aggr = 0   # ...that were bets/raises
        self.reraised = 0    # times they re-raised our preflop raise
        self.sb_acts = 0     # their first preflop action as the button (limp or raise; folds are hidden)
        self.sb_raises = 0   # ...that was a raise
        self.btn_hands = 0   # hands we played on the button past our first action
        self.vpip = 0        # ...where their first action was a call or raise

    def stats(self):
        def shrink(hits, n, prior, k):
            return (hits + prior * k) / (n + k)
        return {
            "fold": shrink(self.folds, self.tested, UNKNOWN_OPP["fold"], 8),
            "aggr": shrink(self.aggr, self.acts, UNKNOWN_OPP["aggr"], 20),
            "vpip": min(1.0, shrink(self.vpip, self.btn_hands, UNKNOWN_OPP["vpip"], 10)),
            "pre_fold": shrink(self.pre_folds, self.pre_tested, 0.35, 8),
            "pre_raise": shrink(self.sb_raises, self.sb_acts, 0.50, 10),
            "reraise": shrink(self.reraised, self.pre_tested, 0.15, 10),
            "post_aggr": shrink(self.post_aggr, self.post_acts, 0.35, 15),
        }


class PokeyBot:
    MODE = "baseline"

    def __init__(self):
        self.rng = random.Random(2026)
        self.opp = OpponentModel()
        self.total_time = 0.0
        self.decisions = 0
        self.cur_hole = None
        self._reset_hand()

    # ---- safety wrapper -------------------------------------------------
    def act(self, state):
        t0 = time.perf_counter()
        try:
            self._track(state)
            action = self._decide(state, t0)
        except Exception:
            action = self._safe(state)
        self.total_time += time.perf_counter() - t0
        self.decisions += 1
        return action

    def _safe(self, state):
        try:
            return self._check() if not (state.call_amount or 0) else Action.fold()
        except Exception:
            return Action.fold()

    @staticmethod
    def _check():
        return Action.call()

    # ---- hand tracking + opponent model --------------------------------
    def _reset_hand(self):
        self.seen = 0
        self.is_button = False
        self.first_opp_seen = False
        self.pending_test = False
        self.pending_pre = False
        self.stole = False
        self.raised_pre = False
        self.counted_reraise = False
        self.acted_preflop = False

    def _track(self, state):
        hole = tuple(sorted(str(c) for c in state.hole_cards))
        if hole != self.cur_hole:
            if self.pending_test:
                self.opp.folds += 1  # we bet pre-river and never acted again
            if self.pending_pre:
                self.opp.pre_folds += 1
            self.cur_hole = hole
            self._reset_hand()
        else:
            self.pending_test = False  # they answered, so they did not fold
            self.pending_pre = False

        board = state.community_cards or []
        bb = state.big_blind or 20
        if not board and not self.acted_preflop:
            self.is_button = bool(state.is_dealer)

        history = state.action_history or ()
        me = state.my_player_id
        for rec in history[self.seen:]:
            name = (rec.action or "").lower()
            if rec.player_id == me or any(w in name for w in SKIP_WORDS):
                continue
            if (not self.is_button and not self.first_opp_seen
                    and rec.street == "preflop"):
                self.first_opp_seen = True
                if name in ("call", "check") or name in AGGRO_ACTIONS:
                    self.opp.sb_acts += 1
                    if name in AGGRO_ACTIONS:
                        self.opp.sb_raises += 1
            if self.is_button and not self.first_opp_seen:
                self.first_opp_seen = True
                if name == "call" or name in AGGRO_ACTIONS:
                    self.opp.vpip += 1
            if name == "fold":
                continue
            self.opp.acts += 1
            if name in AGGRO_ACTIONS:
                self.opp.aggr += 1
            if rec.street != "preflop":
                self.opp.post_acts += 1
                if name in AGGRO_ACTIONS:
                    self.opp.post_aggr += 1
        self.seen = len(history)

    # ---- decision -------------------------------------------------------
    def _decide(self, state, t0):
        bb = float(state.big_blind or 20)
        hole = [to_int(c) for c in state.hole_cards]
        board = [to_int(c) for c in (state.community_cards or [])]
        to_call = float(state.call_amount or 0)
        pot = float(state.pot_size or 0)
        mine = float(state.my_bet or 0)
        can_raise = bool(state.can_raise)
        hi = state.max_raise_to
        my_stack = state.my_stack
        if my_stack is not None:
            stack = float(my_stack)
            to_call = min(to_call, stack)
        elif can_raise and hi:
            stack = max(float(hi) - mine, to_call)
        else:
            stack = to_call
        if not hi or stack <= to_call:
            can_raise = False

        avg = self.total_time / self.decisions if self.decisions else 0.0
        budget = NORMAL_BUDGET if avg < AVG_LIMIT else LOW_BUDGET
        opp = self.opp.stats()
        feats = extract(hole, board, pot / bb, to_call / bb, stack / bb, self.is_button,
                        opp, self.rng, eq_samples=200, deadline=t0 + budget)
        legal = [1 if to_call > 0 else 0, 1, int(can_raise), int(can_raise), int(can_raise)]

        eq_r = None
        if board and to_call > 0:
            # They bet: estimate our equity vs the hands they bet with, not a random hand.
            keep = min(0.85, max(0.12, 0.08 + RANGE_WIDTH * opp["post_aggr"]))
            keep /= 1.0 + 0.4 * min(to_call / max(pot, 1.0), 2.0)   # bigger bets, stronger ranges
            eq_r = range_equity(hole, board, keep, RANGE_SAMPLES, self.rng,
                                deadline=time.perf_counter() + budget)
        eq_v = None
        if board and to_call == 0 and can_raise:
            # Before betting: equity vs the hands that would CALL a bet (their
            # calling range is the share of hands that don't fold to our bets).
            keep = min(0.95, max(0.25, 1.0 - opp["fold"]))
            eq_v = range_equity(hole, board, keep, RANGE_SAMPLES, self.rng,
                                deadline=time.perf_counter() + budget)
        steal = self._steal(board, to_call, mine, bb, can_raise, opp, feats)
        if steal is not None:
            return self._to_action(state, steal, pot, to_call, mine, False)
        use_model = self.MODE == "classifier" or (
            # Hybrid: the rule exploits beat passive pot-odds bots (the
            # starter); the classifier beats everyone else we tested.
            self.MODE == "hybrid" and opp["aggr"] >= PASSIVE_AGGR)
        if use_model:
            choice = "CALL"  # classifier not used in this build
            if eq_r is not None and choice != "FOLD":
                # Range guard: the model sees equity vs a random hand only.
                if eq_r < feats[12] + RANGE_MARGIN:
                    choice = "FOLD"
                elif choice not in ("CALL",) and eq_r < RANGE_RAISE:
                    choice = "CALL"
        else:
            choice = baseline(feats, legal, opp, self.is_button, self.rng, eq_r, eq_v)

        if not board and not self.acted_preflop:
            self.acted_preflop = True
            if self.is_button and choice != "FOLD":
                self.opp.btn_hands += 1
        return self._to_action(state, choice, pot, to_call, mine, len(board) == 5)

    def _steal(self, board, to_call, mine, bb, can_raise, opp, feats):
        """Preflop exploit vs bots that fold most hands to a raise above ~3bb."""
        if board:
            return None
        if self.raised_pre and to_call > 0 and not self.counted_reraise:
            self.counted_reraise = True
            self.opp.reraised += 1
        if self.raised_pre and to_call > 0 and not self.stole:
            if opp["reraise"] >= LOOSE_RERAISE:
                return None  # they re-raise lots of hands: a re-raise isn't strength
            s = feats[6]
            if s >= REREISE_JAM and can_raise:
                return "ALLIN"
            return "CALL" if s >= STEAL_CONTINUE else "FOLD"
        if opp["pre_fold"] < STEAL_FOLD and not (opp["pre_raise"] < TIGHT_RAISE and self.opp.sb_acts >= 10):
            return None
        if (not self.stole and to_call > bb / 2 + 1 and opp["pre_raise"] < TIGHT_RAISE
                and self.opp.sb_acts >= 10):
            # A tight raiser (raises its top hands, limps the rest): respect the raise.
            s = feats[6]
            if s >= 0.75 and can_raise:
                return "RAISE_P"
            return "CALL" if s >= TIGHT_CALL else "FOLD"
        if (self.stole or self.raised_pre) and to_call > 0 and opp["reraise"] < LOOSE_RERAISE:
            # They re-raised our raise: a re-raising range is strong (equity vs a
            # random hand would overrate ours badly), so continue only with strength.
            s = feats[6]
            if s >= REREISE_JAM and can_raise:
                return "ALLIN"
            return "CALL" if s >= STEAL_CONTINUE else "FOLD"
        if opp["pre_fold"] < STEAL_FOLD:
            return None
        first_in = self.is_button and mine < bb and to_call <= bb / 2 + 1
        vs_limp = (not self.is_button) and to_call == 0
        if can_raise and (first_in or vs_limp):
            self.stole = True
            return "STEAL"
        return None

    def _to_action(self, state, choice, pot, to_call, mine, river):
        if choice == "FOLD":
            return Action.fold() if to_call > 0 else self._check()
        if choice == "CALL" or not state.can_raise:
            return Action.call() if to_call > 0 else self._check()
        lo = state.min_raise_to or 1
        hi = state.max_raise_to or lo
        board_now = state.community_cards or []
        if not board_now and choice in RAISE_FRACS and (
                (self.is_button and mine < float(state.big_blind or 20) and to_call <= float(state.big_blind or 20) / 2 + 1)
                or (not self.is_button and to_call == 0)):
            choice = "STEAL"  # first-in / vs-limp raises use the steal size, which also measures their folds
        if not board_now and choice in ("STEAL", "ALLIN") or (not board_now and choice in RAISE_FRACS):
            self.raised_pre = True
        if choice == "STEAL":
            target = int(STEAL_BB * float(state.big_blind or 20))
            target = int(max(min(target, hi), min(lo, hi)))
            if target < hi:
                self.pending_pre = True
                self.opp.pre_tested += 1
            return Action.raise_to(target)
        if choice == "ALLIN":
            target = hi
        else:
            target = mine + to_call + RAISE_FRACS[choice] * (pot + to_call)
        target = int(max(min(target, hi), min(lo, hi)))
        if target < hi and not river:
            self.pending_test = True
            self.opp.tested += 1
            if not (state.community_cards or []):
                self.pending_pre = True
                self.opp.pre_tested += 1
        return Action.raise_to(target)


def baseline(f, legal, opp, is_button, rng, eq_r=None, eq_v=None):
    """Rule-based EV policy from the blueprint (the version we trust)."""
    eq, strength, odds = f[5], f[6], f[12]
    facing = f[15] > 0
    call_frac = f[13] * 3.0          # to_call / pot
    spr = f[14] * 20.0
    can_raise = legal[2] == 1
    if f[0] == 1:  # preflop
        if call_frac >= 1.5:          # facing a big raise or a shove
            return "CALL" if eq > odds + 0.02 else "FOLD"
        if facing and is_button and call_frac < 0.5:   # first in from the button
            if strength >= OPEN_MIN and can_raise:
                return "RAISE_P"
            return "CALL" if strength >= LIMP_MIN else "FOLD"
        if facing:                    # big blind facing an open
            if strength >= 0.60 and can_raise:
                return "RAISE_P"
            return "CALL" if eq > odds + 0.03 and strength >= BB_DEFEND else "FOLD"
        if strength >= 0.55 and can_raise:   # big blind, limped to us
            return "RAISE_H"
        return "CALL"
    street = 1 if f[1] == 1 else (2 if f[2] == 1 else 3)
    margin = 0.01 if street == 1 else 0.03
    if call_frac >= 1.0:
        margin += 0.03
    # Exploit switches, on only with evidence (the stats are shrunk to priors).
    passive = opp["aggr"] < PASSIVE_AGGR  # bets only with strong hands
    folder = opp["fold"] > 0.50           # gives up to our bets
    if facing and eq_r is not None:
        if eq_r >= RANGE_RAISE + 0.05 and can_raise:
            return "ALLIN" if spr <= 1.5 else "RAISE_P"
        return "CALL" if eq_r >= odds + margin else "FOLD"
    if facing:
        if eq >= 0.75 and can_raise:
            return "ALLIN" if spr <= 1.5 else "RAISE_P"
        if passive:
            # A bet from a passive bot means a strong hand: equity vs a random
            # hand overstates ours, so demand much more.
            return "CALL" if eq >= max(odds + margin + 0.15, 0.70) else "FOLD"
        return "CALL" if eq >= odds + margin else "FOLD"
    if eq_v is not None and can_raise:
        # Value bets judged against the hands that call, not a random hand.
        if eq_v >= VALUE_BET and spr <= 1.5:
            return "ALLIN"
        if eq_v >= VALUE_BIG:
            return "RAISE_O" if passive else "RAISE_P"
        if eq_v >= VALUE_BET:
            return "RAISE_H"
        if passive and opp["fold"] > 0.35 and eq < 0.50 and street < 3:
            return "RAISE_H"
        if eq < 0.35 and folder and rng.random() < 0.3:
            return "RAISE_H"
        return "CALL"
    if can_raise and eq >= 0.55 and spr <= 1.5:
        return "ALLIN"
    if can_raise and eq >= 0.75:
        # Pot-odds bots call any pair whatever the size: value-bet big.
        return "RAISE_O" if passive else "RAISE_P"
    if can_raise and eq >= (0.55 if opp["fold"] < 0.2 else 0.60):
        return "RAISE_H"
    if can_raise and passive and opp["fold"] > 0.35 and eq < 0.50 and street < 3:
        # ...and fold every unpaired hand whatever the size: bluff small.
        return "RAISE_H"
    if can_raise and eq < 0.35 and folder and rng.random() < 0.3:
        return "RAISE_H"
    return "CALL"
