"""
A fast 5-7 card hand evaluator in pure Python, for Monte Carlo equity.

Cards are ints 0..51: rank * 4 + suit, rank 0..12 for 2..A, suit 0..3 for
C, D, H, S. A hand is described by four 13-bit masks, one per suit (bit r
set when the card of rank r in that suit is held), so adding a card is one
OR and no per-rank loop is needed. Scores are ints: higher wins, equal
splits, and they order hands exactly like card_utils.evaluate.
"""

RANK_CHARS = "23456789TJQKA"
SUIT_CHARS = "CDHS"

# Tables over 13-bit rank masks, built once at import.
_N = 1 << 13
HIGHBIT = [0] * _N  # index of the highest set bit
POP = [0] * _N  # number of set bits
TOP1 = [0] * _N  # mask of the highest set bit
TOP2 = [0] * _N  # mask of the two highest set bits
TOP3 = [0] * _N
TOP5 = [0] * _N
STRAIGHT = [0] * _N  # top rank + 1 of the best straight, 0 if none

for _m in range(1, _N):
    _h = _m.bit_length() - 1
    HIGHBIT[_m] = _h
    POP[_m] = POP[_m >> 1] + (_m & 1)
    TOP1[_m] = 1 << _h
for _m in range(1, _N):
    _rest = _m ^ TOP1[_m]
    TOP2[_m] = TOP1[_m] | TOP1[_rest]
for _m in range(1, _N):
    _rest = _m ^ TOP1[_m]
    TOP3[_m] = TOP1[_m] | TOP2[_rest]
for _m in range(1, _N):
    _rest = _m ^ TOP2[_m]
    _rest2 = _rest ^ TOP2[_rest]
    TOP5[_m] = TOP2[_m] | TOP2[_rest] | TOP1[_rest2]
for _m in range(1, _N):
    for _top in range(12, 3, -1):
        _run = 0b11111 << (_top - 4)
        if _m & _run == _run:
            STRAIGHT[_m] = _top + 1
            break
    else:
        if _m & 0b1000000001111 == 0b1000000001111:  # the wheel A-2-3-4-5
            STRAIGHT[_m] = 4  # five high: rank index 3, plus one

CAT = 1 << 26  # one hand category's worth of score


def eval_masks(s0, s1, s2, s3):
    """Score of the best 5-card hand in the cards given as suit masks."""
    if POP[s0] >= 5 or POP[s1] >= 5 or POP[s2] >= 5 or POP[s3] >= 5:
        # With at most 7 cards, a flush rules out quads and full houses.
        f = s0 if POP[s0] >= 5 else s1 if POP[s1] >= 5 else (
            s2 if POP[s2] >= 5 else s3)
        sf = STRAIGHT[f]
        if sf:
            return 8 * CAT + sf
        flush = 5 * CAT + TOP5[f]
    else:
        flush = 0
    m1 = s0 | s1 | s2 | s3
    m4 = s0 & s1 & s2 & s3
    if m4:
        r = HIGHBIT[m4]
        return 7 * CAT + (r << 13) + TOP1[m1 ^ (1 << r)]
    a01 = s0 & s1
    a23 = s2 & s3
    m3 = (a01 & (s2 | s3)) | (a23 & (s0 | s1))
    m2 = a01 | a23 | ((s0 | s1) & (s2 | s3))
    if m3:
        t = HIGHBIT[m3]
        rest = m2 ^ (1 << t)
        if rest:
            return 6 * CAT + (t << 4) + HIGHBIT[rest]
    if flush:
        return flush
    st = STRAIGHT[m1]
    if st:
        return 4 * CAT + st
    if m3:
        t = HIGHBIT[m3]
        return 3 * CAT + (t << 13) + TOP2[m1 ^ (1 << t)]
    if m2:
        p1 = HIGHBIT[m2]
        rest = m2 ^ (1 << p1)
        if rest:
            p2 = HIGHBIT[rest]
            return (2 * CAT + (p1 << 17) + (p2 << 13)
                    + TOP1[m1 ^ (1 << p1) ^ (1 << p2)])
        return CAT + (p1 << 13) + TOP3[m1 ^ (1 << p1)]
    return TOP5[m1]


def masks_of(cards):
    """The four suit masks of a list of int cards."""
    s = [0, 0, 0, 0]
    for c in cards:
        s[c & 3] |= 1 << (c >> 2)
    return s


def eval_cards(cards):
    """Score of the best 5-card hand among 5 to 7 int cards."""
    s = [0, 0, 0, 0]
    for c in cards:
        s[c & 3] |= 1 << (c >> 2)
    return eval_masks(s[0], s[1], s[2], s[3])


def category_of(score):
    """0 (high card) .. 8 (straight flush), as card_utils.CATEGORIES."""
    return score >> 26


def card_int(card):
    """A card_utils.Card (or its two-character text) as an int."""
    text = str(card)
    return RANK_CHARS.index(text[0]) * 4 + SUIT_CHARS.index(text[1])


def card_text(c):
    return RANK_CHARS[c >> 2] + SUIT_CHARS[c & 3]
