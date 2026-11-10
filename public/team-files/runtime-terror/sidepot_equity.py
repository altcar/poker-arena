"""Bounded hero-chip-payout estimator for pots with prior-street all-ins.

Unlike full-table showdown equity, this estimates the *chips hero receives*
from the side-pot tiers hero can actually win. The caller must supply an exact
public per-seat contribution ledger, including folded seats. GameStateView
does not expose historical per-seat contributions; never infer them from pot
and current-street bets. This module makes no runtime file or network calls.

Range order follows opponents (live seats except hero). Uses the same
concrete-combo weights and collision-conditioned sampling as equity_engine.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import product
from math import exp, prod
import random

from card_utils import Card, evaluate, full_deck
from equity_engine import (
    ComboWeights, MAX_EXACT_STATES, MAX_SAMPLES, _normalize_range,
)

_ATTEMPTS_PER_SAMPLE = 24


class ContributionLedger:
    """Pickle-safe hand-local reconstruction from observed public stacks.

    At the *first preflop view*, all chips spent in this hand are in
    view.bets, hence starting_stack = stack_remaining + street_bet.
    Later, starting_stack - current_stack is an exact per-seat cumulative
    commitment, including completed streets and folded players. Reset in
    Model.seed(). If first observed on a later street, return None instead
    of guessing historical contributions.
    """

    def __init__(self):
        self.initial = None

    def reset(self):
        self.initial = None

    def observe(self, view):
        if self.initial is None:
            if view.street != "preflop":
                return None
            self.initial = tuple(
                stack + bet for stack, bet in zip(view.stacks, view.bets)
            )
        if len(self.initial) != len(view.stacks):
            return None
        commitments = tuple(
            initial - stack for initial, stack in zip(self.initial, view.stacks)
        )
        if any(chips < 0 for chips in commitments) or sum(commitments) != view.pot:
            return None
        return commitments


@dataclass(frozen=True)
class PayoutEstimate:
    expected_chips: float
    eligible_pot: int
    trials: int
    exact: bool


def _tiers(contributions: tuple[int, ...], folded: tuple[bool, ...], hero_id: int):
    """Return all showdown tiers hero is eligible to contest."""
    previous = 0
    tiers = []
    for level in sorted(set(contributions)):
        amount = sum(
            min(contribution, level) - min(contribution, previous)
            for contribution in contributions
        )
        previous = level
        eligible = tuple(
            seat for seat in range(len(contributions))
            if not folded[seat] and contributions[seat] >= level
        )
        if amount and hero_id in eligible:
            tiers.append((amount, eligible))
    return tuple(tiers)


def _hero_winnings(
    ranks: dict[int, tuple[int, ...]],
    tiers: Sequence[tuple[int, tuple[int, ...]]],
    hero_id: int,
    button: int,
    seats: int,
) -> int:
    """Mirror engine.payouts: sorted tiers, ties and button-relative odd chips."""
    order = tuple((button + i) % seats for i in range(1, seats + 1))
    received = 0
    for amount, eligible in tiers:
        best = max(ranks[seat] for seat in eligible)
        winners = tuple(
            seat for seat in order if seat in eligible and ranks[seat] == best
        )
        if hero_id in winners:
            whole, odd = divmod(amount, len(winners))
            received += whole + (winners.index(hero_id) < odd)
    return received


def estimate_call_payout(
    hero: Sequence[Card],
    board: Sequence[Card],
    hero_id: int,
    *,
    contributions: Sequence[int],
    folded: Sequence[bool],
    opponents: Sequence[int],
    call_cost: int,
    button: int,
    ranges: Sequence[ComboWeights | None] | None = None,
    samples: int = 65,
    seed: int = 0,
) -> PayoutEstimate:
    """Expected chip payout if hero calls and players run out the board.

    Contributions are the public *total from the entire hand before call*
    (not current-street view.bets); folded players' chips remain in pots.
    Returns the whole pot payout, so marginal call EV is expected_chips-cost.
    This assumes no later bets, with uncertainty handled by the policy.
    """
    counts = tuple(contributions)
    dead = tuple(folded)
    rivals = tuple(opponents)
    n = len(counts)
    if (
        n < 2 or len(dead) != n or not 0 <= hero_id < n or dead[hero_id]
        or not 0 <= button < n
        or any(type(x) is not int or x < 0 for x in counts)
        or any(type(x) is not bool for x in dead)
        or type(call_cost) is not int or call_cost < 0
        or set(rivals) != {p for p in range(n) if not dead[p] and p != hero_id}
        or len(rivals) != len(set(rivals))
        or ranges is not None and len(ranges) != len(rivals)
    ):
        raise ValueError("invalid public side-pot ledger or opponent ordering")
    if type(samples) is not int or not 1 <= samples <= MAX_SAMPLES:
        raise ValueError("sample budget out of bounds")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    if len(hero) != 2 or len(board) not in (3, 4, 5):
        raise ValueError("side-pot calculation requires 2 hero cards and 3-5 board cards")
    if not all(isinstance(c, Card) for c in tuple(hero) + tuple(board)):
        raise ValueError("cards must be Card instances")
    known = tuple(hero) + tuple(board)
    if len(set(known)) != len(known):
        raise ValueError("duplicate known cards")
    deck = tuple(c for c in full_deck() if c not in known)
    to_deal = 5 - len(board)
    if len(deck) < 2 * len(rivals) + to_deal:
        raise ValueError("not enough cards")

    after = list(counts)
    after[hero_id] += call_cost
    tiers = _tiers(tuple(after), dead, hero_id)
    eligible_pot = sum(amount for amount, _ in tiers)
    distributions = tuple(
        _normalize_range(None if ranges is None else ranges[i], deck)
        for i in range(len(rivals))
    )
    if not distributions:
        raise ValueError("no opponents remain in hand")

    def payout(hands, completed_board):
        ranks = {hero_id: evaluate(tuple(hero) + completed_board)}
        ranks.update({
            p: evaluate(hand + completed_board)
            for p, hand in zip(rivals, hands)
        })
        return _hero_winnings(ranks, tiers, hero_id, button, n)

    if to_deal == 0 and prod(len(d.combos) for d in distributions) <= MAX_EXACT_STATES:
        # Stable weighted mean. Some valid joint combos can weigh ~1e-400,
        # while input relative weights may be ~1e300.
        total = weighted = 0.0
        maximum = None
        valid = 0
        for entries in product(*(
            tuple(zip(d.combos, d.log_weights)) for d in distributions
        )):
            hands = tuple(hand for hand, _ in entries)
            used = tuple(card for hand in hands for card in hand)
            if len(set(used)) != len(used):
                continue
            joint_log = sum(w for _, w in entries)
            if maximum is None:
                maximum, weight = joint_log, 1.0
            elif joint_log > maximum:
                factor = exp(maximum - joint_log)
                total *= factor
                weighted *= factor
                maximum, weight = joint_log, 1.0
            else:
                weight = exp(joint_log - maximum)
            total += weight
            weighted += weight * payout(hands, tuple(board))
            valid += 1
        if not valid:
            raise ValueError("no collision-free opponent combinations")
        return PayoutEstimate(weighted / total, eligible_pot, valid, True)

    rng = random.Random(seed)
    total_chips = 0.0
    accepted = 0
    for _ in range(samples * _ATTEMPTS_PER_SAMPLE):
        hands = tuple(d.draw(rng) for d in distributions)
        used = set(known)
        for hand in hands:
            used.update(hand)
        if len(used) != len(known) + 2 * len(rivals):
            continue
        remaining_board = rng.sample(
            [card for card in deck if card not in used], to_deal
        )
        completed_board = tuple(board) + tuple(remaining_board)
        total_chips += payout(hands, completed_board)
        accepted += 1
        if accepted == samples:
            break
    if not accepted:
        raise ValueError("no valid side-pot showdown sampled")
    return PayoutEstimate(total_chips / accepted, eligible_pot, accepted, False)
