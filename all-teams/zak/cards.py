"""
Cards as small ints, combos, starting-hand classes and a fast evaluator.

A card id is 4 * rank + suit: rank 0 (deuce) to 12 (ace), suit in
card_utils.SUITS order, so ids follow card_utils.full_deck(). A combo is
two card ids (low, high); there are 1326. Its class is one of the 169
starting hands (pairs, suited, offsuit).
"""

from itertools import combinations

from card_utils import full_deck

DECK = full_deck()
_CARD_ID = {str(card): i for i, card in enumerate(DECK)}

COMBOS: list[tuple[int, int]] = list(combinations(range(52), 2))
_COMBO_ID = {pair: i for i, pair in enumerate(COMBOS)}
NUM_COMBOS = len(COMBOS)  # 1326
NUM_CLASSES = 169
RANK_CHARS = "23456789TJQKA"


def card_id(card) -> int:
    """The id 0..51 of a card_utils.Card."""
    return _CARD_ID[str(card)]


def combo_id(a: int, b: int) -> int:
    """The id 0..1325 of two card ids, in either order."""
    return _COMBO_ID[(a, b) if a < b else (b, a)]


def hand_class(a: int, b: int) -> int:
    """
    Starting-hand class 0..168, a cell of a 13x13 grid: pairs on the
    diagonal, suited at (high, low), offsuit at (low, high).
    """
    high, low = max(a >> 2, b >> 2), min(a >> 2, b >> 2)
    if a & 3 == b & 3:
        return high * 13 + low
    return low * 13 + high


def class_name(cls: int) -> str:
    """e.g. "AKs", "T9o", "77"."""
    row, col = divmod(cls, 13)
    if row == col:
        return RANK_CHARS[row] * 2
    if row > col:
        return RANK_CHARS[row] + RANK_CHARS[col] + "s"
    return RANK_CHARS[col] + RANK_CHARS[row] + "o"


CLASS_OF: list[int] = [hand_class(a, b) for a, b in COMBOS]
COMBOS_OF_CLASS: list[list[int]] = [[] for _ in range(NUM_CLASSES)]
for _i, _cls in enumerate(CLASS_OF):
    COMBOS_OF_CLASS[_cls].append(_i)


# -- evaluator --------------------------------------------------------------

def _straight_high(mask: int) -> int:
    """Value (5..14) of the best straight in a 13-bit rank mask, or 0."""
    for top in range(12, 3, -1):
        if mask >> (top - 4) & 0b11111 == 0b11111:
            return top + 2
    if mask & 0b1000000001111 == 0b1000000001111:  # A-2-3-4-5
        return 5
    return 0


STRAIGHT_HIGH = [_straight_high(m) for m in range(1 << 13)]


def _encode(category: int, kickers) -> int:
    """card_utils.evaluate's tuple as one int with the same order."""
    value = category
    n = 0
    for k in kickers:
        value = value * 16 + k
        n += 1
    return value * 16 ** (5 - n)


def encode_rank(rank: tuple[int, ...]) -> int:
    """An evaluate() result as the int rank7 would give."""
    return _encode(rank[0], rank[1:])


def rank7(cards) -> int:
    """
    Rank of the best 5-card hand among 5 to 7 card ids: higher wins, equal
    splits. Orders hands exactly as card_utils.evaluate does (about 10x
    faster): rank7(ids) == encode_rank(evaluate(cards)).
    """
    counts = [0] * 13
    suit_masks = [0, 0, 0, 0]
    mask = 0
    for c in cards:
        r = c >> 2
        counts[r] += 1
        suit_masks[c & 3] |= 1 << r
        mask |= 1 << r
    # With at most 7 cards a flush rules out quads and a full house.
    for sm in suit_masks:
        if sm.bit_count() >= 5:
            high = STRAIGHT_HIGH[sm]
            if high:
                return _encode(8, (high,))
            top = [r + 2 for r in range(12, -1, -1) if sm >> r & 1][:5]
            return _encode(5, top)
    quads, trips, pairs, singles = [], [], [], []
    for r in range(12, -1, -1):
        n = counts[r]
        if n == 4:
            quads.append(r + 2)
        elif n == 3:
            trips.append(r + 2)
        elif n == 2:
            pairs.append(r + 2)
        elif n == 1:
            singles.append(r + 2)
    if quads:
        q = quads[0]
        kicker = max(v for v in quads[1:] + trips + pairs + singles)
        return _encode(7, (q, kicker))
    if trips and (len(trips) > 1 or pairs):
        second = max(trips[1:] + pairs)
        return _encode(6, (trips[0], second))
    high = STRAIGHT_HIGH[mask]
    if high:
        return _encode(4, (high,))
    if trips:
        return _encode(3, (trips[0], *singles[:2]))
    if len(pairs) >= 2:
        kicker = max(pairs[2:] + singles)
        return _encode(2, (pairs[0], pairs[1], kicker))
    if pairs:
        return _encode(1, (pairs[0], *singles[:3]))
    return _encode(0, singles[:5])


def category_of(rank: int) -> int:
    """The category (0 high card .. 8 straight flush) of a rank7 result."""
    return rank >> 20
