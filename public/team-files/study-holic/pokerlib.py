"""Fast hand evaluation, range weighting and equity for the bot.

Cards are ints: (value - 2) * 4 + suit, value 2..14, suit 0..3.
"""

import random as _random

_RNG = _random.Random(12345)
SUITS = "CDHS"
RANKS = "23456789TJQKA"


def card_int(card):
    return (card.value - 2) * 4 + SUITS.index(card.suit)


def card_str(c):
    return RANKS[c >> 2] + SUITS[c & 3]


def parse(text):
    return RANKS.index(text[0]) * 4 + SUITS.index(text[1])


def _straight_high(mask):
    """mask: bit r set for rank index r (0=2 .. 12=A). Returns high index or -1."""
    for hi in range(12, 3, -1):
        need = 0x1F << (hi - 4)
        if mask & need == need:
            return hi
    # wheel A2345
    if mask & 0x100F == 0x100F:
        return 3
    return -1


def evaluate(cards):
    """cards: 5..7 ints. Returns an int; higher is better."""
    counts = [0] * 13
    suits = [0, 0, 0, 0]
    for c in cards:
        counts[c >> 2] += 1
        suits[c & 3] += 1
    flush_suit = -1
    for s in range(4):
        if suits[s] >= 5:
            flush_suit = s
            break
    if flush_suit >= 0:
        fmask = 0
        for c in cards:
            if c & 3 == flush_suit:
                fmask |= 1 << (c >> 2)
        sh = _straight_high(fmask)
        if sh >= 0:
            return (8 << 20) | sh
    quads = -1
    trips = []
    pairs = []
    singles = []
    mask = 0
    for r in range(12, -1, -1):
        n = counts[r]
        if n:
            mask |= 1 << r
            if n == 4:
                quads = r
            elif n == 3:
                trips.append(r)
            elif n == 2:
                pairs.append(r)
            else:
                singles.append(r)
    if quads >= 0:
        kick = max([r for r in range(13) if counts[r] and r != quads])
        return (7 << 20) | (quads << 4) | kick
    if trips and (len(trips) >= 2 or pairs):
        t = trips[0]
        p = trips[1] if len(trips) >= 2 else -1
        if pairs and pairs[0] > p:
            p = pairs[0]
        return (6 << 20) | (t << 4) | p
    if flush_suit >= 0:
        fr = sorted([c >> 2 for c in cards if c & 3 == flush_suit], reverse=True)[:5]
        return (5 << 20) | (fr[0] << 16) | (fr[1] << 12) | (fr[2] << 8) | (fr[3] << 4) | fr[4]
    sh = _straight_high(mask)
    if sh >= 0:
        return (4 << 20) | sh
    if trips:
        k = singles[:2]
        while len(k) < 2:
            k.append(0)
        return (3 << 20) | (trips[0] << 8) | (k[0] << 4) | k[1]
    if len(pairs) >= 2:
        rest = pairs[2:] + singles
        k = max(rest) if rest else 0
        return (2 << 20) | (pairs[0] << 8) | (pairs[1] << 4) | k
    if pairs:
        k = singles[:3]
        while len(k) < 3:
            k.append(0)
        return (1 << 20) | (pairs[0] << 12) | (k[0] << 8) | (k[1] << 4) | k[2]
    k = singles[:5]
    while len(k) < 5:
        k.append(0)
    return (k[0] << 16) | (k[1] << 12) | (k[2] << 8) | (k[3] << 4) | k[4]


def category(score):
    return score >> 20


ALL_COMBOS = [(a, b) for a in range(52) for b in range(a + 1, 52)]


def hand_class(a, b):
    """169-class key like 'AKs', 'T9o', '77'."""
    ra, rb = a >> 2, b >> 2
    if ra < rb:
        ra, rb = rb, ra
    if ra == rb:
        return RANKS[ra] * 2
    return RANKS[ra] + RANKS[rb] + ("s" if (a & 3) == (b & 3) else "o")


def draw_strength(hole, board):
    """0 none, 1 gutshot/backdoor-ish, 2 strong draw (flush draw or OESD).
    Only meaningful on flop/turn."""
    if len(board) >= 5:
        return 0
    cards = list(hole) + list(board)
    suits = [0, 0, 0, 0]
    for c in cards:
        suits[c & 3] += 1
    fd = False
    for s in range(4):
        if suits[s] == 4 and ((hole[0] & 3) == s or (hole[1] & 3) == s):
            fd = True
    mask = 0
    for c in cards:
        mask |= 1 << (c >> 2)
    if mask & (1 << 12):
        mask_low = (mask << 1) | 1
    else:
        mask_low = mask << 1
    # Use a 14-bit mask with ace low at bit 0.
    m = mask_low
    oesd = False
    gut = False
    for lo in range(0, 10):
        window = (m >> lo) & 0x1F
        cnt = bin(window).count("1")
        if cnt == 4:
            if (window == 0x0F and lo > 0) or (window == 0x1E and lo + 5 <= 13):
                oesd = True
            else:
                gut = True
    if fd or oesd:
        return 2
    if gut:
        return 1
    return 0


def equity(hole, board, combos, weights, samples=500):
    """Share of the pot hole wins against a weighted opponent range,
    rolling out the rest of the board. Ties count half."""
    dead = set(hole) | set(board)
    pool = []
    pw = []
    for (a, b), w in zip(combos, weights):
        if w > 0 and a not in dead and b not in dead:
            pool.append((a, b))
            pw.append(w)
    if not pool:
        return 0.5
    picks = _RNG.choices(pool, weights=pw, k=samples)
    missing = 5 - len(board)
    deck = [c for c in range(52) if c not in dead]
    won = 0.0
    hole = list(hole)
    board = list(board)
    if missing == 0:
        mine = evaluate(hole + board)
        cache = {}
        for opp in picks:
            v = cache.get(opp)
            if v is None:
                v = evaluate(list(opp) + board)
                cache[opp] = v
            if mine > v:
                won += 1
            elif mine == v:
                won += 0.5
        return won / samples
    for opp in picks:
        a, b = opp
        run = []
        while len(run) < missing:
            c = deck[int(_RNG.random() * len(deck))]
            if c != a and c != b and c not in run:
                run.append(c)
        full = board + run
        m = evaluate(hole + full)
        o = evaluate([a, b] + full)
        if m > o:
            won += 1
        elif m == o:
            won += 0.5
    return won / samples
