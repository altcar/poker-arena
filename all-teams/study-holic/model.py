import math

from card_utils import Card, evaluate
from game_state import GameStateView
from player_action import PlayerAction

import pokerlib as P
from preflop_table import PREFLOP

# --- module-level precomputation (not pickled) ---------------------------
_COMBOS = P.ALL_COMBOS
_PF_EQ = [PREFLOP[P.hand_class(a, b)] for a, b in _COMBOS]


def _percentiles(values):
    """Percentile in [0, 1] of each value, 1 = best (ties share)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    n = len(values)
    pct = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        p = ((i + j) / 2 + 0.5) / n
        for k in range(i, j + 1):
            pct[order[k]] = p
        i = j + 1
    return pct


_PF_PCT = _percentiles(_PF_EQ)

# Prior frequencies (fold, call, raise, big raise share of raises) and weight.
_PRIORS = {
    "pf_open": (0.25, 0.25, 0.50),
    "pf_vs_open": (0.40, 0.40, 0.20),
    "pf_vs_limp": (0.0, 0.65, 0.35),
    "pf_vs_3bet": (0.45, 0.35, 0.20),
    "post_first": (0.0, 0.55, 0.45),
    "post_vs_bet": (0.40, 0.45, 0.15),
    "pf_vs_shove": (0.55, 0.45, 0.0),
}
_PRIOR_N = 6.0
_STREETS = {"preflop": 0, "flop": 1, "turn": 2, "river": 3}

PARAMS = {
    "vthr": 0.60,
    "vthr_river": 0.58,
    "bluff": 1.0,
    "pf_raise_thr": 0.60,
    "call_margin_bb": 0.03,
    "post_raise_thr": 0.78,
    "samples": 1000,
    "implied": 0.15,
}


def _soft_top(pct, f, width=0.07, floor=0.02):
    """Weight for a hand at percentile pct when the action is taken with the
    top f of the range."""
    thr = 1.0 - f
    if pct >= thr:
        return 1.0
    return max(floor, math.exp(-(thr - pct) / width))


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None
        self.stats = {}  # team -> key -> [fold, call, raise, raise_big]
        self.hands_seen = {}
        self.params = dict(PARAMS)
        self._reset_hand()

    # -- per hand ---------------------------------------------------------
    def _reset_hand(self):
        self.hole = []
        self.inited = False
        self.street_idx = 0
        self.nboard = 0
        self.bets = {}
        self.last_raise = 20
        self.opp = None
        self.opp_log = []  # (key, kind, big, nboard)
        self.pf_raises = 0
        self.i_was_aggressor = False
        self.my_pf_action = None

    def seed(self, hand, player_id):
        self._reset_hand()
        self.hand = hand
        self.player_id = player_id
        self.hole = [P.card_int(c) for c in hand]

    # -- tracking ---------------------------------------------------------
    def _init_hand(self, gs):
        if self.inited:
            return
        self.inited = True
        n = gs.num_players
        me = self.player_id
        self.opp = (me + 1) % n if n == 2 else None
        btn = gs.button
        sb = btn if n == 2 else (btn + 1) % n
        bb = (sb + 1) % n
        self.bets = {p: 0 for p in range(n)}
        self.bets[sb] = gs.small_blind
        self.bets[bb] = gs.big_blind
        self.sb_seat = sb
        self.bb = gs.big_blind
        self.last_raise = gs.big_blind
        self.street_idx = 0
        self.pot_tracked = gs.small_blind + gs.big_blind
        self.pf_raises = 0
        self.limped = False
        self.i_shoved = False
        self.start_me = gs.stacks[me] + self.bets[me]

    def _stat(self, team):
        st = self.stats.get(team)
        if st is None:
            st = {k: [0, 0, 0, 0] for k in _PRIORS}
            self.stats[team] = st
        return st

    def freq(self, team, key):
        """(fold, call, raise, big share of raise) with priors."""
        c = self._stat(team)[key]
        pf, pc, pr = _PRIORS[key]
        n = c[0] + c[1] + c[2]
        tot = n + _PRIOR_N
        f = (c[0] + pf * _PRIOR_N) / tot
        ca = (c[1] + pc * _PRIOR_N) / tot
        r = (c[2] + pr * _PRIOR_N) / tot
        big = (c[3] + 0.5 * 2) / (c[2] + 2)
        return f, ca, r, big

    def _process(self, actions, gs):
        for a in actions:
            if a.player_id < 0:
                if a.kind == "reveal_card":
                    self.nboard += 1
                    if self.nboard in (3, 4, 5):
                        self.street_idx += 1
                        self.bets = {p: 0 for p in self.bets}
                        self.last_raise = self.bb
                continue
            if a.kind not in ("fold", "call", "raise"):
                continue
            p = a.player_id
            cur = max(self.bets.values())
            facing = self.bets[p] < cur
            pot_before = self.pot_tracked
            if p == self.opp:
                self._record_opp(a, facing, cur, pot_before, gs)
            if a.kind == "call":
                add = cur - self.bets[p]
                self.bets[p] = cur
                self.pot_tracked += add
                if self.street_idx == 0 and p == self.sb_seat and cur == self.bb and self.pf_raises == 0:
                    self.limped = True
            elif a.kind == "raise":
                inc = a.amount - cur
                if inc >= self.last_raise:
                    self.last_raise = inc
                self.pot_tracked += a.amount - self.bets[p]
                self.bets[p] = a.amount
                if self.street_idx == 0:
                    self.pf_raises += 1
                if p == self.player_id:
                    self.i_was_aggressor = True
                    if self.street_idx == 0 and a.amount >= self.start_me:
                        self.i_shoved = True
                elif p == self.opp:
                    self.i_was_aggressor = False

    def _situation(self, p, facing):
        if self.street_idx == 0:
            if self.pf_raises == 0:
                if p == self.sb_seat:
                    return "pf_open"
                return "pf_vs_limp"
            if self.i_shoved and p == self.opp:
                return "pf_vs_shove"
            if self.pf_raises == 1:
                return "pf_vs_open"
            return "pf_vs_3bet"
        return "post_vs_bet" if facing else "post_first"

    def _record_opp(self, a, facing, cur, pot_before, gs):
        key = self._situation(a.player_id, facing)
        team = gs.teams[self.opp]
        st = self._stat(team)[key]
        big = False
        if a.kind == "fold":
            st[0] += 1
        elif a.kind == "call":
            st[1] += 1
        else:
            st[2] += 1
            inc = a.amount - cur
            if self.street_idx == 0:
                big = a.amount >= 4 * self.bb * max(1, self.pf_raises)
            else:
                big = inc >= 0.9 * pot_before
            if a.amount >= 400:
                big = True
            if big:
                st[3] += 1
        self.opp_log.append((key, a.kind, big, self.nboard))

    # -- range ------------------------------------------------------------
    def opp_range(self, gs):
        team = gs.teams[self.opp]
        board = [P.card_int(c) for c in gs.board]
        dead = set(self.hole) | set(board)
        live = [i for i, (a, b) in enumerate(_COMBOS) if a not in dead and b not in dead]
        w = {i: 1.0 for i in live}
        boards_needed = {}
        for key, kind, big, nb in self.opp_log:
            f, c, r, bigs = self.freq(team, key)
            if key.startswith("pf"):
                if kind == "raise":
                    ff = r * (bigs if big else 1.0)
                    ff = max(ff, 0.03)
                    for i in live:
                        w[i] *= _soft_top(_PF_PCT[i], ff)
                elif kind == "call":
                    if key in ("pf_vs_limp",):
                        for i in live:
                            if _PF_PCT[i] > 1 - r:
                                w[i] *= 0.35
                    elif key == "pf_open":
                        for i in live:
                            pc = _PF_PCT[i]
                            w[i] *= _soft_top(pc, c + r) * (0.6 if pc > 1 - r else 1.0)
                    else:
                        for i in live:
                            pc = _PF_PCT[i]
                            w[i] *= _soft_top(pc, c + r, width=0.1) * (0.55 if pc > 1 - r else 1.0)
            else:
                if nb not in boards_needed:
                    boards_needed[nb] = None
        if boards_needed:
            # Postflop filters, using made strength percentile within the
            # current weighted range on the board at that time.
            for key, kind, big, nb in self.opp_log:
                if key.startswith("pf"):
                    continue
                f, c, r, bigs = self.freq(team, key)
                bd = board[:nb]
                pct = self._post_pct(live, w, bd)
                if kind == "raise":
                    ff = r * (bigs if big else 1.0)
                    if key == "post_first":
                        ff = r if not big else r * bigs
                    ff = min(1.0, max(ff, 0.06))
                    for i in live:
                        w[i] *= _soft_top(pct[i], ff, width=0.08, floor=0.04)
                elif kind == "call":
                    if key == "post_vs_bet":
                        for i in live:
                            pc = pct[i]
                            w[i] *= _soft_top(pc, c + r, width=0.1, floor=0.05) * (0.6 if pc > 1 - r else 1.0)
                    else:  # check
                        for i in live:
                            if pct[i] > 1 - r:
                                w[i] *= 0.4
        combos = [_COMBOS[i] for i in live]
        weights = [w[i] for i in live]
        return combos, weights

    def _post_pct(self, live, w, bd):
        scores = {}
        for i in live:
            a, b = _COMBOS[i]
            if a in bd or b in bd:
                continue
            s = P.evaluate([a, b] + bd)
            d = P.draw_strength((a, b), bd) if len(bd) < 5 else 0
            scores[i] = (s, d)
        # weighted percentile by made strength
        order = sorted(scores, key=lambda i: scores[i][0])
        tot = sum(w[i] for i in order) or 1.0
        pct = {}
        acc = 0.0
        for i in order:
            ww = w[i]
            pct[i] = (acc + ww / 2) / tot
            acc += ww
        for i in order:
            d = scores[i][1]
            if d == 2:
                pct[i] = max(pct[i], 0.8)
            elif d == 1:
                pct[i] = max(pct[i], 0.55)
        for i in live:
            if i not in pct:
                pct[i] = 0.0
        return pct

    # -- acting -----------------------------------------------------------
    def _raise(self, gs, total):
        if not gs.can_raise:
            return PlayerAction.call(self.player_id)
        lo, hi = gs.raise_range
        total = int(min(hi, max(lo, int(total))))
        if total >= hi * 0.85:
            total = hi
        return PlayerAction.raise_to(self.player_id, total)

    def _check_fold(self, gs):
        if gs.call_amount == 0:
            return PlayerAction.call(self.player_id)
        return PlayerAction.fold(self.player_id)

    def run(self, actions, gs):
        try:
            self._init_hand(gs)
            self._process(actions, gs)
            self.street_idx = _STREETS.get(gs.street, self.street_idx)
            self.nboard = len(gs.board)
            if gs.num_players != 2:
                return self._simple(gs)
            if gs.street == "preflop":
                return self._preflop(gs)
            return self._postflop(gs)
        except Exception:
            try:
                return self._check_fold(gs)
            except Exception:
                return PlayerAction.fold(self.player_id)

    def end_game(self, actions, gs):
        try:
            self._init_hand(gs)
            self._process(actions, gs)
        except Exception:
            pass

    def _equity(self, gs, samples):
        combos, weights = self.opp_range(gs)
        board = [P.card_int(c) for c in gs.board]
        return P.equity(self.hole, board, combos, weights, samples)

    def _simple(self, gs):
        board = [P.card_int(c) for c in gs.board]
        n = len(gs.players_in_hand) - 1
        e1 = P.equity(self.hole, board, _COMBOS, [1.0] * len(_COMBOS), 300)
        e = e1 ** max(1, n)
        if e > 0.6:
            return self._raise(gs, gs.pot)
        if e > gs.pot_odds:
            return PlayerAction.call(self.player_id)
        return self._check_fold(gs)

    def _opp_team(self, gs):
        return gs.teams[self.opp]

    def _preflop(self, gs):
        me = self.player_id
        team = self._opp_team(gs)
        call = gs.call_amount
        pot = gs.pot
        bb = gs.big_blind
        my_stack = gs.stacks[me]
        my_pct = _PF_PCT[_COMBOS.index(tuple(sorted(self.hole)))]
        cls = P.hand_class(*self.hole)
        eq_rand = PREFLOP[cls]
        # SB first decision
        if me == self.sb_seat and self.pf_raises == 0 and not gs.acted[me]:
            return self._sb_first(gs, team, my_pct)
        # Everything else: equity against the estimated range.
        e = self._equity(gs, self.params["samples"])
        if call == 0:
            # BB after a limp (or checked to us)
            fr = self.freq(team, "pf_vs_open")  # how they handle raises
            if e > 0.6:
                return self._raise(gs, 4 * bb)
            if e < 0.45 and fr[0] > 0.6 and my_pct > 0.25:
                return self._raise(gs, 3 * bb)
            return PlayerAction.call(me)
        po = call / (pot + call)
        stack_after = my_stack - call
        committed = stack_after <= 0 or stack_after < 0.6 * (pot + call)
        if committed or not gs.can_raise:
            if e > po:
                if gs.can_raise and stack_after > 0:
                    return self._raise(gs, gs.max_raise_to)
                return PlayerAction.call(me)
            return PlayerAction.fold(me)
        # value 3bet / 4bet
        fr = self.freq(team, "pf_vs_3bet")
        cur = gs.current_bet
        raise_thr = self.params["pf_raise_thr"] - (0.02 if self.pf_raises > 1 else 0.0)
        if e > raise_thr:
            if cur >= 3 * bb or self.pf_raises >= 2:
                return self._raise(gs, gs.max_raise_to)
            return self._raise(gs, 3 * cur)
        # light 3bet steal vs high folders
        if self.pf_raises == 1 and fr[0] > 0.6 and e > 0.38 and cur <= 3 * bb:
            return self._raise(gs, 3 * cur)
        margin = self.params["call_margin_bb"] if me != self.sb_seat else 0.0
        if e > po + margin:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)

    def _eq_top(self, lo, hi, n=400):
        """Equity preflop (to showdown) against hands whose preflop
        percentile is in the top band (1-hi .. 1-lo), soft edges."""
        dead = set(self.hole)
        combos = []
        weights = []
        for i, (a, b) in enumerate(_COMBOS):
            if a in dead or b in dead:
                continue
            pc = _PF_PCT[i]
            w = _soft_top(pc, hi, width=0.05)
            if lo > 0 and pc > 1 - lo:
                w *= 0.5
            combos.append((a, b))
            weights.append(w)
        return P.equity(self.hole, [], combos, weights, n)

    def _sb_first(self, gs, team, my_pct):
        me = self.player_id
        bb = gs.big_blind
        sb_in = gs.bets[me]
        stack = gs.stacks[me] + sb_in
        opp_total = gs.stacks[self.opp] + gs.bets[self.opp]
        eff = min(stack, opp_total)
        f, c, r, bigs = self.freq(team, "pf_vs_open")
        fs, cs, _, _ = self.freq(team, "pf_vs_shove")
        _, _, rl, _ = self.freq(team, "pf_vs_limp")
        open_to = int(2.5 * bb)
        ev = {"fold": -sb_in}
        # open raise
        e_c = self._eq_top(r, c + r)
        e_r = self._eq_top(0, max(r, 0.03))
        skill = 0.05 * open_to
        ev_r_resp = max(-open_to, e_r * 2 * eff - eff)
        implied = (e_c - 0.5) * 2 * self.params["implied"] * (eff - open_to)
        ev["open"] = f * bb + c * (e_c * 2 * open_to - open_to + skill + implied) + r * ev_r_resp
        # open shove
        q = max(0.03, cs)
        e_q = self._eq_top(0, q)
        ev["shove"] = (1 - q) * bb + q * (e_q * 2 * eff - eff)
        # limp
        e_l = self._eq_top(0, 1.0) if my_pct > 0 else 0.5
        e_lr = self._eq_top(0, max(rl, 0.03))
        ev["limp"] = (1 - rl) * (e_l * 2 * bb - bb) + rl * max(-bb, e_lr * 2 * eff - eff)
        best = max(ev, key=ev.get)
        self.last_ev = ev
        if best == "open":
            return self._raise(gs, open_to)
        if best == "shove":
            return self._raise(gs, gs.max_raise_to)
        if best == "limp":
            return PlayerAction.call(me)
        return PlayerAction.fold(me)

    def _postflop(self, gs):
        me = self.player_id
        team = self._opp_team(gs)
        call = gs.call_amount
        pot = gs.pot
        street = gs.street
        e = self._equity(gs, self.params["samples"])
        my_stack = gs.stacks[me]
        opp_stack = gs.stacks[self.opp]
        eff = min(my_stack, opp_stack + (gs.bets[self.opp] - gs.bets[me]))
        fvb = self.freq(team, "post_vs_bet")
        fold_to_bet = fvb[0]
        hole = self.hole
        board = [P.card_int(c) for c in gs.board]
        draw = P.draw_strength(hole, board) if street != "river" else 0
        if call == 0:
            if not gs.can_raise:
                return PlayerAction.call(me)
            vthr = self.params["vthr"] if street != "river" else self.params["vthr_river"]
            if fold_to_bet < 0.3:
                vthr -= 0.03
            if e >= vthr:
                size = 0.7 * pot
                if e > 0.8 or fold_to_bet < 0.3:
                    size = pot
                if eff <= 1.3 * pot:
                    size = eff + gs.bets[me]
                return self._raise(gs, gs.current_bet + size)
            # bluff / semi-bluff
            bet = 0.6 * pot
            be = bet / (pot + bet)
            if street != "river":
                if (draw == 2 and fold_to_bet > 0.25) or (fold_to_bet > be + 0.12 and e < 0.45):
                    if self._coin((0.75 if draw else 0.6) * self.params["bluff"]):
                        return self._raise(gs, gs.current_bet + bet)
            else:
                if e < 0.3 and fold_to_bet > be + 0.1 and self._coin(0.7 * self.params["bluff"]):
                    return self._raise(gs, gs.current_bet + bet)
            return PlayerAction.call(me)
        po = call / (pot + call)
        stack_after = my_stack - call
        if gs.can_raise and stack_after > 0:
            rthr = self.params["post_raise_thr"] + (0.04 if street == "river" else 0.0)
            # v5: never raise a bet with less than two pair (audit #3)
            if e > rthr and evaluate(list(self.hand) + list(gs.board))[0] >= 2:
                total = gs.current_bet * 3
                if stack_after < 1.5 * (pot + call):
                    total = gs.max_raise_to
                return self._raise(gs, total)
        need = po + (0.02 if street == "river" else 0.0)
        if e > need:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)

    def _coin(self, p):
        return P._RNG.random() < p
