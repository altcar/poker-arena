"""The Bot interface and game types. docs/API.md is the source of truth for these."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

Street = Literal[1, 2, 3, 4]  # preflop, flop, turn, river


@dataclass(frozen=True)
class LegalActions:
    can_fold: bool
    can_check: bool
    call_amount: int  # chips to add to call; 0 if checking is possible
    min_raise_to: int | None  # None = raising not allowed
    max_raise_to: int | None  # all-in, as a raise_to value


@dataclass(frozen=True)
class ActionRecord:
    street: Street
    actor: Literal["me", "opp"]
    kind: Literal["fold", "check", "call", "raise"]
    raise_to: int | None  # same semantics as Action.raise_to


@dataclass(frozen=True)
class GameState:
    hand_id: int
    is_button: bool  # heads-up: button = small blind, acts first preflop, last postflop
    hole_cards: tuple[str, str]
    board: tuple[str, ...]  # 0, 3, 4 or 5 cards
    street: Street
    small_blind: int
    big_blind: int
    starting_stack: int  # per player, at the start of this hand
    my_committed: int  # total chips I've put in THIS HAND (all streets, incl. blind)
    opp_committed: int
    history: tuple[ActionRecord, ...]  # whole hand so far, blinds excluded
    legal: LegalActions
    time_left_ms: int | None = None  # if the environment exposes a clock
    opp_starting_stack: int | None = None  # None: legacy equal-stack assumption


@dataclass(frozen=True)
class Action:
    kind: Literal["fold", "check", "call", "raise"]
    raise_to: int | None = None  # for "raise": my TOTAL commitment for the hand after raising


@dataclass(frozen=True)
class HandResult:
    hand_id: int
    was_button: bool
    my_delta: int  # chips won (+) or lost (-)
    history: tuple[ActionRecord, ...]  # the complete hand, including actions after my last decision
    board: tuple[str, ...]
    opp_hole_cards: tuple[str, str] | None  # if shown at showdown


@dataclass(frozen=True)
class OpponentStats:  # None = not enough data yet; consumers must use defaults
    hands: int = 0
    vpip: float | None = None  # share of hands they voluntarily put chips in preflop
    pfr: float | None = None  # share of hands they raised preflop
    three_bet: float | None = None  # re-raise preflop when facing a raise
    fold_to_cbet: float | None = None  # fold when the preflop raiser bets the flop
    fold_to_bet: float | None = None  # fold when facing any postflop bet/raise
    aggression: float | None = None  # postflop (bets + raises) / calls
    wtsd: float | None = None  # share of seen flops that reach showdown
    avg_bet_frac: float | None = None  # average postflop bet size / pot


class Bot(Protocol):
    name: str

    def new_match(self, match_id: str, opponent: str | None) -> None: ...

    def act(self, state: GameState) -> Action: ...  # must return a legal Action, never raise

    def hand_over(self, result: HandResult) -> None: ...


def clamp_to_legal(action: Action, legal: LegalActions) -> Action:
    """Return the nearest legal action. If the action type itself is illegal: check > call > fold."""
    if action.kind == "raise" and legal.min_raise_to is not None:
        low = legal.min_raise_to
        high = low if legal.max_raise_to is None else legal.max_raise_to
        target = low if action.raise_to is None else action.raise_to
        return Action("raise", max(low, min(target, high)))
    if legal.can_check:
        return Action("check")  # includes a fold when checking is free
    if action.kind == "fold" and legal.can_fold:
        return Action("fold")
    if legal.call_amount > 0:
        return Action("call")
    return Action("fold")
