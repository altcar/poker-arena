"""
Fast pure-Python hand evaluator for Monte Carlo work.

Cards are ints 0..51: rank * 4 + suit, rank 0 = deuce .. 12 = ace.
evaluate(cards) takes 5 to 7 card ints and returns an int: higher wins,
equal ties. The category is score >> 20 (0 high card .. 8 straight flush),
matching card_utils.CATEGORIES.
"""

RANK_CHARS = "23456789TJQKA"
SUIT_CHARS = "CDHS"


def _straight_high(mask):
    for high in range(12, 3, -1):
        window = 0b11111 << (high - 4)
        if mask & window == window:
            return high
    wheel = (1 << 12) | 0b1111
    if mask & wheel == wheel:
        return 3
    return -1


def _top5(mask):
    packed = 0
    shift = 16
    for rank in range(12, -1, -1):
        if mask & (1 << rank):
            packed |= rank << shift
            shift -= 4
            if shift < 0:
                break
    return packed


STRAIGHT = [_straight_high(m) for m in range(1 << 13)]
TOP5 = [_top5(m) for m in range(1 << 13)]
POPCOUNT = [bin(m).count("1") for m in range(1 << 13)]


def card_int(card):
    """A card_utils.Card (or "AS"-style string) as an int."""
    text = str(card)
    return RANK_CHARS.index(text[0]) * 4 + SUIT_CHARS.index(text[1])


def evaluate(cards):
    counts = [0] * 13
    s0 = s1 = s2 = s3 = 0
    for c in cards:
        r = c >> 2
        counts[r] += 1
        s = c & 3
        if s == 0:
            s0 |= 1 << r
        elif s == 1:
            s1 |= 1 << r
        elif s == 2:
            s2 |= 1 << r
        else:
            s3 |= 1 << r
    # With at most 7 cards a flush excludes quads and full houses.
    for m in (s0, s1, s2, s3):
        if POPCOUNT[m] >= 5:
            sh = STRAIGHT[m]
            if sh >= 0:
                return (8 << 20) | sh
            return (5 << 20) | TOP5[m]
    mask = s0 | s1 | s2 | s3
    quads = -1
    trips = -1
    trips2 = -1
    pair1 = -1
    pair2 = -1
    for r in range(12, -1, -1):
        n = counts[r]
        if n < 2:
            continue
        if n == 4:
            quads = r
        elif n == 3:
            if trips < 0:
                trips = r
            elif trips2 < 0:
                trips2 = r
        elif pair1 < 0:
            pair1 = r
        elif pair2 < 0:
            pair2 = r
    if quads >= 0:
        rest = mask & ~(1 << quads)
        return (7 << 20) | (quads << 16) | ((TOP5[rest] >> 16) << 12)
    if trips >= 0 and (trips2 >= 0 or pair1 >= 0):
        p = trips2 if trips2 > pair1 else pair1
        return (6 << 20) | (trips << 16) | (p << 12)
    sh = STRAIGHT[mask]
    if sh >= 0:
        return (4 << 20) | sh
    if trips >= 0:
        rest = mask & ~(1 << trips)
        return (3 << 20) | (trips << 16) | ((TOP5[rest] >> 12) << 8)
    if pair2 >= 0:
        rest = mask & ~(1 << pair1) & ~(1 << pair2)
        return ((2 << 20) | (pair1 << 16) | (pair2 << 12)
                | ((TOP5[rest] >> 16) << 8))
    if pair1 >= 0:
        rest = mask & ~(1 << pair1)
        return (1 << 20) | (pair1 << 16) | ((TOP5[rest] >> 8) << 4)
    return TOP5[mask]
