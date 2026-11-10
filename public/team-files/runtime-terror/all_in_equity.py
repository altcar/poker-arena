"""Side-pot aware equity for closed all-in poker decisions.

The caller passes current preflop contributions, folded seats, and the cost
to call. This module independently mirrors the organiser's contribution-
tier payout logic to estimate expected chips received after a call. It does
NOT confuse outright three-way win probability with multiway pot EV.
"""

import random

from card_utils import evaluate, full_deck


def hero_showdown_payout(contributed, folded, hands, board, button, hero_id):
    """Exact hero payout at one fixed showdown (including split side pots).

    The same tier and odd-chip rules are used by engine.payouts. This
    function deliberately has no dependency on the organisers' engine.
    """
    live = [seat for seat, is_folded in enumerate(folded) if not is_folded]
    if hero_id not in live:
        return 0
    if len(live) == 1:
        return sum(contributed)

    ranks = {
        seat: evaluate(list(hands[seat]) + list(board))
        for seat in live
    }
    order = [(button + step) % len(contributed)
             for step in range(1, len(contributed) + 1)]

    won = 0
    previous = 0
    for level in sorted(set(contributed)):
        pot = sum(
            min(chips, level) - min(chips, previous)
            for chips in contributed
        )
        previous = level
        eligible = [seat for seat in live if contributed[seat] >= level]
        if hero_id not in eligible:
            continue

        best = max(ranks[seat] for seat in eligible)
        winners = [
            seat for seat in order
            if seat in eligible and ranks[seat] == best
        ]
        if hero_id in winners:
            share, odd = divmod(pot, len(winners))
            won += share + (1 if winners.index(hero_id) < odd else 0)
    return won


def expected_call_payout(
    hand, board, contributed, folded, button, hero_id, cost,
    samples=240, seed=0,
):
    """Monte Carlo estimate of chips *received*, not outright win equity.

    Preflop contributed totals include posted blinds, folded dead money and
    all existing bets. Before sampling we add the hero's incremental call.
    When everyone else is all-in, this is a complete payoff model: no
    future betting or strategic equity-realisation assumptions are needed.
    """
    if cost < 0 or samples < 1:
        raise ValueError("cost must be nonnegative; samples must be positive")
    if len(contributed) != len(folded):
        raise ValueError("contributions and folded seats must align")

    opponents = [
        seat for seat in range(len(contributed))
        if seat != hero_id and not folded[seat]
    ]
    final_contributed = list(contributed)
    final_contributed[hero_id] += cost
    known = set(hand) | set(board)
    deck = [card for card in full_deck() if card not in known]
    remaining_board = 5 - len(board)
    draw_count = remaining_board + 2 * len(opponents)
    if draw_count > len(deck) or remaining_board < 0:
        raise ValueError("not enough unexposed cards to sample")

    rng = random.Random(seed)
    payout = 0
    for _ in range(samples):
        drawn = rng.sample(deck, draw_count)
        completed_board = list(board) + drawn[:remaining_board]
        hands = [() for _ in contributed]
        hands[hero_id] = hand
        for i, seat in enumerate(opponents):
            offset = remaining_board + 2 * i
            hands[seat] = drawn[offset:offset + 2]
        payout += hero_showdown_payout(
            final_contributed, folded, hands, completed_board,
            button, hero_id,
        )
    return payout / samples
