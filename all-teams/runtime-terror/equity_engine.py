"""Bounded, reproducible showdown equity for the four-street poker bot.

Integration API:
    estimate_equity(hero, board, opponents=1,
                    ranges=[{(card_a, card_b): positive_weight}, ...],
                    samples=120, seed=123) -> EquityResult
    showdown_equity(...) -> float

The optional ranges sequence is aligned with opponent seat order. An entry
is None for a uniform unknown opponent, or a mapping of concrete two-Card
tuples to nonnegative weights; these are not 169-class starting hand charts.
Weights need not sum to one. Blocked hands are removed, and the remaining
weights are normalized by the calculation.

Multiway sampling proposes independent opponent hands, rejecting entire
deals with collisions: this preserves the product-weight distribution
conditioned on distinct cards, rather than introducing sequential bias.
The proposal count is bounded. Fewer accepted trials are reported explicitly
when the opponent range combinations rarely permit disjoint hands.

Only standard library and organiser-provided card_utils are used.
No network, dynamic file reads, global mutable state, or other dependencies.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations, product
from math import exp, isfinite, log, log1p, prod
import random

from card_utils import Card, evaluate, full_deck

# OpponentModel can return concrete Card-combination weights for a specific
# team, street, and public action history. None means an uninformative prior.
ComboWeights = Mapping[tuple[Card, Card], float]
MAX_SAMPLES = 2_000
MAX_EXACT_STATES = 1_400  # Hard limit even with an enormous exact_limit argument.
DEFAULT_EXACT_LIMIT = MAX_EXACT_STATES
_ATTEMPTS_PER_SAMPLE = 24


@dataclass(frozen=True)
class EquityResult:
    """Expected pot share, outright win/tie rates, and the evaluated deal count."""

    equity: float
    win_rate: float
    tie_rate: float
    trials: int
    exact: bool


@dataclass(frozen=True)
class _Distribution:
    combos: tuple[tuple[Card, Card], ...]
    weights: tuple[float, ...]
    log_weights: tuple[float, ...]
    cumulative: tuple[float, ...]
    total: float

    def draw(self, rng: random.Random) -> tuple[Card, Card]:
        index = bisect_right(self.cumulative, rng.random() * self.total)
        return self.combos[min(index, len(self.combos) - 1)]


def _key(card: Card) -> tuple[int, str]:
    return (card.value, card.suit)


def _normalize_range(
    source: ComboWeights | None, available: Sequence[Card]
) -> _Distribution:
    permitted = set(available)
    # Preserve relative scale in logarithms. Summing duplicate reversed
    # combos as floats could overflow (1e308 + 1e308), while tiny positive
    # weights must remain available for exact joint enumeration.
    merged_logs: dict[tuple[Card, Card], float] = {}
    if source is None:
        for combo in combinations(available, 2):
            merged_logs[combo] = 0.0
    else:
        for cards, weight in source.items():
            if len(cards) != 2 or not all(isinstance(card, Card) for card in cards):
                raise ValueError("range keys must be pairs of Card instances")
            first, second = cards
            if first == second:
                raise ValueError("range includes a repeated card")
            if (
                not isinstance(weight, (int, float))
                or weight < 0
                or (isinstance(weight, float) and not isfinite(weight))
            ):
                raise ValueError("range weights must be finite and nonnegative")
            if weight == 0 or first not in permitted or second not in permitted:
                continue
            combo = tuple(sorted((first, second), key=_key))
            current_log = log(weight)
            previous = merged_logs.get(combo)
            if previous is None:
                merged_logs[combo] = current_log
            else:
                # Stable log(exp(previous) + exp(current_log)).
                peak = max(previous, current_log)
                merged_logs[combo] = peak + log1p(exp(-abs(previous - current_log)))

    if not merged_logs:
        raise ValueError("opponent range has no possible unblocked combinations")
    ordered = sorted(
        merged_logs.items(), key=lambda pair: (_key(pair[0][0]), _key(pair[0][1]))
    )
    combos = tuple(combo for combo, _ in ordered)
    log_weights = tuple(log_weight for _, log_weight in ordered)
    # Scale the largest hand weight to one for bounded cumulative draws.
    peak = max(log_weights)
    weights = tuple(exp(value - peak) for value in log_weights)
    cumulative = []
    running = 0.0
    for weight in weights:
        running += weight
        cumulative.append(running)
    return _Distribution(combos, weights, log_weights, tuple(cumulative), running)


def _outcome(
    hero_rank: tuple[int, ...], other_ranks: Sequence[tuple[int, ...]]
) -> tuple[float, bool, bool]:
    best = max(other_ranks)
    if hero_rank > best:
        return 1.0, True, False
    if hero_rank < best:
        return 0.0, False, False
    return 1.0 / (1 + sum(rank == best for rank in other_ranks)), False, True


def _exact_river(
    hero: tuple[Card, Card],
    board: tuple[Card, ...],
    distributions: Sequence[_Distribution],
) -> EquityResult:
    hero_rank = evaluate(hero + board)
    weighted_equity = weighted_wins = weighted_ties = total = 0.0
    valid_deals = 0
    max_joint_log = None

    # Exact computation is permitted only when its input product has been
    # hard-capped to MAX_EXACT_STATES by estimate_equity.
    ranked = [
        tuple(
            (combo, log_weight, evaluate(combo + board))
            for combo, log_weight in zip(dist.combos, dist.log_weights)
        )
        for dist in distributions
    ]
    for deal in product(*ranked):
        cards = tuple(card for item in deal for card in item[0])
        if len(set(cards)) != len(cards):
            continue
        joint_log = sum(item[1] for item in deal)
        # Rescale weighted running totals to the largest valid log weight.
        # Thus collision-only low-weight deals (e.g. 1e-400) remain valid.
        if max_joint_log is None:
            max_joint_log = joint_log
            weight = 1.0
        elif joint_log > max_joint_log:
            scale = exp(max_joint_log - joint_log)
            weighted_equity *= scale
            weighted_wins *= scale
            weighted_ties *= scale
            total *= scale
            max_joint_log = joint_log
            weight = 1.0
        else:
            weight = exp(joint_log - max_joint_log)
        share, win, tie = _outcome(hero_rank, [item[2] for item in deal])
        total += weight
        weighted_equity += weight * share
        weighted_wins += weight * win
        weighted_ties += weight * tie
        valid_deals += 1

    if not valid_deals:
        raise ValueError("no possible collision-free opponent hands")
    return EquityResult(
        weighted_equity / total, weighted_wins / total,
        weighted_ties / total, valid_deals, True
    )


def estimate_equity(
    hero: Sequence[Card],
    board: Sequence[Card],
    opponents: int = 1,
    *,
    ranges: Sequence[ComboWeights | None] | None = None,
    samples: int = 120,
    seed: int = 0,
    exact_river: bool = True,
    exact_limit: int = DEFAULT_EXACT_LIMIT,
) -> EquityResult:
    """Estimate the hero's showdown pot share against plausible opponent hands.

    Uses weighted exact enumeration on a river if the product of opponent
    range sizes fits min(exact_limit, MAX_EXACT_STATES), so callers
    cannot bypass the fixed tournament-work ceiling.
    Otherwise samples up to samples collision-free showdown deals.

    With very restrictive or mutually incompatible multiway ranges, trials
    can be smaller than samples. If no deal is possible within the capped
    number of proposals, raises ValueError. Callers can use a broad-prior
    fallback, but that decision must be explicit rather than hidden here.
    """
    if not isinstance(opponents, int) or isinstance(opponents, bool) or opponents < 1:
        raise ValueError("opponents must be a positive integer")
    if (
        not isinstance(samples, int)
        or isinstance(samples, bool)
        or not 1 <= samples <= MAX_SAMPLES
    ):
        raise ValueError(f"samples must be between 1 and {MAX_SAMPLES}")
    if not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if not isinstance(exact_limit, int) or exact_limit < 0:
        raise ValueError("exact_limit must be nonnegative")
    if len(hero) != 2 or len(board) not in (0, 3, 4, 5):
        raise ValueError("hero must have two cards; board must have 0, 3, 4 or 5 cards")
    if not all(isinstance(card, Card) for card in (*hero, *board)):
        raise ValueError("hero and board must contain Card instances")
    known = tuple(hero) + tuple(board)
    if len(set(known)) != len(known):
        raise ValueError("hero and board contain duplicate cards")
    deck = tuple(card for card in full_deck() if card not in known)
    missing_board = 5 - len(board)
    if len(deck) < missing_board + opponents * 2:
        raise ValueError("not enough remaining cards for opponents and board")
    if ranges is not None and len(ranges) != opponents:
        raise ValueError("ranges must contain exactly one entry per opponent")

    distributions = tuple(
        _normalize_range(None if ranges is None else ranges[index], deck)
        for index in range(opponents)
    )
    if len(board) == 5 and exact_river and exact_limit > 0:
        states = prod(len(dist.combos) for dist in distributions)
        if states <= min(exact_limit, MAX_EXACT_STATES):
            return _exact_river(tuple(hero), tuple(board), distributions)

    rng = random.Random(seed)
    earned = wins = ties = 0.0
    accepted = 0
    # Reject the whole independently drawn deal, not just a later hand.
    # This keeps conditional multiway probabilities unbiased.
    for _ in range(samples * _ATTEMPTS_PER_SAMPLE):
        opponent_hands = [dist.draw(rng) for dist in distributions]
        blocked = set(known)
        for pair in opponent_hands:
            blocked.update(pair)
        if len(blocked) != len(known) + 2 * opponents:
            continue
        future = rng.sample(
            [card for card in deck if card not in blocked], missing_board
        )
        finished_board = tuple(board) + tuple(future)
        hero_rank = evaluate(tuple(hero) + finished_board)
        rival_ranks = [evaluate(hand + finished_board) for hand in opponent_hands]
        share, win, tie = _outcome(hero_rank, rival_ranks)
        earned += share
        wins += win
        ties += tie
        accepted += 1
        if accepted == samples:
            break

    if not accepted:
        raise ValueError("no valid collision-free deal sampled for these ranges")
    return EquityResult(
        earned / accepted, wins / accepted, ties / accepted, accepted, False
    )


def showdown_equity(*args, **kwargs) -> float:
    """Float-only helper for street policies."""
    return estimate_equity(*args, **kwargs).equity
