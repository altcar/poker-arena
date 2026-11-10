"""
Fast hand evaluation and equity maths for model.py, in pure Python.

Cards are ints 0..51: rank * 4 + suit, rank 0 (two) .. 12 (ace), suit 0..3
in card_utils order (C, D, H, S). A hand's value is an int: higher wins,
equal values split. Evaluator() builds its lookup tables once; they are
plain lists and dicts, so they are pickled with the model and the worker
never rebuilds them.

The 1326 two-card combos are indexed 0..1325 (Evaluator.combos). Their
169 starting-hand classes are a 13 x 13 grid: pairs on the diagonal,
suited hands at [high][low], offsuit hands at [low][high].
"""

import time

RANKS = "23456789TJQKA"
SUITS = "CDHS"


def card_id(card):
    """A card_utils.Card as an int 0..51."""
    return RANKS.index(card.rank) * 4 + SUITS.index(card.suit)


def hand_class(a, b):
    """The 0..168 starting-hand class of two card ids."""
    ra, rb = a >> 2, b >> 2
    if ra < rb:
        ra, rb = rb, ra
    if ra == rb:
        return ra * 13 + ra
    if (a & 3) == (b & 3):
        return ra * 13 + rb
    return rb * 13 + ra


def class_name(index):
    """"AKs", "T9o", "77" for a class index."""
    row, col = divmod(index, 13)
    if row == col:
        return RANKS[row] * 2
    if row > col:
        return RANKS[row] + RANKS[col] + "s"
    return RANKS[col] + RANKS[row] + "o"


def _pack(category, kickers):
    value = category
    for i in range(5):
        value = (value << 4) | (kickers[i] if i < len(kickers) else 0)
    return value


def category_of(value):
    """0 (high card) .. 8 (straight flush), as card_utils.CATEGORIES."""
    return value >> 20


def _straight_high(mask):
    """Top rank of the best straight in a 13-bit rank mask, or -1."""
    ext = (mask << 1) | ((mask >> 12) & 1)  # bit 0 is the ace played low
    for top in range(13, 3, -1):
        window = 0x1F << (top - 4)
        if ext & window == window:
            return top - 1
    return -1


def _nonflush_value(counts):
    present = 0
    for r in range(13):
        if counts[r]:
            present |= 1 << r
    desc = [r for r in range(12, -1, -1) if counts[r]]
    quads = [r for r in desc if counts[r] == 4]
    trips = [r for r in desc if counts[r] == 3]
    pairs = [r for r in desc if counts[r] == 2]
    if quads:
        q = quads[0]
        return _pack(7, [q] + [r for r in desc if r != q][:1])
    if trips and (len(trips) > 1 or pairs):
        t = trips[0]
        return _pack(6, [t, max(trips[1:] + pairs)])
    high = _straight_high(present)
    if high >= 0:
        return _pack(4, [high])
    if trips:
        t = trips[0]
        return _pack(3, [t] + [r for r in desc if r != t][:2])
    if len(pairs) > 1:
        a, b = pairs[0], pairs[1]
        return _pack(2, [a, b] + [r for r in desc if r != a and r != b][:1])
    if pairs:
        p = pairs[0]
        return _pack(1, [p] + [r for r in desc if r != p][:3])
    return _pack(0, desc[:5])


def _count_vectors(total, ranks=13, cap=4):
    """Every tuple of `ranks` counts, each 0..cap, summing to total."""
    if ranks == 1:
        if total <= cap:
            yield (total,)
        return
    for n in range(min(cap, total) + 1):
        for rest in _count_vectors(total - n, ranks - 1, cap):
            yield (n,) + rest


class Evaluator:
    """Lookup tables for 5- to 7-card hands, plus per-combo data."""

    def __init__(self):
        self.rkey = [5 ** (c >> 2) for c in range(52)]
        self.skey = [1 << (3 * (c & 3)) for c in range(52)]
        self.rbit = [1 << (c >> 2) for c in range(52)]
        # Without a flush, a hand's value depends only on its ranks: key =
        # sum of 5 ** rank over the cards (unique, as no rank has 5 cards).
        self.nonflush = {}
        for total in (5, 6, 7):
            for counts in _count_vectors(total):
                key = 0
                for r, n in enumerate(counts):
                    key += n * 5 ** r
                self.nonflush[key] = _nonflush_value(counts)
        # With 7 cards or fewer a flush rules out quads and full houses, so
        # the flush suit's ranks alone give the value.
        self.flush = [0] * 8192
        for mask in range(8192):
            if bin(mask).count("1") >= 5:
                high = _straight_high(mask)
                if high >= 0:
                    self.flush[mask] = _pack(8, [high])
                else:
                    top = [r for r in range(12, -1, -1) if mask >> r & 1]
                    self.flush[mask] = _pack(5, top[:5])
        # Suit counts packed 3 bits per suit -> the suit with 5+, or -1.
        self.flush_suit = [-1] * 4096
        for sc in range(4096):
            for s in range(4):
                if (sc >> (3 * s)) & 7 >= 5:
                    self.flush_suit[sc] = s
        self.combos = [(a, b) for a in range(52) for b in range(a)]
        self.combo_index = {}
        self.ck, self.cs, self.cbit, self.csm, self.cls = [], [], [], [], []
        for i, (a, b) in enumerate(self.combos):
            self.combo_index[(a, b)] = i
            self.combo_index[(b, a)] = i
            self.ck.append(self.rkey[a] + self.rkey[b])
            self.cs.append(self.skey[a] + self.skey[b])
            self.cbit.append((1 << a) | (1 << b))
            masks = [0, 0, 0, 0]
            masks[a & 3] |= self.rbit[a]
            masks[b & 3] |= self.rbit[b]
            self.csm.append(tuple(masks))
            self.cls.append(hand_class(a, b))

    def value(self, cards):
        """The value of 5 to 7 card ids."""
        key = 0
        sc = 0
        rkey, skey = self.rkey, self.skey
        for c in cards:
            key += rkey[c]
            sc += skey[c]
        s = self.flush_suit[sc]
        if s < 0:
            return self.nonflush[key]
        mask = 0
        for c in cards:
            if c & 3 == s:
                mask |= self.rbit[c]
        return self.flush[mask]

    def street(self, hero, board, rng, budget=0.25, max_runouts=160):
        """
        Hero's share of the pot against every combo, and every combo's
        strength, on a board of 3 to 5 cards.

        Returns (live, heq, hs, runouts): live lists the combos not blocked
        by hero's cards or the board; heq[i] is hero's chance against combo
        i (ties count half), hs[i] is combo i's percentile among the live
        combos, averaged over the runouts (roughly its equity against a
        random hand). The river is exact, the turn enumerates every river,
        the flop samples runouts until max_runouts or the time budget.
        """
        deadline = time.perf_counter() + budget
        dead = 0
        for c in hero:
            dead |= 1 << c
        for c in board:
            dead |= 1 << c
        cbit, ck, cs, csm = self.cbit, self.ck, self.cs, self.csm
        live = [i for i in range(1326) if not cbit[i] & dead]
        deck = [c for c in range(52) if not dead >> c & 1]
        need = 5 - len(board)
        if need <= 0:
            runouts = [()]
        elif need == 1:
            runouts = [(c,) for c in deck]
            rng.shuffle(runouts)
        else:
            runouts = []
            for _ in range(max_runouts):
                runouts.append(tuple(rng.sample(deck, need)))
        rkey, skey, rbit = self.rkey, self.skey, self.rbit
        nonflush, flush, flush_suit = self.nonflush, self.flush, self.flush_suit
        bk0 = 0
        bs0 = 0
        bm0 = [0, 0, 0, 0]
        for c in board:
            bk0 += rkey[c]
            bs0 += skey[c]
            bm0[c & 3] |= rbit[c]
        h0, h1 = hero
        hk = rkey[h0] + rkey[h1]
        hsk = skey[h0] + skey[h1]
        hm = [0, 0, 0, 0]
        hm[h0 & 3] |= rbit[h0]
        hm[h1 & 3] |= rbit[h1]
        win = [0.0] * 1326
        hsum = [0.0] * 1326
        cnt = [0] * 1326
        done = 0
        for ro in runouts:
            if done >= 8 and time.perf_counter() > deadline:
                break
            bk, bs = bk0, bs0
            bm = list(bm0)
            robit = 0
            for c in ro:
                bk += rkey[c]
                bs += skey[c]
                bm[c & 3] |= rbit[c]
                robit |= 1 << c
            s = flush_suit[bs + hsk]
            hv = nonflush[bk + hk] if s < 0 else flush[bm[s] | hm[s]]
            vals = []
            idxs = []
            for i in live:
                if cbit[i] & robit:
                    continue
                s = flush_suit[bs + cs[i]]
                if s < 0:
                    vals.append(nonflush[bk + ck[i]])
                else:
                    vals.append(flush[bm[s] | csm[i][s]])
                idxs.append(i)
            n = len(vals)
            if n < 2:
                continue
            order = sorted(vals)
            mid = {}
            j = 0
            while j < n:
                v = order[j]
                k = j + 1
                while k < n and order[k] == v:
                    k += 1
                mid[v] = (j + k - 1) * 0.5 / (n - 1)
                j = k
            for v, i in zip(vals, idxs):
                hsum[i] += mid[v]
                cnt[i] += 1
                if hv > v:
                    win[i] += 1.0
                elif hv == v:
                    win[i] += 0.5
            done += 1
        heq = [0.0] * 1326
        hs = [0.0] * 1326
        for i in live:
            if cnt[i]:
                heq[i] = win[i] / cnt[i]
                hs[i] = hsum[i] / cnt[i]
            else:
                heq[i] = 0.5
                hs[i] = 0.5
        return live, heq, hs, done

    def equity_multi(self, hero, board, opponents, rng, samples=300):
        """Hero's share of the pot against `opponents` random hands."""
        dead = set(hero) | set(board)
        deck = [c for c in range(52) if c not in dead]
        need = 5 - len(board)
        total = 0.0
        for _ in range(samples):
            cards = rng.sample(deck, need + 2 * opponents)
            full = list(board) + cards[:need]
            mine = self.value(list(hero) + full)
            best = 0
            ties = 0
            for k in range(opponents):
                v = self.value(cards[need + 2 * k:need + 2 * k + 2] + full)
                if v > best:
                    best, ties = v, 1
                elif v == best:
                    ties += 1
            if mine > best:
                total += 1.0
            elif mine == best:
                total += 1.0 / (1 + ties)
        return total / samples
