"""
No-limit hold'em bot.

Core idea: estimate equity against a modelled opponent range (Monte Carlo
with a fast evaluator), then bet, call or fold on pot odds with margins.
The opponent's range starts as a width (the strongest fraction of all
starting hands) learned per team name across hands: how often they raise
preflop, how big, how often they fold to opens and to bets, and how
aggressive they are after the flop.
"""

import random

from card_utils import Card
from fast_eval import ALL_CARDS, card_int, eval7
from game_state import GameStateView
from player_action import PlayerAction
from preflop_table import PREFLOP_EQ


def _ranked_combos():
    """All 1326 hole-card combos, strongest preflop first."""
    combos = []
    for i, a in enumerate(ALL_CARDS):
        for b in ALL_CARDS[i + 1:]:
            va, vb = a >> 2, b >> 2
            hi, lo = (va, vb) if va >= vb else (vb, va)
            suited = hi != lo and (a & 3) == (b & 3)
            combos.append((PREFLOP_EQ[(hi, lo, suited)], a, b))
    combos.sort(reverse=True)
    return [(a, b) for _, a, b in combos]


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


# Opponent tendencies, each with a mild prior so early hands stay balanced.

def _raise_width(st, big):
    """How much of the deck their preflop raise (or big raise) stands for.

    Two estimates, and the wider wins: how often they raise (an honest
    raiser who raises X% of the time holds the top X%), and twice the mean
    preflop rank of raised hands they showed down (uniform over a top-X
    range averages X/2; random raisers average 0.5, so this widens them).
    """
    if big:
        freq = _clamp((st["pf_big"] + 1.2) / (st["pf_ops"] + 6), 0.05, 1.0)
        shown = 2 * ((st["shb_sum"] + 2 * freq) / (st["shb_n"] + 4))
    else:
        freq = _clamp((st["pf_raises"] + 2.5) / (st["pf_ops"] + 8), 0.07, 1.0)
        shown = 2 * ((st["shr_sum"] + 2 * freq) / (st["shr_n"] + 4))
    return _clamp(max(freq, min(shown, 1.0)), 0.05, 1.0)


def _raise_trust(st):
    """1.0 when their raises mean what their frequency says, smaller when
    showdowns prove they raise junk (their raises then narrow them less)."""
    freq = _clamp((st["pf_raises"] + 2.5) / (st["pf_ops"] + 8), 0.07, 1.0)
    shown = 2 * ((st["shr_sum"] + 2 * freq) / (st["shr_n"] + 4))
    if shown <= freq:
        return 1.0
    return _clamp(freq / shown, 0.35, 1.0)


def _defend_width(st):
    """How much of the deck they continue with when facing a raise."""
    conts = st["pf_face_ops"] - st["pf_folds"]
    return _clamp((conts + 3.0) / (st["pf_face_ops"] + 5), 0.3, 1.0)


def _open_fold_rate(st):
    return (st["open_folds"] + 2.0) / (st["open_ops"] + 4)


def _fold_to_bet(st):
    return (st["bet_folds"] + 1.3) / (st["bet_face_ops"] + 4)


def _post_aggr(st):
    return (st["post_raises"] + 0.8) / (st["post_ops"] + 6)


class Model:
    """Equity bot with per-team opponent modelling."""

    def __init__(self):
        self.hand: list[Card] = []
        self.player_id = None
        self.rng = random.Random(271828)
        self.ranked = _ranked_combos()
        total = len(self.ranked)
        self.rank_frac = {
            combo: i / total for i, combo in enumerate(self.ranked)
        }
        self.stats = {}
        self.hands_done = 0
        self.debug = False
        # Per-hand state, reset by seed().
        self.cards = []
        self.log = []
        self.pf_aggressor = False
        self.range_w = 1.0
        self.opp_agg = 0
        self.board_n = 0

    def seed(self, hand: list[Card], player_id: int):
        self.hand = hand
        self.player_id = player_id
        self.cards = [card_int(c) for c in hand]
        self.log = []
        self.pf_aggressor = False
        self.range_w = 1.0
        self.opp_agg = 0
        self.board_n = 0
        self.rng.seed(
            (self.cards[0] * 53 + self.cards[1]) * 100003
            + self.hands_done * 7919
            + player_id
        )

    def run(
        self, actions: list[PlayerAction], game_state: GameStateView
    ) -> PlayerAction:
        self.log.extend(actions)
        me = self.player_id
        for action in actions:
            if action.player_id == -1:
                if action.kind == "reveal_card":
                    self.board_n += 1
            elif (action.player_id != me and action.kind == "raise"
                  and self.board_n >= 3):
                self.opp_agg += 1
        try:
            kind, total = self._decide(game_state)
        except Exception:
            if self.debug:
                raise
            kind = "call"
            if game_state.call_amount > 3 * game_state.big_blind:
                kind = "fold"
            total = 0
        if kind == "raise" and game_state.can_raise:
            lo, hi = game_state.raise_range
            total = int(total)
            if total < lo:
                total = lo
            elif total > hi:
                total = hi
            if game_state.street == "preflop":
                self.pf_aggressor = True
            return PlayerAction.raise_to(me, total)
        if kind == "fold" and game_state.call_amount > 0:
            return PlayerAction.fold(me)
        return PlayerAction.call(me)

    def end_game(
        self, actions: list[PlayerAction], game_state: GameStateView
    ):
        self.hands_done += 1
        try:
            self.log.extend(actions)
            if game_state.num_players == 2:
                self._learn(game_state)
        except Exception:
            if self.debug:
                raise

    # -- decisions ---------------------------------------------------------

    def _decide(self, gs):
        if gs.num_players == 2:
            if gs.street == "preflop":
                return self._hu_preflop(gs)
            return self._hu_postflop(gs)
        return self._multiway(gs)

    def _hu_preflop(self, gs):
        me = self.player_id
        opp = 1 - me
        st = self._stat(gs, opp)
        bb = gs.big_blind
        a, b = self.cards
        va, vb = a >> 2, b >> 2
        hi, lo = (va, vb) if va >= vb else (vb, va)
        eq0 = PREFLOP_EQ[(hi, lo, hi != lo and (a & 3) == (b & 3))]
        ca = gs.call_amount
        cb = gs.current_bet
        my_stack = gs.stacks[me]

        if ca == 0:
            # Big blind with the option after a limp: their range is capped.
            if gs.can_raise and eq0 >= 0.46:
                return "raise", 4 * bb
            return "call", 0

        if cb <= bb:
            # Small blind, first in.
            if _raise_width(st, True) >= 0.40 and st["pf_ops"] >= 6:
                # They shove wide: only open hands that call a shove.
                if eq0 >= 0.50:
                    return "raise", 5 * bb // 2
                return "fold", 0
            threebet = (st["pf_reraise"] + 1.0) / (st["pf_face_raise"] + 6)
            open_thr = min(0.42 + max(0.0, threebet - 0.15) * 0.5, 0.50)
            open_any = st["open_ops"] >= 6 and _open_fold_rate(st) > 0.60
            if st["open_ops"] >= 6 and _open_fold_rate(st) < 0.15:
                # They defend almost always: size the open by strength.
                if eq0 >= open_thr:
                    size = 2.0 + 6.0 * (eq0 - 0.42)
                    return "raise", int(bb * min(size, 5.5))
                return "fold", 0
            if eq0 >= 0.68:
                return "raise", 3 * bb
            if open_any or eq0 >= open_thr:
                return "raise", 5 * bb // 2
            return "fold", 0

        # Facing a raise.
        self.pf_aggressor = False
        opp_total = gs.bets[opp] + gs.stacks[opp]
        big = cb >= 8 * bb or gs.stacks[opp] == 0 or cb * 5 >= opp_total * 3
        w = _raise_width(st, big)
        eq = self._mc([], 500, 1, self._candidates([], w, 1.0, 0.0))
        po = gs.pot_odds
        if gs.stacks[opp] == 0 or ca >= my_stack:
            # Calling closes the action or puts me all in: pure odds.
            if eq > po + 0.015:
                self.range_w = w
                return "call", 0
            return "fold", 0
        if eq >= 0.62 and gs.can_raise:
            target = 3 * cb
            if target * 20 >= (my_stack + gs.bets[me]) * 9:
                target = gs.max_raise_to
            self.range_w = _clamp(w * 0.75, 0.04, 1.0)
            return "raise", target
        if eq > po + 0.03:
            self.range_w = w
            return "call", 0
        return "fold", 0

    def _hu_postflop(self, gs):
        me = self.player_id
        opp = 1 - me
        st = self._stat(gs, opp)
        street = gs.street
        pot = gs.pot
        ca = gs.call_amount
        my_stack = gs.stacks[me]
        opp_active = gs.status(opp) == "active"

        if self.range_w >= 0.999 and self.pf_aggressor:
            self.range_w = _defend_width(st)
        # Every bet or raise they make keeps only the top of their range
        # by current board strength, more so if they are passive. A check
        # into me caps the top off instead.
        tighten = _clamp(0.3 + 1.4 * _post_aggr(st), 0.25, 1.0)
        board = [card_int(c) for c in gs.board]
        capped = 0.0
        if ca == 0 and gs.button == me:
            # They checked into me: their strongest hands are gone.
            capped = _clamp(_post_aggr(st), 0.05, 0.5)
        combos = self._candidates(
            board, self.range_w, tighten ** self.opp_agg, capped
        )
        if street == "river":
            eq = self._river(board, combos)
        else:
            eq = self._mc(board, 600, 1, combos)

        fold_rate = _fold_to_bet(st)
        seen = st["bet_face_ops"]

        if ca == 0:
            if not gs.can_raise or not opp_active:
                return "call", 0
            station = seen >= 6 and fold_rate <= 0.18
            size = 0
            if station and eq >= 0.75:
                size = 2 * pot
            elif station and eq >= 0.58:
                size = pot
            elif eq >= 0.80:
                size = pot
            elif eq >= (0.64 if street == "river" else 0.60):
                size = int(pot * (0.55 + 1.8 * (eq - 0.60)))
            elif (street == "flop" and self.pf_aggressor and eq >= 0.42
                  and (fold_rate >= 0.25 or seen < 4)):
                size = pot // 2
            elif (street == "flop" and self.pf_aggressor and eq < 0.42
                  and seen >= 4 and fold_rate >= 0.52
                  and self.rng.random() < 0.5):
                size = pot // 2  # c-bet with air: they fold to it a lot
            elif (street != "river" and not self.pf_aggressor
                  and self.opp_agg == 0 and eq >= 0.52):
                size = pot * 3 // 5  # stab: nobody has shown strength
            elif (street != "river" and seen >= 4 and fold_rate >= 0.52
                  and eq < 0.40 and self.rng.random() < 0.5):
                size = pot * 11 // 20
            elif (street == "river" and seen >= 4 and fold_rate >= 0.52
                  and eq < 0.40 and self.rng.random() < 0.55):
                size = pot * 3 // 5
            elif (street == "river" and seen >= 6 and fold_rate <= 0.15
                  and eq >= 0.56):
                size = pot // 2
            if size == 0:
                return "call", 0
            if size * 20 >= my_stack * 11:
                size = my_stack
            return "raise", gs.bets[me] + size

        po = gs.pot_odds
        if not opp_active:
            return ("call", 0) if eq > po + 0.01 else ("fold", 0)
        margin = 0.025
        if 2 * ca > pot - ca:
            margin += 0.015
        if ca > pot - ca:
            margin += 0.02
        if st["post_ops"] >= 5 and _post_aggr(st) < 0.2:
            margin += 0.05
        if 3 * ca >= 2 * (pot - ca) and _raise_trust(st) >= 0.8:
            margin += 0.04
        raise_thr = 0.82 if street == "river" else 0.76
        if eq >= raise_thr and gs.can_raise:
            cb = gs.current_bet
            target = 3 * cb if 3 * cb > cb + pot else cb + pot
            if (target - gs.bets[me]) * 20 >= my_stack * 11:
                target = gs.max_raise_to
            return "raise", target
        if ca >= my_stack:
            margin = 0.015
        if eq > po + margin:
            return "call", 0
        return "fold", 0

    def _multiway(self, gs):
        k = len(gs.players_in_hand) - 1
        if k < 1:
            return "call", 0
        board = [card_int(c) for c in gs.board]
        eq = self._mc(board, 320, k)
        ca = gs.call_amount
        pot = gs.pot
        fair = 1.0 / (k + 1)
        my_stack = gs.stacks[self.player_id]
        if ca == 0:
            threshold = 1.6 * fair
            if gs.can_raise and eq >= threshold and eq >= 0.45:
                size = pot if eq >= 0.7 else pot * 2 // 3
                if size * 20 >= my_stack * 11:
                    size = my_stack
                return "raise", gs.bets[self.player_id] + size
            return "call", 0
        po = gs.pot_odds
        if gs.can_raise and eq >= 1.9 * fair and eq >= 0.5 and eq > po + 0.1:
            return "raise", gs.current_bet + pot
        if ca >= my_stack:
            return ("call", 0) if eq > po + 0.01 else ("fold", 0)
        if eq > po + 0.03:
            return "call", 0
        return "fold", 0

    # -- equity ------------------------------------------------------------

    def _candidates(self, board, width, keep, capped):
        """The opponent's candidate combos: the strongest `width` of
        starting hands, then filtered by strength on this board: keep the
        top `keep` share when they have bet or raised, or drop the top
        `capped` share when they checked into me. None means any hand."""
        dead = set(self.cards)
        dead.update(board)
        count = int(len(self.ranked) * width)
        if count < 20:
            count = 20
        combos = [
            (a, b) for a, b in self.ranked[:count]
            if a not in dead and b not in dead
        ]
        if len(combos) < 12:
            return None
        if width >= 0.999 and keep >= 0.999 and capped <= 0.0:
            return None
        if board and (keep < 0.999 or capped > 0.0):
            combos.sort(
                key=lambda c: eval7([c[0], c[1]] + board), reverse=True
            )
            if keep < 0.999:
                combos = combos[:max(12, int(len(combos) * keep))]
            elif capped > 0.0:
                drop = int(len(combos) * capped)
                if len(combos) - drop >= 12:
                    combos = combos[drop:]
        return combos

    def _mc(self, board, samples, opps, combos=None):
        """Equity by Monte Carlo: my hand against opps opponents, drawn
        from the candidate combos when given (heads-up), else at random."""
        mine = self.cards
        dead = set(mine)
        dead.update(board)
        pool = [c for c in ALL_CARDS if c not in dead]
        need = 5 - len(board)
        rnd = self.rng.random
        wins = 0.0
        take = need + 2 * opps
        for _ in range(samples):
            cand = pool[:]
            end = len(cand)
            if combos is not None:
                a, b = combos[int(rnd() * len(combos))]
                drawn = []
                while len(drawn) < need:
                    j = int(rnd() * end)
                    c = cand[j]
                    end -= 1
                    cand[j] = cand[end]
                    if c != a and c != b:
                        drawn.append(c)
                full = board + drawn
                m = eval7(mine + full)
                t = eval7([a, b] + full)
                if m > t:
                    wins += 1.0
                elif m == t:
                    wins += 0.5
            else:
                drawn = []
                while len(drawn) < take:
                    j = int(rnd() * end)
                    c = cand[j]
                    end -= 1
                    cand[j] = cand[end]
                    drawn.append(c)
                full = board + drawn[:need]
                m = eval7(mine + full)
                best = 0
                ties = 0
                for i in range(opps):
                    o = eval7(drawn[need + 2 * i: need + 2 * i + 2] + full)
                    if o > best:
                        best = o
                        ties = 1
                    elif o == best:
                        ties += 1
                if m > best:
                    wins += 1.0
                elif m == best:
                    wins += 1.0 / (1 + ties)
        return wins / samples

    def _river(self, board, combos):
        """Exact equity on the river against the candidate combos (any
        hand when combos is None)."""
        mine = self.cards
        dead = set(mine)
        dead.update(board)
        m = eval7(mine + board)
        if combos is None:
            pool = [c for c in ALL_CARDS if c not in dead]
            combos = [
                (a, pool[j])
                for i, a in enumerate(pool)
                for j in range(i + 1, len(pool))
            ]
        wins = 0.0
        for a, b in combos:
            t = eval7([a, b] + board)
            if m > t:
                wins += 1.0
            elif m == t:
                wins += 0.5
        return wins / len(combos)

    # -- opponent modelling --------------------------------------------------

    def _stat(self, gs, opp):
        team = gs.teams[opp]
        st = self.stats.get(team)
        if st is None:
            st = {
                "hands": 0,
                "pf_ops": 0, "pf_raises": 0, "pf_big": 0,
                "pf_face_ops": 0, "pf_folds": 0,
                "pf_face_raise": 0, "pf_reraise": 0,
                "open_ops": 0, "open_folds": 0,
                "post_ops": 0, "post_raises": 0,
                "bet_face_ops": 0, "bet_folds": 0,
                "shr_sum": 0.0, "shr_n": 0,
                "shb_sum": 0.0, "shb_n": 0,
            }
            self.stats[team] = st
        return st

    def _learn(self, gs):
        """Update the opponent's counters from this hand's full action log
        (heads-up only)."""
        me = self.player_id
        opp = 1 - me
        st = self._stat(gs, opp)
        st["hands"] += 1
        bb = gs.big_blind
        bb_id = 1 - gs.button
        street_i = 0
        board_n = 0
        raise_seen = False
        my_open_pending = False
        opp_raised_pf = False
        opp_big_pf = False
        shown = []
        for action in self.log:
            pid = action.player_id
            if pid == -1:
                if action.kind == "reveal_card":
                    board_n += 1
                    if board_n in (3, 4, 5):
                        street_i += 1
                        raise_seen = False
                continue
            if action.kind == "reveal_card":
                if pid == opp:
                    shown.append(card_int(action.card))
                continue
            if action.kind == "win":
                continue
            facing = raise_seen or (street_i == 0 and pid != bb_id)
            if pid == opp:
                if street_i == 0:
                    st["pf_ops"] += 1
                    if facing:
                        st["pf_face_ops"] += 1
                        if action.kind == "fold":
                            st["pf_folds"] += 1
                    if raise_seen:
                        st["pf_face_raise"] += 1
                        if action.kind == "raise":
                            st["pf_reraise"] += 1
                    if action.kind == "raise":
                        st["pf_raises"] += 1
                        opp_raised_pf = True
                        if action.amount >= 8 * bb:
                            st["pf_big"] += 1
                            opp_big_pf = True
                else:
                    st["post_ops"] += 1
                    if action.kind == "raise":
                        st["post_raises"] += 1
                    if facing:
                        st["bet_face_ops"] += 1
                        if action.kind == "fold":
                            st["bet_folds"] += 1
                if my_open_pending:
                    st["open_ops"] += 1
                    if action.kind == "fold":
                        st["open_folds"] += 1
                    my_open_pending = False
            elif street_i == 0 and action.kind == "raise" and not raise_seen:
                my_open_pending = True
            if action.kind == "raise":
                raise_seen = True
        if opp_raised_pf and len(shown) == 2:
            a, b = shown
            frac = self.rank_frac.get((a, b))
            if frac is None:
                frac = self.rank_frac.get((b, a), 0.5)
            st["shr_sum"] += frac
            st["shr_n"] += 1
            if opp_big_pf:
                st["shb_sum"] += frac
                st["shb_n"] += 1
