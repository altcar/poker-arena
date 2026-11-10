# DO NOT CHANGE THIS CODE

from typing import Annotated, Literal

from pydantic import (
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    validate_call,
)

from card_utils import Card
from player_action import DEALER, PlayerAction

Street = Literal["preflop", "flop", "turn", "river"]
Status = Literal["active", "folded", "all_in"]

# The street each board size opens: the flop is the 3rd card dealt, the
# turn the 4th, the river the 5th. The 1st and 2nd cards open nothing.
BOARD_SIZES: dict[int, Street] = {3: "flop", 4: "turn", 5: "river"}


class InvalidAction(ValueError):
    """The action is not legal in the current game state."""


# Checks a call's arguments against their type hints and raises
# ValidationError (a ValueError) if they don't match. strict: no coercion,
# so "5" and True are not chips.
_validated = validate_call(config=ConfigDict(strict=True))


class GameState:
    """
    The public state of the current hand: nothing private, such as a
    player's hole cards, lives here.

    Every agent (each player and the engine) holds its own copy, updated
    the same way from the actions it is shown. Players are numbered
    0..num_players-1, and every per-player list is indexed by player id.

    Fields:
        button       player id on the button
        small_blind  chips
        big_blind    chips
        stacks       chips behind, per player
        bets         chips bet this street, per player
        folded       per player
        acted        per player: acted this street since the last full raise
        board        the Cards dealt to the table so far
        street       "preflop", "flop", "turn" or "river"
        pot          every chip put in this hand, this street's bets too
        last_raise   size of the last full raise this street (the next
                     raise must be at least this much more)
        to_act       player id to act, None when the street is closed
        teams        team name, per player: the seat changes every hand,
                     the name does not
    """

    def __init__(
        self,
        stacks: list[int] = [500, 500],
        teams: list[str] | None = None,
    ):
        """stacks, teams: per player; player i has stacks[i]."""
        self.setup_hand(stacks, teams=teams)

    # -- setup ---------------------------------------------------------------

    @_validated
    def setup_hand(
        self,
        stacks: Annotated[list[NonNegativeInt], Field(min_length=2)],
        button: NonNegativeInt = 0,
        small_blind: PositiveInt = 10,
        big_blind: PositiveInt = 20,
        teams: list[Annotated[str, Field(min_length=1)]] | None = None,
    ):
        """
        Seats one player per stack and posts the blinds.

        teams: the team in each seat; defaults to "seat 0", "seat 1", ...
        """
        count = len(stacks)
        # The types check each argument; these check them against each other.
        if button >= count:
            raise ValueError(
                f"button must be a player id 0..{count - 1}, not {button}"
            )
        if small_blind > big_blind:
            raise ValueError(
                f"small_blind {small_blind} is bigger than big_blind "
                f"{big_blind}"
            )
        if teams is None:
            teams = [f"seat {p}" for p in range(count)]
        if len(teams) != count:
            raise ValueError(
                f"{len(teams)} teams for {count} stacks: give one per seat"
            )
        if len(set(teams)) != count:
            raise ValueError(f"team names must be unique, not {teams}")
        self.teams = list(teams)
        self.button = button
        self.small_blind = small_blind
        self.big_blind = big_blind
        self.stacks = list(stacks)
        self.bets = [0] * count
        self.folded = [False] * count
        self.acted = [False] * count
        self.board: list[Card] = []
        self.street: Street = "preflop"
        self.pot = 0
        self.last_raise = big_blind
        self.to_act: int | None = None

        # Heads-up the button posts the small blind.
        small = button if count == 2 else (button + 1) % count
        big = (small + 1) % count
        self._commit(small, min(small_blind, self.stacks[small]))
        self._commit(big, min(big_blind, self.stacks[big]))
        self._pass_turn(after=big)

    @_validated
    def set_stack(self, player_id: NonNegativeInt, chips: NonNegativeInt):
        """Chips behind for one player."""
        if player_id >= self.num_players:
            raise ValueError(f"no player {player_id}")
        self.stacks[player_id] = chips

    # -- queries: the table --------------------------------------------------

    @property
    def num_players(self) -> int:
        """Players at the table, folded ones included."""
        return len(self.stacks)

    @property
    def players_in_hand(self) -> list[int]:
        """Ids of players who have not folded (all-in players included)."""
        return [p for p in range(self.num_players) if not self.folded[p]]

    @property
    def active_players(self) -> list[int]:
        """Ids of players who can still bet: not folded and not all-in."""
        return [
            p for p in range(self.num_players) if self.status(p) == "active"
        ]

    @property
    def is_street_closed(self) -> bool:
        """True when nobody owes a decision on this street, so the dealer
        deals next (or the hand ends)."""
        return self.to_act is None

    @property
    def is_hand_over(self) -> bool:
        """True when everyone else has folded, or betting on the river is
        closed (the hand goes to showdown)."""
        return len(self.players_in_hand) == 1 or (
            self.street == "river" and self.is_street_closed
        )

    @property
    def current_bet(self) -> int:
        """The street total each player must reach to stay in."""
        return max(self.bets)

    # -- queries: any player -------------------------------------------------

    def status(self, player_id: int) -> Status:
        """"active", "folded" or "all_in"."""
        if self.folded[player_id]:
            return "folded"
        return "all_in" if self.stacks[player_id] == 0 else "active"

    def effective_stack(self, player_id: int) -> int:
        """The most this player can win or lose from here: their chips
        behind, capped by the biggest stack behind among the others still in
        the hand. Chips already bet are not counted."""
        others = [
            self.stacks[p] for p in self.players_in_hand if p != player_id
        ]
        return min(self.stacks[player_id], max(others, default=0))

    def spr(self, player_id: int) -> float:
        """Stack-to-pot ratio: effective_stack(player_id) / pot. Low values
        (below ~3) mean the player is close to committed to the pot."""
        return self.effective_stack(player_id) / self.pot

    # -- queries: the player to act ------------------------------------------

    @property
    def call_amount(self) -> int:
        """Chips you add by calling (0 = check). A stack smaller than the
        bet calls all-in for less."""
        me = self.to_act
        return min(self.current_bet - self.bets[me], self.stacks[me])

    @property
    def max_raise_to(self) -> int:
        """All-in: the largest street total you can reach."""
        return self.bets[self.to_act] + self.stacks[self.to_act]

    @property
    def min_raise_to(self) -> int:
        """The smallest legal raise (all-in when your stack is shorter)."""
        return min(self.current_bet + self.last_raise, self.max_raise_to)

    @property
    def can_raise(self) -> bool:
        """False after acting when only a short all-in has come since, when
        your stack cannot beat the bet, or when nobody is left to call."""
        others = [p for p in self.active_players if p != self.to_act]
        return (
            not self.acted[self.to_act]
            and self.max_raise_to > self.current_bet
            and len(others) > 0
        )

    @property
    def legal_actions(self) -> list[str]:
        """The kinds of PlayerAction the player to act may return: always
        "fold" and "call" (a call checks when nothing is owed), plus "raise"
        when can_raise. Empty when the street is closed."""
        if self.is_street_closed:
            return []
        return ["fold", "call"] + (["raise"] if self.can_raise else [])

    @property
    def raise_range(self) -> tuple[int, int] | None:
        """(min_raise_to, max_raise_to), the totals a raise_to may name, or
        None when raising is not allowed."""
        if not self.can_raise:
            return None
        return (self.min_raise_to, self.max_raise_to)

    @property
    def pot_odds(self) -> float:
        """The share of the final pot you pay by calling:
        call_amount / (pot + call_amount). Calling breaks even when your
        chance of winning equals this. 0.0 when calling is free."""
        if self.call_amount == 0:
            return 0.0
        return self.call_amount / (self.pot + self.call_amount)

    # -- validation ----------------------------------------------------------

    def validate_action(self, action: PlayerAction):
        """
        Raises InvalidAction, with the reason, if the move is illegal.

        Fold and call are always legal on your turn (a short stack calls
        all-in for less); a raise must be allowed and within range. Any
        other kind is not a betting move.
        """
        if action.player_id != self.to_act:
            raise InvalidAction(
                f"player {action.player_id} acted out of turn; "
                f"it is player {self.to_act}'s turn"
            )
        if action.kind in ("fold", "call"):
            return
        if action.kind != "raise":
            raise InvalidAction(
                f"{action.kind!r} is not a move: fold, call or raise"
            )
        if not self.can_raise:
            raise InvalidAction("you cannot raise now; fold or call")
        low, high = self.min_raise_to, self.max_raise_to
        if not low <= action.amount <= high:
            raise InvalidAction(
                f"raise_to({action.amount}) is outside the allowed "
                f"range: the total must be between {low} and {high}"
            )

    # -- updates -------------------------------------------------------------

    def process_action(self, action: PlayerAction):
        """
        Updates the state with one action.

        Betting actions are validated first and raise InvalidAction if
        illegal, so no copy, the engine's included, can apply a bad move.
        Once the hand is over, a player's reveal_card (a shown hole card)
        and win (chips paid from the pot) end it.
        """
        # The dealer's only action is reveal_card; a player's reveal_card
        # or win ends the hand; anything else is betting.
        if action.player_id == DEALER:
            self.deal_card(action.card)
            return
        if action.kind in ("reveal_card", "win"):
            self._settle(action)
            return
        self.validate_action(action)
        # Validated, so player_id is to_act and the to-act queries are theirs.
        player_id = action.player_id
        if action.kind == "fold":
            self.folded[player_id] = True
        elif action.kind == "call":
            self._commit(player_id, self.call_amount)
        elif action.kind == "raise":
            raise_size = action.amount - self.current_bet
            self._commit(player_id, action.amount - self.bets[player_id])
            if raise_size >= self.last_raise:  # only a full raise reopens
                self.last_raise = raise_size
                self.acted = [False] * self.num_players
        else:  # backstop: validate_action already rejects other kinds
            raise InvalidAction(f"{action.kind!r} is not a move")
        self.acted[player_id] = True
        self._pass_turn(after=player_id)

    @_validated
    def deal_card(self, card: Card):
        """Adds a card to the board; the 3rd, 4th and 5th open a street.

        Called by the dealer's reveal_card. Raises InvalidAction once the
        hand is over, while betting is still open, and for a card already
        on the board.
        """
        if self.is_hand_over:
            raise InvalidAction("the hand is over")
        if not self.is_street_closed:
            raise InvalidAction(
                f"betting is still open: it is player {self.to_act}'s turn"
            )
        if card in self.board:
            raise InvalidAction(f"{card} is already on the board")
        self.board.append(card)
        # Only the card that completes a street moves the hand on.
        if len(self.board) in BOARD_SIZES:
            self._next_street(BOARD_SIZES[len(self.board)])

    def _settle(self, action: PlayerAction):
        """A player's reveal_card or win: only process_action calls this.

        Legal once the hand is over, for a player who has not folded. A
        reveal_card changes nothing (hole cards never live here); a win
        adds its chips to the player's stack, and pot stays every chip put
        in this hand. The engine is trusted to pay out the pot exactly.
        """
        if not self.is_hand_over:
            raise InvalidAction(
                f"{action.kind!r} only comes once the hand is over"
            )
        player_id = action.player_id
        if not 0 <= player_id < self.num_players:
            raise InvalidAction(f"no player {player_id}")
        if self.folded[player_id]:
            raise InvalidAction(f"player {player_id} has folded")
        if action.kind == "win":
            self.stacks[player_id] += action.amount

    def _next_street(self, street: Street):
        """Opens a street: only deal_card calls this."""
        self.street = street
        # Bets, acted flags and the raise size are per street, so they reset.
        self.bets = [0] * self.num_players
        self.acted = [False] * self.num_players
        self.last_raise = self.big_blind
        # After the flop, the first active player after the button acts first.
        self._pass_turn(after=self.button)

    def _commit(self, player_id: int, chips: int):
        # Backstop: callers cap or validate, so this should never fire.
        assert chips <= self.stacks[player_id], "bet exceeds stack"
        self.stacks[player_id] -= chips
        self.bets[player_id] += chips
        self.pot += chips

    def _pass_turn(self, after: int):
        """Next player that owes a decision, or None when the street is
        closed (the engine then deals the next street or ends the hand)."""
        active = self.active_players
        waiting = [
            p
            for p in active
            if not self.acted[p] or self.bets[p] < self.current_bet
        ]
        lone_and_matched = (
            len(active) == 1 and self.bets[active[0]] >= self.current_bet
        )
        if len(self.players_in_hand) == 1 or lone_and_matched or not waiting:
            self.to_act = None
            return
        for step in range(1, self.num_players + 1):
            player_id = (after + step) % self.num_players
            if player_id in waiting:
                self.to_act = player_id
                return


class GameStateView:
    """
    A read-only window onto a GameState: what your model is given.

    Reading any public field or query (listed in PUBLIC) gives the state's
    current value; lists come back as tuples so they cannot be changed.
    Nothing can be set, and the update methods (process_action,
    setup_hand, set_stack, deal_card) are not available.
    """

    # The GameState fields and queries a model may read.
    PUBLIC = frozenset({
        # the table
        "button", "small_blind", "big_blind", "stacks", "bets", "folded",
        "acted", "board", "street", "pot", "last_raise", "to_act", "teams",
        "num_players", "players_in_hand", "active_players",
        "is_street_closed", "is_hand_over", "current_bet",
        # any player
        "status", "effective_stack", "spr",
        # the player to act
        "call_amount", "max_raise_to", "min_raise_to", "can_raise",
        "legal_actions", "raise_range", "pot_odds", "validate_action",
    })

    __slots__ = ("_state",)

    def __init__(self, state: GameState):
        object.__setattr__(self, "_state", state)

    def __getattr__(self, name):
        # Only runs for names not found on the view itself, i.e. everything
        # but _state: forward the public ones to the state.
        if name not in GameStateView.PUBLIC:
            raise AttributeError(f"GameStateView has no {name!r}")
        value = getattr(self._state, name)
        return tuple(value) if isinstance(value, list) else value

    def __setattr__(self, name, value):
        raise AttributeError("GameStateView is read-only")

    def __dir__(self):
        # Lets dir() and notebook autocompletion list the public names.
        return sorted(GameStateView.PUBLIC)
