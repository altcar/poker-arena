"""Pure-Python hold'em hand evaluator (stdlib only, any Python 3).

Cards are ints 0..51: rank = c >> 2 (0 = deuce ... 12 = ace), suit = c & 3.
evaluate(cards) takes 3 to 7 cards and returns an int: higher is better,
equal means a tie. category(score) gives 0 (high card) .. 8 (straight flush).
"""

RANKS = "23456789TJQKA"
SUITS = "cdhs"

(HIGH_CARD, PAIR, TWO_PAIR, TRIPS, STRAIGHT,
 FLUSH, FULL_HOUSE, QUADS, STRAIGHT_FLUSH) = range(9)
CATEGORY_NAMES = ["high card", "pair", "two pair", "trips", "straight",
                  "flush", "full house", "quads", "straight flush"]


def card(s):
    """'As' -> int."""
    return (RANKS.index(s[0].upper()) << 2) | SUITS.index(s[1].lower())


def cards(text):
    """'As Kd 7h' -> [ints]."""
    return [card(t) for t in text.split()]


def card_str(c):
    return RANKS[c >> 2] + SUITS[c & 3]


def _build_straight_table():
    table = [-1] * 8192
    wheel = 0b1000000001111  # A2345
    for m in range(8192):
        for top in range(12, 3, -1):
            w = 0b11111 << (top - 4)
            if m & w == w:
                table[m] = top
                break
        else:
            if m & wheel == wheel:
                table[m] = 3
    return table


STRAIGHT_HIGH = _build_straight_table()  # rank mask -> top rank of best straight, or -1
POPCOUNT = [bin(m).count("1") for m in range(8192)]  # works on any Python 3 (no int.bit_count)


def _top(mask, n):
    out = []
    r = 12
    while r >= 0 and len(out) < n:
        if (mask >> r) & 1:
            out.append(r)
        r -= 1
    return out


def _pack(cat, ranks):
    k = 0
    for r in ranks:
        k = (k << 4) | r
    return (cat << 20) | (k << (4 * (5 - len(ranks))))


def evaluate(cs):
    counts = [0] * 13
    sm = [0, 0, 0, 0]
    for c in cs:
        r = c >> 2
        counts[r] += 1
        sm[c & 3] |= 1 << r

    # With at most 7 cards, a flush rules out quads and full houses.
    for m in sm:
        if POPCOUNT[m] >= 5:
            sh = STRAIGHT_HIGH[m]
            if sh >= 0:
                return _pack(STRAIGHT_FLUSH, [sh])
            return _pack(FLUSH, _top(m, 5))

    mask = sm[0] | sm[1] | sm[2] | sm[3]
    quad = -1
    trips = []
    pairs = []
    for r in range(12, -1, -1):
        n = counts[r]
        if n == 4:
            quad = r
        elif n == 3:
            trips.append(r)
        elif n == 2:
            pairs.append(r)

    if quad >= 0:
        return _pack(QUADS, [quad] + _top(mask & ~(1 << quad), 1))
    if trips and (len(trips) > 1 or pairs):
        t = trips[0]
        p = max(trips[1] if len(trips) > 1 else -1, pairs[0] if pairs else -1)
        return _pack(FULL_HOUSE, [t, p])
    sh = STRAIGHT_HIGH[mask]
    if sh >= 0:
        return _pack(STRAIGHT, [sh])
    if trips:
        t = trips[0]
        return _pack(TRIPS, [t] + _top(mask & ~(1 << t), 2))
    if len(pairs) >= 2:
        a, b = pairs[0], pairs[1]
        return _pack(TWO_PAIR, [a, b] + _top(mask & ~(1 << a) & ~(1 << b), 1))
    if pairs:
        p = pairs[0]
        return _pack(PAIR, [p] + _top(mask & ~(1 << p), 3))
    return _pack(HIGH_CARD, _top(mask, 5))


def category(score):
    return score >> 20
