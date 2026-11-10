"""
Equity: our share of the pot at showdown against a range of hands.

Preflop: from the class-vs-class table (pk_tables), with card removal.
Postflop: time-boxed Monte Carlo against a weighted list of combos, and an
exact count over the range on the river.
"""

import bisect
import time

from pk_cards import CLASS_SIZE, COMBO_CLASS, COMBOS, class_of
from pk_eval import CARD_KEY, FLUSH, FLUSH_SUIT, MASK39, NO_FLUSH, RANK_BIT
from pk_tables import EQUITY

# For each card, the class of every combo that uses it (51 per card).
_CARD_CLASSES = [[] for _ in range(52)]
for _i, (_a, _b) in enumerate(COMBOS):
    _CARD_CLASSES[_a].append(COMBO_CLASS[_i])
    _CARD_CLASSES[_b].append(COMBO_CLASS[_i])
del _i, _a, _b


def live_counts(a, b):
    """Per class, how many of its combos use neither card a nor card b."""
    counts = CLASS_SIZE[:]
    for k in _CARD_CLASSES[a]:
        counts[k] -= 1
    for k in _CARD_CLASSES[b]:
        counts[k] -= 1
    counts[class_of(a, b)] += 1  # the combo (a, b) itself, removed twice
    return counts


def preflop_equity(hole, weights):
    """Equity of hole (two card ints) all in preflop against a range of
    169 class weights. 0.5 for an empty range."""
    a, b = hole
    row = class_of(a, b) * 169
    counts = live_counts(a, b)
    num = 0.0
    den = 0.0
    for j in range(169):
        w = weights[j]
        if w > 0.0:
            n = w * counts[j]
            num += n * EQUITY[row + j]
            den += n
    if den <= 0.0:
        return 0.5
    return num / den / 1000.0


def _flush_value(f, cards, x, y):
    s = FLUSH_SUIT[f]
    m = 0
    for c in cards:
        if c & 3 == s:
            m |= RANK_BIT[c]
    if x & 3 == s:
        m |= RANK_BIT[x]
    if y & 3 == s:
        m |= RANK_BIT[y]
    return FLUSH[m]


def hand_value(hole, board):
    """Value (pk_eval) of two hole cards with a board of 3 to 5 cards."""
    k = CARD_KEY[hole[0]] + CARD_KEY[hole[1]]
    for c in board:
        k += CARD_KEY[c]
    f = ((k >> 39) + 0x3333) & 0x8888
    if f:
        return _flush_value(f, board, hole[0], hole[1])
    return NO_FLUSH[k & MASK39]


def mc_equity(hole, board, combos, weights, rng, deadline,
              max_samples=2000, min_samples=200):
    """
    Equity of hole against the combos (pairs of card ints) weighted by
    weights, with the board (0, 3 or 4 cards) completed at random; exact on
    the river. Stops at max_samples, or once past deadline (a
    time.perf_counter() value) with at least min_samples. 0.5 when no combo
    is possible.
    """
    h1, h2 = hole
    dead = set(board)
    dead.add(h1)
    dead.add(h2)
    live = []
    cum = []
    total = 0.0
    for (a, b), w in zip(combos, weights):
        if w > 0.0 and a not in dead and b not in dead:
            total += w
            live.append((a, b))
            cum.append(total)
    if not live:
        return 0.5
    ck = CARD_KEY
    nf = NO_FLUSH
    m39 = MASK39
    board = list(board)
    bk = 0
    for c in board:
        bk += ck[c]
    hk = ck[h1] + ck[h2]
    need = 5 - len(board)

    if need <= 0:  # the river: count every combo
        km = bk + hk
        f = ((km >> 39) + 0x3333) & 0x8888
        mine = _flush_value(f, board, h1, h2) if f else nf[km & m39]
        won = 0.0
        prev = 0.0
        for i, (a, b) in enumerate(live):
            w = cum[i] - prev
            prev = cum[i]
            kt = bk + ck[a] + ck[b]
            f = ((kt >> 39) + 0x3333) & 0x8888
            theirs = _flush_value(f, board, a, b) if f else nf[kt & m39]
            if mine > theirs:
                won += w
            elif mine == theirs:
                won += 0.5 * w
        return won / total

    deck = [c for c in range(52) if c not in dead]
    nd = len(deck)
    rand = rng.random
    perf = time.perf_counter
    find = bisect.bisect_left
    won = 0
    n = 0
    while n < max_samples:
        if (n & 63) == 0 and n >= min_samples and perf() > deadline:
            break
        a, b = live[find(cum, rand() * total)]
        run = []
        k = bk
        while len(run) < need:
            c = deck[int(rand() * nd)]
            if c != a and c != b and c not in run:
                run.append(c)
                k += ck[c]
        km = k + hk
        f = ((km >> 39) + 0x3333) & 0x8888
        mine = _flush_value(f, board + run, h1, h2) if f else nf[km & m39]
        kt = k + ck[a] + ck[b]
        f = ((kt >> 39) + 0x3333) & 0x8888
        theirs = _flush_value(f, board + run, a, b) if f else nf[kt & m39]
        if mine > theirs:
            won += 2
        elif mine == theirs:
            won += 1
        n += 1
    if n == 0:
        return 0.5
    return won / (2.0 * n)


def mc_profile(hole, board, combos, weights, groups, nb, rng, deadline,
               max_samples=2000, min_samples=200):
    """
    mc_equity, also split by group: groups[i] (0..nb-1) is combo i's
    group, e.g. its strength band within the range. Returns (eq, wins,
    counts): wins[g] / counts[g] is our equity against group g. Exact on
    the river (counts are weights there).
    """
    h1, h2 = hole
    dead = set(board)
    dead.add(h1)
    dead.add(h2)
    live = []
    cum = []
    total = 0.0
    for i, (a, b) in enumerate(combos):
        w = weights[i]
        if w > 0.0 and a not in dead and b not in dead:
            total += w
            live.append((a, b, groups[i]))
            cum.append(total)
    wins = [0.0] * nb
    counts = [0.0] * nb
    if not live:
        return 0.5, wins, counts
    ck = CARD_KEY
    nf = NO_FLUSH
    m39 = MASK39
    board = list(board)
    bk = 0
    for c in board:
        bk += ck[c]
    hk = ck[h1] + ck[h2]
    need = 5 - len(board)

    if need <= 0:
        km = bk + hk
        f = ((km >> 39) + 0x3333) & 0x8888
        mine = _flush_value(f, board, h1, h2) if f else nf[km & m39]
        prev = 0.0
        for i, (a, b, g) in enumerate(live):
            w = cum[i] - prev
            prev = cum[i]
            kt = bk + ck[a] + ck[b]
            f = ((kt >> 39) + 0x3333) & 0x8888
            theirs = _flush_value(f, board, a, b) if f else nf[kt & m39]
            counts[g] += w
            if mine > theirs:
                wins[g] += w
            elif mine == theirs:
                wins[g] += 0.5 * w
        return sum(wins) / total, wins, counts

    deck = [c for c in range(52) if c not in dead]
    nd = len(deck)
    rand = rng.random
    perf = time.perf_counter
    find = bisect.bisect_left
    n = 0
    while n < max_samples:
        if (n & 63) == 0 and n >= min_samples and perf() > deadline:
            break
        a, b, g = live[find(cum, rand() * total)]
        run = []
        k = bk
        while len(run) < need:
            c = deck[int(rand() * nd)]
            if c != a and c != b and c not in run:
                run.append(c)
                k += ck[c]
        km = k + hk
        f = ((km >> 39) + 0x3333) & 0x8888
        mine = _flush_value(f, board + run, h1, h2) if f else nf[km & m39]
        kt = k + ck[a] + ck[b]
        f = ((kt >> 39) + 0x3333) & 0x8888
        theirs = _flush_value(f, board + run, a, b) if f else nf[kt & m39]
        counts[g] += 1.0
        if mine > theirs:
            wins[g] += 1.0
        elif mine == theirs:
            wins[g] += 0.5
        n += 1
    if n == 0:
        return 0.5, wins, counts
    return sum(wins) / n, wins, counts
