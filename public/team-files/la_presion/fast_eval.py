"""
Fast 7-card hand evaluator for Monte Carlo equity.

Cards are small ints: (value << 2) | suit, value 2..14 (ace high), suit
0..3. eval7 returns one int per hand; bigger is stronger and equal ints
tie, with exactly the same ordering as card_utils.evaluate (checked by
dev_check_eval.py).
"""

from card_utils import Card

SUIT_INDEX = {"C": 0, "D": 1, "H": 2, "S": 3}

ALL_CARDS = [v << 2 | s for v in range(2, 15) for s in range(4)]

WHEEL = (1 << 14) | (1 << 5) | (1 << 4) | (1 << 3) | (1 << 2)


def card_int(card: Card) -> int:
    """A card_utils.Card as this module's packed int."""
    return card.value << 2 | SUIT_INDEX[card.suit]


def eval7(cards) -> int:
    """
    Strength of the best 5-card hand among 5 to 7 packed-int cards.

    The result packs (category, tiebreaks) into one int: category in the
    top bits, then up to five 4-bit ranks, most significant first.
    """
    rc = [0] * 15
    sc = [0, 0, 0, 0]
    sm = [0, 0, 0, 0]
    m = 0
    for c in cards:
        v = c >> 2
        s = c & 3
        rc[v] += 1
        sc[s] += 1
        sm[s] |= 1 << v
        m |= 1 << v

    # Flush and straight flush. With 7 cards a flush rules out quads and
    # full houses (not enough cards for both), so returning early is safe.
    for s in range(4):
        if sc[s] >= 5:
            fm = sm[s]
            x = fm & (fm >> 1) & (fm >> 2) & (fm >> 3) & (fm >> 4)
            if x:
                return (8 << 20) | ((x.bit_length() + 3) << 16)
            if fm & WHEEL == WHEEL:
                return (8 << 20) | (5 << 16)
            out = 0
            n = 0
            v = 14
            while n < 5:
                if fm >> v & 1:
                    out = out << 4 | v
                    n += 1
                v -= 1
            return (5 << 20) | out

    quad = 0
    trips = 0
    trips2 = 0
    p1 = 0
    p2 = 0
    for v in range(14, 1, -1):
        n = rc[v]
        if n == 2:
            if not p1:
                p1 = v
            elif not p2:
                p2 = v
        elif n == 3:
            if not trips:
                trips = v
            elif not trips2:
                trips2 = v
        elif n == 4:
            quad = v

    if quad:
        for v in range(14, 1, -1):
            if v != quad and rc[v]:
                return (7 << 20) | (quad << 16) | (v << 12)
        return (7 << 20) | (quad << 16)

    if trips:
        pair = trips2 if trips2 > p1 else p1
        if pair:
            return (6 << 20) | (trips << 16) | (pair << 12)

    x = m & (m >> 1) & (m >> 2) & (m >> 3) & (m >> 4)
    if x:
        return (4 << 20) | ((x.bit_length() + 3) << 16)
    if m & WHEEL == WHEEL:
        return (4 << 20) | (5 << 16)

    if trips:
        out = (3 << 20) | (trips << 16)
        shift = 12
        for v in range(14, 1, -1):
            if v != trips and rc[v]:
                out |= v << shift
                if shift == 8:
                    break
                shift = 8
        return out

    if p2:
        for v in range(14, 1, -1):
            if v != p1 and v != p2 and rc[v]:
                return (2 << 20) | (p1 << 16) | (p2 << 12) | (v << 8)
        return (2 << 20) | (p1 << 16) | (p2 << 12)

    if p1:
        out = (1 << 20) | (p1 << 16)
        shift = 12
        for v in range(14, 1, -1):
            if v != p1 and rc[v]:
                out |= v << shift
                if shift == 4:
                    break
                shift -= 4
        return out

    out = 0
    n = 0
    for v in range(14, 1, -1):
        if rc[v]:
            out = out << 4 | v
            n += 1
            if n == 5:
                break
    return out
