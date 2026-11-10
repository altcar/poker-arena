"""
Preflop ranges: the 169 classes ranked by equity against a random hand
(pk_tables.RANKING), and "top x%" ranges counted in combos (pairs 6,
suited 4, offsuit 12, out of 1326).

A range is a list of 169 class weights (0..1). top_weights(0.3) is the top
30% of combos; the class on the edge gets the fraction that fits, so
equities change smoothly with the percentage.
"""

from pk_cards import CLASS_SIZE
from pk_tables import RANKING

TOTAL_COMBOS = 1326

BEFORE = [0.0] * 169  # share of all combos in better classes
AFTER = [0.0] * 169  # the same, this class included
_cum = 0
for _k in RANKING:
    BEFORE[_k] = _cum / TOTAL_COMBOS
    _cum += CLASS_SIZE[_k]
    AFTER[_k] = _cum / TOTAL_COMBOS
del _k, _cum

# Where a class sits, 0 (best) .. 1 (worst): the middle of its band.
PERCENTILE = [(BEFORE[k] + AFTER[k]) / 2 for k in range(169)]


def top_weights(pct):
    """Class weights of the top pct (0..1) of hands."""
    weights = [0.0] * 169
    for k in range(169):
        lo = BEFORE[k]
        hi = AFTER[k]
        if pct >= hi:
            weights[k] = 1.0
        elif pct > lo:
            weights[k] = (pct - lo) / (hi - lo)
    return weights


def band_weights(lo_pct, hi_pct):
    """Class weights of hands between the top lo_pct and the top hi_pct,
    e.g. a calling range that leaves out the hands that would raise."""
    top = top_weights(hi_pct)
    cut = top_weights(lo_pct)
    return [max(0.0, t - c) for t, c in zip(top, cut)]
