"""Fast integer hand ranking, adapted from the supplied poker_core evaluator."""

# ------------------------------------------------------------ evaluator ---
# Score = category << 20 | up to five 4-bit kickers. Higher is better.

def _build_tables():
    straight = [-1] * 8192
    top5 = [0] * 8192
    for m in range(8192):
        # straight high card
        best = -1
        for hi in range(12, 3, -1):
            need = 0b11111 << (hi - 4)
            if m & need == need:
                best = hi
                break
        if best < 0 and (m & 0b1000000001111) == 0b1000000001111:
            best = 3  # wheel A-2-3-4-5
        straight[m] = best
        v = 0
        n = 0
        for r in range(12, -1, -1):
            if m >> r & 1:
                v = (v << 4) | r
                n += 1
                if n == 5:
                    break
        top5[m] = v << (4 * (5 - n))
    return straight, top5


STRAIGHT, TOP5 = _build_tables()
POP = [bin(i).count("1") for i in range(8192)]


def evaluate(cards):
    """Evaluate the best 5-card hand from 5..7 cards."""
    rc = [0] * 13
    sm = [0, 0, 0, 0]
    for c in cards:
        r = c >> 2
        rc[r] += 1
        sm[c & 3] |= 1 << r
    for m in sm:
        if POP[m] >= 5:
            sf = STRAIGHT[m]
            if sf >= 0:
                return (8 << 20) | (sf << 16)
            flush = (5 << 20) | TOP5[m]
            break
    else:
        flush = 0
    quad = -1
    trips = []
    pairs = []
    for r in range(12, -1, -1):
        n = rc[r]
        if n == 4:
            quad = r
        elif n == 3:
            trips.append(r)
        elif n == 2:
            pairs.append(r)
    rmask = sm[0] | sm[1] | sm[2] | sm[3]
    if quad >= 0:
        k = TOP5[rmask & ~(1 << quad)] >> 16
        return (7 << 20) | (quad << 16) | (k << 12)
    if trips and (len(trips) > 1 or pairs):
        t = trips[0]
        p = trips[1] if len(trips) > 1 else -1
        if pairs and pairs[0] > p:
            p = pairs[0]
        return (6 << 20) | (t << 16) | (p << 12)
    if flush:
        return flush
    st = STRAIGHT[rmask]
    if st >= 0:
        return (4 << 20) | (st << 16)
    if trips:
        t = trips[0]
        k = TOP5[rmask & ~(1 << t)] >> 12  # top two kickers
        return (3 << 20) | (t << 16) | (k << 8)
    if len(pairs) >= 2:
        a, b = pairs[0], pairs[1]
        k = TOP5[rmask & ~(1 << a) & ~(1 << b)] >> 16
        return (2 << 20) | (a << 16) | (b << 12) | (k << 8)
    if pairs:
        a = pairs[0]
        k = TOP5[rmask & ~(1 << a)] >> 8  # top three kickers
        return (1 << 20) | (a << 16) | (k << 4)
    return TOP5[rmask]


