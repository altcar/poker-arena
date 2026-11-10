"""
Hand ranges, equity and the expected-value decision.

The opponent's range is a weight per starting-hand class preflop (169) and
per two-card combo after the flop (1326). Each of its moves reweights the
range: a bet or raise keeps mostly the top share of hands that it bets that
often, a call keeps the middle, a check thins out the top. A bluffiness
factor (beta, learned at showdowns) keeps part of every hand, so a random
or tricky opponent is never read as honest.

Equity is then measured against that range (preflop from the class table,
on the river exactly, on the flop and turn by Monte Carlo), and every legal
move is scored by its expected chips given how often this opponent folds,
calls and raises; the best one is played.
"""

import bisect
import math
import time

from bot_eval import HIGHBIT, POP, STRAIGHT, eval_masks
from bot_preflop import EQUITY

# -- cards and combos -----------------------------------------------------

# A card as one bit of a 52-bit hand: suit-major, 13 bits per suit.
CBIT = [1 << ((c & 3) * 13 + (c >> 2)) for c in range(52)]
M13 = 8191


def eval_bits(h):
    return eval_masks(h & M13, (h >> 13) & M13, (h >> 26) & M13, h >> 39)


def class_of(a, b):
    """Starting-hand class 0..168 of two int cards."""
    ra, rb = a >> 2, b >> 2
    hi, lo = (ra, rb) if ra >= rb else (rb, ra)
    if hi == lo:
        return hi * 13 + hi
    if (a & 3) == (b & 3):
        return hi * 13 + lo  # suited
    return lo * 13 + hi  # offsuit


COMBOS = [(a, b) for a in range(52) for b in range(a + 1, 52)]
NCOMBO = len(COMBOS)
COMBO_BITS = [CBIT[a] | CBIT[b] for a, b in COMBOS]
COMBO_CLASS = [class_of(a, b) for a, b in COMBOS]
CLASS_COMBOS = [[] for _ in range(169)]
for _i, _k in enumerate(COMBO_CLASS):
    CLASS_COMBOS[_k].append(_i)
CLASS_N = [len(x) for x in CLASS_COMBOS]

# Equity of each class against a random hand, and its percentile (0 the
# weakest, 1 the strongest) weighted by combos.
PF_EQ = [
    sum(CLASS_N[j] * EQUITY[k][j] for j in range(169)) / NCOMBO
    for k in range(169)
]
PF_ORDER = sorted(range(169), key=lambda k: PF_EQ[k])  # weakest first
PF_PCT = [0.0] * 169
_cum = 0
for _k in PF_ORDER:
    PF_PCT[_k] = (_cum + CLASS_N[_k] / 2) / NCOMBO
    _cum += CLASS_N[_k]

# Straight outs: how many ranks would complete a straight for a rank
# mask that has none yet.
STRAIGHT_OUTS = [0] * 8192
for _m in range(8192):
    if not STRAIGHT[_m]:
        STRAIGHT_OUTS[_m] = sum(
            1 for _r in range(13)
            if not _m & (1 << _r) and STRAIGHT[_m | (1 << _r)]
        )


def bits_of(cards):
    h = 0
    for c in cards:
        h |= CBIT[c]
    return h


def _sig(x):
    if x > 30:
        return 1.0
    if x < -30:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


# -- preflop ranges -------------------------------------------------------


def class_counts(dead_bits):
    """Combos per class still possible given the dead cards."""
    out = [0] * 169
    for k in range(169):
        n = 0
        for i in CLASS_COMBOS[k]:
            if not COMBO_BITS[i] & dead_bits:
                n += 1
        out[k] = n
    return out


def pf_positions(weights, counts):
    """Each class's position in the weighted range (0 weakest, 1 top)."""
    total = 0.0
    for k in range(169):
        total += weights[k] * counts[k]
    pos = [0.0] * 169
    if total <= 0:
        return pos, 0.0
    cum = 0.0
    for k in PF_ORDER:
        m = weights[k] * counts[k]
        pos[k] = (cum + m / 2) / total
        cum += m
    return pos, total


def pf_update(weights, counts, kind, a, b, beta, tau=0.06):
    """Reweights a preflop range after a move (see _multiplier)."""
    pos, total = pf_positions(weights, counts)
    if total <= 0:
        return
    for k in range(169):
        if weights[k] > 0:
            weights[k] *= beta + (1 - beta) * _multiplier(
                pos[k], kind, a, b, tau)


def _multiplier(v, kind, a, b, tau):
    """How much of a hand at range position v survives a move: "top" keeps
    the top a; "band" keeps above a and thins the top b (they would have
    raised); "check" thins the top a (they would have bet)."""
    if kind == "top":
        return _sig((v - (1 - a)) / tau)
    if kind == "band":
        return _sig((v - a) / tau) * (1 - 0.6 * _sig((v - (1 - b)) / tau))
    if kind == "check":
        return 1 - 0.6 * _sig((v - (1 - a)) / tau)
    return 1.0


class PfEquity:
    """Equity of one class against a preflop range, overall and against the
    top q of the range."""

    def __init__(self, my_class, weights, counts):
        row = EQUITY[my_class]
        masses = []
        for k in reversed(PF_ORDER):  # strongest first
            m = weights[k] * counts[k]
            if m > 0:
                masses.append((m, row[k]))
        self.masses = masses
        self.total = sum(m for m, _ in masses)

    def top(self, q):
        """Equity against the strongest q (0..1) of the range."""
        if self.total <= 0:
            return 0.5
        want = max(q, 0.02) * self.total
        got = 0.0
        acc = 0.0
        for m, e in self.masses:
            take = min(m, want - got)
            acc += take * e
            got += take
            if got >= want - 1e-12:
                break
        return acc / got if got > 0 else 0.5

    def all(self):
        return self.top(1.0)


# -- postflop ranges ------------------------------------------------------


def street_keys(board, dead_bits):
    """Strength of every live combo on this board, as a percentile among
    them (0 weakest, 1 strongest), with draws on the flop and turn counted
    like middling made hands. Returns (u per combo id, ids by u)."""
    bb = bits_of(board)
    live = [i for i in range(NCOMBO) if not COMBO_BITS[i] & dead_bits]
    raw = [eval_bits(bb | COMBO_BITS[i]) for i in live]
    idx = sorted(range(len(live)), key=raw.__getitem__)
    n = len(live)
    u = [0.0] * NCOMBO
    j = 0
    while j < n:
        k = j
        while k + 1 < n and raw[idx[k + 1]] == raw[idx[j]]:
            k += 1
        p = (j + k + 1) / 2.0 / n
        for t in range(j, k + 1):
            u[live[idx[t]]] = p
        j = k + 1
    if len(board) < 5:
        flop = len(board) == 3
        bs = [(bb >> (13 * s)) & M13 for s in range(4)]
        brank = bs[0] | bs[1] | bs[2] | bs[3]
        bouts = STRAIGHT_OUTS[brank]
        fd_u, oe_u, both_u, gut_u = (
            (0.60, 0.55, 0.72, 0.42) if flop else (0.52, 0.48, 0.62, 0.35))
        for i in live:
            a, b = COMBOS[i]
            fd = False
            for c in (a, b):
                s = c & 3
                m = bs[s] | (1 << (a >> 2) if (a & 3) == s else 0) | (
                    1 << (b >> 2) if (b & 3) == s else 0)
                if POP[m] == 4 and POP[bs[s]] < 4:
                    fd = True
            rm = brank | (1 << (a >> 2)) | (1 << (b >> 2))
            outs = STRAIGHT_OUTS[rm] if not STRAIGHT[rm] else 0
            oe = outs >= 2 and bouts < 2
            gut = outs == 1 and bouts == 0
            if fd and oe:
                d = both_u
            elif fd:
                d = fd_u
            elif oe:
                d = oe_u
            elif gut:
                d = gut_u
            else:
                continue
            u0 = u[i]
            if u0 >= 0.4:
                u[i] = min(0.995, max(u0, d) + 0.07)
            else:
                u[i] = max(u0, d)
    order = sorted(live, key=u.__getitem__)
    return u, order


def positions(weights, order):
    pos = {}
    total = 0.0
    for i in order:
        total += weights[i]
    if total <= 0:
        return pos, 0.0
    cum = 0.0
    for i in order:
        w = weights[i]
        if w > 0:
            pos[i] = (cum + w / 2) / total
            cum += w
    return pos, total


def post_update(weights, order, kind, a, b, beta, tau=0.07):
    pos, total = positions(weights, order)
    if total <= 0:
        return
    for i, v in pos.items():
        weights[i] *= beta + (1 - beta) * _multiplier(v, kind, a, b, tau)


class SampleEquity:
    """Outcomes against sampled (or every) opponent hands, by their range
    position, so equity against the top q of the range is one lookup."""

    def __init__(self, items):
        # items: (position, weight, outcome)
        items.sort()
        self.pos = [x[0] for x in items]
        cw = [0.0]
        co = [0.0]
        for _, w, o in items:
            cw.append(cw[-1] + w)
            co.append(co[-1] + w * o)
        self.cw = cw
        self.co = co

    def top(self, q):
        n = len(self.pos)
        if n == 0:
            return 0.5
        i = bisect.bisect_left(self.pos, 1.0 - q)
        i = min(i, max(0, n - 25))  # at least 25 samples
        w = self.cw[n] - self.cw[i]
        if w <= 0:
            return 0.5
        return (self.co[n] - self.co[i]) / w

    def all(self):
        return self.top(1.0)


def post_equity(hand, board, weights, order, rng, samples, deadline):
    """Equity of hand against the weighted range on this board."""
    pos, total = positions(weights, order)
    my_bits = CBIT[hand[0]] | CBIT[hand[1]]
    bb = bits_of(board)
    items = []
    if total <= 0:
        return SampleEquity(items)
    if len(board) == 5:
        mine = eval_bits(my_bits | bb)
        for i, v in pos.items():
            theirs = eval_bits(COMBO_BITS[i] | bb)
            o = 1.0 if mine > theirs else 0.5 if mine == theirs else 0.0
            items.append((v, weights[i], o))
        return SampleEquity(items)
    ids = list(pos)
    cum = []
    acc = 0.0
    for i in ids:
        acc += weights[i]
        cum.append(acc)
    picks = rng.choices(ids, cum_weights=cum, k=samples)
    need = 5 - len(board)
    base = my_bits | bb
    deck = [c for c in range(52) if not CBIT[c] & base]
    nd = len(deck)
    rand = rng.random
    for t, i in enumerate(picks):
        if t & 63 == 63 and time.perf_counter() > deadline:
            break
        ob = COMBO_BITS[i]
        run = 0
        got = 0
        while got < need:
            c = CBIT[deck[int(rand() * nd)]]
            if c & ob or c & run:
                continue
            run |= c
            got += 1
        mine = eval_bits(base | run)
        theirs = eval_bits(ob | bb | run)
        o = 1.0 if mine > theirs else 0.5 if mine == theirs else 0.0
        items.append((pos[i], 1.0, o))
    return SampleEquity(items)


def combo_id(a, b):
    if a > b:
        a, b = b, a
    # Index of (a, b) in COMBOS.
    return a * 51 - a * (a - 1) // 2 + (b - a - 1)
