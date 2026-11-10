"""
Our bot: heads-up no-limit hold'em by expected value against a modelled
opponent.

* bot_eval.py: a fast hand evaluator (ints and bit masks).
* bot_preflop.py: precomputed equity of every starting hand against every
  other (169 x 169).
* bot_brain.py: opponent ranges and equity against them.
* bot_opp.py: what we learn about each opponent, by team name.
* bot_decide.py: the expected-value choice of move.

Everything in the model is plain data (dicts, lists, numbers and a
random.Random), so it pickles; nothing is read from or written to disk.
"""

import random
import time

from card_utils import Card
from game_state import GameStateView
from player_action import PlayerAction

from bot_brain import (
    CBIT, COMBO_BITS, COMBO_CLASS, NCOMBO, PF_PCT, PfEquity, bits_of,
    class_counts, class_of, combo_id, eval_bits, pf_update, post_equity,
    post_update, street_keys,
)
from bot_decide import choose
from bot_eval import card_int
from bot_opp import (
    bet_freq, bet_size, bluffiness, bucket_of, limp_raise_freq,
    limp_raise_size, mix_floor, new_stats, open_freqs, response,
)

TIME_BUDGET = 0.12  # seconds of Monte Carlo per decision at most
SAMPLES = 1200
MOVE_INDEX = {"fold": 0, "call": 1, "raise": 2}
STREETS = {3: "flop", 4: "turn", 5: "river"}
DEBUG = False  # local testing only: re-raise instead of falling back


class Model:
    def __init__(self):
        self.hand = []
        self.cards = []
        self.player_id = None
        self.rng = random.Random(20261108)
        self.opps = {}  # team name -> stats dict (bot_opp.new_stats)
        self.h = None  # this hand's tracking state, rebuilt every hand
        self.faults = 0  # decisions that fell back to the safe move

    # -- the engine's calls ----------------------------------------------

    def seed(self, hand: list[Card], player_id: int):
        self.hand = hand
        self.player_id = player_id
        self.cards = [card_int(c) for c in hand]
        self.h = None

    def run(self, actions: list[PlayerAction],
            game_state: GameStateView) -> PlayerAction:
        try:
            self._ingest(actions, game_state)
            kind, amount = self._decide(game_state)
            return self._legal(kind, amount, game_state)
        except Exception:
            if DEBUG:
                raise
            self.faults += 1
            return self._safe(game_state)

    def end_game(self, actions: list[PlayerAction],
                 game_state: GameStateView):
        try:
            self._ingest(actions, game_state)
            self._learn_showdown()
            h = self.h
            if h is not None and h["name"] is not None:
                self._stats(h["name"])["hands"] += 1
        except Exception:
            if DEBUG:
                raise
            self.faults += 1
        self.h = None

    # -- safety ----------------------------------------------------------

    def _safe(self, gs):
        me = self.player_id
        try:
            if gs.call_amount == 0:
                return PlayerAction.call(me)
        except Exception:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)

    def _legal(self, kind, amount, gs):
        me = self.player_id
        if kind == "raise":
            rr = gs.raise_range
            if rr is None:
                return PlayerAction.call(me)
            lo, hi = rr
            total = int(min(hi, max(lo, int(amount))))
            return PlayerAction.raise_to(me, total)
        if kind == "fold" and gs.call_amount > 0:
            return PlayerAction.fold(me)
        return PlayerAction.call(me)

    # -- tracking the hand -----------------------------------------------

    def _stats(self, name):
        s = self.opps.get(name)
        if s is None:
            s = new_stats()
            self.opps[name] = s
        return s

    def _start_hand(self, gs):
        n = gs.num_players
        me = self.player_id
        button = gs.button
        sb_seat = button if n == 2 else (button + 1) % n
        bb_seat = (sb_seat + 1) % n
        bets = [0] * n
        bets[sb_seat] = gs.small_blind
        bets[bb_seat] = gs.big_blind
        opp = None
        name = None
        if n == 2:
            opp = 1 - me
            name = gs.teams[opp]
        dead = CBIT[self.cards[0]] | CBIT[self.cards[1]]
        self.h = {
            "n": n,
            "sb_seat": sb_seat,
            "bb": gs.big_blind,
            "opp": opp,
            "name": name,
            "street": "preflop",
            "board": [],
            "bets": bets,
            "cur": gs.big_blind,
            "pot": gs.small_blind + gs.big_blind,
            "opp_acted": False,
            "opp_pf_raised": False,  # the opponent raised preflop
            "opp_limped": False,
            "opp_bet_street": False,  # bet or raised on this street
            "opp_cont": [],  # postflop streets where it called or bet
            "pfw": [1.0] * 169,
            "counts": class_counts(dead),
            "w": None,
            "u": None,
            "order": None,
            "keys_len": 0,
            "aggr": [],  # board sizes at the opponent's bets and raises
            "shown": [],
        }

    def _ingest(self, actions, gs):
        if self.h is None:
            self._start_hand(gs)
        h = self.h
        for a in actions:
            p = a.player_id
            kind = a.kind
            if kind == "reveal_card":
                if p < 0:
                    h["board"].append(card_int(a.card))
                    if len(h["board"]) in STREETS:
                        h["street"] = STREETS[len(h["board"])]
                        h["bets"] = [0] * h["n"]
                        h["cur"] = 0
                        h["opp_bet_street"] = False
                elif p == h["opp"]:
                    h["shown"].append(card_int(a.card))
                continue
            if kind == "win":
                continue
            if p == h["opp"]:
                try:
                    self._observe(h, kind, a.amount)
                except Exception:
                    if DEBUG:
                        raise
                    self.faults += 1
            # Apply the move to our running copy of the bets.
            if kind == "call":
                add = h["cur"] - h["bets"][p]
                if add > 0:
                    h["bets"][p] = h["cur"]
                    h["pot"] += add
            elif kind == "raise":
                add = a.amount - h["bets"][p]
                h["bets"][p] = a.amount
                h["cur"] = max(h["cur"], a.amount)
                h["pot"] += max(0, add)

    def _observe(self, h, kind, amount):
        """Learns from one opponent move, and narrows its range."""
        stats = self._stats(h["name"])
        b = bluffiness(stats)
        opp = h["opp"]
        c = h["cur"] - h["bets"][opp]
        pot = h["pot"]
        key = self._facing_key(h, opp)
        if kind == "raise":
            h["opp_bet_street"] = True
        if h["street"] == "preflop":
            first = not h["opp_acted"]
            h["opp_acted"] = True
            w, counts = h["pfw"], h["counts"]
            if first and opp == h["sb_seat"] and h["cur"] == h["bb"]:
                f_fold, f_limp, f_raise = open_freqs(stats)
                stats["pf_open"][MOVE_INDEX[kind]] += 1
                if kind == "call":
                    h["opp_limped"] = True
                    pf_update(w, counts, "band", f_fold, f_raise,
                              mix_floor(b, 1 - f_fold - f_raise))
                elif kind == "raise":
                    self._aggr(h, f_raise)
                    pf_update(w, counts, "top", f_raise, 0,
                              mix_floor(b, f_raise))
            elif c <= 0:
                pr = limp_raise_freq(stats)
                if kind == "raise":
                    stats["pf_limp"][1] += 1
                    stats["limp_raise"][0] += amount / h["bb"]
                    stats["limp_raise"][1] += 1
                    self._aggr(h, pr)
                    pf_update(w, counts, "top", pr, 0, mix_floor(b, pr))
                elif kind == "call":
                    stats["pf_limp"][0] += 1
                    pf_update(w, counts, "check", pr, 0, b)
            else:
                ratio = c / max(1.0, pot - c)
                pf, pc, pr = response(stats, key, ratio)
                stats["vs"][key][bucket_of(ratio)][MOVE_INDEX[kind]] += 1
                if kind == "call":
                    pf_update(w, counts, "band", pf, pr,
                              mix_floor(b, 1 - pf - pr))
                elif kind == "raise":
                    self._aggr(h, pr)
                    pf_update(w, counts, "top", pr, 0, mix_floor(b, pr))
            if kind == "raise":
                h["opp_pf_raised"] = True
            return
        self._ensure_post(h)
        if kind == "raise" or (kind == "call" and c > 0):
            if h["street"] not in h["opp_cont"]:
                h["opp_cont"].append(h["street"])
        w, order = h["w"], h["order"]
        if c <= 0:
            pb = bet_freq(stats)
            if kind == "raise":
                stats["open_post"][1] += 1
                stats["bet_ratio"][0] += min(3.0, amount / max(1, pot))
                stats["bet_ratio"][1] += 1
                self._aggr(h, pb)
                post_update(w, order, "top", pb, 0, mix_floor(b, pb))
            elif kind == "call":
                stats["open_post"][0] += 1
                post_update(w, order, "check", pb, 0, b)
        else:
            ratio = c / max(1.0, pot - c)
            pf, pc, pr = response(stats, key, ratio)
            stats["vs"][key][bucket_of(ratio)][MOVE_INDEX[kind]] += 1
            if kind == "call":
                post_update(w, order, "band", pf, pr,
                            mix_floor(b, 1 - pf - pr))
            elif kind == "raise":
                self._aggr(h, pr)
                post_update(w, order, "top", pr, 0, mix_floor(b, pr))

    def _aggr(self, h, f):
        """Notes an aggressive move: board size, and the share of hands an
        honest range would raise here (for the showdown check)."""
        h["aggr"].append((len(h["board"]), f))

    def _facing_key(self, h, opp):
        """The situation the opponent is in (or will be in, after our raise)
        when it faces a bet: see bot_opp.FOLD_PRIOR."""
        if h["street"] == "preflop":
            if h["opp_pf_raised"]:
                return "pre_3b"
            if h["opp_limped"]:
                return "pre_limp"
            return "pre_open"
        if h["opp_bet_street"]:
            return "post_raise"
        earlier = sum(1 for s in h["opp_cont"] if s != h["street"])
        return "post_bet%d" % min(2, earlier)

    def _ensure_post(self, h):
        """Combo weights and strength order for the current board."""
        board = h["board"]
        if len(board) < 3:
            return
        dead = CBIT[self.cards[0]] | CBIT[self.cards[1]] | bits_of(board)
        if h["w"] is None:
            pfw = h["pfw"]
            h["w"] = [
                0.0 if COMBO_BITS[i] & dead else pfw[COMBO_CLASS[i]]
                for i in range(NCOMBO)
            ]
        if h["keys_len"] != len(board):
            w = h["w"]
            for i in range(NCOMBO):
                if w[i] and COMBO_BITS[i] & dead:
                    w[i] = 0.0
            h["u"], h["order"] = street_keys(board, dead)
            h["keys_len"] = len(board)

    # -- deciding --------------------------------------------------------

    def _decide(self, gs):
        h = self.h
        me = self.player_id
        if h["n"] != 2 or h["opp"] is None:
            return self._multiway(gs)
        opp = h["opp"]
        street = gs.street
        board = [card_int(c) for c in gs.board]
        if board != h["board"]:  # trust the engine's board
            h["board"] = board
        stats = self._stats(h["name"])
        start = time.perf_counter()
        if street == "preflop":
            eq = PfEquity(class_of(*self.cards), h["pfw"], h["counts"])
        else:
            self._ensure_post(h)
            eq = post_equity(self.cards, board, h["w"], h["order"],
                             self.rng, SAMPLES, start + TIME_BUDGET)
        bets = gs.bets
        stacks = gs.stacks
        limp_spot = (street == "preflop" and me == h["sb_seat"]
                     and gs.current_bet <= gs.big_blind)
        look = None
        ip = me == gs.button
        if street != "preflop" and not ip and gs.call_amount == 0:
            look = {"pb": bet_freq(stats),
                    "size": bet_size(stats) * gs.pot,
                    "beta": bluffiness(stats)}
        elif limp_spot:
            look = {"pb": limp_raise_freq(stats),
                    "size": limp_raise_size(stats) * gs.big_blind,
                    "beta": bluffiness(stats)}
        kind, amount, evs = choose(
            gs.pot, gs.call_amount, bets[me], bets[opp], stacks[me],
            stacks[opp], gs.raise_range, street, ip,
            gs.big_blind, eq, stats, self._facing_key(h, opp), limp_spot,
            look,
        )
        return kind, amount

    def _multiway(self, gs):
        """More than two players: a simple equity-against-random-hands
        bot (the event is heads-up; this only has to be legal and sane)."""
        board = [card_int(c) for c in gs.board]
        others = max(1, len(gs.players_in_hand) - 1)
        base = CBIT[self.cards[0]] | CBIT[self.cards[1]]
        bb = bits_of(board)
        deck = [c for c in range(52) if not CBIT[c] & (base | bb)]
        need = 5 - len(board)
        won = 0.0
        n = 300
        for _ in range(n):
            pick = self.rng.sample(deck, need + 2 * others)
            run = bits_of(pick[:need])
            mine = eval_bits(base | bb | run)
            best = 0
            for k in range(others):
                t = eval_bits(CBIT[pick[need + 2 * k]]
                              | CBIT[pick[need + 2 * k + 1]] | bb | run)
                best = max(best, t)
            won += 1.0 if mine > best else 0.5 if mine == best else 0.0
        e = won / n
        c = gs.call_amount
        if e > 0.7 and gs.can_raise:
            return "raise", gs.pot
        if c == 0:
            return "call", 0
        if e > gs.pot_odds * 1.15:
            return "call", 0
        return "fold", 0

    # -- learning at showdown --------------------------------------------

    def _learn_showdown(self):
        h = self.h
        if h is None or h["name"] is None or len(h["shown"]) != 2:
            return
        stats = self._stats(h["name"])
        stats["showdowns"] += 1
        a, b = h["shown"]
        board = h["board"]
        keys = {}
        for size, f in h["aggr"]:
            if size < 3:
                pct = PF_PCT[class_of(a, b)]
            else:
                if size not in keys:
                    part = board[:size]
                    keys[size] = street_keys(part, bits_of(part))[0]
                pct = keys[size][combo_id(a, b)]
            stats["sd"][2] += 1
            stats["sd"][1] += max(0.0, (f - 0.5) / max(f, 0.01))
            if pct < 0.5:
                stats["sd"][0] += 1
