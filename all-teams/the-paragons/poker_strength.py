"""Pure Python poker mathematics. No engine internals or extra packages."""

from bisect import bisect_right
from itertools import combinations


def _straight(mask):
    for high in range(12, 3, -1):
        if mask & (31 << (high - 4)) == 31 << (high - 4):
            return high + 2
    return 5 if mask & 4111 == 4111 else 0


STRAIGHTS = tuple(_straight(mask) for mask in range(8192))
COMBOS = tuple(combinations(range(52), 2))


def card_id(card):
    return (card.value - 2) * 4 + "CDHS".index(card.suit)


def _pack(category, values):
    result = category << 20
    for i, value in enumerate(values):
        result |= value << (16 - 4 * i)
    return result


def rank_hand(cards):
    """Comparable integer for the best five of five to seven integer cards."""
    counts = [0] * 15
    suits = [0] * 4
    mask = 0
    for card in cards:
        value = card // 4 + 2
        bit = 1 << (value - 2)
        counts[value] += 1
        suits[card % 4] |= bit
        mask |= bit
    flush_mask = 0
    for suit in suits:
        if suit.bit_count() >= 5:
            flush_mask = suit
            straight = STRAIGHTS[suit]
            if straight:
                return _pack(8, [straight])
            break
    quads = [r for r in range(14, 1, -1) if counts[r] == 4]
    if quads:
        q = quads[0]
        return _pack(7, [q, next(r for r in range(14, 1, -1) if counts[r] and r != q)])
    trips = [r for r in range(14, 1, -1) if counts[r] >= 3]
    pairs = [r for r in range(14, 1, -1) if counts[r] >= 2]
    if trips:
        others = [r for r in pairs if r != trips[0]]
        if others:
            return _pack(6, [trips[0], others[0]])
    if flush_mask:
        return _pack(5, [r for r in range(14, 1, -1) if flush_mask & (1 << (r - 2))][:5])
    if STRAIGHTS[mask]:
        return _pack(4, [STRAIGHTS[mask]])
    if trips:
        t = trips[0]
        return _pack(3, [t] + [r for r in range(14, 1, -1) if counts[r] and r != t][:2])
    if len(pairs) >= 2:
        return _pack(2, pairs[:2] + [next(r for r in range(14, 1, -1) if counts[r] and r not in pairs[:2])])
    if pairs:
        p = pairs[0]
        return _pack(1, [p] + [r for r in range(14, 1, -1) if counts[r] and r != p][:3])
    return _pack(0, [r for r in range(14, 1, -1) if counts[r]][:5])


def preflop_score(hand):
    """Ordering for range construction; not a claimed solved strategy."""
    a, b = sorted((c // 4 + 2 for c in hand), reverse=True)
    suited = hand[0] % 4 == hand[1] % 4
    if a == b:
        return 0.50 + a * 0.025
    gap = a - b
    score = 0.17 + 0.025 * a + 0.012 * b
    score += 0.035 * suited
    score += {1: 0.025, 2: 0.012, 3: 0.0}.get(gap, -0.025)
    if a == 14:
        score += 0.025
        if b <= 5 and suited:
            score += 0.018
    return score


PREFLOP_SCORES = tuple(preflop_score(hand) for hand in COMBOS)
SORTED_PREFLOP = sorted(PREFLOP_SCORES)
PREFLOP_PERCENTILES = tuple(bisect_right(SORTED_PREFLOP, x) / len(COMBOS) for x in PREFLOP_SCORES)


def draw_potential(hand, board):
    """Small range-likelihood allowance for unmade flush/straight draws."""
    if len(board) >= 5:
        return 0.0
    cards = hand + tuple(board)
    suits = [0] * 4
    mask = 0
    for card in cards:
        suits[card % 4] += 1
        mask |= 1 << (card // 4)
    flush = any(suits[c % 4] == 4 for c in hand)
    windows = [31 << i for i in range(9)] + [4111]
    board_mask = 0
    for card in board:
        board_mask |= 1 << (card // 4)
    # Only unmade draws with a rank contributed by our hole cards count.
    straight = not STRAIGHTS[mask] and any(
        (mask & window).bit_count() == 4 and (mask & window & ~board_mask)
        for window in windows
    )
    return 0.13 * flush + 0.07 * straight
