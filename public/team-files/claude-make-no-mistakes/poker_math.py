"""Dependency-free 5..7 card evaluator and range features. Cards are 0..51."""
from bisect import bisect_left, bisect_right


def encode(card):
    return (card.value - 2) * 4 + 'CDHS'.index(card.suit)


def straight(mask):
    for high in range(12, 3, -1):
        if (mask >> (high - 4)) & 31 == 31:
            return high + 2
    return 5 if mask & 4111 == 4111 else 0


def rank(cards):
    counts = [0] * 15
    suits = [0] * 4
    mask = 0
    for c in cards:
        r = c // 4 + 2
        counts[r] += 1
        suits[c % 4] |= 1 << (r - 2)
        mask |= 1 << (r - 2)
    values = [r for r in range(14, 1, -1) if counts[r]]
    flush = next((s for s in suits if s.bit_count() >= 5), 0)
    if flush:
        s = straight(flush)
        if s:
            return (8, s)
    quads = [r for r in values if counts[r] == 4]
    if quads:
        return (7, quads[0], next(r for r in values if r != quads[0]))
    trips = [r for r in values if counts[r] >= 3]
    pairs = [r for r in values if counts[r] >= 2]
    if trips and len(pairs) >= 2:
        return (6, trips[0], next(r for r in pairs if r != trips[0]))
    if flush:
        return (5, *[r for r in range(14, 1, -1) if flush & (1 << (r - 2))][:5])
    s = straight(mask)
    if s:
        return (4, s)
    if trips:
        return (3, trips[0], *[r for r in values if r != trips[0]][:2])
    if len(pairs) >= 2:
        return (2, *pairs[:2], next(r for r in values if r not in pairs[:2]))
    if pairs:
        return (1, pairs[0], *[r for r in values if r != pairs[0]][:3])
    return (0, *values[:5])


def hand_key(a, b):
    hi, lo = sorted((a // 4 + 2, b // 4 + 2), reverse=True)
    rs = '23456789TJQKA'
    return rs[hi - 2] + rs[lo - 2] + ('' if hi == lo else 's' if a % 4 == b % 4 else 'o')


def pre_strength(a, b):
    """Ordering heuristic only; never represented as equilibrium equity."""
    hi, lo = sorted((a // 4 + 2, b // 4 + 2), reverse=True)
    if hi == lo:
        return .50 + hi * .025
    suited = a % 4 == b % 4
    return .20 + hi * .021 + lo * .009 + .035 * suited + .018 * (hi-lo == 1) + .008 * (hi-lo == 2) + .018 * (hi == 14)


def draw_strength(hole, board):
    if len(board) >= 5:
        return 0.0
    cards = list(hole) + list(board)
    sc = [sum(c % 4 == s for c in cards) for s in range(4)]
    fd = any(sc[s] == 4 and any(c % 4 == s for c in hole) for s in range(4))
    mask = 0
    for c in cards:
        mask |= 1 << (c // 4)
    outs = 0
    if not straight(mask):
        for r in range(13):
            if not mask & (1 << r) and straight(mask | (1 << r)):
                outs += 4
    return min(.30, .16 * fd + .015 * outs)


def board_features(hole, board):
    """Current-hand percentile against legal random holdings plus draw proxy."""
    blocked = set(hole) | set(board)
    deck = [c for c in range(52) if c not in blocked]
    combos = [(a, b) for i, a in enumerate(deck) for b in deck[i+1:]]
    ranks = [rank(list(h) + list(board)) for h in combos]
    ordered = sorted(ranks)
    n = len(ordered)
    strength = [(bisect_left(ordered, r) + bisect_right(ordered, r)) / (2*n) for r in ranks]
    draw = [draw_strength(h, board) for h in combos]
    return combos, ranks, strength, draw
