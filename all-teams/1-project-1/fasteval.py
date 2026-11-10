"""
Fast 5-7 card hand evaluator on integer cards.

A card is an int 0..51: rank index (0 = deuce .. 12 = ace) * 4 + suit.
eval_cards returns an int: higher is a better hand, equal ints split.
The ordering matches card_utils.evaluate.
"""

RANKS = "23456789TJQKA"
SUITS = "CDHS"


def _straight_table():
    table = [0] * 8192
    for mask in range(8192):
        for high in range(12, 3, -1):
            window = 0b11111 << (high - 4)
            if mask & window == window:
                table[mask] = high + 1
                break
        else:
            if mask & 0b1000000001111 == 0b1000000001111:  # A-2-3-4-5
                table[mask] = 4  # five-high (index 3) + 1
    return table


STRAIGHT = _straight_table()
CARD_INDEX = {r + s: RANKS.index(r) * 4 + SUITS.index(s)
              for r in RANKS for s in SUITS}


def card_to_int(card):
    return CARD_INDEX[str(card)]


def _top_bits(mask, n):
    out = []
    r = 12
    while r >= 0 and len(out) < n:
        if mask >> r & 1:
            out.append(r)
        r -= 1
    return out


def eval_cards(cards):
    counts = [0] * 13
    suit_masks = [0, 0, 0, 0]
    suit_counts = [0, 0, 0, 0]
    rank_mask = 0
    for c in cards:
        r = c >> 2
        s = c & 3
        counts[r] += 1
        suit_masks[s] |= 1 << r
        suit_counts[s] += 1
        rank_mask |= 1 << r

    flush_mask = 0
    for s in range(4):
        if suit_counts[s] >= 5:
            flush_mask = suit_masks[s]
            sf = STRAIGHT[flush_mask]
            if sf:
                return (8 << 20) | sf
            break

    quads = trips = -1
    trips2 = -1
    pairs = []
    singles = []
    for r in range(12, -1, -1):
        n = counts[r]
        if n == 0:
            continue
        if n == 4:
            quads = r
        elif n == 3:
            if trips < 0:
                trips = r
            elif trips2 < 0:
                trips2 = r
        elif n == 2:
            pairs.append(r)
        else:
            singles.append(r)

    if quads >= 0:
        kicker = -1
        for r in range(12, -1, -1):
            if r != quads and counts[r]:
                kicker = r
                break
        return (7 << 20) | (quads << 4) | kicker
    if trips >= 0 and (trips2 >= 0 or pairs):
        second = max(trips2, pairs[0] if pairs else -1)
        return (6 << 20) | (trips << 4) | second
    if flush_mask:
        top = _top_bits(flush_mask, 5)
        return ((5 << 20) | (top[0] << 16) | (top[1] << 12) | (top[2] << 8)
                | (top[3] << 4) | top[4])
    st = STRAIGHT[rank_mask]
    if st:
        return (4 << 20) | st
    if trips >= 0:
        k = singles[:2]
        return (3 << 20) | (trips << 8) | (k[0] << 4) | k[1]
    if len(pairs) >= 2:
        rest = sorted(pairs[2:] + singles, reverse=True)
        return (2 << 20) | (pairs[0] << 8) | (pairs[1] << 4) | rest[0]
    if pairs:
        k = singles[:3]
        return (1 << 20) | (pairs[0] << 12) | (k[0] << 8) | (k[1] << 4) | k[2]
    k = singles[:5]
    return ((k[0] << 16) | (k[1] << 12) | (k[2] << 8) | (k[3] << 4) | k[4])


def has_draw(hole, board):
    """A flush draw or an open-ended straight draw using a hole card."""
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
    for low in range(0, 9):
        window = 0b1111 << low
        if mask & window == window and hole_mask & window:
            return True
    return False
