"""
Fast hand evaluation and Monte Carlo equity, in plain Python.

Cards are ints: rank * 4 + suit, rank 0..12 (2..A), suit 0..3 (C, D, H, S).
A hand's score is an int: higher wins, equal splits.
"""

import random

RANK_CHARS = "23456789TJQKA"
SUIT_CHARS = "CDHS"

# Score = category * 16**5 + up to five tie-break ranks in base 16.
_BASE = 16 ** 5


def _pack(category, ranks):
    value = 0
    for i in range(5):
        value = value * 16 + (ranks[i] if i < len(ranks) else 0)
    return category * _BASE + value


def _straight_top(mask):
    """Top rank of the best straight in a 13-bit rank mask, or -1."""
    for top in range(12, 3, -1):
        window = 0b11111 << (top - 4)
        if mask & window == window:
            return top
    wheel = (1 << 12) | 0b1111
    if mask & wheel == wheel:
        return 3
    return -1


def _build_flush():
    table = [0] * 8192
    for mask in range(8192):
        if bin(mask).count("1") < 5:
            continue
        top = _straight_top(mask)
        if top >= 0:
            table[mask] = _pack(8, [top])
        else:
            ranks = [r for r in range(12, -1, -1) if mask >> r & 1][:5]
            table[mask] = _pack(5, ranks)
    return table


def _score_counts(counts):
    """Best non-flush score for rank counts (5 to 7 cards)."""
    mask = 0
    for r in range(13):
        if counts[r]:
            mask |= 1 << r
    quads = [r for r in range(12, -1, -1) if counts[r] == 4]
    trips = [r for r in range(12, -1, -1) if counts[r] == 3]
    pairs = [r for r in range(12, -1, -1) if counts[r] == 2]
    present = [r for r in range(12, -1, -1) if counts[r]]
    if quads:
        q = quads[0]
        return _pack(7, [q, [r for r in present if r != q][0]])
    if trips and (len(trips) > 1 or pairs):
        t = trips[0]
        rest = trips[1:] + pairs
        return _pack(6, [t, max(rest)])
    top = _straight_top(mask)
    if top >= 0:
        return _pack(4, [top])
    if trips:
        t = trips[0]
        return _pack(3, [t] + [r for r in present if r != t][:2])
    if len(pairs) >= 2:
        a, b = pairs[0], pairs[1]
        return _pack(2, [a, b, [r for r in present if r != a and r != b][0]])
    if pairs:
        p = pairs[0]
        return _pack(1, [p] + [r for r in present if r != p][:3])
    return _pack(0, present[:5])


def _build_nonflush():
    table = {}
    counts = [0] * 13

    def walk(rank, cards, key):
        if rank == 13:
            if cards >= 5:
                table[key] = _score_counts(counts)
            return
        for n in range(0, min(4, 7 - cards) + 1):
            counts[rank] = n
            walk(rank + 1, cards + n, key + n * (1 << (3 * rank)))
        counts[rank] = 0

    walk(0, 0, 0)
    return table


# Built on first use, not at import: start-up time counts against the
# first call's time limit, so keep importing cheap.
FLUSH = None
NONFLUSH = None


def build_tables():
    global FLUSH, NONFLUSH
    if NONFLUSH is None:
        FLUSH = _build_flush()
        NONFLUSH = _build_nonflush()


RANK_KEY = [1 << (3 * (c >> 2)) for c in range(52)]
SUIT_KEY = [1 << (4 * (c & 3)) for c in range(52)]
FULL_DECK = list(range(52))


def card_int(card):
    """A card_utils.Card as an int."""
    return RANK_CHARS.index(card.rank) * 4 + SUIT_CHARS.index(card.suit)


def card_str(c):
    return RANK_CHARS[c >> 2] + SUIT_CHARS[c & 3]


def score(cards):
    """Score of the best five of 5 to 7 card ints."""
    if NONFLUSH is None:
        build_tables()
    k = 0
    s = 0
    for c in cards:
        k += RANK_KEY[c]
        s += SUIT_KEY[c]
    if (s + 0x3333) & 0x8888:
        for suit in range(4):
            if (s >> (4 * suit)) & 15 >= 5:
                m = 0
                for c in cards:
                    if c & 3 == suit:
                        m |= 1 << (c >> 2)
                return FLUSH[m]
    return NONFLUSH[k]


def category_of(value):
    """0 high card .. 8 straight flush."""
    return value // _BASE


# -- hand classes ----------------------------------------------------------

def class_index(c1, c2):
    """One of 169 starting-hand classes: pairs 0..12 (by rank), then
    suited, then offsuit, by (high, low)."""
    r1, r2 = c1 >> 2, c2 >> 2
    hi, lo = (r1, r2) if r1 >= r2 else (r2, r1)
    if hi == lo:
        return hi
    idx = hi * (hi - 1) // 2 + lo  # 0..77 over hi > lo
    if c1 & 3 == c2 & 3:
        return 13 + idx
    return 91 + idx


def class_name(index):
    if index < 13:
        return RANK_CHARS[index] * 2
    suited = index < 91
    idx = index - 13 if suited else index - 91
    hi = 1
    while (hi + 1) * hi // 2 <= idx:
        hi += 1
    lo = idx - hi * (hi - 1) // 2
    return RANK_CHARS[hi] + RANK_CHARS[lo] + ("s" if suited else "o")


def all_combos():
    """All 1326 two-card combos as (c1, c2) with c1 > c2."""
    return [(a, b) for a in range(52) for b in range(a)]


COMBOS = all_combos()
COMBO_CLASS = [class_index(a, b) for a, b in COMBOS]
CLASS_COMBOS = [[] for _ in range(169)]
for _i, _cls in enumerate(COMBO_CLASS):
    CLASS_COMBOS[_cls].append(_i)
CLASS_SIZE = [len(c) for c in CLASS_COMBOS]


# -- equity ------------------------------------------------------------------

def equity_vs_combos(hero, board, combos, weights, samples, rng):
    """
    Hero's share of the pot against an opponent holding one of combos
    (list of (c1, c2)) with the given weights, by Monte Carlo over the
    rest of the board. Combos that clash with known cards are skipped.
    Returns (equity, samples used).
    """
    dead = set(hero) | set(board)
    live = []
    live_w = []
    for combo, w in zip(combos, weights):
        if w > 0 and combo[0] not in dead and combo[1] not in dead:
            live.append(combo)
            live_w.append(w)
    if not live:
        return 0.5, 0
    deck = [c for c in FULL_DECK if c not in dead]
    need = 5 - len(board)
    board = list(board)
    picks = rng.choices(live, weights=live_w, k=samples)
    won = 0.0
    if need == 0:
        mine = score(hero + board)
        cache = {}
        for a, b in picks:
            theirs = cache.get((a, b))
            if theirs is None:
                theirs = score([a, b] + board)
                cache[(a, b)] = theirs
            if mine > theirs:
                won += 1.0
            elif mine == theirs:
                won += 0.5
        return won / samples, samples
    sample = rng.sample
    for a, b in picks:
        drawn = sample(deck, need + 2)
        run = [c for c in drawn if c != a and c != b][:need]
        full = board + run
        mine = score(hero + full)
        theirs = score([a, b] + full)
        if mine > theirs:
            won += 1.0
        elif mine == theirs:
            won += 0.5
    return won / samples, samples


def equity_vs_random(hero, board, samples, rng):
    """Hero's share against one uniformly random hand."""
    dead = set(hero) | set(board)
    deck = [c for c in FULL_DECK if c not in dead]
    need = 5 - len(board)
    board = list(board)
    won = 0.0
    sample = rng.sample
    for _ in range(samples):
        drawn = sample(deck, need + 2)
        full = board + drawn[2:]
        mine = score(hero + full)
        theirs = score(drawn[:2] + full)
        if mine > theirs:
            won += 1.0
        elif mine == theirs:
            won += 0.5
    return won / samples


def new_rng(seed=None):
    return random.Random(seed)
