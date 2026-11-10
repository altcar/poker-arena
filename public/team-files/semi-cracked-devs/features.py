"""Features shared by the label generator and the live bot (stdlib only).

All chip amounts are in big blinds. The bot MUST call extract() exactly the
same way the generator does, or the model sees inputs it was never trained on.
"""
import time

from poker_eval import evaluate, STRAIGHT_HIGH, POPCOUNT

WINDOWS = [0b11111 << i for i in range(9)] + [0b1000000001111]

FEATURE_NAMES = [
    "preflop", "flop", "turn", "river", "is_button",
    "equity", "strength", "flush_draw", "straight_draw",
    "board_paired", "board_flushy", "board_straighty",
    "pot_odds", "call_frac", "spr", "facing_bet",
    "opp_fold", "opp_aggr", "opp_vpip",
]

# Neutral prior used when the opponent model has too few samples.
UNKNOWN_OPP = {"fold": 0.40, "aggr": 0.40, "vpip": 0.50}


def mc_equity(hole, board, n, rng, deadline=None):
    """Equity vs one random hand, by Monte Carlo. Stops early at deadline."""
    dead = set(hole) | set(board)
    deck = [c for c in range(52) if c not in dead]
    need = 5 - len(board)
    total = 0.0
    done = 0
    for i in range(n):
        s = rng.sample(deck, 2 + need)
        full = board + s[2:]
        a = evaluate(hole + full)
        b = evaluate(s[:2] + full)
        total += 1.0 if a > b else (0.5 if a == b else 0.0)
        done += 1
        if deadline is not None and (i & 31) == 31 and time.perf_counter() > deadline:
            break
    return total / done if done else 0.5


def preflop_strength(hole):
    r1, r2 = sorted((hole[0] >> 2, hole[1] >> 2), reverse=True)
    if r1 == r2:
        return 0.55 + 0.45 * r1 / 12
    s = 0.6 * (r1 + r2) / 24
    if (hole[0] & 3) == (hole[1] & 3):
        s += 0.06
    gap = r1 - r2
    if gap <= 4:
        s += 0.06 - 0.015 * (gap - 1)
    return max(0.0, min(1.0, s))


# Rough percentile band of each made-hand category on a postflop board.
CAT_BANDS = [(0.00, 0.30), (0.30, 0.70), (0.70, 0.85), (0.85, 0.91), (0.91, 0.95),
             (0.95, 0.975), (0.975, 0.99), (0.99, 0.998), (0.998, 1.0)]


def strength(hole, board):
    """Cheap 0..1 made-hand strength (no simulation)."""
    if not board:
        return preflop_strength(hole)
    sc = evaluate(hole + board)
    cat = sc >> 20
    if cat == evaluate(board) >> 20:
        # Hole cards don't improve on the board: we're playing the board.
        hi = max(hole[0] >> 2, hole[1] >> 2)
        return 0.05 + 0.25 * hi / 12
    top = (sc >> 16) & 15
    lo, hi = CAT_BANDS[cat]
    return lo + (hi - lo) * top / 12


def draws(hole, board):
    if len(board) not in (3, 4):
        return 0, 0
    sc = [0, 0, 0, 0]
    mask = 0
    for c in hole + board:
        sc[c & 3] += 1
        mask |= 1 << (c >> 2)
    fd = 0
    for s in range(4):
        if sc[s] == 4 and ((hole[0] & 3) == s or (hole[1] & 3) == s):
            fd = 1
    sd = 0
    if STRAIGHT_HIGH[mask] < 0:
        for w in WINDOWS:
            if POPCOUNT[mask & w] == 4:
                sd = 1
                break
    return fd, sd


def texture(board):
    if not board:
        return 0, 0.0, 0.0
    counts = [0] * 13
    suits = [0, 0, 0, 0]
    mask = 0
    for c in board:
        counts[c >> 2] += 1
        suits[c & 3] += 1
        mask |= 1 << (c >> 2)
    paired = 1 if max(counts) >= 2 else 0
    flushy = max(suits) / 5
    straighty = max(POPCOUNT[mask & w] for w in WINDOWS) / 5
    return paired, flushy, straighty


def extract(hole, board, pot, to_call, stack, is_button, opp, rng,
            eq_samples=200, deadline=None):
    """pot includes any bet we face; stack is our effective stack behind."""
    street = {0: 0, 3: 1, 4: 2, 5: 3}[len(board)]
    eq = mc_equity(hole, board, eq_samples, rng, deadline)
    fd, sd = draws(hole, board)
    paired, flushy, straighty = texture(board)
    pot = max(pot, 1e-6)
    return [
        1.0 if street == 0 else 0.0,
        1.0 if street == 1 else 0.0,
        1.0 if street == 2 else 0.0,
        1.0 if street == 3 else 0.0,
        1.0 if is_button else 0.0,
        eq,
        strength(hole, board),
        float(fd), float(sd),
        float(paired), flushy, straighty,
        to_call / (pot + to_call) if to_call > 0 else 0.0,
        min(to_call / pot, 3.0) / 3.0,
        min(stack / pot, 20.0) / 20.0,
        1.0 if to_call > 0 else 0.0,
        opp["fold"], opp["aggr"], opp["vpip"],
    ]


# Equity added by a draw, by board size (flop, turn); used to rank opponent hands.
DRAW_BONUS = {3: (0.20, 0.12), 4: (0.10, 0.06)}


def hand_rank_strength(hole, board):
    """Made-hand strength plus a draw bonus: how good a hand looks to the player holding it."""
    s = strength(hole, board)
    if len(board) in DRAW_BONUS:
        fd, sd = draws(hole, board)
        fb, sb = DRAW_BONUS[len(board)]
        s = min(1.0, s + fd * fb + sd * sb)
    return s


def range_equity(hole, board, keep_frac, n, rng, deadline=None):
    """Equity vs the opponent's top keep_frac of hands (by hand_rank_strength) on this board.

    The idea: a bet comes from a range, not a random hand. Bots that bet rarely bet
    strong hands, so equity vs a random hand overrates ours when they bet.
    """
    dead = set(hole) | set(board)
    deck = [c for c in range(52) if c not in dead]
    need = 5 - len(board)
    probe = sorted(hand_rank_strength(rng.sample(deck, 2), board) for _ in range(80))
    thr = probe[min(len(probe) - 1, int((1.0 - keep_frac) * len(probe)))]
    total = 0.0
    done = 0
    tries = 0
    while done < n and tries < n * 12:
        tries += 1
        opp = rng.sample(deck, 2)
        if hand_rank_strength(opp, board) < thr:
            continue
        rest = [c for c in deck if c != opp[0] and c != opp[1]]
        full = board + rng.sample(rest, need)
        a = evaluate(hole + full)
        b = evaluate(opp + full)
        total += 1.0 if a > b else (0.5 if a == b else 0.0)
        done += 1
        if deadline is not None and (done & 15) == 15 and time.perf_counter() > deadline:
            break
    return total / done if done else 0.5
