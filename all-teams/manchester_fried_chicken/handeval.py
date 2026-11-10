"""
Fast hand evaluation on integer cards, plus Monte Carlo equity.

A card is an int 0..51: rank index (0 = deuce .. 12 = ace) * 4 + suit.
evaluate_ids(cards) returns an int; higher wins, equal splits. Same order
as card_utils.evaluate (checked in tests), but ~50x faster.

Tables are built once when this module is imported (pure computation, no
files): a prime-product table for non-flush hands of 5 to 7 cards and a
bitmask table for flushes.
"""

import itertools

PRIMES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41)
SUIT_CHARS = "CDHS"
RANK_CHARS = "23456789TJQKA"

CARD_PRIME = tuple(PRIMES[c >> 2] for c in range(52))
CARD_SUITBIT = tuple(1 << (4 * (c & 3)) for c in range(52))
CARD_RANKBIT = tuple(1 << (c >> 2) for c in range(52))

# Score layout: category << 20 | five kickers of 4 bits each.
CAT_SHIFT = 20


def _kick(ranks):
    v = 0
    for i, r in enumerate(ranks[:5]):
        v |= r << (4 * (4 - i))
    return v


def _straight_high(mask):
    """Highest rank index topping a straight in a 13-bit rank mask, or -1."""
    for top in range(12, 3, -1):
        need = 0x1F << (top - 4)
        if mask & need == need:
            return top
    if mask & 0x100F == 0x100F:  # A-2-3-4-5
        return 3
    return -1


STRAIGHT_HIGH = tuple(_straight_high(m) for m in range(8192))


def _score_counts(counts):
    """Best non-flush 5-card score from rank counts (index 0..12)."""
    mask = 0
    for r in range(13):
        if counts[r]:
            mask |= 1 << r
    desc = [r for r in range(12, -1, -1) if counts[r]]
    quads = [r for r in desc if counts[r] == 4]
    trips = [r for r in desc if counts[r] == 3]
    pairs = [r for r in desc if counts[r] == 2]
    if quads:
        q = quads[0]
        kick = [r for r in desc if r != q][:1]
        return (7 << CAT_SHIFT) | _kick([q] + kick)
    if trips and (len(trips) >= 2 or pairs):
        t = trips[0]
        p = max(trips[1] if len(trips) > 1 else -1,
                pairs[0] if pairs else -1)
        return (6 << CAT_SHIFT) | _kick([t, p])
    sh = STRAIGHT_HIGH[mask]
    if sh >= 0:
        return (4 << CAT_SHIFT) | _kick([sh])
    if trips:
        t = trips[0]
        return (3 << CAT_SHIFT) | _kick([t] + [r for r in desc if r != t][:2])
    if len(pairs) >= 2:
        a, b = pairs[0], pairs[1]
        kick = [r for r in desc if r != a and r != b][:1]
        return (2 << CAT_SHIFT) | _kick([a, b] + kick)
    if pairs:
        p = pairs[0]
        return (1 << CAT_SHIFT) | _kick([p] + [r for r in desc if r != p][:3])
    return _kick(desc[:5])


def _build_nonflush():
    table = {}
    counts = [0] * 13

    def rec(start, left, prod):
        if left == 0:
            return
        for r in range(start, 13):
            if counts[r] < 4:
                counts[r] += 1
                p = prod * PRIMES[r]
                total = sum(counts)
                if total >= 5:
                    table[p] = _score_counts(counts)
                rec(r, left - 1, p)
                counts[r] -= 1

    rec(0, 7, 1)
    return table


def _flush_score(mask):
    sh = STRAIGHT_HIGH[mask]
    if sh >= 0:
        return (8 << CAT_SHIFT) | _kick([sh])
    desc = [r for r in range(12, -1, -1) if mask >> r & 1]
    return (5 << CAT_SHIFT) | _kick(desc[:5])


NONFLUSH = _build_nonflush()
FLUSH = {}
for _m in range(8192):
    if bin(_m).count("1") >= 5:
        FLUSH[_m] = _flush_score(_m)


def evaluate_ids(cards):
    """Score of the best 5-card hand in 5 to 7 int cards."""
    p = 1
    s = 0
    for c in cards:
        p *= CARD_PRIME[c]
        s += CARD_SUITBIT[c]
    f = (s + 0x3333) & 0x8888
    if f:
        suit = (f.bit_length() - 4) >> 2
        mask = 0
        for c in cards:
            if c & 3 == suit:
                mask |= CARD_RANKBIT[c]
        return FLUSH[mask]
    return NONFLUSH[p]


def category_of(score):
    return score >> CAT_SHIFT


def to_id(card):
    """card_utils.Card -> int."""
    return (card.value - 2) * 4 + SUIT_CHARS.index(card.suit)


def id_str(c):
    return RANK_CHARS[c >> 2] + SUIT_CHARS[c & 3]


# --- preflop hand classes (13x13 grid) -------------------------------------
# index = row * 13 + col with rank indexes; pairs on the diagonal, suited
# above it (row > col), offsuit below (row < col).


def class_of(a, b):
    ra, rb = a >> 2, b >> 2
    hi, lo = (ra, rb) if ra >= rb else (rb, ra)
    if hi == lo:
        return hi * 13 + hi
    if (a & 3) == (b & 3):
        return hi * 13 + lo
    return lo * 13 + hi


def class_name(i):
    r, c = divmod(i, 13)
    if r == c:
        return RANK_CHARS[r] * 2
    if r > c:
        return RANK_CHARS[r] + RANK_CHARS[c] + "s"
    return RANK_CHARS[c] + RANK_CHARS[r] + "o"


ALL_COMBOS = tuple(itertools.combinations(range(52), 2))
CLASS_COMBOS = [[] for _ in range(169)]
for _a, _b in ALL_COMBOS:
    CLASS_COMBOS[class_of(_a, _b)].append((_a, _b))
CLASS_COMBOS = tuple(tuple(x) for x in CLASS_COMBOS)
CLASS_SIZE = tuple(len(x) for x in CLASS_COMBOS)
