"""
Adaptive heads-up no-limit hold'em bot.

Architecture (all in this file, pure Python, pickle-safe):

1. Fast 5-7 card evaluator on integer cards (0..51), table driven.
2. A per-hand tracker that replays every action the engine shows us, so we
   know the exact context of each opponent decision (street, bet size,
   facing a raise, previous-street line, position).
3. An opponent model: hierarchical, prior-smoothed counters of what the
   opponent does in each context (fold / call / raise, bet after check,
   bluff share at showdown), learnt online across hands.
4. A range model: the opponent's hand distribution, built by weighting
   every possible holding by how likely the opponent's observed actions
   are for it, given the learnt frequencies ("they raise 30% here, so they
   raise the top 30% of their range, plus some bluffs").
5. An EV engine: Monte Carlo equity against that weighted range, then a
   one-street lookahead EV for fold, check/call (including check-to-induce
   / check-raise lines), and several raise sizes, with response
   probabilities from the opponent model. The best action is chosen, with
   a small mixed strategy among near-equal actions.
"""

import math
import random
import time
from bisect import bisect_left

from card_utils import Card
from game_state import GameStateView
from player_action import PlayerAction

DEALER = -1
RANK_CHARS = "23456789TJQKA"
SUIT_CHARS = "CDHS"

# --------------------------------------------------------------------------
# Fast evaluator
# --------------------------------------------------------------------------


def _build_tables():
    bitcount = [0] * 8192
    highbit = [0] * 8192
    for m in range(1, 8192):
        bitcount[m] = bitcount[m >> 1] + (m & 1)
        highbit[m] = m.bit_length() - 1
    straight = [0] * 8192
    for m in range(8192):
        for top in range(12, 3, -1):
            mask = 0b11111 << (top - 4)
            if m & mask == mask:
                straight[m] = top + 1
                break
        else:
            if m & 0b1000000001111 == 0b1000000001111:
                straight[m] = 4  # wheel: five high (index 3) + 1
    tops = []
    for k in range(6):
        table = [0] * 8192
        if k:
            for m in range(8192):
                v = 0
                left = m
                for i in range(k):
                    if left:
                        h = highbit[left]
                        left &= ~(1 << h)
                        v |= h << (4 * (k - 1 - i))
                table[m] = v
        tops.append(table)
    # Number of ranks that would complete a straight (no straight yet).
    outs = [0] * 8192
    for m in range(8192):
        if straight[m]:
            continue
        n = 0
        for r in range(13):
            if not m & (1 << r) and straight[m | (1 << r)]:
                n += 1
        outs[m] = n
    return bitcount, highbit, straight, tops, outs


_BC, _HB, _ST, _TOPS, _SOUTS = _build_tables()
_TOP1, _TOP2, _TOP3, _TOP5 = _TOPS[1], _TOPS[2], _TOPS[3], _TOPS[5]


def hand_value(cards):
    """Strength of the best 5-card hand in 5 to 7 int cards: higher wins."""
    m1 = m2 = m3 = m4 = 0
    s = [0, 0, 0, 0]
    for c in cards:
        b = 1 << (c >> 2)
        s[c & 3] |= b
        if m1 & b:
            if m2 & b:
                if m3 & b:
                    m4 |= b
                else:
                    m3 |= b
            else:
                m2 |= b
        else:
            m1 |= b
    fl = 0
    for sm in s:
        if _BC[sm] >= 5:
            st = _ST[sm]
            if st:
                return 0x800000 | st
            fl = 0x500000 | _TOP5[sm]
            break
    if m4:
        q = _HB[m4]
        return 0x700000 | (q << 4) | _HB[m1 & ~(1 << q)]
    if m3:
        t = _HB[m3]
        rest = m2 & ~(1 << t)
        if rest:
            return 0x600000 | (t << 4) | _HB[rest]
    if fl:
        return fl
    st = _ST[m1]
    if st:
        return 0x400000 | st
    if m3:
        t = _HB[m3]
        return 0x300000 | (t << 8) | _TOP2[m1 & ~(1 << t)]
    if m2:
        p1 = _HB[m2]
        r = m2 & ~(1 << p1)
        if r:
            p2 = _HB[r]
            return (0x200000 | (p1 << 8) | (p2 << 4)
                    | _HB[m1 & ~(1 << p1) & ~(1 << p2)])
        return 0x100000 | (p1 << 12) | _TOP3[m1 & ~(1 << p1)]
    return _TOP5[m1]


def draw_bonus(hole, board):
    """Rough extra strength for flush and straight draws that use a hole
    card (flop and turn only)."""
    if len(board) >= 5:
        return 0.0
    scale = 1.0 if len(board) == 3 else 0.6
    bonus = 0.0
    counts = [0, 0, 0, 0]
    for c in board:
        counts[c & 3] += 1
    for c in hole:
        counts[c & 3] += 1
    for c in hole:
        if counts[c & 3] == 4:
            bonus += 0.30
            break
    mb = 0
    for c in board:
        mb |= 1 << (c >> 2)
    ma = mb | (1 << (hole[0] >> 2)) | (1 << (hole[1] >> 2))
    oa = _SOUTS[ma]
    if oa > _SOUTS[mb] and not _ST[ma]:
        bonus += 0.20 if oa >= 2 else 0.08
    return bonus * scale


def card_int(card):
    return RANK_CHARS.index(card.rank) * 4 + SUIT_CHARS.index(card.suit)


# --------------------------------------------------------------------------
# Preflop strength: heads-up equity of each of the 169 starting hands
# against a random hand (computed offline with hand_value, 40k deals each).
# --------------------------------------------------------------------------

PF_TABLE = """
AA:0.8531 AKs:0.6754 AKo:0.6517 AQs:0.6598 AQo:0.6473 AJs:0.6557 AJo:0.6348
ATs:0.6481 ATo:0.6255 A9s:0.6303 A9o:0.6082 A8s:0.6226 A8o:0.5971 A7s:0.6106
A7o:0.5875 A6s:0.5978 A6o:0.5798 A5s:0.6006 A5o:0.5773 A4s:0.5927 A4o:0.5688
A3s:0.5855 A3o:0.5621 A2s:0.5751 A2o:0.5512 KK:0.8232 KQs:0.6309 KQo:0.6163
KJs:0.6221 KJo:0.6046 KTs:0.6197 KTo:0.5959 K9s:0.6021 K9o:0.5773 K8s:0.5830
K8o:0.5571 K7s:0.5757 K7o:0.5512 K6s:0.5677 K6o:0.5446 K5s:0.5531 K5o:0.5318
K4s:0.5482 K4o:0.5232 K3s:0.5435 K3o:0.5120 K2s:0.5337 K2o:0.5072 QQ:0.8000
QJs:0.6033 QJo:0.5836 QTs:0.5933 QTo:0.5712 Q9s:0.5773 Q9o:0.5543 Q8s:0.5611
Q8o:0.5381 Q7s:0.5451 Q7o:0.5174 Q6s:0.5357 Q6o:0.5100 Q5s:0.5265 Q5o:0.5011
Q4s:0.5180 Q4o:0.4899 Q3s:0.5059 Q3o:0.4802 Q2s:0.5075 Q2o:0.4769 JJ:0.7778
JTs:0.5766 JTo:0.5528 J9s:0.5556 J9o:0.5257 J8s:0.5418 J8o:0.5163 J7s:0.5216
J7o:0.4974 J6s:0.5072 J6o:0.4805 J5s:0.5040 J5o:0.4687 J4s:0.4900 J4o:0.4647
J3s:0.4792 J3o:0.4545 J2s:0.4760 J2o:0.4442 TT:0.7490 T9s:0.5372 T9o:0.5117
T8s:0.5235 T8o:0.5002 T7s:0.5089 T7o:0.4805 T6s:0.4905 T6o:0.4600 T5s:0.4735
T5o:0.4443 T4s:0.4628 T4o:0.4388 T3s:0.4602 T3o:0.4252 T2s:0.4491 T2o:0.4150
99:0.7204 98s:0.5087 98o:0.4791 97s:0.4885 97o:0.4673 96s:0.4721 96o:0.4439
95s:0.4575 95o:0.4228 94s:0.4357 94o:0.4059 93s:0.4308 93o:0.4031 92s:0.4211
92o:0.3918 88:0.6933 87s:0.4769 87o:0.4539 86s:0.4600 86o:0.4254 85s:0.4432
85o:0.4123 84s:0.4305 84o:0.3951 83s:0.4052 83o:0.3702 82s:0.4042 82o:0.3725
77:0.6604 76s:0.4526 76o:0.4224 75s:0.4398 75o:0.4025 74s:0.4173 74o:0.3862
73s:0.4023 73o:0.3604 72s:0.3772 72o:0.3463 66:0.6340 65s:0.4290 65o:0.3985
64s:0.4112 64o:0.3780 63s:0.3959 63o:0.3570 62s:0.3790 62o:0.3377 55:0.6004
54s:0.4163 54o:0.3837 53s:0.3972 53o:0.3617 52s:0.3762 52o:0.3392 44:0.5677
43s:0.3853 43o:0.3530 42s:0.3682 42o:0.3308 33:0.5349 32s:0.3566 32o:0.3224
22:0.5030
"""


def _parse_pf_table():
    table = {}
    for item in PF_TABLE.split():
        if ":" not in item:
            continue
        name, eq = item.split(":")
        table[name] = float(eq)
    return table


def _class_name(a, b):
    ra, rb = a >> 2, b >> 2
    if ra < rb:
        ra, rb = rb, ra
    if ra == rb:
        return RANK_CHARS[ra] * 2
    return RANK_CHARS[ra] + RANK_CHARS[rb] + ("s" if (a & 3) == (b & 3)
                                              else "o")


def _build_combos():
    table = _parse_pf_table()
    combos = []
    for a in range(52):
        for b in range(a + 1, 52):
            combos.append((table.get(_class_name(a, b), 0.5), a, b))
    combos.sort()
    eqs = [e for e, _, _ in combos]
    pairs = [(a, b) for _, a, b in combos]
    # Percentile (0 weakest, 1 strongest) with ties sharing their midpoint.
    n = len(combos)
    pct = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and eqs[j + 1] == eqs[i]:
            j += 1
        mid = (i + j + 1) / 2 / n
        for k in range(i, j + 1):
            pct[k] = mid
        i = j + 1
    return pairs, eqs, pct, table


COMBOS, COMBO_EQ, COMBO_PCT, PF_EQ = _build_combos()


def preflop_equity(a, b):
    return PF_EQ.get(_class_name(a, b), 0.5)


# --------------------------------------------------------------------------
# Opponent-model priors: what an unknown hackathon bot does, as
# (fold, call, raise) probabilities. Learnt counts override them.
# --------------------------------------------------------------------------

PRIOR_K = (5.0, 4.0, 4.0)  # prior strength at each level, root to leaf

_PFR_LEVEL = {1: (0.45, 0.40, 0.15), 2: (0.45, 0.38, 0.17),
              3: (0.35, 0.50, 0.15)}
_PFR_BUCKET = {"S": 0.8, "M": 1.0, "L": 1.15, "X": 1.3, "A": 1.3}
_FB_BUCKET = {
    0: {"S": (0.30, 0.55, 0.15), "M": (0.38, 0.48, 0.14),
        "L": (0.45, 0.42, 0.13), "X": (0.52, 0.36, 0.12),
        "A": (0.55, 0.45, 0.0)},
    1: {"S": (0.35, 0.50, 0.15), "M": (0.42, 0.45, 0.13),
        "L": (0.48, 0.42, 0.10), "X": (0.52, 0.40, 0.08),
        "A": (0.50, 0.50, 0.0)},
}


def _norm(v):
    s = v[0] + v[1] + v[2]
    if s <= 0:
        return (0.0, 1.0, 0.0)
    return (v[0] / s, v[1] / s, v[2] / s)


def _sig(x):
    if x > 30:
        return 1.0
    if x < -30:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def band(p, lo, hi, soft=0.04):
    """Soft indicator of lo <= p <= hi."""
    if hi <= lo:
        return 0.0
    return _sig((p - lo) / soft) * _sig((hi - p) / soft)


def bucket_post(frac, allin):
    if allin:
        return "A"
    if frac < 0.4:
        return "S"
    if frac < 0.7:
        return "M"
    if frac < 1.1:
        return "L"
    return "X"


def bucket_pre(ratio, allin):
    if allin:
        return "A"
    if ratio <= 2.25:
        return "S"
    if ratio <= 3.25:
        return "M"
    if ratio <= 5.0:
        return "L"
    return "X"


# --------------------------------------------------------------------------
# Weighted-range equity queries
# --------------------------------------------------------------------------


class RangeEq:
    """Our showdown results against samples of the opponent's range,
    sorted by the opponent's current strength (weakest first), so we can
    ask for our equity against any band of their range by mass."""

    def __init__(self, pcts, weights, results):
        order = sorted(range(len(pcts)), key=pcts.__getitem__)
        total = sum(weights) or 1.0
        cm = [0.0]
        cr = [0.0]
        m = r = 0.0
        for i in order:
            w = weights[i] / total
            m += w
            r += w * results[i]
            cm.append(m)
            cr.append(r)
        self.cm = cm
        self.cr = cr
        self.res = [results[i] for i in order]
        self.all = r / m if m > 0 else 0.5

    def _cum(self, x):
        cm = self.cm
        if x <= 0:
            return 0.0
        if x >= cm[-1]:
            return self.cr[-1]
        i = bisect_left(cm, x)
        lo, hi = cm[i - 1], cm[i]
        frac = (x - lo) / (hi - lo) if hi > lo else 1.0
        return self.cr[i - 1] + frac * (self.cr[i] - self.cr[i - 1])

    def band(self, lo, hi):
        """(equity, mass) against the opponent hands in percentile band."""
        lo = max(0.0, min(1.0, lo))
        hi = max(0.0, min(1.0, hi))
        if hi - lo < 1e-4:
            x = min(max(lo, 0.0), 0.9999)
            i = min(len(self.res) - 1, max(0, bisect_left(self.cm, x) - 1))
            return (self.res[i] if self.res else 0.5), 0.0
        return (self._cum(hi) - self._cum(lo)) / (hi - lo), hi - lo

    def mix(self, bands):
        num = den = 0.0
        for lo, hi in bands:
            e, m = self.band(lo, hi)
            num += e * m
            den += m
        if den <= 1e-6:
            return self.band(bands[0][0], bands[0][1])[0]
        return num / den


def weighted_pct(values, weights):
    """Weighted percentile of each value (ties share the midpoint)."""
    n = len(values)
    order = sorted(range(n), key=values.__getitem__)
    total = sum(weights) or 1.0
    pct = [0.0] * n
    acc = 0.0
    i = 0
    while i < n:
        j = i
        v = values[order[i]]
        mass = weights[order[i]]
        while j + 1 < n and values[order[j + 1]] == v:
            j += 1
            mass += weights[order[j]]
        mid = (acc + mass / 2) / total
        for k in range(i, j + 1):
            pct[order[k]] = mid
        acc += mass
        i = j + 1
    return pct


# --------------------------------------------------------------------------
# The model
# --------------------------------------------------------------------------


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None
        # Feature switches (for ablations; all on in the submission).
        self.learn = True  # update the opponent model from observations
        self.trap = True  # model check-to-induce / check-raise lines
        # Opponent model: team name -> {context key: [fold, call, raise]}.
        self.stats = {}
        self.rng = random.Random(7_351_2026)
        self.time_budget = 1.2  # seconds of Monte Carlo per decision, max
        self.min_samples = 600
        self.max_samples = 2400
        self.target_ess = 450.0
        self.debug = False
        self.errors = 0
        self.last_error = ""
        self.hands_seen = 0
        self._new_hand()

    # ---------------------------------------------------------------- API

    def seed(self, hand, player_id):
        self.hand = hand
        self.player_id = player_id
        self._new_hand()

    def run(self, actions, game_state):
        me = self.player_id
        try:
            if not self.t_started:
                self._start_tracker(game_state)
            self._feed(actions)
            return self._decide(game_state)
        except Exception as error:  # never fault: check or fold instead
            if self.debug:
                raise
            self.errors += 1
            self.last_error = repr(error)[:300]
            try:
                if game_state.call_amount == 0:
                    return PlayerAction.call(me)
            except Exception:
                pass
            return PlayerAction.fold(me)

    def end_game(self, actions, game_state):
        try:
            if not self.t_started and game_state.street == "preflop":
                # We never acted (e.g. the small blind folded to us): the
                # hand ended preflop, so start = stacks + bets - winnings.
                won = [0] * game_state.num_players
                for a in actions:
                    if a.kind == "win":
                        won[a.player_id] += a.amount
                stacks = game_state.stacks
                bets = game_state.bets
                start = [stacks[p] + bets[p] - won[p]
                         for p in range(game_state.num_players)]
                self._start_tracker(game_state, start)
            if self.t_started:
                self._feed(actions)
                self._learn_showdown()
            self.hands_seen += 1
        except Exception as error:
            if self.debug:
                raise
            self.errors += 1
            self.last_error = repr(error)[:300]

    # ------------------------------------------------------------ tracker

    def _new_hand(self):
        self.t_started = False
        self.my_cards = [card_int(c) for c in self.hand] if self.hand else []
        self.t_n = 0
        self.t_start_sum = 0
        self.t_opp = None
        self.t_team = None
        self.t_button = 0
        self.t_bb = 20
        self.t_bets = []
        self.t_stacks = []
        self.t_folded = []
        self.t_board = []
        self.t_street = "preflop"
        self.t_cur = 0
        self.t_last_raise = 20
        self.t_nraises = 0
        self.t_prev_total = 20  # raise-to before the last raise (preflop)
        self.t_last_frac = 0.0
        self.t_last_allin = False
        self.t_aggr = None  # last raiser this street
        self.t_prev_aggr = None  # last raiser on the previous street
        self.t_street_actions = 0
        self.t_opp_line = {}  # street -> opponent's last action kind
        self.t_opp_dec = []  # opponent decisions this hand
        self.t_opp_shown = []

    def _start_tracker(self, gs, start=None):
        """Replays the hand from the blinds. At our first turn the hand is
        still preflop, so every chip put in is in bets."""
        n = gs.num_players
        self.t_n = n
        self.t_button = gs.button
        self.t_bb = gs.big_blind
        if start is None:
            stacks = gs.stacks
            bets = gs.bets
            start = [stacks[p] + bets[p] for p in range(n)]
        self.t_stacks = list(start)
        self.t_start_sum = sum(start)
        self.t_bets = [0] * n
        self.t_folded = [False] * n
        sb = self.t_button if n == 2 else (self.t_button + 1) % n
        bb = (sb + 1) % n
        for p, amt in ((sb, gs.small_blind), (bb, gs.big_blind)):
            amt = min(amt, self.t_stacks[p])
            self.t_stacks[p] -= amt
            self.t_bets[p] += amt
        self.t_cur = max(self.t_bets)
        self.t_last_raise = gs.big_blind
        self.t_prev_total = gs.big_blind
        if n == 2:
            self.t_opp = 1 - self.player_id
            self.t_team = gs.teams[self.t_opp]
            if self.t_team not in self.stats:
                self.stats[self.t_team] = {}
        self.t_started = True

    def _feed(self, actions):
        for a in actions:
            self._track(a)

    def _track(self, a):
        p = a.player_id
        kind = a.kind
        if p == DEALER:
            self.t_board.append(card_int(a.card))
            nb = len(self.t_board)
            if nb in (3, 4, 5):
                self.t_prev_aggr = self.t_aggr
                self.t_aggr = None
                self.t_street = {3: "flop", 4: "turn", 5: "river"}[nb]
                self.t_bets = [0] * self.t_n
                self.t_cur = 0
                self.t_last_raise = self.t_bb
                self.t_nraises = 0
                self.t_street_actions = 0
            return
        if kind == "reveal_card":
            if p == self.t_opp:
                self.t_opp_shown.append(card_int(a.card))
            return
        if kind == "win":
            return
        if p == self.t_opp and self.t_n == 2:
            ctx = self._context(p)
            outcome = {"fold": 0, "call": 1, "raise": 2}[kind]
            if outcome == 0 and ctx["free"]:
                outcome = 1  # folding when checking is free: a check
            dec = {"street": self.t_street, "ctx": ctx, "out": outcome,
                   "nb": len(self.t_board)}
            if kind == "raise" and self.t_street != "preflop":
                pot_after_call = self._pot() + max(
                    0, self.t_cur - self.t_bets[p])
                dec["frac"] = (a.amount - self.t_cur) / max(1, pot_after_call)
            self.t_opp_dec.append(dec)
            if self.learn:
                self._record(ctx, outcome, dec.get("frac"))
            self.t_opp_line[self.t_street] = kind
        self._apply(p, kind, a.amount)

    def _pot(self):
        """Every chip put in this hand: starting chips minus chips behind."""
        return self.t_start_sum - sum(self.t_stacks)

    def _apply(self, p, kind, amount):
        if kind == "fold":
            self.t_folded[p] = True
        elif kind == "call":
            amt = min(self.t_cur - self.t_bets[p], self.t_stacks[p])
            self.t_stacks[p] -= amt
            self.t_bets[p] += amt
        elif kind == "raise":
            call_part = self.t_cur - self.t_bets[p]
            pot_after_call = self._pot() + call_part
            inc = amount - self.t_cur
            self.t_last_frac = inc / max(1, pot_after_call)
            add = amount - self.t_bets[p]
            self.t_stacks[p] -= add
            self.t_bets[p] += add
            if inc >= self.t_last_raise:
                self.t_last_raise = inc
            self.t_prev_total = self.t_cur if self.t_cur > 0 else self.t_bb
            self.t_cur = amount
            self.t_nraises += 1
            self.t_aggr = p
            self.t_last_allin = self.t_stacks[p] == 0
        self.t_street_actions += 1

    # ------------------------------------------------------ context keys

    def _context(self, p, hypo=None):
        """The context of player p's decision, now (or after a hypothetical
        raise of ours: hypo = (raise_to, our_total_after, we_allin))."""
        me = self.player_id
        street = self.t_street
        bets, stacks = self.t_bets, self.t_stacks
        cur = self.t_cur
        nraises = self.t_nraises
        last_frac = self.t_last_frac
        last_allin = self.t_last_allin
        prev_total = self.t_prev_total
        if hypo is not None:
            total, frac, we_allin = hypo
            prev_total = cur if cur > 0 else self.t_bb
            cur = total
            nraises += 1
            last_frac = frac
            last_allin = we_allin
        to_call = min(cur - bets[p], stacks[p])
        can_raise = stacks[p] > to_call and not last_allin
        facing_allin = last_allin or (to_call > 0 and to_call >= stacks[p])
        if facing_allin:
            can_raise = False
        free = to_call <= 0
        if street == "preflop":
            if nraises == 0:
                if not free:
                    keys = [("pfo",)]
                    priors = [(0.25, 0.20, 0.55)]
                else:
                    keys = [("pfl",)]
                    priors = [(0.0, 0.70, 0.30)]
            else:
                lvl = min(nraises, 3)
                ratio = cur / max(1, prev_total)
                bk = bucket_pre(ratio, facing_allin)
                base = _PFR_LEVEL[lvl]
                f = _PFR_BUCKET[bk]
                leaf = _norm((base[0] * f, base[1], 0.0 if bk == "A"
                              else base[2]))
                keys = [("pfr",), ("pfr", lvl), ("pfr", lvl, bk)]
                priors = [(0.45, 0.40, 0.15), base, leaf]
        else:
            if free:
                role = "oop" if self.t_street_actions == 0 else "ip"
                pa = self.t_prev_aggr
                ag = "a" if pa == p else ("h" if pa is not None else "n")
                rp = (0.0, 0.65, 0.35) if role == "oop" else (0.0, 0.55,
                                                               0.45)
                shift = {"a": 0.12, "h": -0.08, "n": 0.0}[ag]
                lp = (0.0, rp[1] - shift, rp[2] + shift)
                keys = [("ub",), ("ub", role), ("ub", street, role, ag)]
                priors = [(0.0, 0.6, 0.4), rp, lp]
            else:
                fr = 1 if nraises >= 2 else 0
                bk = bucket_post(last_frac, facing_allin)
                prev = {"flop": "preflop", "turn": "flop",
                        "river": "turn"}[street]
                line = self.t_opp_line.get(prev, "-")[:1]
                bp = _FB_BUCKET[fr][bk]
                keys = [("fb",), ("fb", fr, bk), ("fb", street, fr, bk, line)]
                priors = [(0.40, 0.45, 0.15), bp, bp]
        return {"keys": keys, "priors": priors, "can_raise": can_raise,
                "free": free}

    def _record(self, ctx, outcome, frac=None):
        st = self.stats.setdefault(self.t_team, {})
        for key in ctx["keys"]:
            c = st.get(key)
            if c is None:
                c = st[key] = [0, 0, 0]
            c[outcome] += 1
        if frac is not None:
            s = st.setdefault("betsize", [0.0, 0])
            s[0] += min(frac, 3.0)
            s[1] += 1

    def _probs(self, ctx):
        """(fold, call, raise) for the opponent in this context, and the
        number of observations behind the most specific estimate."""
        st = self.stats.get(self.t_team, {})
        keys, priors = ctx["keys"], ctx["priors"]
        post = None
        prev_prior = None
        n_used = 0
        for level, key in enumerate(keys):
            prior = priors[level]
            if post is None:
                base = prior
            else:
                base = _norm(tuple(
                    prior[i] * (post[i] / prev_prior[i]
                                if prev_prior[i] > 1e-9 else 1.0)
                    for i in range(3)))
            c = st.get(key) or (0, 0, 0)
            n = c[0] + c[1] + c[2]
            k = PRIOR_K[min(level, 2)]
            post = tuple((c[i] + k * base[i]) / (n + k) for i in range(3))
            prev_prior = prior
            n_used = max(n_used, n) if level == 0 else n + n_used * 0.25
        f, c, r = post
        if not ctx["can_raise"]:
            c, r = c + r, 0.0
        if ctx["free"]:
            c, f = c + f, 0.0
        return (f, c, r), n_used

    def _bluff_share(self, street):
        if street == "preflop":
            return 0.08
        st = self.stats.get(self.t_team, {})
        weak, total = st.get("bluff", (0, 0))
        b = (weak + 4 * 0.25) / (total + 4)
        return min(0.6, max(0.08, b))

    def _opp_bet_frac(self):
        st = self.stats.get(self.t_team, {})
        s = st.get("betsize", (0.0, 0))
        return (s[0] + 3 * 0.66) / (s[1] + 3)

    # --------------------------------------------------- range and equity

    def _likelihood(self, dec, pcts, weights):
        (f, c, r), _ = self._probs(dec["ctx"])
        out = dec["out"]
        b = self._bluff_share(dec["street"])
        if dec["street"] == "preflop" and dec["ctx"]["keys"][0][0] == "pfo":
            b = 0.0
        v = r * (1 - b)
        bl = r * b
        eps = 0.03
        trap = 0.12 if dec["ctx"]["free"] else 0.0
        for i, p in enumerate(pcts):
            if out == 2:
                L = band(p, 1 - v, 1.01) + band(p, -0.01, bl)
            elif out == 1:
                lo = max(bl, 1 - v - c)
                L = band(p, lo, 1 - v + 1e-9) + (trap if p > 1 - v else 0.0)
                if c > 0.97:
                    L = 1.0
            else:
                L = band(p, -0.01, f)
            weights[i] *= L + eps

    def _build_range(self, gs):
        t0 = time.perf_counter()
        rng = self.rng
        mine = self.my_cards
        board = list(self.t_board)
        dead = set(mine) | set(board)
        # Preflop weights over all live combos (sorted weakest first).
        idx = [i for i, (a, b) in enumerate(COMBOS)
               if a not in dead and b not in dead]
        w = [1.0] * len(idx)
        pf_pct_all = [COMBO_PCT[i] for i in idx]
        for dec in self.t_opp_dec:
            if dec["street"] != "preflop":
                continue
            pcts = weighted_pct(pf_pct_all, w)
            self._likelihood(dec, pcts, w)
        cum = []
        acc = 0.0
        for x in w:
            acc += x
            cum.append(acc)
        post_decs = {}
        for dec in self.t_opp_dec:
            if dec["street"] != "preflop":
                post_decs.setdefault(dec["nb"], []).append(dec)
        live = [c for c in range(52) if c not in dead]
        k_left = 5 - len(board)
        my_final = hand_value(mine + board) if k_left == 0 else None

        samples = []
        sw = []
        results = []
        cur_pct_vals = []
        while True:
            chunk = 400
            picks = rng.choices(idx, cum_weights=cum, k=chunk)
            ch_cards = [COMBOS[i] for i in picks]
            ch_w = [1.0] * chunk
            # Weight by postflop actions, street by street.
            for nb in (3, 4, 5):
                decs = post_decs.get(nb)
                if not decs:
                    continue
                proxy = self._proxy(ch_cards, board[:nb], ch_w)
                for dec in decs:
                    pcts = weighted_pct(proxy, ch_w)
                    self._likelihood(dec, pcts, ch_w)
            # Current strength proxy, for ordering.
            if not board:
                cv = [COMBO_PCT[i] for i in picks]
            else:
                cv = self._proxy(ch_cards, board, None)
            # Showdown result over one random runout.
            for (o1, o2) in ch_cards:
                if k_left:
                    extra = rng.sample(live, k_left + 2)
                    run = [x for x in extra if x != o1 and x != o2][:k_left]
                    fb = board + run
                    mv = hand_value(mine + fb)
                else:
                    fb = board
                    mv = my_final
                ov = hand_value([o1, o2] + fb)
                results.append(1.0 if mv > ov else (0.5 if mv == ov else 0.0))
            samples.extend(ch_cards)
            sw.extend(ch_w)
            cur_pct_vals.extend(cv)
            n = len(sw)
            s1 = sum(sw)
            s2 = sum(x * x for x in sw)
            ess = s1 * s1 / s2 if s2 > 0 else 0.0
            elapsed = time.perf_counter() - t0
            if n >= self.max_samples or elapsed > self.time_budget:
                break
            if n >= self.min_samples and ess >= self.target_ess:
                break
        pcts = weighted_pct(cur_pct_vals, sw)
        return RangeEq(pcts, sw, results)

    def _proxy(self, cards, board, weights):
        """Opponent strength on this board: percentile of made hand
        (among the samples) plus a draw bonus, re-ranked."""
        vals = [hand_value([a, b] + board) for (a, b) in cards]
        if len(board) >= 5:
            return vals
        ww = weights if weights is not None else [1.0] * len(vals)
        pct = weighted_pct(vals, ww)
        return [pct[i] + draw_bonus(cards[i], board)
                for i in range(len(cards))]

    # -------------------------------------------------------- decisions

    def _realize(self, ip, eq, street, initiative=False):
        gap = {"preflop": 0.12, "flop": 0.08, "turn": 0.05}.get(street, 0.0)
        r = 1.0 - (0.0 if ip else gap) + (0.25 * (eq - 0.5) if gap else 0.0)
        if initiative:
            r += 0.03
        return max(0.7, min(1.15, r))

    def _decide(self, gs):
        me = self.player_id
        if self.t_n != 2 or len(gs.players_in_hand) != 2:
            return self._multiway(gs)
        opp = self.t_opp
        street = gs.street
        pot = gs.pot
        to_call = gs.call_amount
        bets = gs.bets
        stacks = gs.stacks
        ip = me == gs.button  # heads-up: the button acts last postflop
        cr = self._build_range(gs)
        eq = cr.all

        cands = []  # (ev, kind, total)
        if to_call > 0:
            cands.append((0.0, "fold", 0))
        cands.append((self._ev_call(gs, cr, eq, ip), "call", 0))
        if gs.can_raise:
            low, high = gs.raise_range
            for total in self._raise_sizes(gs, low, high):
                cands.append((self._ev_raise(gs, cr, total, ip), "raise",
                              total))
        self.last_eval = [(round(e, 1), k, t) for e, k, t in cands]
        self.last_eq = eq
        best = max(c[0] for c in cands)
        margin = max(1.0, 0.012 * pot)
        near = [c for c in cands if c[0] >= best - margin]
        if len(near) > 1:
            ws = [math.exp(-(best - c[0]) / (margin / 2)) for c in near]
            choice = self.rng.choices(near, weights=ws, k=1)[0]
        else:
            choice = near[0]
        _, kind, total = choice
        if kind == "fold":
            return PlayerAction.fold(me)
        if kind == "call":
            return PlayerAction.call(me)
        return PlayerAction.raise_to(me, int(total))

    def _raise_sizes(self, gs, low, high):
        pot = gs.pot
        bb = gs.big_blind
        cur = gs.current_bet
        my_bet = gs.bets[self.player_id]
        out = set()
        if gs.street == "preflop":
            if self.t_nraises == 0:
                if gs.call_amount > 0:  # SB open
                    sizes = [2 * bb, int(2.5 * bb), 3 * bb]
                else:  # BB vs limp
                    sizes = [3 * bb, 4 * bb]
            else:
                sizes = [int(2.5 * cur), 3 * cur]
        else:
            if cur == 0:
                sizes = [int(f * pot) for f in (0.33, 0.5, 0.75, 1.0)]
            else:
                call_part = cur - my_bet
                after = pot + call_part
                sizes = [cur + int(f * after) for f in (0.6, 1.0)]
        for s in sizes:
            s = min(high, max(low, s))
            # Leaving less than a quarter pot behind: just go all-in.
            behind = high - s
            if behind < 0.3 * (pot + 2 * (s - my_bet)):
                s = high
            out.add(s)
        out.add(high)
        return sorted(out)

    def _ev_call(self, gs, cr, eq, ip):
        street = gs.street
        pot = gs.pot
        to_call = gs.call_amount
        me, opp = self.player_id, self.t_opp
        stacks = gs.stacks
        if to_call > 0:
            if street == "preflop" and self.t_nraises == 0:
                return self._ev_limp(gs, cr, ip)
            new_pot = pot + to_call
            allin = stacks[me] <= to_call or stacks[opp] == 0
            if allin or street == "river":
                return eq * new_pot - to_call
            return self._realize(ip, eq, street) * eq * new_pot - to_call
        # Check.
        closes = street == "preflop" or self.t_street_actions > 0
        if closes:
            if street == "river":
                return eq * pot
            return self._realize(ip, eq, street) * eq * pot
        return self._ev_check_first(gs, cr, eq, ip)

    def _ev_limp(self, gs, cr, ip):
        bb = gs.big_blind
        to_call = gs.call_amount
        me, opp = self.player_id, self.t_opp
        my_bet = gs.bets[me]
        ctx = self._context_after_limp()
        (_, pc, pr), _ = self._probs(ctx)
        b = 0.1
        v, bl = pr * (1 - b), pr * b
        eq_chk = cr.band(bl, 1 - v)[0]
        eq_r = cr.mix([(1 - v, 1.0), (0.0, bl)])
        r_ip = self._realize(ip, eq_chk, "preflop")
        ev_no = r_ip * eq_chk * 2 * bb - to_call
        e_total = min(my_bet + gs.stacks[me], gs.bets[opp] + gs.stacks[opp])
        x = min(e_total, 4 * bb)
        cost = x - my_bet
        if x >= e_total:
            ev_call = eq_r * 2 * x - cost
        else:
            ev_call = self._realize(ip, eq_r, "preflop") * eq_r * 2 * x - cost
        ev_r = max(-to_call, ev_call)
        return (1 - pr) * ev_no + pr * ev_r

    def _context_after_limp(self):
        # The opponent (big blind) facing our limp: a free decision.
        return {"keys": [("pfl",)], "priors": [(0.0, 0.70, 0.30)],
                "can_raise": True, "free": True}

    def _ev_check_first(self, gs, cr, eq, ip):
        """We act first and check: the opponent may bet into us."""
        street = gs.street
        pot = gs.pot
        me, opp = self.player_id, self.t_opp
        r_me = self._realize(ip, eq, street)
        if not self.trap:
            return (eq if street == "river" else r_me * eq) * pot
        ctx = self._context_checked_to(opp)
        (_, _, pb), _ = self._probs(ctx)
        b = self._bluff_share(street)
        v, bl = pb * (1 - b), pb * b
        eq_bet = cr.mix([(1 - v, 1.0), (0.0, bl)])
        eq_chk = cr.band(bl, 1 - v)[0] if 1 - v - bl > 1e-3 else eq
        if street == "river":
            ev_no = eq_chk * pot
        else:
            ev_no = self._realize(ip, eq_chk, street) * eq_chk * pot
        bet = max(1, min(int(self._opp_bet_frac() * pot), gs.stacks[opp]))
        call_cost = min(bet, gs.stacks[me])
        allin = call_cost >= gs.stacks[me] or bet >= gs.stacks[opp]
        if street == "river" or allin:
            ev_call = eq_bet * (pot + bet + call_cost) - call_cost
        else:
            ev_call = (self._realize(ip, eq_bet, street) * eq_bet
                       * (pot + bet + call_cost) - call_cost)
        ev_cr = -1e9
        e_total = min(gs.stacks[me], gs.stacks[opp])
        if not allin and e_total > bet:
            cctx = {"keys": [("fb",), ("fb", 1, "A"),
                             ("fb", street, 1, "A", "r")],
                    "priors": [(0.40, 0.45, 0.15), _FB_BUCKET[1]["A"],
                               _FB_BUCKET[1]["A"]],
                    "can_raise": False, "free": False}
            (pf_cr, _, _), _ = self._probs(cctx)
            q = max(0.02, pb * (1 - pf_cr))
            eq_cont = cr.band(1 - min(q, v if v > 0.02 else q), 1.0)[0]
            ev_cr = (pf_cr * (pot + bet)
                     + (1 - pf_cr) * (eq_cont * (pot + 2 * e_total)
                                      - e_total))
        ev_bet = max(0.0, ev_call, ev_cr)
        return (1 - pb) * ev_no + pb * ev_bet

    def _context_checked_to(self, opp):
        street = self.t_street
        pa = self.t_prev_aggr
        ag = "a" if pa == opp else ("h" if pa is not None else "n")
        rp = (0.0, 0.55, 0.45)
        shift = {"a": 0.12, "h": -0.08, "n": 0.0}[ag]
        lp = (0.0, rp[1] - shift, rp[2] + shift)
        return {"keys": [("ub",), ("ub", "ip"), ("ub", street, "ip", ag)],
                "priors": [(0.0, 0.6, 0.4), rp, lp],
                "can_raise": True, "free": True}

    def _ev_raise(self, gs, cr, total, ip):
        street = gs.street
        pot = gs.pot
        me, opp = self.player_id, self.t_opp
        bets, stacks = gs.bets, gs.stacks
        cur = gs.current_bet
        my_max = bets[me] + stacks[me]
        opp_max = bets[opp] + stacks[opp]
        a = total - bets[me]
        eff_total = min(total, opp_max)
        c = eff_total - bets[opp]
        we_allin = total >= my_max
        opp_allin = c >= stacks[opp]
        call_part = cur - bets[me]
        frac = (total - cur) / max(1, pot + call_part)
        ctx = self._context(opp, hypo=(total, frac, we_allin or opp_allin))
        (pf, pc, pr), n_obs = self._probs(ctx)
        if we_allin or opp_allin:
            pc, pr = pc + pr, 0.0
        b = self._bluff_share(street)
        v, bl = pr * (1 - b), pr * b
        lo = max(bl, 1 - v - pc)
        eq_c = cr.band(lo, 1 - v)[0] if pc > 1e-3 else cr.all
        a_eff = eff_total - bets[me]
        pot_called = pot + a_eff + c
        if street == "river" or we_allin or opp_allin:
            ev_called = eq_c * pot_called - a_eff
        else:
            r = self._realize(ip, eq_c, street, initiative=True)
            ev_called = r * eq_c * pot_called - a_eff
        ev_rr = 0.0
        if pr > 0:
            e_total = min(my_max, opp_max)
            final = pot - bets[me] - bets[opp] + 2 * e_total
            eq_rr = cr.mix([(1 - v, 1.0), (0.0, bl)])
            ev_rr = max(-a, eq_rr * final - (e_total - bets[me]))
        ev = pf * pot + pc * ev_called + pr * ev_rr
        # Be careful with fold equity we have little evidence for.
        sd = math.sqrt(max(pf * (1 - pf), 0.0) / (n_obs + 5.0))
        ev -= 0.3 * sd * abs(pot - ev_called)
        return ev

    # -------------------------------------------------------- showdown

    def _learn_showdown(self):
        """Opponent showed cards: how often were their bets weak?"""
        if not self.learn or self.t_n != 2 or len(self.t_opp_shown) != 2:
            return
        hole = self.t_opp_shown
        board = self.t_board
        st = self.stats.setdefault(self.t_team, {})
        c = st.setdefault("bluff", [0, 0])
        rng = self.rng
        for dec in self.t_opp_dec:
            if dec["street"] == "preflop" or dec["out"] != 2:
                continue
            bs = board[:dec["nb"]]
            dead = set(bs) | set(hole)
            live = [x for x in range(52) if x not in dead]
            mine = hand_value(hole + bs) + 0.0
            below = 0
            trials = 120
            for _ in range(trials):
                o = rng.sample(live, 2)
                if hand_value(o + bs) < mine:
                    below += 1
            p = below / trials + draw_bonus(hole, bs)
            c[1] += 1
            if p < 0.45:
                c[0] += 1

    # -------------------------------------------------------- multiway

    def _multiway(self, gs):
        """More than one opponent: plain equity against random hands."""
        me = self.player_id
        opps = len(gs.players_in_hand) - 1
        mine = self.my_cards
        board = [card_int(c) for c in gs.board]
        dead = set(mine) | set(board)
        live = [c for c in range(52) if c not in dead]
        k = 5 - len(board)
        won = 0.0
        trials = 300
        rng = self.rng
        for _ in range(trials):
            draw = rng.sample(live, k + 2 * opps)
            fb = board + draw[:k]
            mv = hand_value(mine + fb)
            best = 0
            for i in range(opps):
                ov = hand_value(draw[k + 2 * i:k + 2 * i + 2] + fb)
                best = max(best, ov)
            if mv > best:
                won += 1
            elif mv == best:
                won += 0.5
        eq = won / trials
        if eq > 1.4 / (opps + 1) and eq > 0.45 and gs.can_raise:
            low, high = gs.raise_range
            return PlayerAction.raise_to(me, min(high, max(low, gs.pot)))
        if gs.call_amount == 0 or eq > gs.pot_odds * 1.1:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)
