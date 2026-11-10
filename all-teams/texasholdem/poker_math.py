"""Fast, dependency-free hand ranking and sampled equity for our bot.

Cards are integers: rank index * 4 + suit index. Ranks compare exactly like
card_utils.evaluate, including kickers and the ace-low straight.
"""
from bisect import bisect_right
from itertools import combinations


def encode(card):
    return (card.value - 2) * 4 + "CDHS".index(card.suit)


def straight_high(mask):
    for high in range(14, 5 - 1, -1):
        run = 31 << (high - 4)
        if mask & run == run:
            return high
    return 5 if mask & 16444 == 16444 else 0  # A, 2, 3, 4, 5


def rank_hand(cards):
    counts = [0] * 15
    suits = [0] * 4
    mask = 0
    for card in cards:
        rank, suit = card // 4 + 2, card % 4
        counts[rank] += 1
        suits[suit] |= 1 << rank
        mask |= 1 << rank
    flush = next((m for m in suits if m.bit_count() >= 5), 0)
    if flush:
        high = straight_high(flush)
        if high:
            return (8, high)
    fours = [r for r in range(14, 1, -1) if counts[r] == 4]
    if fours:
        return (7, fours[0], next(r for r in range(14, 1, -1)
                                   if counts[r] and r != fours[0]))
    trips = [r for r in range(14, 1, -1) if counts[r] >= 3]
    pairs = [r for r in range(14, 1, -1) if counts[r] >= 2]
    if trips:
        other = [r for r in pairs if r != trips[0]]
        if other:
            return (6, trips[0], other[0])
    if flush:
        return (5, *[r for r in range(14, 1, -1) if flush & (1 << r)][:5])
    high = straight_high(mask)
    if high:
        return (4, high)
    values = [r for r in range(14, 1, -1) if counts[r]]
    if trips:
        return (3, trips[0], *[r for r in values if r != trips[0]][:2])
    if len(pairs) >= 2:
        return (2, pairs[0], pairs[1], next(r for r in values if r not in pairs[:2]))
    if pairs:
        return (1, pairs[0], *[r for r in values if r != pairs[0]][:3])
    return (0, *values[:5])


def preflop_strength(hand):
    """Ordering heuristic for range inference, NOT a measured equity."""
    high, low = sorted((c // 4 + 2 for c in hand), reverse=True)
    if high == low:
        return 0.50 + high * 0.025
    score = 0.25 + high * 0.022 + low * 0.008
    score += 0.035 if hand[0] % 4 == hand[1] % 4 else 0.0
    score += 0.02 if high - low == 1 else 0.0
    score -= 0.02 if high - low >= 5 else 0.0
    return score


def draw_strength(hand, board):
    if len(board) >= 5:
        return 0.0
    cards = tuple(hand) + tuple(board)
    flush_draw = any(sum(c % 4 == s for c in cards) == 4
                     and any(c % 4 == s for c in hand) for s in range(4))
    mask = 0
    for c in cards:
        mask |= 1 << (c // 4 + 2)
    if mask & (1 << 14):
        mask |= 1 << 1
    board_mask = 0
    for card in board:
        board_mask |= 1 << (card // 4 + 2)
    if board_mask & (1 << 14):
        board_mask |= 1 << 1
    private_ranks = mask & ~board_mask
    straight_draw = any((mask & (31 << low)).bit_count() == 4
                        and private_ranks & (31 << low)
                        for low in range(1, 11))
    return (0.16 if flush_draw else 0.0) + (0.09 if straight_draw else 0.0)


def made_strength(hand, board):
    """Cheap action-likelihood feature; actual decisions use equity below."""
    rank = rank_hand(tuple(hand) + tuple(board))
    if len(board) == 5 and rank == rank_hand(board):
        return 0.20  # Playing the board is not a personal monster.
    hole_ranks = [c // 4 + 2 for c in hand]
    board_ranks = [c // 4 + 2 for c in board]
    if rank[0] >= 4:
        return 0.94
    if rank[0] == 3:
        return 0.88 if rank[1] in hole_ranks else 0.35
    if rank[0] == 2:
        own_pairs = sum(r in hole_ranks for r in rank[1:3])
        return 0.82 if own_pairs == 2 else (0.62 if own_pairs else 0.24)
    if rank[0] == 1:
        pair = rank[1]
        if pair not in hole_ranks:
            return 0.20 + 0.01 * (max(hole_ranks) - 8)
        if pair > max(board_ranks):
            return 0.77
        if pair == max(board_ranks):
            return 0.65 + 0.006 * (max(hole_ranks) - 8)
        return 0.48 if pair >= sorted(board_ranks)[len(board_ranks) // 2] else 0.37
    return 0.12 + 0.01 * (max(hole_ranks) - 8)


def all_hands(excluded):
    return list(combinations([c for c in range(52) if c not in excluded], 2))


def sample_equity(hand, board, ranges, rng, samples=480):
    """Equity against weighted ranges, removing blockers and splitting ties.

    Multiway ranges are sampled sequentially conditional on already drawn
    cards. This is an approximation; heads-up sampling is exact for the
    supplied range. All loops have a fixed bound.
    """
    known = set(hand) | set(board)
    available = [c for c in range(52) if c not in known]
    distributions = []
    for hands, weights in ranges:
        choices, cumulative, total = [], [], 0.0
        for pair, weight in zip(hands, weights):
            if pair[0] in known or pair[1] in known or weight <= 0:
                continue
            choices.append(pair)
            total += weight
            cumulative.append(total)
        distributions.append((choices, cumulative, total))
    if not distributions:
        return 1.0
    # On the river heads-up, enumerate all remaining opponent hands.
    if len(board) == 5 and len(distributions) == 1:
        choices, cumulative, total = distributions[0]
        if total <= 0:
            return 0.5
        mine, won, previous = rank_hand(tuple(hand) + tuple(board)), 0.0, 0.0
        for pair, upper in zip(choices, cumulative):
            theirs = rank_hand(pair + tuple(board))
            won += (upper - previous) * (1.0 if mine > theirs else 0.5 if mine == theirs else 0.0)
            previous = upper
        return won / total
    won = 0.0
    for _ in range(samples):
        used, opponents = set(known), []
        for choices, cumulative, total in distributions:
            pair = None
            for _attempt in range(24):
                if total <= 0:
                    break
                candidate = choices[min(bisect_right(cumulative, rng.random() * total), len(choices) - 1)]
                if candidate[0] not in used and candidate[1] not in used:
                    pair = candidate
                    break
            if pair is None:
                pair = tuple(rng.sample([c for c in available if c not in used], 2))
            used.update(pair)
            opponents.append(pair)
        runout = tuple(board) + tuple(rng.sample([c for c in available if c not in used], 5 - len(board)))
        mine = rank_hand(tuple(hand) + runout)
        ranks = [rank_hand(pair + runout) for pair in opponents]
        best = max(ranks)
        if mine > best:
            won += 1.0
        elif mine == best:
            won += 1.0 / (1 + ranks.count(best))
    return won / samples
