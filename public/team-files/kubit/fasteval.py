"""
Fast hand evaluation for the bot, in plain Python.

Cards are ints 0..51: rank = c >> 2 (0 = deuce .. 12 = ace), suit = c & 3.
eval_cards(cards) ranks 5 to 7 cards in one pass (no 21 five-card
combinations like card_utils.evaluate) and returns one int: higher wins,
equal ties. The category is score >> 20 (0 high card .. 8 straight flush).
"""

RANK_CHARS = "23456789TJQKA"
SUIT_CHARS = "CDHS"

HIGH_CARD, PAIR, TWO_PAIR, TRIPS, STRAIGHT, FLUSH, FULL_HOUSE, QUADS, \
    STRAIGHT_FLUSH = range(9)


def _build_straight_table():
    """STRAIGHT_HIGH[mask] = top rank (0..12) of the best straight in a
    13-bit rank mask, or -1. The wheel A-2-3-4-5 is 5-high (rank 3)."""
    table = [-1] * 8192
    for mask in range(8192):
        for top in range(12, 3, -1):
            window = 0b11111 << (top - 4)
            if mask & window == window:
                table[mask] = top
                break
        else:
            wheel = (1 << 12) | 0b1111
            if mask & wheel == wheel:
                table[mask] = 3
    return table


STRAIGHT_HIGH = _build_straight_table()
POPCOUNT = [bin(m).count("1") for m in range(8192)]


def card_to_int(card) -> int:
    """A card_utils.Card to an int."""
    return RANK_CHARS.index(card.rank) * 4 + SUIT_CHARS.index(card.suit)


def int_to_str(c: int) -> str:
    return RANK_CHARS[c >> 2] + SUIT_CHARS[c & 3]


def str_to_int(text: str) -> int:
    return RANK_CHARS.index(text[0]) * 4 + SUIT_CHARS.index(text[1])


def _top_bits(mask: int, n: int) -> int:
    """The n highest ranks in mask, packed 4 bits each, highest first."""
    out = 0
    taken = 0
    r = 12
    while taken < n and r >= 0:
        if mask >> r & 1:
            out = (out << 4) | r
            taken += 1
        r -= 1
    return out << (4 * (n - taken))


def eval_cards(cards) -> int:
    """Rank 5 to 7 int cards: one int, higher is better."""
    counts = [0] * 13
    suit_masks = [0, 0, 0, 0]
    suit_counts = [0, 0, 0, 0]
    rmask = 0
    for c in cards:
        r = c >> 2
        s = c & 3
        counts[r] += 1
        suit_masks[s] |= 1 << r
        suit_counts[s] += 1
        rmask |= 1 << r

    # With 7 cards a flush rules out quads and full houses, so check it
    # first.
    for s in range(4):
        if suit_counts[s] >= 5:
            m = suit_masks[s]
            sf = STRAIGHT_HIGH[m]
            if sf >= 0:
                return (STRAIGHT_FLUSH << 20) | (sf << 16)
            return (FLUSH << 20) | _top_bits(m, 5)

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
        kicker = _top_bits(rmask & ~(1 << quad), 1)
        return (QUADS << 20) | (quad << 16) | (kicker << 12)
    if trips and (len(trips) >= 2 or pairs):
        t = trips[0]
        p = trips[1] if len(trips) >= 2 else -1
        if pairs and pairs[0] > p:
            p = pairs[0]
        return (FULL_HOUSE << 20) | (t << 16) | (p << 12)
    st = STRAIGHT_HIGH[rmask]
    if st >= 0:
        return (STRAIGHT << 20) | (st << 16)
    if trips:
        t = trips[0]
        kick = _top_bits(rmask & ~(1 << t), 2)
        return (TRIPS << 20) | (t << 16) | (kick << 8)
    if len(pairs) >= 2:
        p1, p2 = pairs[0], pairs[1]
        kick = _top_bits(rmask & ~(1 << p1) & ~(1 << p2), 1)
        return (TWO_PAIR << 20) | (p1 << 16) | (p2 << 12) | (kick << 8)
    if pairs:
        p = pairs[0]
        kick = _top_bits(rmask & ~(1 << p), 3)
        return (PAIR << 20) | (p << 16) | (kick << 4)
    return (HIGH_CARD << 20) | _top_bits(rmask, 5)


def has_draw(hole, board) -> bool:
    """A flush draw or open-ended straight draw that uses a hole card
    (flop and turn only)."""
    if len(board) >= 5:
        return False
    cards = list(hole) + list(board)
    for s in range(4):
        n = sum(1 for c in cards if c & 3 == s)
        if n == 4 and any(c & 3 == s for c in hole):
            return True
    mask = 0
    for c in cards:
        mask |= 1 << (c >> 2)
    hole_mask = 0
    for c in hole:
        hole_mask |= 1 << (c >> 2)
    # Four in a row (open-ended) that includes a hole rank.
    for low in range(0, 9):
        window = 0b1111 << low
        if mask & window == window and hole_mask & window:
            return True
    return False
