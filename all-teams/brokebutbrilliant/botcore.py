"""
Fast pure-Python hand evaluation and equity helpers for the bot.

Cards are ints: rank_index * 4 + suit_index, with rank_index 0..12 for
2..A and suit_index 0..3 for C, D, H, S. Scores are ints: a higher score
wins, equal scores split. The category (0 high card .. 8 straight flush)
is score >> 24, the same order as card_utils.CATEGORIES.
"""

RANK_CHARS = "23456789TJQKA"
SUIT_CHARS = "CDHS"


def card_to_int(card):
    return RANK_CHARS.index(card.rank) * 4 + SUIT_CHARS.index(card.suit)


def _build_straight_table():
    """STRAIGHT[mask] = (high rank index + 1) of the best straight in a
    13-bit rank mask, or 0. The wheel A-2-3-4-5 is 5-high."""
    table = [0] * 8192
    windows = []
    for high in range(12, 3, -1):
        windows.append((high, 0b11111 << (high - 4)))
    wheel = (1 << 12) | 0b1111
    for mask in range(8192):
        best = 0
        for high, window in windows:
            if mask & window == window:
                best = high + 1
                break
        if not best and mask & wheel == wheel:
            best = 3 + 1
        table[mask] = best
    return table


STRAIGHT = _build_straight_table()
BITCOUNT = [bin(m).count("1") for m in range(8192)]


def _top_bits(mask, count):
    """Pack the highest `count` ranks set in mask, 4 bits each."""
    out = 0
    taken = 0
    r = 12
    while r >= 0 and taken < count:
        if mask >> r & 1:
            out = (out << 4) | r
            taken += 1
        r -= 1
    while taken < count:
        out <<= 4
        taken += 1
    return out


def evaluate(cards):
    """Score of the best 5-card hand in 3 to 7 int cards."""
    rc = [0] * 13
    s0 = s1 = s2 = s3 = 0
    for c in cards:
        r = c >> 2
        rc[r] += 1
        s = c & 3
        if s == 0:
            s0 |= 1 << r
        elif s == 1:
            s1 |= 1 << r
        elif s == 2:
            s2 |= 1 << r
        else:
            s3 |= 1 << r
    for m in (s0, s1, s2, s3):
        if BITCOUNT[m] >= 5:
            sh = STRAIGHT[m]
            if sh:
                return (8 << 24) | sh
            return (5 << 24) | _top_bits(m, 5)
    quad = -1
    trips = []
    pairs = []
    singles = []
    for r in range(12, -1, -1):
        n = rc[r]
        if n == 0:
            continue
        if n == 1:
            singles.append(r)
        elif n == 2:
            pairs.append(r)
        elif n == 3:
            trips.append(r)
        else:
            quad = r
    if quad >= 0:
        rest = trips + pairs + singles
        kicker = max(rest) if rest else 0
        return (7 << 24) | (quad << 4) | kicker
    if trips and (len(trips) >= 2 or pairs):
        t = trips[0]
        p = trips[1] if len(trips) >= 2 else -1
        if pairs and pairs[0] > p:
            p = pairs[0]
        return (6 << 24) | (t << 4) | p
    sh = STRAIGHT[s0 | s1 | s2 | s3]
    if sh:
        return (4 << 24) | sh
    singles += [0, 0, 0, 0, 0]
    if trips:
        return (3 << 24) | (trips[0] << 8) | (singles[0] << 4) | singles[1]
    if len(pairs) >= 2:
        k = singles[0]
        if len(pairs) >= 3 and pairs[2] > k:
            k = pairs[2]
        return (2 << 24) | (pairs[0] << 8) | (pairs[1] << 4) | k
    if pairs:
        return ((1 << 24) | (pairs[0] << 12) | (singles[0] << 8)
                | (singles[1] << 4) | singles[2])
    return ((singles[0] << 16) | (singles[1] << 12) | (singles[2] << 8)
            | (singles[3] << 4) | singles[4])


def hand_class(hole, board):
    """How an opponent's hand connects with the board, for range
    narrowing: 3 strong (two pair using a hole card or better), 2 top pair
    or overpair, 1 weaker pair or a draw, 0 air."""
    score = evaluate(hole + board)
    cat = score >> 24
    bcat = evaluate(board) >> 24 if len(board) >= 3 else 0
    if cat >= 3 and cat > bcat:
        return 3
    if cat == 2 and bcat == 0:
        return 3
    top = max(c >> 2 for c in board)
    h0 = hole[0] >> 2
    h1 = hole[1] >> 2
    if cat == 1 and bcat == 0:
        pair_rank = (score >> 12) & 15
        if pair_rank >= top:
            return 2
        return 1
    if cat == 2 and bcat == 1:
        # One real pair plus the board pair.
        if (h0 == h1 and h0 > top) or h0 == top or h1 == top:
            return 2
        return 1
    if cat > bcat:
        return 1
    if len(board) < 5:
        # Flush draw using a hole card.
        suits = [0, 0, 0, 0]
        for c in board:
            suits[c & 3] += 1
        for h in hole:
            suits[h & 3] += 1
        for h in hole:
            if suits[h & 3] >= 4:
                return 1
        # Open-ended straight draw using a hole card.
        mask = 0
        for c in board:
            mask |= 1 << (c >> 2)
        full = mask | (1 << h0) | (1 << h1)
        for low in range(0, 9):
            window = 0b1111 << low
            if full & window == window and mask & window != window:
                return 1
    return 0


# Preflop classes: 169 hand types, indexed by (high, low, suited).
def class_key(a, b):
    ra = a >> 2
    rb = b >> 2
    hi, lo = (ra, rb) if ra >= rb else (rb, ra)
    suited = 1 if (a & 3) == (b & 3) and ra != rb else 0
    return hi * 26 + lo * 2 + suited


def all_combos():
    out = []
    for a in range(52):
        for b in range(a + 1, 52):
            out.append((a, b))
    return out
