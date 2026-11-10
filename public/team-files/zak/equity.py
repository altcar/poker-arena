"""
Our equity against each of the 1326 opponent combos at once. Equity against
any range is then a weighted average of this vector, so one computation
serves every range a decision looks at.
"""

import random

from cards import CLASS_OF, COMBOS, NUM_COMBOS, hand_class, rank7
from tables import EQ169


def equity_vector(hand: list[int], board: list[int], rng: random.Random,
                  flop_runouts: int = 60) -> list[float]:
    """
    eq[c]: our share of the pot at showdown against combo c (ties split),
    0 for combos that clash with our cards or the board. Preflop from the
    class equity table; flop by sampled runouts; turn and river exactly.
    """
    dead = set(hand) | set(board)
    live = [i for i, (a, b) in enumerate(COMBOS)
            if a not in dead and b not in dead]
    eq = [0.0] * NUM_COMBOS
    if not board:
        row = EQ169[hand_class(*hand)]
        for i in live:
            eq[i] = row[CLASS_OF[i]]
        return eq
    rest = [c for c in range(52) if c not in dead]
    missing = 5 - len(board)
    if missing == 0:
        runouts = [()]
    elif missing == 1:
        runouts = [(c,) for c in rest]
    else:
        runouts = [tuple(rng.sample(rest, 2)) for _ in range(flop_runouts)]
    counted = [0] * NUM_COMBOS
    for runout in runouts:
        full = board + list(runout)
        ours = rank7(hand + full)
        taken = set(runout)
        for i in live:
            a, b = COMBOS[i]
            if a in taken or b in taken:
                continue
            theirs = rank7([a, b, *full])
            eq[i] += 1.0 if ours > theirs else 0.5 if ours == theirs else 0.0
            counted[i] += 1
    return [e / n if n else 0.0 for e, n in zip(eq, counted)]


def range_equity(eq: list[float], weights: list[float]) -> float:
    """Our equity against a range (weights over combos, any scale)."""
    total = sum(weights)
    if total <= 0:
        return 0.5
    return sum(w * e for w, e in zip(weights, eq) if w) / total
