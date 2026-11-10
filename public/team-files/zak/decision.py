"""
Choosing a move.

Every candidate (fold, call or check, raises to a few sizes) is scored by
expected chips from here against the opponent model: its range from this
hand's moves, and how it answers each bet size. Preflop, while we have
little data on a team, we play the push/fold equilibrium instead, with a
probability that falls as the team's data grows (trust).

Survival: busting costs every chip we would have won in the hands left.
Each option's outcomes (chances of each change to our stack) are scored by

    F(s) = s + V * g(s),   g(s) = 1 - exp(-s / scale),

where s is our stack after the hand and V the chips we expect to win in
the rest of the event (see Model.future_value). A nearly busted stack is
worth almost as little as a busted one, since it cannot earn our edge.
"""

import math

import random
from dataclasses import dataclass, field

from cards import hand_class
from equity import equity_vector, range_equity
from hand_history import HandHistory
from opponents import FOLD, SMALL, OpponentModel, Situation
from player_action import PlayerAction
from policy_net import PolicyNet, context_features, option_features
from strength import StrengthCache
from tables import CALL, MAX_BB, PUSH


@dataclass
class Params:
    """Tunable constants."""

    raise_fracs: tuple[float, ...] = (0.5, 0.75, 1.0)  # of the pot after a call
    realisation: float = 0.9  # share of equity realised while cards are to come
    flop_runouts: int = 60
    # Survival (F above); V comes from Model.future_value.
    survival_scale_bb: float = 10.0  # g(s) reaches 63% at this stack
    edge_prior_bb: float = 0.25  # expected bb won per hand before data
    edge_prior_hands: float = 200.0  # weight of that prior, in hands
    future_cap_chips: float = 5000.0  # at most this much to lose by busting
    bleed_prior_hands: float = 10.0  # hands of "no chips lost by folding"


@dataclass
class Option:
    """A candidate move and its score (chips, relative to folding now)."""

    action: PlayerAction
    ev: float
    fold_p: float = 0.0  # raises: the chance the opponent folds
    # (chance, change to our stack from now) for each way the hand ends
    outcomes: list[tuple[float, float]] = field(default_factory=list)
    equity: float = 0.0  # against the range it plays against
    size: float = 0.0  # chips it adds, over the pot
    all_in: bool = False


def survival(outcomes: list[tuple[float, float]], stack: int, value: float,
             scale: float) -> float:
    """V * (E[g(stack + change)] - g(stack)): the future chips an option
    puts at risk (negative) or protects."""
    if value <= 0:
        return 0.0
    g = lambda s: 1.0 - math.exp(-max(0.0, s) / scale)
    now = g(stack)
    return value * sum(p * (g(stack + d) - now) for p, d in outcomes)


# -- push/fold equilibrium ----------------------------------------------------

def effective_bb(state, me: int) -> int:
    """Effective stack in big blinds, chips already bet included, as the
    push/fold tables count it: 1..MAX_BB."""
    other = 1 - me
    chips = min(state.stacks[me] + state.bets[me],
                state.stacks[other] + state.bets[other])
    return max(1, min(MAX_BB, round(chips / state.big_blind)))


def push_fold(state, me: int, hand: list[int], rng: random.Random) -> PlayerAction:
    """Heads-up push/fold equilibrium: shove or fold first in; otherwise
    continue (call a shove, re-shove anything smaller) with the calling
    range."""
    cls = hand_class(*hand)
    e = effective_bb(state, me)
    shove = PlayerAction.raise_to(me, state.max_raise_to) if state.can_raise else None
    opener = state.bets[me] < state.big_blind and state.current_bet == state.big_blind
    if opener:
        if shove and rng.random() < PUSH[e][cls]:
            return shove
        return PlayerAction.fold(me)
    strong = rng.random() < CALL[e][cls]
    if state.call_amount == 0:
        return shove if strong and shove else PlayerAction.call(me)
    if not strong:
        return PlayerAction.fold(me)
    return shove or PlayerAction.call(me)


# -- expected chips -----------------------------------------------------------

def raise_targets(state, params: Params) -> list[int]:
    """Street totals to consider raising to: pot fractions and all-in."""
    if not state.can_raise:
        return []
    low, high = state.raise_range
    after_call = state.pot + state.call_amount
    totals = {state.current_bet + round(f * after_call) for f in params.raise_fracs}
    totals.add(high)
    return sorted({min(high, max(low, t)) for t in totals})


def evaluate_options(
    state, me: int, history: HandHistory, opponents: OpponentModel,
    strengths: StrengthCache, params: Params, rng: random.Random,
) -> list[Option]:
    """
    Every candidate move with its expected chips. A raise wins the pot when
    the opponent folds, plays on against its calling range when it calls,
    and when it re-raises we assume it goes all-in and we then fold or call,
    whichever its raising range makes better.
    """
    opp = 1 - me
    team = history.teams[opp]
    board = history.board
    weights = opponents.query_hand_distribution(team, history, opp, strengths)
    eq = equity_vector(history.hand, board, rng, params.flop_runouts)
    to_come = len(board) < 5
    pot, owe, my_stack = state.pot, state.call_amount, state.stacks[me]
    my_bet, opp_bet = state.bets[me], state.bets[opp]
    # Chips from earlier streets, and the street total if both go all-in.
    dead = pot - sum(state.bets)
    all_in_total = min(my_bet + my_stack, opp_bet + state.stacks[opp])

    def realised(all_in: bool) -> float:
        return params.realisation if to_come and not all_in else 1.0

    opp_all_in = state.stacks[opp] == 0
    p_win = range_equity(eq, weights)
    options = [Option(PlayerAction.call(me),
                      realised(opp_all_in or owe >= my_stack)
                      * p_win * (pot + owe) - owe,
                      outcomes=[(p_win, pot), (1 - p_win, -owe)],
                      equity=p_win, size=owe / pot, all_in=owe >= my_stack)]
    if owe > 0:
        options.append(Option(PlayerAction.fold(me), 0.0, outcomes=[(1.0, 0)],
                              equity=p_win))

    buckets = strengths.buckets(board)
    opp_raised = any(r.player == opp and r.kind == "raise"
                     for r in history.records)
    for total in raise_targets(state, params):
        added = total - my_bet
        their_call = min(total - opp_bet, state.stacks[opp])
        pot_after = pot + added
        sit = Situation.of(state.street, their_call, pot_after, opp_raised)
        can_reraise = (state.stacks[opp] > their_call and my_stack > added)
        resp = opponents.respond(team, sit, weights, buckets, can_reraise)
        all_in = added >= my_stack or their_call >= state.stacks[opp]
        p_called = range_equity(eq, resp.calling)
        called = (realised(all_in) * p_called * (pot_after + their_call)
                  - added)
        outcomes = [(resp.fold, pot),
                    (resp.call * p_called, pot + their_call),
                    (resp.call * (1 - p_called), -added)]
        reraised = 0.0
        if resp.raise_ > 0:
            p_jam = range_equity(eq, resp.raising)
            jam_cost = all_in_total - my_bet
            jam_call = p_jam * (dead + 2 * all_in_total) - jam_cost
            reraised = max(-added, jam_call)
            if jam_call > -added:
                outcomes += [(resp.raise_ * p_jam, dead + 2 * all_in_total - jam_cost),
                             (resp.raise_ * (1 - p_jam), -jam_cost)]
            else:
                outcomes.append((resp.raise_, -added))
        ev = resp.fold * pot + resp.call * called + resp.raise_ * reraised
        options.append(Option(PlayerAction.raise_to(me, total), ev, resp.fold,
                              outcomes, equity=p_called, size=added / pot,
                              all_in=added >= my_stack))
    return options


def choose(
    state, me: int, history: HandHistory, opponents: OpponentModel,
    strengths: StrengthCache, params: Params, rng: random.Random,
    future_value: float = 0.0, policy: PolicyNet | None = None,
) -> PlayerAction:
    """
    The move to make: the option with the most expected chips plus
    survival value, plus the policy network's adjustment (in pots) when
    there is one. Preflop, while trust in the opponent model is low, the
    push/fold equilibrium's move instead, unless that scores worse than
    the best fold or check.
    """
    options = evaluate_options(state, me, history, opponents, strengths,
                               params, rng)
    scale = params.survival_scale_bb * state.big_blind
    stack = state.stacks[me]
    pot = max(1, state.pot)
    team = history.teams[1 - me]
    use_net = policy is not None and not policy.is_zero()
    if use_net:
        facing = Situation(state.street, "medium", False)
        style = opponents.style(team, facing)
        context = context_features(state, me, opponents.team_trust(team),
                                   style[FOLD], sum(style[SMALL:]) / 4)

    def score(o: Option) -> float:
        surv = survival(o.outcomes, stack, future_value, scale)
        total = o.ev + surv
        if use_net:
            total += pot * policy.forward(
                context + option_features(o, pot, surv))
        return total

    best = max(options, key=score)
    if state.street == "preflop" and rng.random() >= opponents.team_trust(team):
        nash = push_fold(state, me, history.hand, rng)
        match = [o for o in options if o.action == nash]
        passive = [o for o in options if o.action.kind == "fold"
                   or (o.action.kind == "call" and state.call_amount == 0)]
        if not match or not passive or score(match[0]) >= max(map(score, passive)):
            return nash
    return best.action
