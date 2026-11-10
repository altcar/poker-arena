"""
An exploitative heads-up bot.

It counts, per opponent (by team name), what they do in each betting spot
and turns that into a hand range: a bot that 3-bets 30% of the time is
taken to 3-bet its best 30%, mixed with "any hand" as far as the hands it
shows down say it plays at random. After the flop the same idea works on
absolute hand strength on the board: the bot learns, per spot, the
strength above which the opponent bets, calls or raises. It then plays the
move with the most expected chips against that range: preflop from a
table of class-vs-class equities, after the flop by Monte Carlo.
"""

import random

import fasteval as fe
import ranges
from player_action import DEALER, PlayerAction

DEFAULTS = {
    "open_size": 2.5,  # button open raise, in big blinds
    "iso_size": 4.0,  # big blind raise over a limp, in big blinds
    "k_ip": 1.0,  # share of equity realised after the flop, in position
    "k_oop": 0.9,  # out of position
    "kslope": 0.4,  # strong hands realise more equity, weak ones less
    "kslope_post": 0.3,  # the same, for checks and calls after the flop
    "prior_n": 3.0,  # weight of the prior frequencies, in hands
    "alpha0": 0.85,  # prior: how much ranges follow hand strength
    "alpha_post": 0.8,  # the same, for bets and calls after the flop
    "alpha_check": 0.4,  # how much a check caps the range
    "eps": 0.04,  # softness of range edges
    "samples": 400,  # Monte Carlo samples per equity estimate
    "allow_limp": True,
    "bet_sizes": (0.5, 0.8),  # postflop bets as a share of the pot
    "raise_mult": 3.0,  # postflop raise size, times the bet faced
    "margin": 0.0,  # chips an aggressive move must beat passive by, x pot
    "learn_post": True,  # learn from showdowns how random their bets are
    "alpha_min": -0.6,  # below 0: their bets (or checks) mean the opposite
    "decay": 0.985,  # per hand: old observations fade, for style switchers
    "debug": False,  # raise errors instead of playing safe
}

# Prior frequencies of the opponent's actions in each spot: halfway between
# a hand-set guess and what a field of 17 typical bots did in testing.
PRIORS = {
    "sb_first": {"shove": 0.09, "raise": 0.37, "limp": 0.21, "fold": 0.33},
    "bb_vs_limp": {"shove": 0.08, "raise": 0.23, "check": 0.69},
    "bb_vs_raise": {"shove": 0.08, "raise": 0.10, "call": 0.345,
                    "fold": 0.475},
    "vs_shove_first": {"call": 0.41, "fold": 0.59},
    "vs_shove_raised": {"call": 0.64, "fold": 0.36},
    "vs_shove_limped": {"call": 0.53, "fold": 0.47},
    "vs_reraise": {"shove": 0.15, "raise": 0.05, "call": 0.45,
                   "fold": 0.35},
    "pf_open": {"bet": 0.37, "check": 0.63},
    "pf_vs_bet_s": {"raise": 0.12, "call": 0.57, "fold": 0.31},
    "pf_vs_bet_l": {"raise": 0.13, "call": 0.49, "fold": 0.38},
    "pf_vs_allin": {"call": 0.52, "fold": 0.48},
}
# Actions in each spot from the strongest hands to the weakest.
ORDERS = {
    "sb_first": ("shove", "raise", "limp", "fold"),
    "bb_vs_limp": ("shove", "raise", "check"),
    "bb_vs_raise": ("shove", "raise", "call", "fold"),
    "vs_shove_first": ("call", "fold"),
    "vs_shove_raised": ("call", "fold"),
    "vs_shove_limped": ("call", "fold"),
    "vs_reraise": ("shove", "raise", "call", "fold"),
    "pf_open": ("bet", "check"),
    "pf_vs_bet_s": ("raise", "call", "fold"),
    "pf_vs_bet_l": ("raise", "call", "fold"),
    "pf_vs_allin": ("call", "fold"),
}
BINS = 20

SIZES = fe.CLASS_SIZE
ORDER = ranges.ORDER
_DRAW_LEVEL = fe._pack(1, [4, 12, 11, 10])  # a draw ranks like a weak pair


def _ramp(x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return x


def _in_band(mid, lo, hi, eps):
    inside = 1.0
    if lo > 0.0:
        inside *= _ramp((mid - lo) / eps + 0.5)
    if hi < 1.0:
        inside *= _ramp((hi - mid) / eps + 0.5)
    return inside


def _band(freqs, order, action):
    """(lo, hi): where action's hands sit in the range, strongest at 0."""
    lo = 0.0
    for a in order:
        f = freqs.get(a, 0.0)
        if a == action:
            return lo, lo + f
        lo += f
    return 0.0, 1.0


def _narrow_classes(w, lo, hi, alpha, eps):
    """Class weights times the chance of an action taken by band lo..hi of
    the current range (ranked by preflop strength), mixed with taking it
    at random at its overall rate."""
    total = 0.0
    for j in range(169):
        total += w[j] * SIZES[j]
    if total <= 0:
        return list(w)
    rate = (1.0 - alpha) * (hi - lo)
    out = list(w)
    cum = 0.0
    for j in ORDER:
        m = w[j] * SIZES[j]
        if m <= 0:
            continue
        mid = (cum + m / 2) / total
        cum += m
        out[j] = w[j] * max(0.0, alpha * _in_band(mid, lo, hi, eps) + rate)
    return out


def _strength(a, b, board):
    """Made-hand score on board; flush and open-ended straight draws count
    as a weak pair while cards are to come."""
    value = fe.score([a, b] + board)
    if len(board) < 5 and value < _DRAW_LEVEL:
        cards = [a, b] + board
        for suit in range(4):
            n = 0
            for c in cards:
                if c & 3 == suit:
                    n += 1
            if n >= 4 and (a & 3 == suit or b & 3 == suit):
                return _DRAW_LEVEL
        mask = 0
        for c in cards:
            mask |= 1 << (c >> 2)
        hole_mask = (1 << (a >> 2)) | (1 << (b >> 2))
        for low in range(0, 9):
            window = 0b1111 << low
            if mask & window == window and hole_mask & window:
                return _DRAW_LEVEL
    return value


def _inverse(hist, target):
    """Where cumulative hist reaches target, in 0..1."""
    acc = 0.0
    for b in range(BINS):
        h = hist[b]
        if h > 0 and acc + h >= target:
            return (b + (target - acc) / h) / BINS
        acc += h
    return 1.0


class Model:
    def __init__(self, params=None):
        self.p = dict(DEFAULTS)
        if params:
            self.p.update(params)
        self.priors = {spot: dict(v) for spot, v in PRIORS.items()}
        for spot, v in self.p.pop("priors", {}).items():
            self.priors[spot] = dict(v)
        self.stats = {}  # team -> spot -> action -> count
        self.cal = {}  # team -> postflop spot -> strength histogram
        self.sd = {}  # team -> [sum of (0.5 - shown), sum of (0.5 - expected)]
        self.sdp = {}  # the same for bets and raises after the flop
        self.sdc = {}  # the same for checks after the flop
        self.rng = random.Random(20261107)
        self.hand = []
        self.player_id = None
        self.ints = []
        self.started = False
        self.tracking = False

    # -- the protocol ----------------------------------------------------

    def seed(self, hand, player_id):
        self.hand = hand
        self.player_id = player_id
        self.ints = [fe.card_int(c) for c in hand]
        self.cls = fe.class_index(self.ints[0], self.ints[1])
        self.started = False
        self.tracking = False

    def run(self, actions, game_state):
        try:
            fe.build_tables()  # once; kept out of start-up on purpose
            if not self.started:
                self._start(game_state)
            if self.tracking:
                self._consume(actions)
            if game_state.num_players != 2 or not self.tracking:
                return self._fallback(game_state)
            if game_state.street == "preflop":
                return self._preflop(game_state)
            return self._postflop(game_state)
        except Exception:
            if self.p.get("debug"):
                raise
            return self._safe(game_state)

    def end_game(self, actions, game_state):
        try:
            if not self.started and game_state.num_players == 2:
                # We never acted (the button folded at once): learn it.
                self._start(game_state, self._initial_stacks(actions,
                                                             game_state))
            if self.started and self.tracking:
                self._consume(actions)
                self._learn_showdown()
        except Exception:
            if self.p.get("debug"):
                raise

    # -- moves -----------------------------------------------------------

    def _safe(self, gs):
        try:
            if gs.call_amount == 0:
                return PlayerAction.call(self.player_id)
        except Exception:
            pass
        return PlayerAction.fold(self.player_id)

    def _raise(self, gs, total):
        if not gs.can_raise:
            return PlayerAction.call(self.player_id)
        lo, hi = gs.raise_range
        total = int(min(hi, max(lo, int(total))))
        return PlayerAction.raise_to(self.player_id, total)

    # -- hand tracking ---------------------------------------------------

    def _initial_stacks(self, actions, gs):
        """Stacks at the start of a hand we never acted in, from the final
        stacks, the pot awards and what each seat put in."""
        bets = [0, 0]
        small = gs.button
        bets[small] = gs.small_blind
        bets[1 - small] = gs.big_blind
        cur = gs.big_blind
        won = [0, 0]
        for a in actions:
            p = a.player_id
            if p not in (0, 1):
                continue
            if a.kind == "call":
                bets[p] = max(bets[p], cur)
            elif a.kind == "raise":
                bets[p] = a.amount
                cur = max(cur, a.amount)
            elif a.kind == "win":
                won[p] += a.amount
        return [gs.stacks[p] - won[p] + bets[p] for p in range(2)]

    def _start(self, gs, init=None):
        self.started = True
        n = gs.num_players
        self.tracking = n == 2 and (gs.street == "preflop" or
                                    init is not None)
        if not self.tracking:
            return
        me = self.player_id
        self.opp = 1 - me
        self.team = gs.teams[self.opp]
        if init is None:
            init = [gs.stacks[p] + gs.bets[p] for p in range(n)]
        self.t_init = init
        self.t_stack = list(init)
        self.t_bets = [0, 0]
        small = gs.button
        self._commit(small, min(gs.small_blind, init[small]))
        self._commit(1 - small, min(gs.big_blind, init[1 - small]))
        self.t_cur = max(self.t_bets)
        self.t_board = []
        self.bb = gs.big_blind
        self.button = gs.button
        self.opp_pre = []  # (spot, action) in order
        self.opp_post = []  # (board length, spot, action)
        self.opp_acted_pre = False
        self.opp_last = "none"  # their last preflop move
        self.my_last = "none"  # our last preflop move
        self.my_bucket = "pf_vs_bet_l"  # spot for their reply to our bet
        self.shown = []
        self._live_key = -1
        if self.team not in self.stats:
            self.stats[self.team] = {}
            self.cal[self.team] = {}
            self.sd[self.team] = [0.0, 0.0]
        self._fade(self.team)

    def _fade(self, team):
        """Every hand, what we learned counts a little less."""
        d = self.p["decay"]
        if d >= 1.0:
            return
        for acts in self.stats[team].values():
            for a in acts:
                acts[a] *= d
        for hist in self.cal[team].values():
            for b in range(BINS):
                hist[b] *= d
        for table in (self.sd, self.sdp, self.sdc):
            pair = table.get(team)
            if pair is not None:
                pair[0] *= d
                pair[1] *= d

    def _commit(self, p, chips):
        chips = max(0, min(chips, self.t_stack[p]))
        self.t_stack[p] -= chips
        self.t_bets[p] += chips

    def _pot(self):
        return sum(self.t_init) - sum(self.t_stack)

    def _is_shove(self, p, total):
        put = self.t_init[p] - self.t_stack[p] + (total - self.t_bets[p])
        return total >= self.t_bets[p] + self.t_stack[p] or \
            put >= 0.45 * self.t_init[p]

    def _count(self, spot, action):
        table = self.stats[self.team].setdefault(spot, {})
        table[action] = table.get(action, 0) + 1

    def _consume(self, actions):
        for a in actions:
            if a.player_id == DEALER:
                if a.kind == "reveal_card" and a.card is not None:
                    self.t_board.append(fe.card_int(a.card))
                    if len(self.t_board) in (3, 4, 5):
                        self.t_bets = [0, 0]
                        self.t_cur = 0
                continue
            if a.kind == "reveal_card":
                if a.player_id == self.opp and a.card is not None:
                    self.shown.append(fe.card_int(a.card))
                continue
            if a.kind not in ("fold", "call", "raise"):
                continue
            p = a.player_id
            if p not in (0, 1):
                continue
            if p == self.opp:
                self._classify_opp(a.kind, a.amount)
            else:
                self._note_mine(a.kind, a.amount)
            if a.kind == "fold":
                pass
            elif a.kind == "call":
                self._commit(p, self.t_cur - self.t_bets[p])
            else:
                self._commit(p, a.amount - self.t_bets[p])
                self.t_cur = max(self.t_cur, self.t_bets[p])

    def _raise_kind(self, p, amount):
        return "shove" if self._is_shove(p, amount) else "raise"

    def _classify_opp(self, kind, amount):
        o = self.opp
        if len(self.t_board) < 3:
            spot = None
            act = None
            if not self.opp_acted_pre and self.my_last == "none":
                spot = "sb_first"
                act = {"fold": "fold", "call": "limp"}.get(kind)
                if kind == "raise":
                    act = self._raise_kind(o, amount)
            elif not self.opp_acted_pre:
                if self.my_last == "limp":
                    spot = "bb_vs_limp"
                    act = "check" if kind != "raise" else \
                        self._raise_kind(o, amount)
                elif self.my_last == "raise":
                    spot = "bb_vs_raise"
                    act = {"fold": "fold", "call": "call"}.get(kind)
                    if kind == "raise":
                        act = self._raise_kind(o, amount)
                elif self.my_last == "shove":
                    spot = "vs_shove_first"
                    act = "fold" if kind == "fold" else "call"
            else:
                if self.my_last == "shove":
                    if self.opp_last in ("raise", "shove"):
                        spot = "vs_shove_raised"
                    else:
                        spot = "vs_shove_limped"
                    act = "fold" if kind == "fold" else "call"
                elif self.my_last == "raise":
                    spot = "vs_reraise"
                    act = {"fold": "fold", "call": "call"}.get(kind)
                    if kind == "raise":
                        act = self._raise_kind(o, amount)
            self.opp_acted_pre = True
            if act is not None:
                self.opp_last = act
            if spot is not None and act is not None:
                self._count(spot, act)
                if act != "fold":
                    self.opp_pre.append((spot, act))
            return
        facing = self.t_cur > self.t_bets[o]
        if not facing:
            act = "bet" if kind == "raise" else "check"
            spot = "pf_open"
        else:
            spot = self.my_bucket
            if spot == "pf_vs_allin":
                act = "fold" if kind == "fold" else "call"
            else:
                act = {"fold": "fold", "call": "call", "raise": "raise"}[kind]
        on_street = 0
        for e in self.opp_post:
            if e[0] == len(self.t_board):
                on_street += 1
        hist = self._range_hist() if on_street < 3 else None
        if hist is not None:
            table = self.cal[self.team].setdefault(spot, [0.0] * BINS)
            for b in range(BINS):
                table[b] += hist[b]
            self._count(spot, act)
        if act != "fold":
            self.opp_post.append((len(self.t_board), spot, act))

    def _note_mine(self, kind, amount):
        me = self.player_id
        if len(self.t_board) < 3:
            if kind == "call":
                if self.my_last == "none" and me == self.button and \
                        self.t_cur <= self.bb:
                    self.my_last = "limp"
                elif self.t_cur <= self.t_bets[me]:
                    self.my_last = "check"
                else:
                    self.my_last = "called"
            elif kind == "raise":
                self.my_last = self._raise_kind(me, amount)
            return
        if kind == "raise":
            o = self.opp
            pot = self._pot()
            incr = amount - self.t_cur
            allin = amount >= self.t_bets[me] + self.t_stack[me] or \
                amount - self.t_bets[o] >= self.t_stack[o]
            if allin:
                self.my_bucket = "pf_vs_allin"
            elif incr < 0.6 * max(1, pot):
                self.my_bucket = "pf_vs_bet_s"
            else:
                self.my_bucket = "pf_vs_bet_l"

    def _learn_showdown(self):
        """Shown hands tell how much the opponent's actions follow hand
        strength: compare where the shown hand ranks with where the model
        put the hands that take those actions."""
        if len(self.shown) != 2:
            return
        if self.opp_pre:
            pct = ranges.PERCENTILE[fe.class_index(self.shown[0],
                                                   self.shown[1])]
            spot, act = self.opp_pre[0]
            lo, hi = _band(self._freq(spot), ORDERS[spot], act)
            if hi - lo <= 0.85:
                sd = self.sd[self.team]
                sd[0] += 0.5 - pct
                sd[1] += 0.5 - (lo + hi) / 2
        combo = (max(self.shown), min(self.shown))
        combos, _ = self._live()
        if combo not in combos:
            return
        idx = combos.index(combo)
        sp = self.sdp.setdefault(self.team, [0.0, 0.0])
        sc = self.sdc.setdefault(self.team, [0.0, 0.0])
        for blen, spot, act in self.opp_post:
            if act not in ("bet", "raise", "check"):
                continue
            lo, hi = self._thresholds(spot)[act]
            if hi - lo > 0.85:
                continue
            table = sc if act == "check" else sp
            table[0] += 0.5 - self._ranks(blen)[idx]
            table[1] += 0.5 - (lo + hi) / 2

    # -- the opponent model ---------------------------------------------

    def _freq(self, spot):
        prior = self.priors[spot]
        counts = self.stats.get(self.team, {}).get(spot, {})
        n = 0
        for v in counts.values():
            n += v
        k = self.p["prior_n"]
        return {a: (counts.get(a, 0) + k * prior[a]) / (n + k) for a in prior}

    def _thresholds(self, spot):
        """Postflop: per action, the band of absolute hand strength (0 =
        the best hand on the board) it is taken with."""
        k = self.p["prior_n"]
        hist = [k / BINS] * BINS
        seen = self.cal.get(self.team, {}).get(spot)
        if seen is not None:
            hist = [hist[b] + seen[b] for b in range(BINS)]
        prior = self.priors[spot]
        counts = self.stats.get(self.team, {}).get(spot, {})
        out = {}
        lo = 0.0
        target = 0.0
        order = ORDERS[spot]
        for i, a in enumerate(order):
            target += counts.get(a, 0) + k * prior[a]
            hi = 1.0 if i == len(order) - 1 else _inverse(hist, target)
            out[a] = (lo, max(lo, hi))
            lo = max(lo, hi)
        return out

    def _alpha(self):
        a0 = self.p["alpha0"]
        num, den = self.sd.get(self.team, [0.0, 0.0])
        num += 3 * 0.3 * a0
        den += 3 * 0.3
        if den <= 0:
            return a0
        return max(self.p["alpha_min"], min(1.0, num / den))

    def _alpha_post(self):
        a0 = self.p["alpha_post"]
        if not self.p["learn_post"]:
            return a0
        num, den = self.sdp.get(self.team, [0.0, 0.0])
        num += 3 * 0.3 * a0
        den += 3 * 0.3
        if den <= 0:
            return a0
        return max(min(0.05, self.p["alpha_min"]), min(1.0, num / den))

    def _alpha_check(self):
        """How much a check caps their range; negative when they check
        strong hands (traps)."""
        a0 = self.p["alpha_check"]
        if not self.p["learn_post"]:
            return a0
        num, den = self.sdc.get(self.team, [0.0, 0.0])
        num -= 3 * 0.3 * a0
        den -= 3 * 0.3
        if den >= 0:
            return a0
        return max(self.p["alpha_min"], min(1.0, num / den))

    def _class_weights(self):
        w = [1.0] * 169
        alpha = self._alpha()
        eps = self.p["eps"]
        for spot, act in self.opp_pre:
            lo, hi = _band(self._freq(spot), ORDERS[spot], act)
            w = _narrow_classes(w, lo, hi, alpha, eps)
        return w

    def _sub(self, w, spot, actions):
        """Class weights of the part of range w that would take one of
        actions in spot."""
        freqs = self._freq(spot)
        lo = 0.0
        found = []
        for a in ORDERS[spot]:
            f = freqs.get(a, 0.0)
            if a in actions:
                found.append((lo, lo + f))
            lo += f
        if not found:
            return w
        lo = min(x for x, _ in found)
        hi = max(y for _, y in found)
        return _narrow_classes(w, lo, hi, self._alpha(), self.p["eps"])

    def _eq_classes(self, w):
        """Our preflop equity against class weights w, card removal
        included."""
        live = self.live_counts
        row = self.cls * 169
        table = ranges.EQ
        num = 0.0
        den = 0.0
        for j in range(169):
            m = w[j] * live[j]
            if m > 0:
                num += m * table[row + j]
                den += m
        if den <= 0:
            return 0.5
        return num / den / 10000

    # -- postflop ranges ---------------------------------------------------

    def _live(self):
        """Opponent combos that do not clash with our cards or the board,
        with their classes; cached until the board changes."""
        key = len(self.t_board)
        if self._live_key != key:
            dead = set(self.ints) | set(self.t_board)
            combos = []
            classes = []
            for idx, (x, y) in enumerate(fe.COMBOS):
                if x not in dead and y not in dead:
                    combos.append((x, y))
                    classes.append(fe.COMBO_CLASS[idx])
            self._live_combos = combos
            self._live_classes = classes
            self._rank_cache = {}
            self._live_key = key
        return self._live_combos, self._live_classes

    def _ranks(self, blen):
        """Each live combo's place among all of them by strength on the
        first blen board cards: 0 best, 1 worst, ties share a place."""
        combos, _ = self._live()
        cached = self._rank_cache.get(blen)
        if cached is not None:
            return cached
        board = self.t_board[:blen]
        s = [_strength(x, y, board) for x, y in combos]
        n = len(s)
        order = sorted(range(n), key=lambda i: -s[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            v = s[order[i]]
            while j < n and s[order[j]] == v:
                j += 1
            mid = (i + j) / 2 / n
            for k in range(i, j):
                r[order[k]] = mid
            i = j
        self._rank_cache[blen] = r
        return r

    def _range_combos(self):
        """Live opponent combos and their weights, narrowed by every
        postflop action they took this hand."""
        combos, classes = self._live()
        cw = self._class_weights()
        weights = [cw[c] for c in classes]
        eps = self.p["eps"]
        ap = self._alpha_post()
        ac = self._alpha_check()
        entries = self.opp_post
        if len(entries) > 6:
            # Long raising wars: keep each street's first and last move,
            # which bounds the work per decision.
            keep = []
            for i, e in enumerate(entries):
                first = i == 0 or entries[i - 1][0] != e[0]
                last = i == len(entries) - 1 or entries[i + 1][0] != e[0]
                if first or last:
                    keep.append(e)
            entries = keep
        for blen, spot, act in entries:
            r = self._ranks(blen)
            lo, hi = self._thresholds(spot)[act]
            rate = self._freq(spot)[act]
            alpha = min(1.0, ac) if act == "check" else ap
            mix = (1.0 - alpha) * rate
            weights = [w * max(0.0, alpha * _in_band(ri, lo, hi, eps) + mix)
                       for w, ri in zip(weights, r)]
        return combos, weights

    def _range_hist(self):
        combos, w = self._range_combos()
        r = self._ranks(len(self.t_board))
        total = sum(w)
        if total <= 0:
            return None
        hist = [0.0] * BINS
        for wi, ri in zip(w, r):
            hist[min(BINS - 1, int(ri * BINS))] += wi / total
        return hist

    def _equity(self, combos, weights):
        if len(self.t_board) == 5:
            board = self.t_board
            mine = fe.score(self.ints + board)
            won = 0.0
            total = 0.0
            for (x, y), wt in zip(combos, weights):
                if wt <= 0:
                    continue
                theirs = fe.score([x, y] + board)
                if mine > theirs:
                    won += wt
                elif mine == theirs:
                    won += wt / 2
                total += wt
            return won / total if total > 0 else 0.5
        e, _ = fe.equity_vs_combos(self.ints, self.t_board, combos, weights,
                                   self.p["samples"], self.rng)
        return e

    # -- preflop -----------------------------------------------------------

    def _k(self, base, e):
        return base * (1.0 + self.p["kslope"] * (e - 0.5))

    def _preflop(self, gs):
        me, o = self.player_id, self.opp
        bb = gs.big_blind
        self.live_counts = [0] * 169
        a, b = self.ints
        for idx, (x, y) in enumerate(fe.COMBOS):
            if x != a and x != b and y != a and y != b:
                self.live_counts[fe.COMBO_CLASS[idx]] += 1
        my_in = gs.bets[me]
        opp_in = gs.bets[o]
        pot = gs.pot
        call = gs.call_amount
        stack = gs.stacks[me]
        T = min(my_in + stack, opp_in + gs.stacks[o])  # all-in level
        w = self._class_weights()
        eq = self._eq_classes
        ip = gs.button == me
        kpos = self.p["k_ip"] if ip else self.p["k_oop"]

        def allin(e):
            return e * 2 * T - (T - my_in)

        def flop_ev(e, level):
            """Seeing a flop with both at level chips in."""
            return self._k(kpos, e) * e * 2 * level - (level - my_in)

        opts = {}
        can_raise = gs.can_raise
        if call > 0:
            opts["fold"] = 0.0
        if self.my_last == "none" and not self.opp_acted_pre and ip:
            # First in from the button.
            f = self._freq("bb_vs_limp")
            ev_check = flop_ev(eq(self._sub(w, "bb_vs_limp", ("check",))),
                               opp_in)
            raised = self._sub(w, "bb_vs_limp", ("raise", "shove"))
            ev_vs_raise = max(-(opp_in - my_in), allin(eq(raised)))
            if self.p["allow_limp"]:
                opts["call"] = f["check"] * ev_check + \
                    (f["raise"] + f["shove"]) * ev_vs_raise
            if can_raise:
                lo, hi = gs.raise_range
                r = min(hi, max(lo, int(self.p["open_size"] * bb)))
                if r < hi:
                    f = self._freq("bb_vs_raise")
                    called = self._sub(w, "bb_vs_raise", ("call",))
                    three = self._sub(w, "bb_vs_raise", ("raise", "shove"))
                    ev_three = max(-(r - my_in), allin(eq(three)))
                    opts[("raise", r)] = f["fold"] * pot + \
                        f["call"] * flop_ev(eq(called), r) + \
                        (f["raise"] + f["shove"]) * ev_three
                f = self._freq("vs_shove_first")
                called = self._sub(w, "vs_shove_first", ("call",))
                opts[("raise", hi)] = f["fold"] * pot + \
                    f["call"] * allin(eq(called))
        elif call == 0:
            # Big blind after a limp: check, raise or shove.
            opts["call"] = flop_ev(eq(w), my_in)
            if can_raise:
                lo, hi = gs.raise_range
                r = min(hi, max(lo, int(self.p["iso_size"] * bb)))
                if r < hi:
                    f = self._freq("vs_reraise")
                    called = self._sub(w, "vs_reraise", ("call",))
                    back = self._sub(w, "vs_reraise", ("raise", "shove"))
                    ev_back = max(-(r - my_in), allin(eq(back)))
                    opts[("raise", r)] = f["fold"] * pot + \
                        f["call"] * flop_ev(eq(called), r) + \
                        (f["raise"] + f["shove"]) * ev_back
                spot = "vs_shove_raised" if self.opp_last in (
                    "raise", "shove") else "vs_shove_limped"
                f = self._freq(spot)
                called = self._sub(w, spot, ("call",))
                opts[("raise", hi)] = f["fold"] * pot + \
                    f["call"] * allin(eq(called))
        else:
            # Facing a raise or an all-in.
            e = eq(w)
            opp_allin = gs.stacks[o] == 0
            after_call = stack - call
            if opp_allin or after_call <= 0 or \
                    after_call < 0.5 * (pot + call):
                opts["call"] = allin(e)
            else:
                opts["call"] = flop_ev(e, my_in + call)
            if can_raise and not opp_allin:
                lo, hi = gs.raise_range
                spot = "vs_shove_raised" if self.opp_last in (
                    "raise", "shove") else "vs_shove_limped"
                f = self._freq(spot)
                called = self._sub(w, spot, ("call",))
                opts[("raise", hi)] = f["fold"] * pot + \
                    f["call"] * allin(eq(called))
        if self.p.get("pushfold"):
            # Variant for comparison: all-in or fold, check when free.
            keep = {}
            for key, ev in opts.items():
                if key == "fold" or (key == "call" and (
                        call == 0 or call >= stack or gs.stacks[o] == 0)):
                    keep[key] = ev
                elif key != "call" and key != "fold" and \
                        key[1] >= gs.max_raise_to:
                    keep[key] = ev
            opts = keep or opts
        return self._pick(gs, opts, pot)

    def _pick(self, gs, opts, pot):
        best = None
        best_ev = None
        margin = self.p["margin"] * pot
        for key, ev in opts.items():
            if key != "fold" and key != "call":
                ev -= margin
            if best_ev is None or ev > best_ev:
                best, best_ev = key, ev
        if best is None or best == "fold":
            return self._safe(gs)
        if best == "call":
            return PlayerAction.call(self.player_id)
        return self._raise(gs, best[1])

    # -- after the flop ------------------------------------------------------

    def _postflop(self, gs):
        me, o = self.player_id, self.opp
        pot = gs.pot
        call = gs.call_amount
        combos, weights = self._range_combos()
        if sum(weights) <= 0:
            weights = [1.0] * len(combos)
        e = self._equity(combos, weights)
        r = self._ranks(len(self.t_board))
        alpha = self._alpha_post()
        eps = self.p["eps"]
        total_w = sum(weights)
        cache = {}

        def reply(bucket):
            """(chance they fold, our equity when they do not)."""
            if bucket in cache:
                return cache[bucket]
            tau = self._thresholds(bucket)["fold"][0]
            cont = 1.0 - self._freq(bucket)["fold"]
            mix = (1.0 - alpha) * cont
            wc = [w * max(0.0, alpha * _in_band(ri, 0.0, tau, eps) + mix)
                  for w, ri in zip(weights, r)]
            kept = sum(wc)
            pf = 1.0 - kept / total_w
            ec = self._equity(combos, wc) if kept > 0 else e
            cache[bucket] = (pf, ec)
            return pf, ec

        k = 1.0 + self.p["kslope_post"] * (e - 0.5)
        opts = {}
        can_raise = gs.can_raise
        opp_stack = gs.stacks[o]
        if call == 0:
            opts["call"] = k * e * pot
            if can_raise:
                lo, hi = gs.raise_range
                totals = {hi}
                for s in self.p["bet_sizes"]:
                    totals.add(min(hi, max(lo, int(s * pot))))
                for total in sorted(totals):
                    b = min(total - gs.bets[me],
                            opp_stack + gs.bets[o] - gs.bets[me])
                    if b <= 0:
                        continue
                    allin = total >= hi or b >= opp_stack
                    bucket = "pf_vs_allin" if allin else (
                        "pf_vs_bet_s" if b < 0.6 * pot else "pf_vs_bet_l")
                    pf, ec = reply(bucket)
                    opts[("raise", total)] = pf * pot + \
                        (1 - pf) * (ec * (pot + 2 * b) - b)
        else:
            opts["fold"] = 0.0
            if call >= gs.stacks[me] or opp_stack == 0:
                opts["call"] = e * (pot + call) - call
            else:
                opts["call"] = k * e * (pot + call) - call
            if can_raise and opp_stack > 0:
                lo, hi = gs.raise_range
                totals = {hi}
                mult = int(gs.current_bet * self.p["raise_mult"])
                if mult < hi:
                    totals.add(min(hi, max(lo, mult)))
                for total in sorted(totals):
                    add = min(total - gs.bets[me], call + opp_stack)
                    allin = total >= hi or add - call >= opp_stack
                    bucket = "pf_vs_allin" if allin else "pf_vs_bet_l"
                    pf, ec = reply(bucket)
                    opts[("raise", total)] = pf * pot + \
                        (1 - pf) * (ec * (pot + add + (add - call)) - add)
        return self._pick(gs, opts, pot)

    # -- more than two players (only in tests) -----------------------------

    def _fallback(self, gs):
        others = max(1, len(gs.players_in_hand) - 1)
        board = [fe.card_int(c) for c in gs.board]
        dead = set(self.ints) | set(board)
        deck = [c for c in range(52) if c not in dead]
        need = 5 - len(board)
        won = 0.0
        n = 150
        for _ in range(n):
            drawn = self.rng.sample(deck, need + 2 * others)
            full = board + drawn[:need]
            mine = fe.score(self.ints + full)
            best = 0
            for i in range(others):
                h = drawn[need + 2 * i:need + 2 * i + 2]
                best = max(best, fe.score(h + full))
            if mine > best:
                won += 1
            elif mine == best:
                won += 0.5
        e = won / n
        if e > 0.75 and gs.can_raise:
            return self._raise(gs, gs.current_bet + gs.pot)
        if gs.call_amount == 0 or e > gs.pot_odds + 0.05:
            return PlayerAction.call(self.player_id)
        return PlayerAction.fold(self.player_id)
