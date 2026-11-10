"""Fast pure-Python hand evaluation and equity helpers.

Cards are ints 0..51: rank index (0 = deuce .. 12 = ace) * 4 + suit index.
eval7 returns an int: higher wins, equal splits.
"""

SUITS = "CDHS"


def card_to_int(card):
    return (card.value - 2) * 4 + SUITS.index(card.suit)


def _build_straights():
    table = [-1] * 8192
    for mask in range(8192):
        best = -1
        for top in range(12, 3, -1):
            need = 0b11111 << (top - 4)
            if mask & need == need:
                best = top
                break
        if best < 0 and mask & 0b1000000001111 == 0b1000000001111:
            best = 3  # wheel, five high
        table[mask] = best
    return table


STRAIGHT = _build_straights()


def _top_bits(mask, n):
    out = 0
    got = 0
    r = 12
    while got < n and r >= 0:
        if mask >> r & 1:
            out = (out << 4) | r
            got += 1
        r -= 1
    while got < n:
        out <<= 4
        got += 1
    return out


def eval7(cards):
    """Rank of the best 5-card hand among 5 to 7 int cards."""
    counts = [0] * 13
    sm = [0, 0, 0, 0]
    sc = [0, 0, 0, 0]
    rmask = 0
    for c in cards:
        r = c >> 2
        s = c & 3
        counts[r] += 1
        sm[s] |= 1 << r
        sc[s] += 1
        rmask |= 1 << r
    for s in range(4):
        if sc[s] >= 5:
            fm = sm[s]
            sf = STRAIGHT[fm]
            if sf >= 0:
                return (8 << 20) | sf
            # a flush beats everything except quads / full house; check those
            flush_score = (5 << 20) | _top_bits(fm, 5)
            break
    else:
        flush_score = 0
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
        kick = 0
        for r in range(12, -1, -1):
            if r != quad and counts[r]:
                kick = r
                break
        return (7 << 20) | (quad << 4) | kick
    if trips and (len(trips) >= 2 or pairs):
        t = trips[0]
        p = trips[1] if len(trips) >= 2 else -1
        if pairs and pairs[0] > p:
            p = pairs[0]
        return (6 << 20) | (t << 4) | p
    if flush_score:
        return flush_score
    st = STRAIGHT[rmask]
    if st >= 0:
        return (4 << 20) | st
    if trips:
        t = trips[0]
        return (3 << 20) | (t << 8) | _top_bits(rmask & ~(1 << t), 2)
    if len(pairs) >= 2:
        p1, p2 = pairs[0], pairs[1]
        return (2 << 20) | (p1 << 8) | (p2 << 4) | (
            _top_bits(rmask & ~(1 << p1) & ~(1 << p2), 1))
    if pairs:
        p = pairs[0]
        return (1 << 20) | (p << 12) | _top_bits(rmask & ~(1 << p), 3)
    return _top_bits(rmask, 5)


def category(score):
    return score >> 20


# All 1326 two-card combos as (a, b) with a > b.
COMBOS = [(a, b) for a in range(52) for b in range(a)]


def hand_class(a, b):
    """169-class index string like 'AKs', 'T9o', '77'."""
    ranks = "23456789TJQKA"
    ra, rb = a >> 2, b >> 2
    if ra < rb:
        ra, rb = rb, ra
    if ra == rb:
        return ranks[ra] * 2
    suited = (a & 3) == (b & 3)
    return ranks[ra] + ranks[rb] + ("s" if suited else "o")
