"""
Fast poker hand evaluator for 5 to 7 card ints (see pk_cards).

rank_cards(cards) returns one int: bigger wins, equal splits. It orders
hands exactly as card_utils.evaluate does (tools/test_pk_eval.py checks
this). value >> 20 is the category, 0 (high card) to 8 (straight flush),
the same numbers as card_utils.CATEGORIES.

How it works: each card adds CARD_KEY[c] to a key that holds a 3-bit count
per rank (bits 0..38) and a 4-bit count per suit (bits 39..54). A suit
with 5 or more cards means a flush, and then nothing but a straight flush
can beat it (with 7 cards a flush leaves no room for quads or a full
house), so the hand is looked up by that suit's 13-bit rank mask. Any other
hand is looked up by its rank counts. Both tables are built at import.
"""

from itertools import combinations_with_replacement

MASK39 = (1 << 39) - 1
FLUSH_TEST = 0x3333  # + suit counts: bit 3 of a field is set at 5 or more
FLUSH_BITS = 0x8888
FLUSH_SUIT = {0x8: 0, 0x80: 1, 0x800: 2, 0x8000: 3}

CARD_KEY = [(1 << (3 * (c >> 2))) + (1 << (39 + 4 * (c & 3)))
            for c in range(52)]
RANK_BIT = [1 << (c >> 2) for c in range(52)]


def _encode(category, values):
    """category and up to 5 tie-break card values (2..14) as one int."""
    v = category
    for i in range(5):
        v = (v << 4) | (values[i] if i < len(values) else 0)
    return v


# Straights by rank mask: highest straight's top card value, or 0.
_STRAIGHTS = [(h + 2, 0b11111 << (h - 4)) for h in range(12, 3, -1)]
_STRAIGHTS.append((5, (1 << 12) | 0b1111))  # the wheel, A-2-3-4-5
STRAIGHT_HIGH = [0] * 8192
for _m in range(8192):
    for _high, _bits in _STRAIGHTS:
        if _m & _bits == _bits:
            STRAIGHT_HIGH[_m] = _high
            break

# Flush (or straight flush) by the flush suit's rank mask (5+ bits set).
FLUSH = [0] * 8192
for _m in range(8192):
    _ranks = [r + 2 for r in range(12, -1, -1) if _m >> r & 1]
    if len(_ranks) >= 5:
        if STRAIGHT_HIGH[_m]:
            FLUSH[_m] = _encode(8, (STRAIGHT_HIGH[_m],))
        else:
            FLUSH[_m] = _encode(5, _ranks[:5])


def _no_flush_value(groups, mask):
    """Best hand, no flush possible. groups: [count, rank index] sorted
    biggest count first, then highest rank; mask: the ranks present."""
    c0, r0 = groups[0]
    if c0 == 4:
        kicker = max(g[1] for g in groups[1:])
        return (7 << 20) | ((r0 + 2) << 16) | ((kicker + 2) << 12)
    if c0 == 3 and groups[1][0] >= 2:
        return (6 << 20) | ((r0 + 2) << 16) | ((groups[1][1] + 2) << 12)
    high = STRAIGHT_HIGH[mask]
    if high:
        return (4 << 20) | (high << 16)
    # Kickers: the other ranks present, highest first.
    if c0 == 3:
        k = sorted((g[1] for g in groups[1:]), reverse=True)
        return _encode(3, (r0 + 2, k[0] + 2, k[1] + 2))
    if c0 == 2 and groups[1][0] == 2:
        r1 = groups[1][1]
        kicker = max(g[1] for g in groups[2:])
        return _encode(2, (r0 + 2, r1 + 2, kicker + 2))
    if c0 == 2:
        k = sorted((g[1] for g in groups[1:]), reverse=True)
        return _encode(1, (r0 + 2, k[0] + 2, k[1] + 2, k[2] + 2))
    return _encode(0, [g[1] + 2 for g in groups[:5]])


def _build_no_flush():
    table = {}
    shift = [1 << (3 * r) for r in range(13)]
    for size in (5, 6, 7):
        # Highest rank first, so equal ranks come together.
        for ranks in combinations_with_replacement(range(12, -1, -1), size):
            groups = []
            prev = -1
            key = 0
            mask = 0
            for r in ranks:
                key += shift[r]
                if r == prev:
                    groups[-1][0] += 1
                else:
                    groups.append([1, r])
                    mask |= 1 << r
                    prev = r
            groups.sort(reverse=True)
            if groups[0][0] <= 4:
                table[key] = _no_flush_value(groups, mask)
    return table


NO_FLUSH = _build_no_flush()
del _m, _ranks, _high, _bits


def rank_cards(cards):
    """The value of the best 5-card hand among 5 to 7 card ints."""
    k = 0
    for c in cards:
        k += CARD_KEY[c]
    f = ((k >> 39) + FLUSH_TEST) & FLUSH_BITS
    if f:
        s = FLUSH_SUIT[f]
        m = 0
        for c in cards:
            if c & 3 == s:
                m |= RANK_BIT[c]
        return FLUSH[m]
    return NO_FLUSH[k & MASK39]


def rank_key(k, cards):
    """rank_cards(cards) when k, the sum of CARD_KEY over cards, is known:
    lets a caller reuse the board's part of the key."""
    f = ((k >> 39) + FLUSH_TEST) & FLUSH_BITS
    if f:
        s = FLUSH_SUIT[f]
        m = 0
        for c in cards:
            if c & 3 == s:
                m |= RANK_BIT[c]
        return FLUSH[m]
    return NO_FLUSH[k & MASK39]


def category(value):
    """0 (high card) .. 8 (straight flush), as in card_utils.CATEGORIES."""
    return value >> 20
