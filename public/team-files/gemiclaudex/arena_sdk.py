"""The old arena SDK data types (Manchester-AI-Society/poker_bot_infrastructure player_sdk/models.py), kept so
the combined bot runs unchanged behind model.py. Plain dataclasses only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Tuple

ActionType = Literal["fold", "call", "raise", "all_in"]


@dataclass(frozen=True)
class ActionRecord:
    """Represents one historical action visible to the bot."""

    player_id: Optional[str]
    action: str
    amount: Optional[int] = None
    street: Optional[str] = None
    timestamp: Optional[float] = None


@dataclass(frozen=True)
class Response:
    """What your bot does this turn. ``act`` must return one of these.

    Build it with one of the four constructors:

    * ``Response.fold()``
    * ``Response.call()``: calls, or checks when nothing is owed
    * ``Response.raise_to(total)``: a bet or a raise; ``total`` is the TOTAL
      you want to have in the pot this betting round, between
      ``game_state.min_raise_to`` and ``game_state.max_raise_to``
    * ``Response.all_in()``: everything you have left

    The arena checks your response against the rules before it is played. An
    illegal one (an amount outside the allowed range, a raise when you cannot
    raise, anything that is not a ``Response``) is a fault: a safe default is
    played instead, and the reason is written to your bot's log.
    """

    action_type: ActionType
    amount: Optional[int] = None

    def __post_init__(self) -> None:
        if self.action_type == "raise":
            if (
                isinstance(self.amount, bool)
                or not isinstance(self.amount, int)
                or self.amount <= 0
            ):
                raise ValueError(
                    "Response.raise_to needs a whole number of chips above "
                    f"zero, not {self.amount!r}"
                )
            return
        if self.amount is not None:
            raise ValueError(
                "fold, call and all_in responses must not include an amount"
            )

    @classmethod
    def fold(cls) -> "Response":
        return cls("fold")

    @classmethod
    def call(cls) -> "Response":
        return cls("call")

    @classmethod
    def raise_to(cls, amount: int) -> "Response":
        return cls("raise", amount=amount)

    @classmethod
    def all_in(cls) -> "Response":
        return cls("all_in")

    def to_dict(self) -> Dict[str, Any]:
        if self.action_type == "raise":
            return {"action": "raise", "amount": self.amount}
        return {"action": self.action_type}


# The older name for the same type; existing bots keep working.
Action = Response


@dataclass(frozen=True)
class GameState:
    """Immutable game state snapshot used by a bot."""

    hole_cards: Tuple[str, ...]
    community_cards: Tuple[str, ...]
    pot_size: int
    stack_sizes: Tuple[int, ...]
    current_bets: Tuple[int, ...]
    current_player_index: int
    call_amount: int
    minimum_raise: int
    action_history: Tuple[ActionRecord, ...]
    turn_id: Optional[int] = None
    legal_actions: Tuple[str, ...] = ()
    my_index: Optional[int] = None
    street: Optional[str] = None
    status: Optional[str] = None
    player_ids: Tuple[Optional[str], ...] = ()
    folded: Tuple[bool, ...] = ()
    all_in: Tuple[bool, ...] = ()
    min_raise_to: Optional[int] = None
    max_raise_to: Optional[int] = None
    dealer_index: Optional[int] = None
    small_blind: Optional[int] = None
    big_blind: Optional[int] = None

    def is_bot_turn(self, bot_player_index: int) -> bool:
        return self.current_player_index == bot_player_index

    @property
    def is_my_turn(self) -> bool:
        """True when it is this bot's turn (requires my_index)."""
        return self.my_index is not None and self.is_bot_turn(self.my_index)

    @property
    def my_stack(self) -> Optional[int]:
        """This bot's current stack, when its seat is known."""
        return self._seat_value(self.stack_sizes)

    @property
    def my_bet(self) -> Optional[int]:
        """This bot's chips committed in the current betting round."""
        return self._seat_value(self.current_bets)

    @property
    def is_dealer(self) -> bool:
        """True when this bot holds the button (heads-up: small blind)."""
        return (
            self.my_index is not None
            and self.dealer_index is not None
            and self.my_index == self.dealer_index
        )

    @property
    def my_player_id(self) -> Optional[str]:
        """This bot's player id, when player ids and seat are known."""
        if (
            self.my_index is None
            or not self.player_ids
            or self.my_index >= len(self.player_ids)
        ):
            return None
        return self.player_ids[self.my_index]

    @property
    def to_call(self) -> int:
        """Alias for call_amount: chips needed to match the current bet."""
        return self.call_amount

    def can(self, action: str) -> bool:
        """True when the named engine action is currently legal."""
        return action in self.legal_actions

    @property
    def can_fold(self) -> bool:
        return self.can("fold")

    @property
    def can_check(self) -> bool:
        return self.can("check")

    @property
    def can_call(self) -> bool:
        return self.can("call")

    @property
    def can_bet(self) -> bool:
        return self.can("bet")

    @property
    def can_raise(self) -> bool:
        return self.can("raise_to")

    @property
    def is_preflop(self) -> bool:
        return self.street == "preflop" or (
            self.street is None and not self.community_cards
        )

    @property
    def is_flop(self) -> bool:
        return self.street == "flop" or (
            self.street is None and len(self.community_cards) == 3
        )

    @property
    def is_turn(self) -> bool:
        return self.street == "turn" or (
            self.street is None and len(self.community_cards) == 4
        )

    @property
    def is_river(self) -> bool:
        return self.street == "river" or (
            self.street is None and len(self.community_cards) == 5
        )

    @property
    def is_showdown(self) -> bool:
        return self.street == "showdown"

    @property
    def summary(self) -> str:
        """One-line human-readable result for the match state."""
        status = self.status or "unknown"
        seats = []
        for index, stack in enumerate(self.stack_sizes):
            player_id = (
                self.player_ids[index]
                if index < len(self.player_ids)
                else None
            )
            name = str(player_id) if player_id else f"seat {index}"
            seats.append((name, stack))
        if not seats:
            return f"status={status}"
        ranked = sorted(seats, key=lambda row: row[1], reverse=True)
        winner, winning_stack = ranked[0]
        stacks = ", ".join(f"{name}={stack}" for name, stack in ranked)
        return (
            f"status={status}, winner={winner} "
            f"({winning_stack} chips), stacks: {stacks}"
        )

    def _seat_value(self, values: Tuple[int, ...]) -> Optional[int]:
        if self.my_index is None or self.my_index >= len(values):
            return None
        return values[self.my_index]


class PokerBot:
    """Stand-in for the old arena's PokerBot base class (ComboBot subclasses it)."""

    def act(self, game_state: GameState) -> Response:
        raise NotImplementedError
