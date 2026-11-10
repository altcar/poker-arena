"""One-street expected-value sizing against an inferred calling range.

This is an approximate response model, not a solved poker strategy. It
explicitly distinguishes overall equity from equity when a bet is called.
"""
from bisect import bisect_left, bisect_right
import math

from poker_math import rank_hand


def choose_river_bet(hand, board, opponent_range, pot, min_bet, max_bet,
                     fold_rate, aggression, big_blind):
    """Return additional chips to bet, or zero to check.

    For each candidate B, estimate:
      P(fold)*pot + P(call)*(equity_when_called*(pot+2*B)-B).
    Compare this with checking to showdown. Opponent raises and future
    actions are not modelled; callers with very strong hands cost a full B.
    """
    if max_bet < min_bet or max_bet <= 0:
        return 0
    board = tuple(board)
    mine = rank_hand(tuple(hand) + board)
    known = set(hand) | set(board)
    hands, weights = opponent_range
    rows, ranks = [], []
    for pair, weight in zip(hands, weights):
        if pair[0] in known or pair[1] in known:
            continue
        rank = rank_hand(pair + board)
        ranks.append(rank)
        if weight > 0:
            outcome = 1.0 if mine > rank else 0.5 if mine == rank else 0.0
            rows.append((rank, weight, outcome))
    total = sum(row[1] for row in rows)
    if not total:
        return 0
    if all(row[2] == 0.5 for row in rows):
        return 0  # A shared unbeatable board needs no value bet.
    ranks.sort()
    outcomes = []
    for rank, weight, outcome in rows:
        left, right = bisect_left(ranks, rank), bisect_right(ranks, rank)
        percentile = (left + right) / (2 * len(ranks))
        outcomes.append((percentile, weight / total, outcome))
    check_ev = sum(weight * outcome * pot for _, weight, outcome in outcomes)
    best_ev, best_bet = check_ev, 0
    candidates = {min_bet, max_bet}
    candidates.update(min(max_bet, max(min_bet, round(pot * fraction)))
                      for fraction in (0.33, 0.50, 0.75, 1.0, 1.5))
    # Aggressive, erratic players make weaker hands more likely to continue.
    indiscriminate = max(0.0, min(0.65, (aggression - 0.30) * 1.2))
    for bet in sorted(candidates):
        size_adjustment = 0.10 * math.log2(max(0.1, bet / max(1, pot)) / 0.5)
        threshold = min(0.95, max(0.03, fold_rate + size_adjustment))
        expected = 0.0
        for percentile, weight, outcome in outcomes:
            call_probability = 1 / (1 + math.exp((threshold - percentile) / 0.075))
            if fold_rate < 0.12:
                # Evidence of a calling station overrides a strength filter.
                call_probability = max(call_probability, 1 - 2 * fold_rate)
            call_probability = (1 - indiscriminate) * call_probability + indiscriminate * (1 - threshold)
            called_ev = outcome * (pot + 2 * bet) - bet
            expected += weight * ((1 - call_probability) * pot + call_probability * called_ev)
        # Avoid thin bets driven by tiny response-model differences.
        if expected > best_ev + max(0.02 * pot, 0.05 * big_blind):
            best_ev, best_bet = expected, bet
    return int(best_bet)
