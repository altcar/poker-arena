"""
The whole hand so far, rebuilt from the actions run() and end_game() are
given (each call only gets the actions since our last turn).
"""

from dataclasses import dataclass, field

from cards import card_id
from player_action import DEALER

STREETS = {3: "flop", 4: "turn", 5: "river"}
_BIG = 10 ** 9  # stands in for unknown stacks while working them out


@dataclass
class ActionRecord:
    """One betting move and the situation it was made in."""

    player: int
    street: str
    kind: str  # "fold", "check", "call" or "raise"
    amount: int  # raise: street total; call: chips added; else 0
    facing: int  # chips the player owed before acting
    pot_before: int
    stack_before: int  # chips behind before acting
    all_in: bool  # this move put the player all-in
    aggressor: bool  # the player raised earlier this hand
    board_len: int  # 0, 3, 4 or 5
    can_raise: bool  # raising was allowed (approximately: chips beyond the
    # call, and someone else still able to bet)
    raise_by: int = 0  # raise: chips above the bet faced

    @property
    def size_frac(self) -> float:
        """A raise's size beyond the call, as a fraction of the pot after
        calling (a pot-sized bet is 1.0); 0 for other moves."""
        if self.kind != "raise":
            return 0.0
        return self.raise_by / max(1, self.pot_before + self.facing)


@dataclass
class HandHistory:
    """
    Blinds are not actions, so they are posted here from the button and
    blind sizes. Starting stacks are worked out from the state the first
    time we see the hand.
    """

    me: int = -1
    hand: list[int] = field(default_factory=list)
    teams: tuple[str, ...] = ()
    button: int = 0
    small_blind: int = 0
    big_blind: int = 0
    start_stacks: list[int] = field(default_factory=list)
    records: list[ActionRecord] = field(default_factory=list)
    board: list[int] = field(default_factory=list)
    shown: dict[int, list[int]] = field(default_factory=dict)
    won: dict[int, int] = field(default_factory=dict)
    finished: bool = False
    _bets: list[int] = field(default_factory=list)
    _stacks: list[int] = field(default_factory=list)
    _pot: int = 0
    _street: str = "preflop"
    _raisers: set[int] = field(default_factory=set)
    _folded: set[int] = field(default_factory=set)

    def start(self, hand: list[int], player_id: int) -> None:
        """A new hand: our cards (ids) and seat."""
        self.__init__(me=player_id, hand=list(hand))

    def observe(self, actions, state) -> None:
        """Adds the actions since our last turn."""
        if not self.teams:
            self._begin(actions, state)
        for action in actions:
            self._apply(action)

    def finish(self, actions, state) -> None:
        """Adds the hand's last actions: shown cards and wins included."""
        self.observe(actions, state)
        self.finished = True

    def opponent(self) -> int:
        """Heads-up: the other seat."""
        return 1 - self.me

    def actions_of(self, player: int) -> list[ActionRecord]:
        return [r for r in self.records if r.player == player]

    # -- replay -------------------------------------------------------------

    def _begin(self, actions, state) -> None:
        # Replay this first batch with huge stacks to find what each player
        # has put in (and won) so far, hence the stacks they started with.
        scratch = HandHistory(me=self.me)
        scratch._setup(state, [_BIG] * state.num_players)
        for action in actions:
            scratch._apply(action)
        start = [state.stacks[p] - (scratch._stacks[p] - _BIG)
                 for p in range(state.num_players)]
        # An all-in for less (a blind or a call): the scratch replay put it
        # in full, and the state's pot (every chip put in) shows by how
        # much. It is the shortest player who has nothing left behind.
        over = scratch._pot - state.pot
        if over > 0:
            all_in = [p for p in range(state.num_players)
                      if state.stacks[p] - scratch.won.get(p, 0) == 0]
            if all_in:
                start[min(all_in, key=lambda p: start[p])] -= over
        self._setup(state, start)

    def _setup(self, state, stacks: list[int]) -> None:
        count = state.num_players
        self.teams = tuple(state.teams)
        self.button = state.button
        self.small_blind = state.small_blind
        self.big_blind = state.big_blind
        self.start_stacks = list(stacks)
        self._stacks = list(stacks)
        self._bets = [0] * count
        small = self.button if count == 2 else (self.button + 1) % count
        big = (small + 1) % count
        self._commit(small, state.small_blind)
        self._commit(big, state.big_blind)

    def _apply(self, action) -> None:
        p, kind = action.player_id, action.kind
        if kind == "reveal_card":
            if p == DEALER:
                self.board.append(card_id(action.card))
                if len(self.board) in STREETS:
                    self._street = STREETS[len(self.board)]
                    self._bets = [0] * len(self._bets)
            else:
                self.shown.setdefault(p, []).append(card_id(action.card))
            return
        if kind == "win":
            self.won[p] = action.amount
            self._stacks[p] += action.amount
            return
        top = max(self._bets)
        facing = min(top - self._bets[p], self._stacks[p])
        others_can_bet = any(
            self._stacks[q] > 0 for q in range(len(self._stacks))
            if q != p and q not in self._folded)
        record = ActionRecord(
            player=p, street=self._street, kind=kind, amount=0,
            facing=facing, pot_before=self._pot,
            stack_before=self._stacks[p], all_in=False,
            aggressor=p in self._raisers, board_len=len(self.board),
            can_raise=others_can_bet and self._stacks[p] > facing,
        )
        if kind == "fold":
            self._folded.add(p)
        elif kind == "call":
            record.kind = "call" if facing > 0 else "check"
            record.amount = facing
            self._commit(p, facing)
        else:
            record.amount = action.amount
            record.raise_by = action.amount - top
            self._commit(p, action.amount - self._bets[p])
            self._raisers.add(p)
        record.all_in = kind != "fold" and self._stacks[p] == 0
        self.records.append(record)

    def _commit(self, p: int, chips: int) -> None:
        chips = min(chips, self._stacks[p])
        self._stacks[p] -= chips
        self._bets[p] += chips
        self._pot += chips
