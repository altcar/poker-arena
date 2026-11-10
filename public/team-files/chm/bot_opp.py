"""
Opponent statistics, kept per team name as plain dicts and lists, and the
smoothed frequencies the bot plays against.

Every count starts from a prior (pseudo-counts) that describes a sensible
unknown opponent, so the bot plays solid poker until it has seen enough
hands, then moves towards what the opponent actually does.
"""

import math

# Bet size buckets: chips to call / pot before that bet (a pot fraction).
BUCKETS = (0.4, 0.8, 1.3, 2.5)

# Situations an opponent can face a bet or raise in, with prior fold rates
# per size bucket and a prior raise rate. "pre_open": the big blind facing
# the small blind's first raise; "pre_limp": a limper facing a raise;
# "pre_3b": facing a re-raise of its own raise; "post_betN": facing the first
# bet of a street after continuing (calling or betting) on N earlier
# postflop streets (2 means two or more), as its range is stronger each
# time; "post_raise": facing a raise of its own bet.
FOLD_PRIOR = {
    "pre_open": (0.25, 0.33, 0.40, 0.45, 0.45),
    "pre_limp": (0.20, 0.28, 0.35, 0.42, 0.45),
    "pre_3b": (0.12, 0.20, 0.28, 0.30, 0.32),
    "post_bet0": (0.25, 0.35, 0.45, 0.52, 0.60),
    "post_bet1": (0.18, 0.26, 0.34, 0.40, 0.46),
    "post_bet2": (0.12, 0.18, 0.25, 0.30, 0.35),
    "post_raise": (0.18, 0.26, 0.33, 0.40, 0.46),
}
RAISE_PRIOR = {
    "pre_open": 0.12, "pre_limp": 0.10, "pre_3b": 0.20,
    "post_bet0": 0.08, "post_bet1": 0.07, "post_bet2": 0.06,
    "post_raise": 0.12,
}
BUCKET_K = 3.0  # pseudo-counts per bucket
OVERALL_K = 4.0  # pseudo-counts for the overall rates


def bucket_of(ratio):
    for i, edge in enumerate(BUCKETS):
        if ratio < edge:
            return i
    return len(BUCKETS)


def new_stats():
    return {
        "hands": 0,
        # Small blind's first move preflop: fold, limp (call), raise.
        "pf_open": [0, 0, 0],
        # Big blind facing a limp: check, raise.
        "pf_limp": [0, 0],
        # Facing a bet or raise, per situation and size bucket: fold, call,
        # raise.
        "vs": {
            key: [[0, 0, 0] for _ in range(len(BUCKETS) + 1)]
            for key in FOLD_PRIOR
        },
        # Postflop with nothing to call: check, bet.
        "open_post": [0, 0],
        # Aggressive moves seen at showdown: weak hands (below the median),
        # the weak hands an honest range would have had, all.
        "sd": [0, 0.0, 0],
        "showdowns": 0,
        # Sizes: postflop bets when checked to (pot fractions), raises of a
        # limp (big blinds, street total).
        "bet_ratio": [0.0, 0],
        "limp_raise": [0.0, 0],
    }


def _logit(p):
    p = min(0.995, max(0.005, p))
    return math.log(p / (1 - p))


def _sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


def freq(counts, priors, k=OVERALL_K):
    """Smoothed frequencies of each outcome."""
    total = sum(counts) + k
    return [(c + k * p) / total for c, p in zip(counts, priors)]


def open_freqs(stats):
    """(fold, limp, raise) for the small blind's first move."""
    return freq(stats["pf_open"], (0.2, 0.2, 0.6))


def limp_raise_freq(stats):
    """How often the big blind raises a limp."""
    return freq(stats["pf_limp"], (0.7, 0.3))[1]


BET_PRIOR = 0.33  # postflop bet rate with nothing to call


def bet_freq(stats):
    """How often the opponent bets postflop when checked to (or first)."""
    return freq(stats["open_post"], (1 - BET_PRIOR, BET_PRIOR))[1]


def _totals(table):
    folds = sum(row[0] for row in table)
    raises = sum(row[2] for row in table)
    seen = sum(sum(row) for row in table)
    return folds, raises, seen


def _mean(values):
    return sum(values) / len(values)


def response(stats, key, ratio):
    """(fold, call, raise) probabilities when the opponent faces a bet of
    ratio times the pot in situation key (see FOLD_PRIOR).

    Each bucket's estimate is shrunk towards the situation's overall rate,
    and each situation's towards the opponent's rate everywhere, so a
    calling station is recognised in every spot from a few hands."""
    vs = stats["vs"]
    g_fold = g_raise = g_seen = 0
    g_fold_prior = g_raise_prior = 0.0
    for k, table in vs.items():
        f, r, n = _totals(table)
        g_fold += f
        g_raise += r
        g_seen += n
        g_fold_prior += n * _mean(FOLD_PRIOR[k])
        g_raise_prior += n * RAISE_PRIOR[k]
    m_fold = (g_fold_prior + OVERALL_K * 0.4) / (g_seen + OVERALL_K)
    m_raise = (g_raise_prior + OVERALL_K * 0.1) / (g_seen + OVERALL_K)
    shift_g = _logit((g_fold + OVERALL_K * m_fold) / (g_seen + OVERALL_K)) \
        - _logit(m_fold)
    raise_scale = ((g_raise + OVERALL_K * m_raise) / (g_seen + OVERALL_K)) \
        / m_raise

    table = vs[key]
    priors = FOLD_PRIOR[key]
    f, r, n = _totals(table)
    mk = _mean(priors)
    own_prior = _sigmoid(_logit(mk) + shift_g)
    shift = _logit((f + OVERALL_K * own_prior) / (n + OVERALL_K)) \
        - _logit(mk)
    b = bucket_of(ratio)
    q = _sigmoid(_logit(priors[b]) + shift)
    row = table[b]
    nb = sum(row)
    pf = (row[0] + BUCKET_K * q) / (nb + BUCKET_K)
    rk = min(0.9, RAISE_PRIOR[key] * raise_scale)
    overall_raise = (r + OVERALL_K * rk) / (n + OVERALL_K)
    pr = (row[2] + BUCKET_K * overall_raise) / (nb + BUCKET_K)
    pf = min(0.97, max(0.01, pf))
    pr = min(0.95, max(0.005, pr))
    if pf + pr > 0.99:
        pr = max(0.0, 0.99 - pf)
    return pf, 1.0 - pf - pr, pr


def bluffiness(stats):
    """b in [0.01, 1]: the share of the opponent's betting range that is
    bluffs (random hands) rather than its top hands. At showdown an honest
    range shows weak hands (below the median) only when it is wider than
    half; bluffs show them half the time. Prior: 12% bluffs."""
    weak, honest_weak, total = stats["sd"]
    k = 6.0
    b = (weak + k * 0.12 * 0.5 - honest_weak) / (
        0.5 * (total + k) - honest_weak)
    return min(1.0, max(0.01, b))


def mix_floor(b, f):
    """The weight kept by hands outside an honest range of share f, so that
    bluffs make up a share b of the range after the move."""
    f = min(0.98, max(0.02, f))
    if b >= 0.999:
        return 1.0
    return min(1.0, b * f / ((1.0 - b) * (1.0 - f)))


def bet_size(stats):
    """The opponent's usual postflop bet, as a pot fraction."""
    total, n = stats["bet_ratio"]
    return (total + 3 * 0.7) / (n + 3)


def limp_raise_size(stats):
    """The opponent's usual raise of a limp, in big blinds (street total)."""
    total, n = stats["limp_raise"]
    return (total + 2 * 4.0) / (n + 2)
