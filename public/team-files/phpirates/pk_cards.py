"""
Cards as ints 0..51 (rank * 4 + suit) and the 169 starting-hand classes.

Ranks 0..12 are 2..A and suits 0..3 are C, D, H, S, the order card_utils
uses. Convert a card_utils.Card with to_int(card): it goes through
str(card), so no Card object ever needs to be stored.

A starting-hand class is a cell of a 13 x 13 grid, numbered row * 13 + col:
(r, r) is a pair, (hi, lo) with row > col is suited, (lo, hi) with row < col
is offsuit. A class has 6 combos (pair), 4 (suited) or 12 (offsuit).
"""

RANKS = "23456789TJQKA"
SUITS = "CDHS"

_INDEX = {
    r + s: i * 4 + j for i, r in enumerate(RANKS) for j, s in enumerate(SUITS)
}


def to_int(card):
    """A card_utils.Card (or its text, e.g. "AS") as an int 0..51."""
    return _INDEX[str(card)]


def name(c):
    """The text of a card int, e.g. 51 -> "AS"."""
    return RANKS[c >> 2] + SUITS[c & 3]


def class_of(a, b):
    """The starting-hand class (0..168) of two card ints."""
    ra = a >> 2
    rb = b >> 2
    if ra == rb:
        return ra * 14
    if ra > rb:
        hi, lo = ra, rb
    else:
        hi, lo = rb, ra
    if (a & 3) == (b & 3):
        return hi * 13 + lo
    return lo * 13 + hi


def class_name(k):
    """E.g. "AA", "AKs", "72o"."""
    i, j = divmod(k, 13)
    if i == j:
        return RANKS[i] * 2
    if i > j:
        return RANKS[i] + RANKS[j] + "s"
    return RANKS[j] + RANKS[i] + "o"


# Every two-card combo (a, b) with a < b: 1326 of them.
COMBOS = [(a, b) for a in range(52) for b in range(a + 1, 52)]
COMBO_CLASS = [class_of(a, b) for a, b in COMBOS]
CLASS_COMBOS = [[] for _ in range(169)]
for _i, _combo in enumerate(COMBOS):
    CLASS_COMBOS[COMBO_CLASS[_i]].append(_combo)
CLASS_SIZE = [len(c) for c in CLASS_COMBOS]
del _i, _combo


def live_class_counts(dead):
    """Per class, how many of its combos use no card in dead (a set)."""
    counts = [0] * 169
    for i, (a, b) in enumerate(COMBOS):
        if a not in dead and b not in dead:
            counts[COMBO_CLASS[i]] += 1
    return counts
