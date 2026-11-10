"""
Fast 5-7 card hand scores for the bot (no validation: trusted input only).

Cards are ints 0..51: rank index (card >> 2) is 0..12 for 2..A, suit is
card & 3. score() returns an int that orders hands exactly like
card_utils.evaluate: category << 20, then tie-break ranks in 4-bit slots.
check_hand_eval.py compares the two on random hands.
"""

RANK_CHARS = "23456789TJQKA"
SUIT_CHARS = "CDHS"

# Straights as rank bitmasks, highest first; the wheel (A2345) is 5-high.
_STRAIGHTS = [(0b11111 << (high - 4), high) for high in range(12, 3, -1)]
_STRAIGHTS.append(((1 << 12) | 0b1111, 3))


def card_int(card) -> int:
    """A card_utils.Card as an int 0..51."""
    return (RANK_CHARS.index(card.rank) << 2) | SUIT_CHARS.index(card.suit)


def straight_high(mask: int) -> int:
    for bits, high in _STRAIGHTS:
        if mask & bits == bits:
            return high
    return -1


def score(cards) -> int:
    """The strength of the best 5-card hand among 5 to 7 int cards."""
    counts = [0] * 13
    suits = [0, 0, 0, 0]
    ranks = 0
    for c in cards:
        r = c >> 2
        counts[r] += 1
        suits[c & 3] |= 1 << r
        ranks |= 1 << r

    flush = 0
    for mask in suits:
        if mask.bit_count() >= 5:
            flush = mask
            break
    if flush:
        high = straight_high(flush)
        if high >= 0:
            return (8 << 20) | high

    quad = -1
    trips = []
    pairs = []
    singles = []
    for r in range(12, -1, -1):
        n = counts[r]
        if n == 1:
            singles.append(r)
        elif n == 2:
            pairs.append(r)
        elif n == 3:
            trips.append(r)
        elif n == 4:
            quad = r

    if quad >= 0:
        kicker = -1
        for r in range(12, -1, -1):
            if r != quad and counts[r]:
                kicker = r
                break
        return (7 << 20) | (quad << 4) | kicker
    if trips and (len(trips) > 1 or pairs):
        second = trips[1] if len(trips) > 1 else -1
        if pairs and pairs[0] > second:
            second = pairs[0]
        return (6 << 20) | (trips[0] << 4) | second
    if flush:
        out = 5
        taken = 0
        for r in range(12, -1, -1):
            if flush >> r & 1:
                out = (out << 4) | r
                taken += 1
                if taken == 5:
                    break
        return out
    high = straight_high(ranks)
    if high >= 0:
        return (4 << 20) | high
    if trips:
        return (3 << 20) | (trips[0] << 8) | (singles[0] << 4) | singles[1]
    if len(pairs) >= 2:
        kicker = singles[0] if singles else -1
        if len(pairs) > 2 and pairs[2] > kicker:
            kicker = pairs[2]
        return (2 << 20) | (pairs[0] << 8) | (pairs[1] << 4) | kicker
    if pairs:
        return ((1 << 20) | (pairs[0] << 12) | (singles[0] << 8)
                | (singles[1] << 4) | singles[2])
    return ((singles[0] << 16) | (singles[1] << 12) | (singles[2] << 8)
            | (singles[3] << 4) | singles[4])


def category(value: int) -> int:
    """card_utils.CATEGORIES index of a score: 0 high card .. 8 straight
    flush."""
    return value >> 20
