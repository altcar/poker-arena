"""tag_chart: tight-aggressive chart bot for 25bb heads-up NLHE.

Preflop: position charts keyed on hand percentile (SB open / BB defend /
3-bet-shove), widened or tightened by a fast opponent model (fold-to-raise,
raise frequency).  Facing big bets preflop we call/shove on equity vs the
opponent's estimated range (top-x% of hands, x from their raise frequency).
Postflop: made-hand buckets (monster / strong / medium / weak / draw / air)
plus board texture, value-bet sizing by bucket, pot-odds calling using
equity vs the opponent's preflop range adjusted by their aggression.
"""

import random

from fasteval import card_to_int, eval_cards, has_draw
from player_action import DEALER, PlayerAction
from preflop_table import EQUITY

RANKS = "23456789TJQKA"


def _cls(a, b):
    ra, rb = a >> 2, b >> 2
    if ra < rb:
        ra, rb = rb, ra
    if ra == rb:
        return RANKS[ra] * 2
    return RANKS[ra] + RANKS[rb] + ("s" if a & 3 == b & 3 else "o")


COMBOS = [(a, b) for a in range(52) for b in range(a + 1, 52)]
_eqs = [EQUITY[_cls(a, b)] for a, b in COMBOS]
ORDER = sorted(range(len(COMBOS)), key=lambda i: -_eqs[i])
SORTED_COMBOS = [COMBOS[i] for i in ORDER]
# percentile of each class (0 = best), combo weighted
PCT = {}
_n = len(COMBOS)
for _rank, _i in enumerate(ORDER):
    _c = _cls(*COMBOS[_i])
    if _c not in PCT:
        PCT[_c] = _rank / _n
del _rank, _i, _c

PRIOR = {
    # name: (hits, total)
    "pf_open_raise": (2.5, 4.0),   # opp as SB raises first in
    "pf_open_fold": (0.6, 4.0),
    "pf_fold_vs_raise": (1.6, 4.0),  # opp folds facing our raise preflop
    "pf_raise_vs_raise": (0.6, 4.0),  # opp re-raises our raise
    "pf_raise_vs_limp": (1.2, 4.0),
    "post_fold_vs_bet": (1.6, 4.0),
    "post_raise_vs_bet": (0.5, 4.0),
    "post_bet_open": (1.5, 4.0),  # opp bets when checked to / first
}


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = 0
        self.rng = random.Random(7741)
        self.st = {k: [0.0, 0.0] for k in PRIOR}
        self.sd_pf = [0.4, 2.0]    # sum/count of preflop pct of hands shown after raising pf
        self.sd_post = [1.6, 2.0]  # sum/count of river strength of hands shown after betting post
        self.h = None

    def seed(self, hand, player_id):
        self.hand = hand
        self.player_id = player_id
        self.h = None

    # ---------------------------------------------------------------- hooks
    def run(self, actions, gs):
        me = self.player_id
        try:
            self._ensure(gs)
            self._process(actions)
            act = self._decide(gs)
            if act is None:
                raise ValueError
            return act
        except Exception:
            try:
                if gs.call_amount == 0:
                    return PlayerAction.call(me)
            except Exception:
                pass
            return PlayerAction.fold(me)

    def end_game(self, actions, gs):
        try:
            self._ensure(gs)
            self._process(actions)
            h = self.h
            oc = h["opp_cards"]
            bd = h["board"]
            if len(oc) == 2 and len(bd) == 5:
                if h["opp_pf_raised"]:
                    self.sd_pf[0] += PCT[_cls(oc[0], oc[1])]
                    self.sd_pf[1] += 1.0
                if h["opp_post_bets"] > 0:
                    used = set(oc) | set(bd)
                    deck = [c for c in range(52) if c not in used]
                    mine = eval_cards(oc + bd)
                    rng = self.rng
                    w = 0.0
                    for _ in range(80):
                        x, y = rng.sample(deck, 2)
                        t = eval_cards([x, y] + bd)
                        w += 1.0 if mine > t else 0.5 if mine == t else 0.0
                    self.sd_post[0] += w / 80
                    self.sd_post[1] += 1.0
        except Exception:
            pass

    # ---------------------------------------------------------------- stats
    def f(self, k):
        h, t = PRIOR[k]
        s = self.st[k]
        return (s[0] + h) / (s[1] + t)

    def _rec(self, k, hit):
        s = self.st[k]
        s[0] += 1.0 if hit else 0.0
        s[1] += 1.0

    def _ensure(self, gs):
        if self.h is not None:
            return
        me = self.player_id
        opp = 1 - me
        bets = [0, 0]
        bets[gs.button] = gs.small_blind
        bets[1 - gs.button] = gs.big_blind
        self.h = {
            "opp": opp, "button": gs.button, "nb": 0,
            "bets": bets, "cur": gs.big_blind,
            "opp_pf_acted": False, "pf_raises": 0, "opp_pf_raised": False,
            "opp_pf_limped": False, "i_pf_raised": False,
            "street_bet": False, "opp_agg_street": False,
            "opp_post_bets": 0, "i_last_aggr": False,
            "board": [], "opp_cards": [],
        }

    def _process(self, actions):
        h = self.h
        me = self.player_id
        opp = h["opp"]
        for a in actions:
            if a.player_id == DEALER:
                if a.kind == "reveal_card":
                    h["nb"] += 1
                    h["board"].append(card_to_int(a.card))
                    if h["nb"] in (3, 4, 5):
                        h["bets"] = [0, 0]
                        h["cur"] = 0
                        h["opp_agg_street"] = False
                continue
            if a.kind == "reveal_card" and a.player_id == opp and a.card is not None:
                h["opp_cards"].append(card_to_int(a.card))
            if a.kind not in ("fold", "call", "raise"):
                continue
            p = a.player_id
            facing = h["cur"] > h["bets"][p]
            pre = h["nb"] < 3
            if p == opp:
                if pre:
                    if p == h["button"] and not h["opp_pf_acted"]:
                        self._rec("pf_open_raise", a.kind == "raise")
                        self._rec("pf_open_fold", a.kind == "fold")
                        if a.kind == "call":
                            h["opp_pf_limped"] = True
                    elif facing and h["i_pf_raised"]:
                        self._rec("pf_fold_vs_raise", a.kind == "fold")
                        self._rec("pf_raise_vs_raise", a.kind == "raise")
                    elif not facing and h["opp_pf_acted"] is False and p != h["button"]:
                        self._rec("pf_raise_vs_limp", a.kind == "raise")
                    h["opp_pf_acted"] = True
                    if a.kind == "raise":
                        h["opp_pf_raised"] = True
                else:
                    if facing:
                        self._rec("post_fold_vs_bet", a.kind == "fold")
                        self._rec("post_raise_vs_bet", a.kind == "raise")
                    else:
                        self._rec("post_bet_open", a.kind == "raise")
                    if a.kind == "raise":
                        h["opp_agg_street"] = True
                        h["opp_post_bets"] += 1
            else:
                if a.kind == "raise":
                    if pre:
                        h["i_pf_raised"] = True
                    h["i_last_aggr"] = True
            if a.kind == "raise" and p == opp:
                h["i_last_aggr"] = False
            if a.kind == "call":
                h["bets"][p] = h["cur"]
            elif a.kind == "raise":
                h["bets"][p] = a.amount
                h["cur"] = max(h["cur"], a.amount)

    # ---------------------------------------------------------------- equity
    def _eq_range(self, hole, board, frac, n):
        """MC equity vs the top `frac` of preflop hands."""
        rng = self.rng
        used = set(hole) | set(board)
        top = max(30, min(len(SORTED_COMBOS), int(frac * len(SORTED_COMBOS))))
        deck = [c for c in range(52) if c not in used]
        need = 5 - len(board)
        won = 0.0
        cnt = 0
        tries = 0
        while cnt < n and tries < n * 4:
            tries += 1
            a, b = SORTED_COMBOS[int(rng.random() * top)]
            if a in used or b in used:
                continue
            if need:
                draw = [c for c in rng.sample(deck, need + 2) if c != a and c != b][:need]
                full = board + draw
            else:
                full = board
            m = eval_cards(hole + full)
            t = eval_cards([a, b] + full)
            won += 1.0 if m > t else 0.5 if m == t else 0.0
            cnt += 1
        return won / cnt if cnt else 0.5

    # ---------------------------------------------------------------- helpers
    def _raise(self, gs, total):
        me = self.player_id
        if not gs.can_raise or gs.raise_range is None:
            return PlayerAction.call(me)
        lo, hi = gs.raise_range
        t = min(hi, max(lo, int(total)))
        # if the raise would commit most of the stack, just go all-in
        if t > 0.6 * hi:
            t = hi
        return PlayerAction.raise_to(me, int(t))

    def _jam(self, gs):
        me = self.player_id
        if not gs.can_raise or gs.raise_range is None:
            return PlayerAction.call(me)
        return PlayerAction.raise_to(me, int(gs.raise_range[1]))

    # ---------------------------------------------------------------- decide
    def _decide(self, gs):
        if gs.street == "preflop" or len(gs.board) < 3:
            return self._preflop(gs)
        return self._postflop(gs)

    def _preflop(self, gs):
        me = self.player_id
        h = self.h
        hole = [card_to_int(c) for c in self.hand]
        cls = _cls(hole[0], hole[1])
        pct = PCT[cls]
        bb = gs.big_blind
        call = gs.call_amount
        pot = gs.pot
        cur = gs.current_bet
        my_stack = gs.stacks[me]
        sb = me == gs.button
        fold_vs = self.f("pf_fold_vs_raise")
        need = call / (pot + call) if call else 0.0

        if sb and cur == bb and call > 0:
            # ---- SB first in: open chart
            if pct < 0.06:
                # premiums: raise (could get 3-bet / called by wide ranges)
                return self._raise(gs, int(2.5 * bb))
            if fold_vs > 0.6:
                return self._raise(gs, 2 * bb)          # steal everything
            if fold_vs < 0.2:
                # station: jam big equity, raise for value, limp marginal
                if EQUITY[cls] > 0.6:
                    return self._jam(gs)
                if pct < 0.45:
                    return self._raise(gs, 3 * bb)
                if pct < 0.85:
                    return PlayerAction.call(me)
                return PlayerAction.fold(me)
            if pct < 0.82:
                return self._raise(gs, int(2.5 * bb))
            if pct < 0.92:
                return PlayerAction.call(me)
            return PlayerAction.fold(me)

        if call == 0:
            # ---- BB after a limp
            limp_raise = 0.35 if self.f("pf_open_fold") < 0.3 else 0.5
            if fold_vs < 0.2 and EQUITY[cls] > 0.6:
                return self._jam(gs)
            if pct < limp_raise:
                return self._raise(gs, 4 * bb)
            return PlayerAction.call(me)

        # ---- facing a raise
        sdw = min(1.0, 2.0 * self.sd_pf[0] / self.sd_pf[1])
        if not h["i_pf_raised"]:
            # BB facing SB open
            frac = min(1.0, max(0.08, self.f("pf_open_raise"), sdw))
        else:
            # facing a re-raise of ours
            frac = min(1.0, max(0.04, self.f("pf_raise_vs_raise") * 0.8, sdw * 0.8))
        big = cur >= 0.4 * (my_stack + gs.bets[me])
        if big:
            frac = min(frac, 1.0) * (0.8 if frac < 0.9 else 1.0)
        eq = self._eq_range(hole, [], frac, 260)
        # stack-off decision vs this range
        all_in_call = call >= my_stack
        if big or all_in_call:
            if eq > need + 0.02:
                if gs.can_raise and not all_in_call:
                    return self._jam(gs)
                return PlayerAction.call(me)
            return PlayerAction.fold(me)
        # small raise: 3-bet-shove strong, call by odds, else fold
        if eq > 0.58 or pct < 0.05:
            return self._jam(gs)
        realize = 0.85 if not sb else 0.95
        if eq * realize > need + 0.03:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)

    # ---------------------------------------------------------------- post
    def _bucket(self, hole, board):
        """Returns (bucket, draw_type) where bucket in 0..5:
        5 monster, 4 strong, 3 medium, 2 weak, 1 draw-only, 0 air."""
        full = eval_cards(hole + board)
        cat = full >> 20
        brank = sorted((c >> 2 for c in board), reverse=True)
        bcount = {}
        for r in brank:
            bcount[r] = bcount.get(r, 0) + 1
        bsuit = [0, 0, 0, 0]
        for c in board:
            bsuit[c & 3] += 1
        board_pairs = sum(1 for v in bcount.values() if v >= 2)
        flushy = max(bsuit)
        hr = sorted((c >> 2 for c in hole), reverse=True)
        top = brank[0]
        river = len(board) == 5
        draw = 0
        if not river:
            if has_draw(hole, board):
                draw = 2 if any(
                    sum(1 for c in hole + board if c & 3 == s) == 4 and any(x & 3 == s for x in hole)
                    for s in range(4)) else 1
        b = 0
        if cat >= 4:
            if cat == 4 and flushy >= 3:
                b = 4 if flushy == 3 else 3
            elif cat == 5 and flushy >= 4:
                hi_flush = max((c >> 2 for c in hole if bsuit[c & 3] >= 4), default=0)
                b = 5 if hi_flush >= 11 else 4
            else:
                b = 5
            if board_pairs and cat in (4, 5):
                b = min(b, 4)
        elif cat == 3:
            if hr[0] == hr[1] or any(bcount.get(r, 0) == 2 for r in hr):
                b = 5  # set / trips with hole card
            else:
                b = 2  # trips on board
        elif cat == 2:
            if hr[0] == hr[1] and hr[0] > top:
                b = 4
            elif board_pairs == 0:
                b = 5 if (hr[0] in bcount and hr[1] in bcount) else 3
            elif board_pairs == 1:
                pr = (full >> 4) & 15
                hp = (full >> 8) & 15
                if hr[0] == hr[1] or hp in hr or pr in hr:
                    b = 4 if (hp in hr or hr[0] > top) else 3
                else:
                    b = 1 if hr[0] >= 12 else 0
            else:
                b = 1
        elif cat == 1:
            pr = (full >> 12) & 15
            if pr not in hr:
                b = 2 if hr[0] >= 12 else 0  # board pair, ace/king high
            elif hr[0] == hr[1]:
                if pr > top:
                    b = 4 if pr >= 9 else 3
                elif len(brank) > 1 and pr > brank[1]:
                    b = 3
                else:
                    b = 2
            elif pr == top:
                kick = hr[1] if hr[0] == pr else hr[0]
                b = 4 if (kick >= 10 or pr >= 12 and kick >= 8) else 3
            elif len(brank) > 1 and pr >= brank[1]:
                b = 3 if not river else 2
            else:
                b = 2
            if flushy >= 4 and b >= 3:
                b -= 1
        else:
            b = 1 if hr[0] == 12 else 0
            if hr[0] == 12 and river:
                b = 1
        if draw and b <= 2:
            b = max(b, 1)
        return b, draw, flushy, board_pairs

    def _postflop(self, gs):
        me = self.player_id
        h = self.h
        hole = [card_to_int(c) for c in self.hand]
        board = [card_to_int(c) for c in gs.board]
        b, draw, flushy, bpairs = self._bucket(hole, board)
        pot = gs.pot
        call = gs.call_amount
        cur = gs.current_bet
        my_stack = gs.stacks[me]
        opp_stack = gs.stacks[h["opp"]]
        eff = min(my_stack, opp_stack + call)
        spr = eff / max(1, pot)
        street = len(board)
        river = street == 5
        ip = me == gs.button
        fold_bet = self.f("post_fold_vs_bet")
        opp_agg = self.f("post_bet_open")
        opp_raise = self.f("post_raise_vs_bet")
        station = fold_bet < 0.3
        rng = self.rng

        # opp preflop range estimate
        sdw = min(1.0, 2.0 * self.sd_pf[0] / self.sd_pf[1])
        if h["opp_pf_raised"]:
            frac = max(0.1, sdw, min(1.0, self.f("pf_open_raise") if h["button"] == h["opp"] else 0.3))
        elif h["opp_pf_limped"]:
            frac = 0.7
        else:
            frac = 0.75
        n = 220
        eq = self._eq_range(hole, board, frac, n)

        if call == 0 and station and b < 5:
            if eq > 0.75 and spr < 2.2:
                return self._jam(gs)
            if eq > 0.62:
                return self._raise(gs, int(pot * 0.85))
            if eq > 0.54:
                return self._raise(gs, int(pot * 0.5))
            if draw == 2 and not river and spr < 2.5 and eq > 0.4:
                return self._jam(gs)
            return PlayerAction.call(me)
        if call == 0:
            # ---- checked to us / first to act
            if b >= 5 or (b == 4 and not river) or (b == 4 and station):
                if spr < 1.3:
                    return self._jam(gs)
                size = 1.0 if station else 0.7
                if b == 4 and not station:
                    size = 0.6 + max(0.0, 0.5 - fold_bet) * 0.8
                return self._raise(gs, int(pot * size))
            if b == 4:  # river strong, non-station
                return self._raise(gs, int(pot * 0.55))
            if b == 3:
                if station or (ip and not river and rng.random() < 0.6) or (river and ip and eq > 0.6):
                    return self._raise(gs, int(pot * 0.5))
                return PlayerAction.call(me)
            # weak / draw / air: bluff by fold equity
            if station:
                if draw == 2 and not river and spr < 2.5 and eq > 0.4:
                    return self._jam(gs)
                return PlayerAction.call(me)
            bluff_p = 0.0
            if fold_bet > 0.35:
                bluff_p = 0.25 + (fold_bet - 0.35) * 1.5
                if h["i_last_aggr"] or h["i_pf_raised"] and street == 3:
                    bluff_p += 0.2
                if draw:
                    bluff_p += 0.25
                if ip:
                    bluff_p += 0.1
                if river and b == 2:
                    bluff_p *= 0.3  # showdown value
            if rng.random() < min(0.95, bluff_p):
                return self._raise(gs, int(pot * (0.55 if not river else 0.65)))
            return PlayerAction.call(me)

        # ---- facing a bet
        need = call / (pot + call)
        bet_frac = call / max(1, pot - call)
        # how wide is their betting range? aggressive -> trust raw equity
        agg = max(opp_agg, opp_raise * 1.5)
        trust = min(1.0, max(0.0, (agg - 0.25) / 0.5))
        sdm = self.sd_post[0] / self.sd_post[1]
        trust = max(trust, min(1.0, max(0.0, (0.82 - sdm) / 0.3)))
        # bucket-based equity vs a value range
        base = [0.06, 0.18, 0.28, 0.45, 0.62, 0.85][b]
        if draw == 2 and not river:
            base = max(base, 0.36 if street == 3 else 0.2)
        elif draw == 1 and not river:
            base = max(base, 0.3 if street == 3 else 0.17)
        if gs.bets[me] > 0:
            base *= 0.65  # they raised our bet
        e = trust * eq + (1 - trust) * (0.5 * base + 0.5 * eq * 0.85)
        all_in_call = call >= my_stack
        if b >= 5:
            if spr < 2.5 or all_in_call:
                return self._jam(gs) if not all_in_call else PlayerAction.call(me)
            return self._raise(gs, cur + int((pot + call) * 0.9))
        if b == 4 and spr < 1.2 and e > need:
            return self._jam(gs) if not all_in_call else PlayerAction.call(me)
        if draw == 2 and not river and fold_bet > 0.45 and spr < 3 and e > 0.3 and not all_in_call:
            return self._jam(gs)
        if b == 4 and trust > 0.7 and e > 0.6 and not all_in_call:
            return self._jam(gs)
        if e > need + 0.02:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)
