# Built by tools/build_flat.py from bot/: shell_v2.clock, shell_v2.legal, shell_v2.replay, shell_v2.host. Edit the source, not this.

from __future__ import annotations

"""Think-time guard for the new engine: time.perf_counter only, no signals.

The engine gives every call 10 s and nothing accumulates; a call past the limit
kills the worker and we check or fold for the rest of the match. So the guard
aims far below it: the street table stops sampling at ``deadline``, and after a
slow move the next decisions use the small tables until the host has recovered.
"""


MOVE_BUDGET_S = 1.0  # what a decision may plan to use; normal moves take far less
HARD_STOP_S = 4.0  # sampling stops here; what is left of a decision takes ms
SLOW_MOVE_S = 2.0  # a move this long means the host is struggling
COOL_DOWN_MOVES = 25  # small tables for this many decisions after a slow move


class Clock:
    def __init__(self) -> None:
        self.cool_down = 0
        self.slowest = 0.0

    def budget(self) -> float:
        """Seconds the next decision may plan to use; 0.0 means small tables."""
        return 0.0 if self.cool_down else MOVE_BUDGET_S

    def charge(self, seconds: float) -> None:
        self.slowest = max(self.slowest, seconds)
        if seconds >= SLOW_MOVE_S:
            self.cool_down = COOL_DOWN_MOVES
        elif self.cool_down:
            self.cool_down -= 1

"""The safe action layer: whatever comes in, a legal PlayerAction goes out.

``to_action`` turns a Decision into a move. ``ensure_legal`` is the last gate
before the engine, and ``safe_action`` is the fallback for when anything at all
has gone wrong. The last two never raise.
"""


from player_action import PlayerAction

from core import ALL_IN, FOLD, PASSIVE, RAISE, Decision, View

CHEAP_PRICE = 0.25  # a call for at most this share of the pot beats folding


def to_action(decision: Decision, state, view: View, me: int) -> PlayerAction:
    """Maps a Decision to a legal move. Raises only on a malformed one."""
    kind = decision.kind
    if kind == FOLD:
        if state.call_amount == 0:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)
    if kind == PASSIVE:
        return PlayerAction.call(me)
    if kind != ALL_IN and kind != RAISE:
        raise ValueError(f"not a decision kind: {kind!r}")
    limits = state.raise_range
    if limits is None:
        return PlayerAction.call(me)
    low, high = limits
    total = view.max_raise_to if kind == ALL_IN else int(decision.total)
    return PlayerAction.raise_to(me, max(low, min(total, high)))


def safe_action(state, me: int) -> PlayerAction:
    """Check; call when it is cheap; otherwise fold. Never raises."""
    try:
        owed = state.call_amount
        if owed == 0 or owed <= CHEAP_PRICE * (state.pot + owed):
            return PlayerAction.call(me)
    except BaseException:
        pass
    return PlayerAction.fold(me)


def ensure_legal(action: object, state, me: int) -> PlayerAction:
    """Returns ``action`` when it is legal here, else the safe move."""
    try:
        if isinstance(action, PlayerAction) and action.player_id == me:
            state.validate_action(action)
            return action
    except BaseException:
        pass
    return safe_action(state, me)

"""Turns the engine's actions and GameStateView into the plain View of a strategy.

The engine does not log blinds and has one ``call`` for call and check, so
``HandLog`` replays the hand into the same Events the strategies always read:
post_small_blind, post_big_blind, fold, check, call (chips added), bet and
raise_to (street total).
"""


import random

from core import card
from player_action import DEALER

from core import (
    I_FOLDED,
    OPP_FOLDED,
    PASSIVE,
    RAISE,
    SHOWDOWN,
    Decision,
    Event,
    HandSummary,
    View,
    fold,
    passive,
    raise_to,
)

STREETS = {"preflop": 0, "flop": 1, "turn": 2, "river": 3}
STREET_OPENING = (3, 4, 5)  # board sizes that open a street


def hole_codes(hand) -> tuple[int, int]:
    first, second = hand
    return card(str(first)), card(str(second))


def blind_seats(button: int, players: int) -> tuple[int, int]:
    """Heads-up the button posts the small blind."""
    small = button if players == 2 else (button + 1) % players
    return small, (small + 1) % players


class HandLog:
    """This hand's Events so far, from my seat."""

    def __init__(self, me: int) -> None:
        self.me = me
        self.events: list[Event] = []
        self.started = False
        self.street = 0
        self.board = 0
        self.bets: dict[int, int] = {}  # this street
        self.left: dict[int, int] = {}  # chips behind
        self.opp_folded = False

    def _start(self, state) -> None:
        """My first turn is always preflop, so stack + bet is the starting stack."""
        self.started = True
        players = state.num_players
        self.left = {p: state.stacks[p] + state.bets[p] for p in range(players)}
        self.bets = dict.fromkeys(range(players), 0)
        small, big = blind_seats(state.button, players)
        self._blind(small, state.small_blind, "post_small_blind")
        self._blind(big, state.big_blind, "post_big_blind")

    def _blind(self, player: int, blind: int, action: str) -> None:
        posted = min(blind, self.left[player])
        self.left[player] -= posted
        self.bets[player] = posted
        self.events.append(Event(player == self.me, action, posted, 0))

    def _apply(self, action) -> None:
        player, kind = action.player_id, action.kind
        if kind == "reveal_card":
            if player == DEALER:
                self.board += 1
                if self.board in STREET_OPENING:
                    self.street += 1
                    self.bets = dict.fromkeys(self.bets, 0)
            return
        if kind == "win":
            return
        mine = player == self.me
        if kind == "fold":
            self.opp_folded = self.opp_folded or not mine
            self.events.append(Event(mine, "fold", None, self.street))
            return
        current = max(self.bets.values())
        if kind == "call":
            added = min(current - self.bets[player], self.left[player])
            if added <= 0:
                self.events.append(Event(mine, "check", None, self.street))
                return
            self.events.append(Event(mine, "call", added, self.street))
        else:
            added = action.amount - self.bets[player]
            name = "raise_to" if current else "bet"
            self.events.append(Event(mine, name, action.amount, self.street))
        self.bets[player] += added
        self.left[player] -= added

    def feed(self, actions, state) -> tuple[Event, ...]:
        """Takes the actions since my last turn; returns the hand so far."""
        if not self.started:
            self._start(state)
        for action in actions:
            self._apply(action)
        return tuple(self.events)

    def finish(self, actions) -> None:
        """The end of a hand I had a turn in: only who folded still matters."""
        if self.started:
            for action in actions:
                self._apply(action)


def build_view(
    state,
    me: int,
    hole: tuple[int, int],
    history: tuple[Event, ...],
    hand_index: int,
    new_hand: bool,
    budget_s: float,
    deadline: float,
    rng: random.Random,
) -> View:
    """Builds the View. Raises on a malformed state; the shell catches that."""
    stacks, bets = state.stacks, state.bets
    others = [p for p in state.players_in_hand if p != me]
    opp_stack = max(stacks[p] for p in others)
    opp_bet = max(bets[p] for p in others)
    limits = state.raise_range
    low, high = limits if limits is not None else (0, 0)
    if limits is not None:
        # Chips past what they can match come straight back: stacks need not be equal.
        high = min(high, max(low, opp_bet + opp_stack))
    is_button = me == state.button
    return View(
        hole=hole,
        board=tuple(card(str(code)) for code in state.board),
        street=STREETS[state.street],
        seat=0 if is_button else 1,
        is_button=is_button,
        pot=state.pot,
        to_call=state.call_amount,
        my_stack=stacks[me],
        opp_stack=opp_stack,
        my_bet=bets[me],
        opp_bet=opp_bet,
        min_raise_to=low,
        max_raise_to=high,
        can_raise=limits is not None,
        can_check=state.call_amount == 0,
        small_blind=state.small_blind,
        big_blind=state.big_blind,
        history=history,
        hand_index=hand_index,
        new_hand=new_hand,
        budget_s=budget_s,
        deadline=deadline,
        rng=rng,
    )


def played_decision(action) -> Decision:
    """The Decision that matches what was really played."""
    if action.kind == "raise":
        return raise_to(action.amount)
    return passive() if action.kind == "call" else fold()


def summary_of(view: View, played: Decision, log: HandLog) -> HandSummary:
    """A finished hand. Unlike the old engine, how it ended is always known."""
    all_in = played.kind == RAISE and played.total >= view.max_raise_to
    all_in = all_in or (played.kind == PASSIVE and view.to_call >= view.my_stack)
    if played.kind == RAISE or played.kind == PASSIVE:
        ending = OPP_FOLDED if log.opp_folded else SHOWDOWN
    else:
        ending = I_FOLDED
    return HandSummary(
        hand_index=view.hand_index,
        seat=view.seat,
        hole=view.hole,
        board=view.board,
        history=view.history,
        my_last=played,
        my_last_street=view.street,
        my_last_all_in=all_in,
        pot_at_last=view.pot,
        ending=ending,
    )

"""Model: the submission entry point on the new engine. Cannot fault.

Built flat into submit/model.py by tools/build_flat.py. The pickle holds only
this class and a few numbers: the strategy is built at the first ``seed``, in the
worker, so the code is the only thing that has to match the organisers' Python.

Order of defence in ``run``: strategy code runs inside a catch-all; a Decision
is mapped to a move by clamping; the move passes ``ensure_legal``, which asks
the engine's own ``validate_action``; ``safe_action`` backs all of it up.
"""


import random
from time import perf_counter

from policy import PolicyV2
from core import Stack



def build_strategy():
    return Stack([PolicyV2()])


class Model:
    def __init__(self) -> None:
        self.hand = []
        self.player_id = None
        self.hand_index = 0
        self.errors = 0
        self.strategy = None  # built at the first seed, in the worker

    def _start(self) -> None:
        self.rng = random.Random(0)  # reseeded before every decision
        self.salt = None  # set by the first decision of the rotation
        self.clock = Clock()
        self.strategy = build_strategy()
        self.strategy.on_half_start(self.rng)

    def seed(self, hand, player_id) -> None:
        self.hand = hand
        self.player_id = player_id
        self.log = None
        self.hole = None
        self.last_view = None
        self.last_played = None
        try:
            if self.strategy is None:
                self._start()
            self.hand_index += 1
            self.hole = hole_codes(hand)
            self.log = HandLog(player_id)
        except BaseException:
            self.errors += 1

    def _seed_rng(self, state, history) -> None:
        """The same line under the same salt always draws the same numbers, so a
        bot and its copy cancel on paired deals. Our cards are in the line, so
        an opponent cannot predict a draw. Random.seed hashes a string itself."""
        line = repr((
            self.hand_index, self.hole, tuple(str(c) for c in state.board),
            self.player_id, state.pot, state.call_amount, state.stacks, history,
        ))  # fmt: skip
        if self.salt is None:
            self.salt = line
        self.rng.seed(self.salt + line)

    def _think(self, actions, state, entered: float):
        new_hand = not self.log.started
        history = self.log.feed(actions, state)
        self._seed_rng(state, history)
        view = build_view(
            state,
            self.player_id,
            self.hole,
            history,
            self.hand_index,
            new_hand,
            self.clock.budget(),
            entered + HARD_STOP_S,
            self.rng,
        )
        self.last_view = view
        self.strategy.observe(view)
        decision = self.strategy.decide(view)
        if decision is None:
            return safe_action(state, self.player_id)
        return to_action(decision, state, view, self.player_id)

    def run(self, actions, game_state):
        entered = perf_counter()
        action = None
        try:
            action = self._think(actions, game_state, entered)
        except BaseException:
            self.errors += 1
        action = ensure_legal(action, game_state, self.player_id)
        try:
            self.last_played = played_decision(action)
            self.clock.charge(perf_counter() - entered)
        except BaseException:
            self.errors += 1
        return action

    def _teach(self, actions, state) -> None:
        """The opponent model; a failure here must not cost the other hooks."""
        try:
            self.strategy.layers[0].on_end_game(
                self.log.events, actions, state, self.player_id
            )
        except BaseException:
            self.errors += 1

    def end_game(self, actions, game_state) -> None:
        """Hands I had no turn in stay invisible to the strategy, as before."""
        try:
            if self.last_view is not None and self.last_played is not None:
                self.log.finish(actions)
                self._teach(actions, game_state)
                self.strategy.on_hand_end(
                    summary_of(self.last_view, self.last_played, self.log)
                )
        except BaseException:
            self.errors += 1
