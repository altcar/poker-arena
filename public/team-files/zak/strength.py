"""
Hand strength: for every combo on a board, its expected percentile against
a random hand once the board is complete (about its equity against a
random hand, so draws count). Preflop it comes from the equity tables.
"""

import random
from bisect import bisect_left, bisect_right
from itertools import combinations

from cards import CLASS_OF, COMBOS, NUM_COMBOS, rank7
from tables import EQ169, PAIRS

BUCKET_EDGES = (0.3, 0.5, 0.7, 0.85, 0.95)  # air .. strong, very strong, nuts
NUM_BUCKETS = len(BUCKET_EDGES) + 1
FLOP_RUNOUTS = 40


def _preflop() -> list[float]:
    """Each combo's percentile among all 1326 by equity against a random
    hand, so preflop strengths spread over 0..1 like the others."""
    by_class = []
    for i in range(len(EQ169)):
        total = sum(PAIRS[i])
        by_class.append(sum(w * e for w, e in zip(PAIRS[i], EQ169[i])) / total)
    equity = [by_class[c] for c in CLASS_OF]
    ordered = sorted(equity)
    n = NUM_COMBOS - 1
    return [(bisect_left(ordered, e)
             + 0.5 * (bisect_right(ordered, e) - bisect_left(ordered, e) - 1)) / n
            for e in equity]


PREFLOP_STRENGTH = _preflop()


def bucket_of(strength: float) -> int:
    """0 (air) .. NUM_BUCKETS - 1 (very strong)."""
    for b, edge in enumerate(BUCKET_EDGES):
        if strength < edge:
            return b
    return NUM_BUCKETS - 1


def board_strengths(board: list[int]) -> list[float]:
    """
    Strength 0..1 of every combo on board (3 to 5 cards, or none). Combos
    that share a card with the board get 0. Flop runouts are sampled with
    a seed from the board, so the same board always gives the same values.
    """
    if not board:
        return list(PREFLOP_STRENGTH)
    used = set(board)
    rest = [c for c in range(52) if c not in used]
    missing = 5 - len(board)
    if missing == 0:
        runouts = [()]
    elif missing == 1:
        runouts = [(c,) for c in rest]
    else:
        everything = list(combinations(rest, missing))
        rng = random.Random(hash(tuple(sorted(board))))
        runouts = rng.sample(everything, FLOP_RUNOUTS)
    total = [0.0] * NUM_COMBOS
    counted = [0] * NUM_COMBOS
    for runout in runouts:
        full = board + list(runout)
        taken = set(full)
        live = [i for i, (a, b) in enumerate(COMBOS)
                if a not in taken and b not in taken]
        ranks = {i: rank7([*COMBOS[i], *full]) for i in live}
        ordered = sorted(ranks.values())
        n = len(ordered) - 1  # opponents: every other live combo, roughly
        for i, r in ranks.items():
            below = bisect_left(ordered, r)
            ties = bisect_right(ordered, r) - below - 1  # not counting itself
            total[i] += (below + 0.5 * ties) / n
            counted[i] += 1
    return [t / c if c else 0.0 for t, c in zip(total, counted)]


class StrengthCache:
    """board_strengths and buckets per board, kept for one hand."""

    def __init__(self):
        self.values: dict[tuple[int, ...], list[float]] = {}

    def strengths(self, board: list[int]) -> list[float]:
        key = tuple(board)
        if key not in self.values:
            self.values[key] = board_strengths(list(board))
        return self.values[key]

    def buckets(self, board: list[int]) -> list[int]:
        return [bucket_of(s) for s in self.strengths(board)]
