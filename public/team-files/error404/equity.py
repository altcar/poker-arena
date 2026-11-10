"""Fast hand evaluation, Chen preflop scores and Monte Carlo equity.

Cards are ints 0..51: value = (c >> 2) + 2 (2..14, ace high), suit = c & 3
(the index into card_utils.SUITS). Plain functions and tables only: none of
this is pickled with the model, so it never needs to be vetted as data.
"""

import math
import time

from card_utils import SUITS


def card_int(card):
    """A card_utils.Card as an int 0..51."""
    return ((card.value - 2) << 2) | SUITS.index(card.suit)


def cards_to_ints(cards):
    return [card_int(card) for card in cards]


# -- hand ranking ----------------------------------------------------------


def _straight_high(present):
    """Top value of a straight in present[2..14] (truthy = value held), the
    wheel counting as 5-high, or 0."""
    for high in range(14, 4, -1):
        if (present[high] and present[high - 1] and present[high - 2]
                and present[high - 3] and present[high - 4]):
            return high
    if present[14] and present[2] and present[3] and present[4] and present[5]:
        return 5
    return 0


def rank_cards(cards):
    """
    The rank of the best 5-card hand among 5 to 7 card ints, in exactly the
    format of card_utils.evaluate: a tuple that compares with < and >, whose
    first number is the category (0 high card .. 8 straight flush).
    """
    counts = [0] * 15
    suited = [[], [], [], []]
    for c in cards:
        v = (c >> 2) + 2
        counts[v] += 1
        suited[c & 3].append(v)
    flush = None
    for values in suited:
        if len(values) >= 5:
            flush = sorted(values, reverse=True)
            break
    if flush is not None:
        present = [0] * 15
        for v in flush:
            present[v] = 1
        high = _straight_high(present)
        if high:
            return (8, high)
    quads, trips, pairs = [], [], []
    for v in range(14, 1, -1):
        n = counts[v]
        if n == 4:
            quads.append(v)
        elif n == 3:
            trips.append(v)
        elif n == 2:
            pairs.append(v)
    if quads:
        q = quads[0]
        for v in range(14, 1, -1):
            if v != q and counts[v]:
                return (7, q, v)
    if trips and (len(trips) > 1 or pairs):
        pair = pairs[0] if pairs else 0
        if len(trips) > 1 and trips[1] > pair:
            pair = trips[1]
        return (6, trips[0], pair)
    if flush is not None:
        return (5, *flush[:5])
    high = _straight_high(counts)
    if high:
        return (4, high)
    if trips:
        t = trips[0]
        kickers = [v for v in range(14, 1, -1) if counts[v] and v != t][:2]
        return (3, t, *kickers)
    if len(pairs) >= 2:
        a, b = pairs[0], pairs[1]
        kicker = next(
            v for v in range(14, 1, -1) if counts[v] and v != a and v != b
        )
        return (2, a, b, kicker)
    if pairs:
        p = pairs[0]
        kickers = [v for v in range(14, 1, -1) if counts[v] and v != p][:3]
        return (1, p, *kickers)
    return (0, *[v for v in range(14, 1, -1) if counts[v]][:5])


# -- preflop strength (Chen formula) ---------------------------------------


def _points(v):
    if v == 14:
        return 10.0
    if v == 13:
        return 8.0
    if v == 12:
        return 7.0
    if v == 11:
        return 6.0
    return v / 2.0


def chen(v1, v2, suited):
    """Chen score of two card values: AA 20, AKs 12, 72o -1."""
    hi, lo = max(v1, v2), min(v1, v2)
    if hi == lo:
        return max(5, math.ceil(_points(hi) * 2))
    score = _points(hi)
    if suited:
        score += 2
    gap = hi - lo - 1
    score -= (0, 1, 2, 4)[gap] if gap < 4 else 5
    if gap <= 1 and hi < 12:
        score += 1
    return math.ceil(score)


def _build_table():
    table = [[0] * 52 for _ in range(52)]
    for a in range(52):
        for b in range(52):
            if a != b:
                table[a][b] = chen((a >> 2) + 2, (b >> 2) + 2,
                                   (a & 3) == (b & 3))
    return table


CHEN = _build_table()  # CHEN[card][card]


# -- Monte Carlo equity ----------------------------------------------------


def _range_pool(deck, cutoff):
    """Every two-card hand of the deck whose Chen score reaches cutoff."""
    pool = []
    for i in range(len(deck)):
        a = deck[i]
        row = CHEN[a]
        for j in range(i + 1, len(deck)):
            if row[deck[j]] >= cutoff:
                pool.append((a, deck[j]))
    return pool


def _draw_hand(deck, used, rng, pool):
    """Two unused cards for an opponent: a hand from pool (the range), or
    any hand if pool is None or has no hand left."""
    if pool:
        for _ in range(60):
            a, b = rng.choice(pool)
            if a not in used and b not in used:
                return [a, b]
    while True:
        a, b = rng.sample(deck, 2)
        if a not in used and b not in used:
            return [a, b]


def equity(hole, board, n_opp, rng, samples=1500, cutoff=None,
           max_seconds=1.5):
    """
    Share of the pot won with hole (2 ints) on board (0 to 5 ints) against
    n_opp opponents, by dealing out the rest `samples` times; a tie counts
    as a split. cutoff (a Chen score) limits opponents to the stronger
    hands. Stops early after max_seconds. 0.5 if nothing could be sampled.
    """
    hole = list(hole)
    board = list(board)
    known = set(hole) | set(board)
    deck = [c for c in range(52) if c not in known]
    missing = 5 - len(board)
    pool = None if cutoff is None else _range_pool(deck, cutoff)
    deadline = time.perf_counter() + max_seconds
    total = 0.0
    done = 0
    for i in range(samples):
        if i % 32 == 31 and time.perf_counter() > deadline:
            break
        drawn = rng.sample(deck, missing) if missing else []
        used = set(drawn)
        full = board + drawn
        mine = rank_cards(hole + full)
        best = None
        ties = 0
        for _ in range(n_opp):
            opp = _draw_hand(deck, used, rng, pool)
            used.update(opp)
            rank = rank_cards(opp + full)
            if best is None or rank > best:
                best, ties = rank, 1
            elif rank == best:
                ties += 1
        if best is None or mine > best:
            total += 1.0
        elif mine == best:
            total += 1.0 / (1 + ties)
        done += 1
    return total / done if done else 0.5
