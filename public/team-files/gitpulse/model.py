"""Heads-up no-limit hold'em bot.

Range-based equity with opponent modelling:
- the opponent's range is a weight per two-card combo (1326 combos),
  narrowed by every action they take, using frequencies learned per team;
- decisions compare rough expected values of fold / call / raise / shove,
  with the opponent's fold, call and raise rates learned per team.
"""

import bisect
import math
import random

from card_utils import Card
from game_state import GameStateView
from player_action import PlayerAction

from fasteval import COMBOS, card_to_int, eval7, hand_class
from preflop_data import CLASSES, EQ, EQ_VS_RANDOM

# ---------------------------------------------------------------------------
# Static tables built once at import.

CLASS_INDEX = {name: i for i, name in enumerate(CLASSES)}
COMBO_CLASS = [CLASS_INDEX[hand_class(a, b)] for a, b in COMBOS]


def _class_percentiles():
    """Per class: share of all combos that are stronger, plus half its own
    (0 = best hand, 1 = worst), ordered by equity vs a random hand."""
    sizes = [0] * len(CLASSES)
    for c in COMBO_CLASS:
        sizes[c] += 1
    order = sorted(range(len(CLASSES)), key=lambda i: -EQ_VS_RANDOM[i])
    pct = [0.0] * len(CLASSES)
    seen = 0
    for i in order:
        pct[i] = (seen + sizes[i] / 2) / 1326.0
        seen += sizes[i]
    return pct


CLASS_PCT = _class_percentiles()
COMBO_PCT = [CLASS_PCT[c] for c in COMBO_CLASS]
# Combos sorted strongest first, preflop.
PF_ORDER = sorted(range(1326), key=lambda k: COMBO_PCT[k])

# Context priors: (fold, call, raise) pseudo counts. Strength 6.
PRIOR_PF = {
    # (raises so far, facing a bet, facing all-in)
    (0, True, False): (0.25, 0.20, 0.55),   # SB first to act
    (0, False, False): (0.0, 0.70, 0.30),   # BB after a limp
    (1, True, False): (0.35, 0.45, 0.20),   # facing an open
    (2, True, False): (0.40, 0.42, 0.18),   # facing a 3-bet
    (3, True, False): (0.35, 0.55, 0.10),
}
PRIOR_ALLIN = (0.55, 0.45, 0.0)
PRIOR_POST_BET = (0.40, 0.48, 0.12)      # facing a bet postflop
PRIOR_POST_CHECK = (0.0, 0.62, 0.38)     # checked to / first: check or bet
PRIOR_STRENGTH = 6.0


def _sig(x):
    if x > 30:
        return 1.0
    if x < -30:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None
        self.rng = random.Random(0x5EED)
        # team name -> {context key: [fold, call, raise]}, plus extras
        self.stats = {}
        self._reset_hand()

    # ------------------------------------------------------------------
    # Engine hooks

    def seed(self, hand: list[Card], player_id: int):
        self.hand = hand
        self.player_id = player_id
        self._reset_hand()
        self.my_cards = [card_to_int(c) for c in hand]

    def run(
        self, actions: list[PlayerAction], game_state: GameStateView
    ) -> PlayerAction:
        try:
            self._absorb(actions, game_state)
            return self._decide(game_state)
        except Exception:
            try:
                if game_state.call_amount == 0:
                    return PlayerAction.call(self.player_id)
            except Exception:
                pass
            return PlayerAction.fold(self.player_id)

    def end_game(self, actions: list[PlayerAction], game_state: GameStateView):
        try:
            self._absorb(actions, game_state, final=True)
            self._learn_showdown(game_state)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Hand tracking

    def _reset_hand(self):
        self.log = []
        self.started = False
        self.my_cards = []
        self.board = []
        self.weights = None
        self.processed = 0          # log entries already turned into events
        self.opp_aggr = []          # streets on which opp bet/raised
        self.opp_shown = []
        self.opp_pf_raised = False
        self.opp_pf_raise_r = 1.0
        self.sim_bets = None
        self.cache = {}

    def _team_stats(self, team):
        s = self.stats.get(team)
        if s is None:
            s = {}
            self.stats[team] = s
        return s

    def _counts(self, key):
        s = self._team_stats(self.opp_team)
        c = s.get(key)
        if c is None:
            if key[0] == "pf":
                if key[3]:
                    prior = PRIOR_ALLIN
                else:
                    prior = PRIOR_PF.get(
                        (min(key[1], 3), key[2], False), (0.35, 0.5, 0.15))
                    if key[1] >= 3 and not key[2]:
                        prior = (0.0, 0.8, 0.2)
            elif key[0] == "post":
                if key[2]:
                    prior = PRIOR_ALLIN
                elif key[1]:
                    prior = PRIOR_POST_BET
                else:
                    prior = PRIOR_POST_CHECK
            else:
                prior = (0.33, 0.33, 0.34)
            c = [p * PRIOR_STRENGTH for p in prior]
            s[key] = c
        return c

    def _freqs(self, key):
        c = self._counts(key)
        t = sum(c) or 1.0
        return c[0] / t, c[1] / t, c[2] / t

    def _extra(self, name, default):
        s = self._team_stats(self.opp_team)
        if name not in s:
            s[name] = list(default)
        return s[name]

    def _absorb(self, actions, gs, final=False):
        if not self.started:
            self.started = True
            self.me = self.player_id
            self.opp = 1 - self.me if gs.num_players == 2 else (
                [p for p in range(gs.num_players) if p != self.me][0])
            self.opp_team = gs.teams[self.opp]
            self.button = gs.button
            self.sb = gs.small_blind
            self.bb = gs.big_blind
            # Pre-flop all chips are this street's bets, so this is exact
            # whenever our first run happens pre-flop (it always does).
            self.start = [gs.stacks[p] + gs.bets[p] for p in range(gs.num_players)]
            self._init_range()
            self._sim_init()
        for a in actions:
            self._sim_step(a)

    # A light replay of the betting, to classify each action.
    def _sim_init(self):
        n = len(self.start)
        self.sim_stacks = list(self.start)
        self.sim_bets = [0] * n
        self.sim_pot = 0
        self.sim_street = 0         # 0 preflop .. 3 river
        self.sim_nraises = 0        # raises this street (blinds excluded)
        self.sim_board = []
        small = self.button if n == 2 else (self.button + 1) % n
        big = (small + 1) % n
        for p, amt in ((small, self.sb), (big, self.bb)):
            amt = min(amt, self.sim_stacks[p])
            self.sim_stacks[p] -= amt
            self.sim_bets[p] += amt
            self.sim_pot += amt
        self.last_aggressor = None
        self.street_aggr = [None, None, None, None]

    def _sim_step(self, a):
        p = a.player_id
        if a.kind == "reveal_card":
            c = card_to_int(a.card)
            if p == -1:
                self.sim_board.append(c)
                self.board = list(self.sim_board)
                if len(self.sim_board) >= 3:
                    self.sim_street = len(self.sim_board) - 2
                    self.sim_bets = [0] * len(self.sim_bets)
                    self.sim_nraises = 0
            elif p == self.opp:
                self.opp_shown.append(c)
            return
        if a.kind == "win":
            return
        current = max(self.sim_bets)
        owe = min(current - self.sim_bets[p], self.sim_stacks[p])
        pot_before = self.sim_pot
        other = [q for q in range(len(self.sim_bets)) if q != p]
        facing_bet = owe > 0
        facing_allin = facing_bet and all(
            self.sim_stacks[q] == 0 for q in other if q != p)
        nr = self.sim_nraises
        street = self.sim_street
        if a.kind == "fold":
            kind = 0
        elif a.kind == "call":
            kind = 1
            self.sim_stacks[p] -= owe
            self.sim_bets[p] += owe
            self.sim_pot += owe
        else:
            kind = 2
            add = a.amount - self.sim_bets[p]
            size = (a.amount - current) / max(1, pot_before + owe)
            self.sim_stacks[p] -= add
            self.sim_bets[p] = a.amount
            self.sim_pot += add
            self.sim_nraises += 1
            self.street_aggr[street] = p
        if p == self.opp:
            self._opp_event(street, nr, facing_bet, facing_allin, kind,
                            pot_before, owe, a)

    def _opp_event(self, street, nr, facing_bet, facing_allin, kind,
                   pot_before, owe, a):
        if street == 0:
            key = ("pf", min(nr, 3), facing_bet, facing_allin)
        else:
            key = ("post", facing_bet, facing_allin)
        freqs = self._freqs(key)
        # Learn first from what they did ...
        self._counts(key)[kind] += 1.0
        if facing_bet and street > 0 and not facing_allin:
            # Bet sizes they faced (pot fraction), for elasticity.
            obs = self._extra("size_obs", (0.7, 1.0))
            obs[0] += min(3.0, owe / max(1, pot_before - owe))
            obs[1] += 1.0
        if kind == 2:
            self.opp_aggr.append(street)
            if street == 0:
                self.opp_pf_raised = True
        # ... then narrow their range with the frequencies before the update.
        if kind != 0 and self.weights is not None:
            if street == 0:
                self._narrow_pf(kind, freqs)
            else:
                self._narrow_post(kind, freqs, facing_bet)

    # ------------------------------------------------------------------
    # Ranges

    def _init_range(self):
        dead = set(self.my_cards)
        w = [0.0 if (a in dead or b in dead) else 1.0 for a, b in COMBOS]
        self.weights = w

    def _remove_board(self):
        dead = set(self.board) | set(self.my_cards)
        w = self.weights
        for k, (a, b) in enumerate(COMBOS):
            if w[k] and (a in dead or b in dead):
                w[k] = 0.0

    def _range_quantiles(self, order):
        """q per combo: weighted share of the range stronger than it plus
        half its own weight, with combos taken in `order` (strongest first)."""
        w = self.weights
        total = sum(w) or 1.0
        q = [1.0] * 1326
        seen = 0.0
        for k in order:
            wk = w[k]
            if wk <= 0:
                continue
            q[k] = (seen + wk / 2) / total
            seen += wk
        return q

    @staticmethod
    def _floor_from_share(b, r):
        """Weight kept by hands outside the top r of a raising range, so
        that bluffs make up a share b of it."""
        b = min(0.95, max(0.01, b))
        r = min(0.98, max(0.02, r))
        return min(1.0, max(0.003, b * r / ((1 - b) * (1 - r))))

    def _bluff_floor_pf(self, r):
        e = self._extra("pf_bluff", (1.5, 10.0))  # bluffs shown, shown
        return self._floor_from_share(e[0] / e[1], r)

    def _bluff_floor_post(self, r):
        e = self._extra("post_bluff", (1.5, 10.0))
        return self._floor_from_share(min(0.95, 1.3 * e[0] / e[1]), r)

    def _narrow_pf(self, kind, freqs):
        f, c, r = freqs
        q = self._range_quantiles(PF_ORDER)
        w = self.weights
        cont = max(0.02, c + r)
        if kind == 2:
            r = max(0.02, r)
            floor = self._bluff_floor_pf(r)
            self.opp_pf_raise_r = r
            for k in range(1326):
                if w[k]:
                    w[k] *= floor + (1 - floor) * _sig((r - q[k]) / 0.05)
        else:
            # Calls: the band after the raises, plus some traps, and
            # whatever they would not fold.
            rr = r
            band_end = min(1.0, r + c) if f > 0.01 else 1.0
            for k in range(1326):
                if w[k]:
                    qk = q[k]
                    keep = _sig((band_end - qk) / 0.05)
                    if qk < rr:
                        keep *= 0.45
                    w[k] *= max(0.03, keep)

    def _post_strength_order(self):
        board = self.board
        w = self.weights
        scores = []
        for k, (a, b) in enumerate(COMBOS):
            if w[k] > 0:
                scores.append((eval7([a, b] + board), k))
        scores.sort(reverse=True)
        return [k for _, k in scores]

    def _narrow_post(self, kind, freqs, facing_bet):
        self._remove_board()
        f, c, r = freqs
        order = self._post_strength_order()
        q = self._range_quantiles(order)
        w = self.weights
        if kind == 2:
            share = max(0.05, min(1.0, r))
            floor = self._bluff_floor_post(share)
            for k in order:
                w[k] *= floor + (1 - floor) * _sig((share - q[k]) / 0.07)
        elif facing_bet:
            cont = max(0.05, min(1.0, c + r))
            for k in order:
                keep = _sig((cont - q[k]) / 0.08)
                if q[k] < 0.5 * r:
                    keep *= 0.6
                w[k] *= max(0.12, keep)
        else:
            # A check: strong hands bet more often, keep some slowplays.
            for k in order:
                if q[k] < r:
                    w[k] *= 0.45
        self.cache = {}

    # ------------------------------------------------------------------
    # Equity

    def _pf_equity(self, top=1.0, order_q=None):
        """Equity vs the range, optionally only its strongest `top` share."""
        mine = CLASS_INDEX[hand_class(*self.my_cards)]
        row = EQ[mine]
        w = self.weights
        q = order_q
        num = 0.0
        den = 0.0
        for k in range(1326):
            wk = w[k]
            if wk <= 0:
                continue
            if top < 1.0 and q[k] > top:
                continue
            num += wk * row[COMBO_CLASS[k]]
            den += wk
        if den <= 0:
            return EQ_VS_RANDOM[mine]
        return num / den / 1000.0

    def _post_samples(self):
        """(q, result) samples of our equity vs their range. q is the
        combo's place in their range by current made strength."""
        key = ("post", len(self.board))
        if key in self.cache:
            return self.cache[key]
        self._remove_board()
        order = self._post_strength_order()
        q = self._range_quantiles(order)
        w = self.weights
        board = self.board
        mine = self.my_cards
        out = []
        if len(board) == 5:
            my_score = eval7(mine + board)
            for k in order:
                a, b = COMBOS[k]
                s = eval7([a, b] + board)
                res = 1.0 if my_score > s else (0.5 if my_score == s else 0.0)
                out.append((q[k], res, w[k]))
        else:
            live = [k for k in order if w[k] > 0]
            if not live:
                self.cache[key] = out
                return out
            cum = []
            t = 0.0
            for k in live:
                t += w[k]
                cum.append(t)
            dead = set(board) | set(mine)
            deck = [c for c in range(52) if c not in dead]
            need = 5 - len(board)
            rng = self.rng
            n = 1600 if need == 2 else 1200
            for _ in range(n):
                k = live[bisect.bisect_left(cum, rng.random() * t)]
                if k >= len(COMBOS):
                    continue
                a, b = COMBOS[k]
                while True:
                    run = rng.sample(deck, need)
                    if a not in run and b not in run:
                        break
                full = board + run
                ms = eval7(mine + full)
                os_ = eval7([a, b] + full)
                res = 1.0 if ms > os_ else (0.5 if ms == os_ else 0.0)
                out.append((q[k], res, 1.0))
        self.cache[key] = out
        return out

    @staticmethod
    def _eq_top(samples, lo, hi):
        """Equity vs the part of their range with lo <= q < hi."""
        num = 0.0
        den = 0.0
        for q, r, wt in samples:
            if lo <= q < hi:
                num += r * wt
                den += wt
        if den <= 0:
            return None
        return num / den

    def _eq_band(self, samples, lo, hi, fallback):
        e = self._eq_top(samples, lo, hi)
        if e is None:
            # widen
            e = self._eq_top(samples, max(0.0, lo - 0.1), min(1.01, hi + 0.1))
        return fallback if e is None else e

    # ------------------------------------------------------------------
    # Decisions

    def _act_call(self):
        return PlayerAction.call(self.player_id)

    def _act_fold(self, gs):
        if gs.call_amount == 0:
            return PlayerAction.call(self.player_id)
        return PlayerAction.fold(self.player_id)

    def _act_raise(self, gs, total):
        rr = gs.raise_range
        if rr is None:
            return PlayerAction.call(self.player_id)
        lo, hi = rr
        total = int(round(total))
        if total >= hi * 0.7 + lo * 0.3 - 1e-9 and total > lo:
            total = hi
        total = max(lo, min(hi, total))
        return PlayerAction.raise_to(self.player_id, total)

    def _decide(self, gs):
        if gs.street == "preflop":
            return self._decide_pf(gs)
        return self._decide_post(gs)

    def _response(self, key, size_frac=None):
        f, c, r = self._freqs(key)
        if size_frac is not None and key[0] == "post" and not key[2]:
            # Bigger bets get fewer calls: scale continuing by pot odds.
            obs = self._extra("size_obs", (0.7, 1.0))
            s_obs = obs[0] / obs[1]
            cont = c + r
            elastic = 0.15 + 0.6 * min(1.0, max(0.0, (1 - cont) * 2))
            po_obs = s_obs / (1 + 2 * s_obs)
            po_new = size_frac / (1 + 2 * size_frac)
            ratio = (po_obs / max(1e-6, po_new)) ** elastic
            new_cont = max(0.02, min(1.0, cont * ratio))
            scale = new_cont / max(1e-6, cont)
            c *= scale
            r *= scale
            f = 1.0 - c - r
        return f, c, r

    # -- preflop --------------------------------------------------------

    def _decide_pf(self, gs):
        me, opp = self.me, self.opp
        pot = gs.pot
        call = gs.call_amount
        my_stack = gs.stacks[me]
        opp_stack = gs.stacks[opp]
        my_bet = gs.bets[me]
        opp_bet = gs.bets[opp]
        in_position = (me == self.button)  # button acts last post-flop
        realize = 0.9 if in_position else 0.78

        q = self._range_quantiles(PF_ORDER)
        eq = self._pf_equity(1.0, q)
        nr = self.sim_nraises

        options = []
        # fold / check
        if call > 0:
            options.append((0.0, "fold", 0))
        # call / check
        if call >= my_stack:
            ev_call = eq * (pot + call) - call
        else:
            ev_call = realize * eq * (pot + call) - call
            if nr == 0 and call > 0:
                # A limp: the big blind may raise us off our equity.
                _, _, r_iso = self._freqs(("pf", 0, False, False))
                ev_call = realize * (1 - r_iso) * eq * (pot + call) - call
            if call == 0:
                ev_call = realize * eq * pot
        options.append((ev_call, "call", 0))

        if gs.can_raise:
            lo, hi = gs.raise_range
            # Shove.
            allin_total = hi
            add = allin_total - my_bet
            opp_add = min(allin_total - opp_bet, opp_stack)
            key = ("pf", min(nr + 1, 3), True, True)
            if opp_add <= 0:
                pass
            else:
                f, c, _ = self._response(key)
                cont = max(0.01, 1 - f)
                eqc = self._pf_equity(cont, q)
                ev_shove = f * pot + (1 - f) * (
                    eqc * (pot + add + opp_add) - add)
                options.append((ev_shove, "raise", allin_total))
            # A normal raise.
            if nr == 0:
                total = 2.5 * self.bb if call > 0 else 4 * self.bb
            else:
                total = opp_bet * 3 if in_position else opp_bet * 3.5
            total = max(lo, min(hi, int(total)))
            if total < hi * 0.5:
                add = total - my_bet
                opp_add = total - opp_bet
                key = ("pf", min(nr + 1, 3), True, False)
                f, c, r = self._response(key)
                cont = max(0.01, c + r)
                eq_c = self._pf_equity(min(1.0, cont), q)
                eq_r = self._pf_equity(max(0.01, r), q)
                new_pot = pot + add + opp_add
                ev_called = realize * eq_c * new_pot - add
                # Facing a re-raise: assume all-in; we call or fold then.
                full = pot + my_stack + min(opp_stack, my_stack)
                ev_reraised = max(-add, eq_r * full - my_stack)
                ev_raise = f * pot + c * ev_called + r * ev_reraised
                # Small edge for raising: initiative, simpler post-flop.
                ev_raise += 0.02 * pot
                options.append((ev_raise, "raise", total))

        options.sort(key=lambda o: o[0], reverse=True)
        best = options[0]
        if best[1] == "fold":
            return self._act_fold(gs)
        if best[1] == "call":
            return self._act_call()
        return self._act_raise(gs, best[2])

    # -- post-flop ------------------------------------------------------

    def _decide_post(self, gs):
        me, opp = self.me, self.opp
        pot = gs.pot
        call = gs.call_amount
        my_stack = gs.stacks[me]
        opp_stack = gs.stacks[opp]
        my_bet = gs.bets[me]
        opp_bet = gs.bets[opp]
        street = len(self.board) - 2  # 1 flop, 2 turn, 3 river
        in_position = (me == self.button)
        if street == 3:
            realize = 1.0
        else:
            realize = 0.97 if in_position else 0.88

        samples = self._post_samples()
        eq = self._eq_band(samples, 0.0, 1.01, 0.5)
        facing_bet = call > 0
        options = []
        if facing_bet:
            options.append((0.0, "fold", 0))
            if call >= my_stack:
                ev_call = eq * (pot + call) - call
            else:
                ev_call = realize * eq * (pot + call) - call
            options.append((ev_call, "call", 0))
        else:
            options.append((realize * eq * pot, "call", 0))

        if gs.can_raise and opp_stack > 0:
            lo, hi = gs.raise_range
            cands = set()
            base = pot + call  # pot after we call
            if facing_bet:
                cands.add(int(opp_bet + 0.8 * base))
            else:
                for frac in (0.4, 0.7, 1.1):
                    cands.add(int(frac * pot))
            cands.add(hi)
            for total in sorted(cands):
                total = max(lo, min(hi, total))
                if total < hi and total >= hi * 0.7 + lo * 0.3:
                    total = hi
                add = total - my_bet
                opp_add = min(total - opp_bet, opp_stack)
                allin = total >= hi or opp_add >= opp_stack
                size_frac = (total - max(opp_bet, my_bet)) / max(1, pot + call)
                key = ("post", True, allin)
                f, c, r = self._response(key, size_frac)
                if allin:
                    c, r = c + r, 0.0
                cont = max(0.01, c + r)
                eq_c = self._eq_band(samples, 0.0, cont, eq)
                new_pot = pot + add + opp_add
                rl = 1.0 if (allin or street == 3) else realize
                ev_called = rl * eq_c * new_pot - add
                ev_reraised = 0.0
                if r > 0:
                    eq_r = self._eq_band(samples, 0.0, max(0.02, r), eq_c)
                    full = pot + my_stack + min(opp_stack, my_stack)
                    ev_reraised = max(-add, eq_r * full - my_stack)
                ev = f * pot + c * ev_called + r * ev_reraised
                options.append((ev, "raise", total))

        options.sort(key=lambda o: o[0], reverse=True)
        best = options[0]
        # Mix in a little randomness among near-equal choices.
        if len(options) > 1 and options[1][0] > best[0] - 0.01 * pot:
            if self.rng.random() < 0.25:
                best = options[1]
        if best[1] == "fold":
            return self._act_fold(gs)
        if best[1] == "call":
            return self._act_call()
        return self._act_raise(gs, best[2])

    # ------------------------------------------------------------------
    # Learning from showdowns

    def _learn_showdown(self, gs):
        if len(self.opp_shown) != 2 or not self.started:
            return
        a, b = self.opp_shown
        if self.opp_pf_raised:
            e = self._extra("pf_bluff", (1.5, 10.0))
            # A bluff: well outside the range we gave them.
            k_pct = CLASS_PCT[CLASS_INDEX[hand_class(a, b)]]
            e[0] += 1.0 if k_pct > self.opp_pf_raise_r + 0.1 else 0.0
            e[1] += 1.0
        post_streets = [s for s in self.opp_aggr if s > 0]
        if post_streets and len(self.board) == 5:
            # Their hand's place among all hands on the final board.
            board = self.board
            dead = set(board) | {a, b}
            theirs = eval7([a, b] + board)
            better = 0
            total = 0
            for x, y in COMBOS:
                if x in dead or y in dead:
                    continue
                total += 1
                if eval7([x, y] + board) > theirs:
                    better += 1
            weak = total and better / total > 0.5
            e = self._extra("post_bluff", (1.5, 10.0))
            e[0] += 1.0 if weak else 0.0
            e[1] += 1.0
