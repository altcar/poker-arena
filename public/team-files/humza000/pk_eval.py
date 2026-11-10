"""
Fast hand evaluation for the bot (pure Python, table driven).

Cards are ints 0..51: rank * 4 + suit, rank 0..12 meaning 2..A, suit 0..3
meaning C, D, H, S (the order of card_utils.RANKS and SUITS).

A hand value is an int that compares like card_utils.evaluate's tuples:
category << 20, then up to five 4-bit tie-breakers (ranks 0..12).

The tables are built once when the module is imported (well under a
second) and are never pickled: only the model's plain attributes are.
"""

RANK_CHARS = "23456789TJQKA"
SUIT_CHARS = "CDHS"

# Per-card contributions: a base-8 rank counter, a base-16 suit counter
# and a one-bit rank mask.
RK = [1 << (3 * (c >> 2)) for c in range(52)]
SK = [1 << (4 * (c & 3)) for c in range(52)]
RB = [1 << (c >> 2) for c in range(52)]
SUIT_OF = [c & 3 for c in range(52)]
RANK_OF = [c >> 2 for c in range(52)]

CARD_INDEX = {
    RANK_CHARS[r] + SUIT_CHARS[s]: r * 4 + s for r in range(13) for s in range(4)
}


def card_to_int(card):
    """A card_utils.Card (or "AS" text) as an int."""
    return CARD_INDEX[str(card)]


def _straight_high(mask):
    """Top rank (0..12) of the best straight in a 13-bit rank mask, or -1.
    The wheel A-2-3-4-5 is 3 (a five-high straight)."""
    for top in range(12, 3, -1):
        window = 0x1F << (top - 4)
        if mask & window == window:
            return top
    if mask & 0x100F == 0x100F:  # A, 2, 3, 4, 5
        return 3
    return -1


STRAIGHT_HIGH = [_straight_high(m) for m in range(8192)]


def _pack(category, ranks):
    value = category
    for i in range(5):
        value = (value << 4) | (ranks[i] if i < len(ranks) else 0)
    return value


def _flush_value(mask):
    high = STRAIGHT_HIGH[mask]
    if high >= 0:
        return _pack(8, [high])
    ranks = [r for r in range(12, -1, -1) if mask >> r & 1][:5]
    return _pack(5, ranks)


FLUSH = [0] * 8192
for _m in range(8192):
    if bin(_m).count("1") >= 5:
        FLUSH[_m] = _flush_value(_m)


def _best_nonflush(counts):
    """Value of the best 5 cards from rank counts (no flush possible)."""
    desc = [r for r in range(12, -1, -1) if counts[r]]
    quads = [r for r in desc if counts[r] >= 4]
    trips = [r for r in desc if counts[r] == 3]
    pairs = [r for r in desc if counts[r] == 2]
    if quads:
        q = quads[0]
        kicker = [r for r in desc if r != q][:1]
        return _pack(7, [q] + kicker)
    if trips and (len(trips) >= 2 or pairs):
        t = trips[0]
        rest = trips[1:] + pairs
        p = max(rest)
        return _pack(6, [t, p])
    mask = 0
    for r in desc:
        mask |= 1 << r
    high = STRAIGHT_HIGH[mask]
    if high >= 0:
        return _pack(4, [high])
    if trips:
        t = trips[0]
        return _pack(3, [t] + [r for r in desc if r != t][:2])
    if len(pairs) >= 2:
        p1, p2 = pairs[0], pairs[1]
        kicker = [r for r in desc if r != p1 and r != p2][:1]
        return _pack(2, [p1, p2] + kicker)
    if pairs:
        p = pairs[0]
        return _pack(1, [p] + [r for r in desc if r != p][:3])
    return _pack(0, desc[:5])


def _build_nonflush():
    table = {}
    counts = [0] * 13

    def fill(rank, left, key, total):
        if 5 <= total <= 7:
            table[key] = _best_nonflush(counts)
        if rank == 13 or left == 0:
            return
        for n in range(0, min(4, left) + 1):
            counts[rank] = n
            fill(rank + 1, left - n, key + n * (1 << (3 * rank)), total + n)
        counts[rank] = 0

    fill(0, 7, 0, 0)
    return table


NONFLUSH = _build_nonflush()

# Straight draws: how many ranks would complete a straight for a rank mask.
STRAIGHT_OUTS = [0] * 8192
for _m in range(8192):
    if STRAIGHT_HIGH[_m] >= 0:
        continue
    _n = 0
    for _r in range(13):
        if not _m >> _r & 1 and STRAIGHT_HIGH[_m | (1 << _r)] >= 0:
            _n += 1
    STRAIGHT_OUTS[_m] = _n


def evaluate(cards):
    """Value of the best five of 5..7 card ints."""
    key = 0
    suits = 0
    for c in cards:
        key += RK[c]
        suits += SK[c]
    if (suits + 0x3333) & 0x8888:
        for s in range(4):
            if (suits >> (4 * s)) & 15 >= 5:
                break
        mask = 0
        for c in cards:
            if c & 3 == s:
                mask |= RB[c]
        return FLUSH[mask]
    return NONFLUSH[key]


def category(value):
    """0 (high card) .. 8 (straight flush), as card_utils.CATEGORIES."""
    return value >> 20


# All 1326 two-card combos, (low, high) card ints.
COMBOS = [(a, b) for b in range(52) for a in range(b)]


def class_index(c1, c2):
    """One of the 169 starting-hand classes:
    0..12 pairs (22..AA), then suited, then offsuit (78 each)."""
    r1, r2 = c1 >> 2, c2 >> 2
    if r1 == r2:
        return r1
    hi, lo = (r1, r2) if r1 > r2 else (r2, r1)
    k = hi * (hi - 1) // 2 + lo  # 0..77
    if (c1 & 3) == (c2 & 3):
        return 13 + k
    return 91 + k


def class_ranks(index):
    """(high rank, low rank, suited) of a class index."""
    if index < 13:
        return index, index, False
    suited = index < 91
    k = index - 13 if suited else index - 91
    hi = 1
    while (hi + 1) * hi // 2 <= k:
        hi += 1
    lo = k - hi * (hi - 1) // 2
    return hi, lo, suited


def class_name(index):
    hi, lo, suited = class_ranks(index)
    if hi == lo:
        return RANK_CHARS[hi] * 2
    return RANK_CHARS[hi] + RANK_CHARS[lo] + ("s" if suited else "o")


CLASS_OF_COMBO = [class_index(a, b) for a, b in COMBOS]
CLASS_COMBOS = [[] for _ in range(169)]
for _i, (_a, _b) in enumerate(COMBOS):
    CLASS_COMBOS[CLASS_OF_COMBO[_i]].append(_i)
