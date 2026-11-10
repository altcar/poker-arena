from dataclasses import dataclass

from card_utils import Card, evaluate
from game_interface import GameInterface
from game_state import GameState, GameStateView, InvalidAction
from player_action import PlayerAction


@dataclass
class HandResult:
    """What happened in one hand. Every per-seat list is indexed by seat."""

    net: list[int]  # chips won (+) or lost (-) per seat; sums to 0
    actions: list[PlayerAction]  # every action, dealer reveals included
    hands: list[list[Card]]  # hole cards per seat
    board: list[Card]  # the board cards revealed
    faults: dict[int, str]  # seat -> reason for its first fault


def play_hand(
    models: list,
    deck: list[Card],
    stacks: list[int],
    button: int = 0,
    small_blind: int = 10,
    big_blind: int = 20,
    teams: list[str] | None = None,
) -> HandResult:
    """
    Plays one hand: models[i] sits in seat i with stacks[i] chips.

    Cards come off the front of deck: seat i gets deck[2i] and deck[2i+1],
    then the board is dealt in order. The deck is not changed. A model whose
    seed or run raises, or whose move is illegal, has that move replaced by
    a check (nothing owed) or a fold, and the reason goes in faults.

    Once the hand is over, the seats left at a showdown show their hole
    cards and each seat paid from the pot gets one win, all applied like
    any other action. Then every seat whose seed did not raise is told,
    with end_game; a raise there is also a fault.

    A seat with no chips is still dealt in, so do not seat busted players.
    """
    count = len(models)
    # Every input is checked before any model sees the hand.
    if len(stacks) != count:
        raise ValueError(f"{count} models for {len(stacks)} stacks")
    if len(deck) < 2 * count + 5:
        raise ValueError(
            f"a deck of {len(deck)} cards is too short for {count} seats"
        )
    if len(set(deck)) != len(deck):
        raise ValueError("the deck has a duplicate card")
    state = GameState(stacks)
    state.setup_hand(stacks, button, small_blind, big_blind, teams)

    hands = [list(deck[2 * p:2 * p + 2]) for p in range(count)]
    board_cards = iter(deck[2 * count:2 * count + 5])
    faults: dict[int, str] = {}
    # None for a seat whose seed raised: it is never asked again.
    seats: list[GameInterface | None] = []
    for p in range(count):
        try:
            seats.append(
                GameInterface(models[p], GameStateView(state), p, hands[p])
            )
        except Exception as error:
            faults[p] = _describe("seed", error)
            seats.append(None)

    actions: list[PlayerAction] = []
    unseen: list[list[PlayerAction]] = [[] for _ in range(count)]
    while not state.is_hand_over:
        if state.is_street_closed:
            action = PlayerAction.reveal_card(next(board_cards))
        else:
            p = state.to_act
            pending, unseen[p] = unseen[p], []
            action = _ask(seats[p], pending, state, faults, p)
        state.process_action(action)
        actions.append(action)
        for pending in unseen:
            pending.append(action)

    contributed = [stacks[p] - state.stacks[p] for p in range(count)]
    won = payouts(contributed, state.folded, hands, state.board, button)
    # Seat order from the button's left, as for odd chips. Nobody shows
    # cards when everyone else folded.
    order = [(button + step) % count for step in range(1, count + 1)]
    shown = [p for p in order if not state.folded[p]]
    if len(shown) == 1:
        shown = []
    endings = [
        PlayerAction.reveal_card(card, p) for p in shown for card in hands[p]
    ]
    endings += [PlayerAction.win(p, won[p]) for p in order if won[p] > 0]
    for action in endings:
        state.process_action(action)
        actions.append(action)
        for pending in unseen:
            pending.append(action)
    for p in range(count):
        pending, unseen[p] = unseen[p], []
        if seats[p] is None:
            continue
        try:
            seats[p].end_game(pending)
        except Exception as error:
            if p not in faults:
                faults[p] = _describe("end_game", error)
    return HandResult(
        net=[won[p] - contributed[p] for p in range(count)],
        actions=actions,
        hands=hands,
        board=list(state.board),
        faults=faults,
    )


def _ask(seat, pending, state, faults, player_id) -> PlayerAction:
    """The seat's move, or a check or fold in its place if it faults."""
    if seat is not None:
        try:
            action = seat.process_action(pending)
            # Rebuild from plain fields, so a forged or subclassed action
            # cannot change under the engine once validated.
            if type(action) is not PlayerAction:
                raise InvalidAction("run() must return a PlayerAction")
            action = PlayerAction(action.player_id, action.kind, action.amount)
            state.validate_action(action)
            return action
        except Exception as error:
            if player_id not in faults:
                faults[player_id] = _describe("run", error)
    if state.call_amount == 0:
        return PlayerAction.call(player_id)
    return PlayerAction.fold(player_id)


def _describe(where: str, error: Exception) -> str:
    """Never raises, even for an exception whose str() does."""
    try:
        message = str(error)
    except Exception:
        message = "(message could not be shown)"
    return f"{where}: {type(error).__name__}: {message}"


def payouts(
    contributed: list[int],
    folded: list[bool],
    hands: list[list[Card]],
    board: list[Card],
    button: int,
) -> list[int]:
    """
    Chips each seat receives from the pot, given what each put in.

    Side pots are split by contribution level among the seats still in;
    ties split a pot evenly, and odd chips go one each to the tied seats
    nearest the button's left.
    """
    count = len(contributed)
    won = [0] * count
    live = [p for p in range(count) if not folded[p]]
    if len(live) == 1:
        won[live[0]] = sum(contributed)
        return won
    ranks = {p: evaluate(list(hands[p]) + list(board)) for p in live}
    # Seat order for odd chips: the button's left first.
    order = [(button + step) % count for step in range(1, count + 1)]
    previous = 0
    for level in sorted(set(contributed)):
        # Each seat's chips between the previous level and this one.
        pot = sum(min(c, level) - min(c, previous) for c in contributed)
        previous = level
        eligible = [p for p in live if contributed[p] >= level]
        best = max(ranks[p] for p in eligible)
        winners = [p for p in order if p in eligible and ranks[p] == best]
        share, odd = divmod(pot, len(winners))
        for i, p in enumerate(winners):
            won[p] += share + (1 if i < odd else 0)
    return won
