"""
Ranges, hand-strength orderings and equity profiles.

An opponent's range is a list of 1326 weights, one per combo in
pk_eval.COMBOS (0 for combos that are impossible or ruled out).

An equity "profile" summarises our equity against a range as a function of
where each opponent hand sits in that range: a list of
(q, weight, weighted_result) bins, where q in [0, 1] is the hand's
percentile inside the range (1 = their best hands). From one profile we can
get our equity against any sub-range, e.g. "the hands that call a pot-sized
bet" = the top (1 - fold%) of the range, without re-simulating.
"""

import math
from bisect import bisect_right

import pk_eval as E
from pk_pretable import PRE_EQ, PRE_STRENGTH

COMBOS = E.COMBOS
CLASS_OF_COMBO = E.CLASS_OF_COMBO
RK, SK, RB = E.RK, E.SK, E.RB
NONFLUSH, FLUSH = E.NONFLUSH, E.FLUSH
STRAIGHT_OUTS = E.STRAIGHT_OUTS
evaluate = E.evaluate

NBINS = 40


def sig(x):
    if x > 30:
        return 1.0
    if x < -30:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


# -- hand strength (absolute, for ordering ranges) ---------------------------


def board_strengths(board):
    """Absolute strength in [0, 1] of every combo on this board (list of
    1326, None where a combo uses a board card): the made hand's percentile
    among all combos, lifted for flush and straight draws before the
    river. Opponents are modelled as acting on roughly this ordering."""
    dead = set(board)
    n = len(board)
    vals = [None] * 1326
    pairs = []
    for i, (a, b) in enumerate(COMBOS):
        if a in dead or b in dead:
            continue
        v = evaluate([a, b] + board)
        vals[i] = v
        pairs.append((v, i))
    pairs.sort()
    total = len(pairs)
    out = [None] * 1326
    k = 0
    while k < total:
        j = k
        v = pairs[k][0]
        while j < total and pairs[j][0] == v:
            j += 1
        pct = (k + j) / 2.0 / total
        for t in range(k, j):
            out[pairs[t][1]] = pct
        k = j
    if n >= 5:
        return out
    # Draws (flop and turn only).
    suit_count = [0, 0, 0, 0]
    bmask = 0
    for c in board:
        suit_count[c & 3] += 1
        bmask |= RB[c]
    board_outs = STRAIGHT_OUTS[bmask]
    scale = 1.0 if n == 3 else 0.55
    for i, (a, b) in enumerate(COMBOS):
        s = out[i]
        if s is None:
            continue
        cat = vals[i] >> 20
        if cat >= 4:
            continue
        d = 0.0
        sa, sb = a & 3, b & 3
        if sa == sb:
            if suit_count[sa] == 2:
                d = 0.38
        else:
            if suit_count[sa] == 3 or suit_count[sb] == 3:
                d = 0.30  # one-card flush draw
        outs = STRAIGHT_OUTS[bmask | RB[a] | RB[b]]
        if outs > board_outs:
            sd = 0.30 if outs >= 2 else 0.14
            d = d + sd - d * sd * 0.5 if d else sd
        if d:
            out[i] = s + (1.0 - s) * d * scale
    return out


# -- range bookkeeping -------------------------------------------------------


def preflop_class_weights_to_combos(class_w, dead):
    w = [0.0] * 1326
    for i, (a, b) in enumerate(COMBOS):
        if a in dead or b in dead:
            continue
        w[i] = class_w[CLASS_OF_COMBO[i]]
    return w


def weighted_percentiles(weights, strengths):
    """q per combo: its position in the weighted range by strength (ties
    share the midpoint). Combos with no weight get q from neighbours."""
    items = [(strengths[i], i) for i in range(len(weights))
             if weights[i] > 0 and strengths[i] is not None]
    items.sort()
    total = 0.0
    for _, i in items:
        total += weights[i]
    q = [0.0] * len(weights)
    if total <= 0:
        return q
    acc = 0.0
    k = 0
    n = len(items)
    while k < n:
        j = k
        s = items[k][0]
        group = 0.0
        while j < n and items[j][0] == s:
            group += weights[items[j][1]]
            j += 1
        mid = (acc + group / 2.0) / total
        for t in range(k, j):
            q[items[t][1]] = mid
        acc += group
        k = j
    return q


def class_percentiles(class_w, ncombos):
    """q per class for a class-weighted preflop range."""
    order = sorted(range(169), key=lambda c: PRE_STRENGTH[c])
    total = sum(class_w[c] * ncombos[c] for c in range(169))
    q = [0.0] * 169
    if total <= 0:
        return q
    acc = 0.0
    for c in order:
        m = class_w[c] * ncombos[c]
        q[c] = (acc + m / 2.0) / total
        acc += m
    return q


# Action likelihoods given a hand's percentile q in the current range.
WIDTH = 0.07


def action_likelihood(kind, q, pf, pc, pr, alpha, trap):
    """P(action | hand at percentile q): a mix of a strength-ordered player
    (raises the top pr, calls the next pc, folds the bottom pf) and a
    player whose actions ignore their cards (alpha = weight of the first)."""
    if kind == "raise":
        ls = sig((q - (1.0 - pr)) / WIDTH)
        base = pr
    else:  # call or check
        top = sig((q - (1.0 - pr)) / WIDTH)
        ls = sig((q - pf) / WIDTH) * (1.0 - top) + trap * top
        base = pc
    v = alpha * ls + (1.0 - alpha) * base
    return v if v > 0.004 else 0.004


# -- equity profiles ---------------------------------------------------------


def _bins_from_samples(samples):
    w = [0.0] * NBINS
    s = [0.0] * NBINS
    qs = [0.0] * NBINS
    for q, weight, result in samples:
        b = int(q * NBINS)
        if b >= NBINS:
            b = NBINS - 1
        w[b] += weight
        s[b] += weight * result
        qs[b] += weight * q
    out = []
    for b in range(NBINS):
        if w[b] > 0:
            out.append(((qs[b] / w[b],), w[b], s[b]))
    return out


def profile_preflop(my_class, my_cards, class_w):
    """HU preflop profile from the 169x169 equity table."""
    dead = set(my_cards)
    ncombos = [0] * 169
    for i, (a, b) in enumerate(COMBOS):
        if a in dead or b in dead:
            continue
        ncombos[CLASS_OF_COMBO[i]] += 1
    q = class_percentiles(class_w, ncombos)
    row = PRE_EQ[my_class]
    samples = []
    for c in range(169):
        m = class_w[c] * ncombos[c]
        if m > 0:
            samples.append((q[c], m, row[c]))
    return _bins_from_samples(samples)


def profile_river(my, board, weights, q):
    """Exact HU river profile: every combo in the range."""
    mine = evaluate(my + board)
    dead = set(my) | set(board)
    samples = []
    for i, (a, b) in enumerate(COMBOS):
        w = weights[i]
        if w <= 0 or a in dead or b in dead:
            continue
        v = evaluate([a, b] + board)
        r = 1.0 if mine > v else (0.5 if mine == v else 0.0)
        samples.append((q[i], w, r))
    return _bins_from_samples(samples)


def _cumulative(weights, dead):
    idx = []
    cum = []
    acc = 0.0
    for i, w in enumerate(weights):
        if w <= 0:
            continue
        a, b = COMBOS[i]
        if a in dead or b in dead:
            continue
        acc += w
        idx.append(i)
        cum.append(acc)
    return idx, cum


def profile_mc(my, board, ranges, n, rng):
    """Monte Carlo profile against one or more opponents.

    ranges: per opponent (weights, q). Returns bins (HU) or raw samples
    (multiway: q is a tuple with one entry per opponent)."""
    dead = set(my) | set(board)
    tables = []
    for weights, q in ranges:
        idx, cum = _cumulative(weights, dead)
        if not idx:
            return []
        tables.append((idx, cum, q))
    deck = [c for c in range(52) if c not in dead]
    nd = len(deck)
    need = 5 - len(board)
    rand = rng.random
    samples = []
    multi = len(tables) > 1
    for _ in range(n):
        used = []
        holes = []
        qs = []
        ok = True
        for idx, cum, q in tables:
            j = bisect_right(cum, rand() * cum[-1])
            if j >= len(idx):
                j = len(idx) - 1
            ci = idx[j]
            a, b = COMBOS[ci]
            if a in used or b in used:
                ok = False
                break
            used.append(a)
            used.append(b)
            holes.append((a, b))
            qs.append(q[ci])
        if not ok:
            continue
        run = []
        while len(run) < need:
            c = deck[int(rand() * nd)]
            if c in used or c in run:
                continue
            run.append(c)
        full = board + run
        mine = evaluate(my + full)
        best = -1
        ties = 0
        for a, b in holes:
            v = evaluate([a, b] + full)
            if v > best:
                best = v
                ties = 1
            elif v == best:
                ties += 1
        if mine > best:
            r = 1.0
        elif mine == best:
            r = 1.0 / (ties + 1)
        else:
            r = 0.0
        if multi:
            samples.append((tuple(qs), 1.0, r))
        else:
            samples.append((qs[0], 1.0, r))
    if multi:
        return samples
    return _bins_from_samples(samples)


def band_weight(band, q):
    """How much of a hand at percentile q is in a sub-range.

    band = (lo, hi, alpha, base, trap): a strength-ordered player keeps
    the hands between lo and hi (None: no cut) plus trap of those above hi;
    a card-blind player keeps base of every hand; alpha mixes the two."""
    lo, hi, alpha, base, trap = band
    v = 1.0
    if lo is not None:
        v = sig((q - lo) / WIDTH)
    if hi is not None:
        top = sig((q - hi) / WIDTH)
        v = v * (1.0 - top) + trap * top
    return alpha * v + (1.0 - alpha) * base


def profile_equity(profile, bands=None):
    """(equity, mass) against the range re-weighted by bands (one per
    opponent; None: the whole range). mass is the share of the range kept."""
    num = 0.0
    den = 0.0
    tot = 0.0
    for qs, w, s in profile:
        f = 1.0
        if bands is not None:
            for k in range(len(qs)):
                if bands[k] is not None:
                    f *= band_weight(bands[k], qs[k])
        num += f * s
        den += f * w
        tot += w
    if den <= 0 or tot <= 0:
        return 0.5, 0.0
    return num / den, den / tot
