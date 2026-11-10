"""Adapter for the organisers' arena (#8). docs/ENVIRONMENT.md has the protocol notes.

Their engine (Manchester-AI-Society/poker_bot_infrastructure, `player_sdk`) calls `PokerBot.act(game_state)`
and expects a `Response`. This module turns their GameState into ours (docs/API.md), runs any `Bot`, and turns
our `Action` back into a `Response`. Differences it handles:

* Cards: theirs "AS" (upper-case suit), ours "As".
* Amounts: their `raise_to` / `bet` / `min_raise_to` / `max_raise_to` are totals for the current **street**;
  ours are totals for the **hand**. History: `call` records the chips added; `bet` and `raise_to` record the
  street total; the first two entries are `post_small_blind` / `post_big_blind`.
* No new-hand / hand-over callbacks: we detect a new hand ourselves, and synthesise the previous hand's
  `HandResult` from what we saw (opponent cards are never shown; when our last bet before the river got no
  further turn, the opponent folded, so we record that fold).
* Anything that goes wrong becomes a safe move (check, else fold), never an exception.

No imports from `player_sdk` here: the `Response` class is passed in, so this file is testable without it.
"""
from __future__ import annotations

from core_allin import opponent_start
from core_interface import Action, ActionRecord, GameState, HandResult, LegalActions, clamp_to_legal

STREETS = {"preflop": 1, "flop": 2, "turn": 3, "river": 4}
BLINDS = ("post_small_blind", "post_big_blind")


def card(code: str) -> str:
    return code[0].upper() + code[1].lower()


def _my_id(gs) -> str:
    ids = tuple(getattr(gs, "player_ids", ()) or ())
    me = gs.my_index
    if me is not None and me < len(ids) and ids[me]:
        return ids[me]
    return f"seat{me}"


def replay_history(gs) -> tuple[list[ActionRecord], dict[str, int], int]:
    """(our ActionRecords without blinds, chips each of us committed on streets BEFORE the current one,
    current street number)."""
    me = _my_id(gs)
    prior = {"me": 0, "opp": 0}
    street_bet = {"me": 0, "opp": 0}
    street = 1
    records: list[ActionRecord] = []
    for rec in gs.action_history:
        s = STREETS.get(getattr(rec, "street", None) or "preflop", street)
        if s != street:
            for who in prior:
                prior[who] += street_bet[who]
                street_bet[who] = 0
            street = s
        who = "me" if rec.player_id == me else "opp"
        act, amount = rec.action, int(rec.amount or 0)
        if act in BLINDS:
            street_bet[who] = amount
            continue
        if act == "fold":
            records.append(ActionRecord(s, who, "fold", None))
        elif act == "check":
            records.append(ActionRecord(s, who, "check", None))
        elif act == "call":
            street_bet[who] += amount
            records.append(ActionRecord(s, who, "call", None))
        elif act in ("bet", "raise_to", "all_in"):
            street_bet[who] = amount
            records.append(ActionRecord(s, who, "raise", prior[who] + amount))
    current = STREETS.get(gs.street or "", None) or {0: 1, 3: 2, 4: 3, 5: 4}.get(len(gs.community_cards), 1)
    if current != street:   # the street advanced after the last recorded action
        for who in prior:
            prior[who] += street_bet[who]
    return records, prior, current


def to_game_state(gs, hand_id: int) -> GameState:
    me = gs.my_index
    opp = 1 - me
    records, prior, street = replay_history(gs)
    my_bet, opp_bet = int(gs.current_bets[me]), int(gs.current_bets[opp])
    my_committed, opp_committed = prior["me"] + my_bet, prior["opp"] + opp_bet
    my_stack = int(gs.stack_sizes[me])
    call = int(gs.call_amount or 0)
    legal = tuple(gs.legal_actions or ())
    can_raise = ("bet" in legal or "raise_to" in legal) and gs.min_raise_to is not None \
        and gs.max_raise_to is not None
    return GameState(
        hand_id=hand_id,
        is_button=(gs.dealer_index == me),
        hole_cards=tuple(card(c) for c in gs.hole_cards),
        board=tuple(card(c) for c in gs.community_cards),
        street=street,
        small_blind=int(gs.small_blind or 10),
        big_blind=int(gs.big_blind or 20),
        starting_stack=my_stack + my_committed,
        opp_starting_stack=int(gs.stack_sizes[opp]) + opp_committed,
        my_committed=my_committed,
        opp_committed=opp_committed,
        history=tuple(records),
        legal=LegalActions(
            can_fold=call > 0,
            can_check=call == 0,
            call_amount=min(call, my_stack) if call > 0 else 0,
            min_raise_to=prior["me"] + int(gs.min_raise_to) if can_raise else None,
            max_raise_to=prior["me"] + int(gs.max_raise_to) if can_raise else None,
        ),
    )


def to_response(action: Action, gs, state: GameState, response_cls):
    """Our Action (hand totals) -> their Response (street totals), always within their legal range."""
    call = int(gs.call_amount or 0)
    if action.kind == "raise" and action.raise_to is not None and state.legal.min_raise_to is not None:
        prior_me = state.my_committed - int(gs.current_bets[gs.my_index])
        street_total = int(action.raise_to) - prior_me
        street_total = max(int(gs.min_raise_to), min(street_total, int(gs.max_raise_to)))
        return response_cls.raise_to(street_total)
    if action.kind == "fold" and call > 0:
        return response_cls.fold()
    return response_cls.call()            # call, or check when nothing is owed


def from_response(resp, gs, state: GameState) -> Action:
    """Their Response (street totals) -> our Action (hand totals), for decisions made by another module."""
    kind = getattr(resp, "action_type", None)
    call = int(gs.call_amount or 0)
    if kind == "fold":
        return Action("fold") if call > 0 else Action("check")
    if kind in ("raise", "all_in") and state.legal.max_raise_to is not None:
        if kind == "all_in" or getattr(resp, "amount", None) is None:
            return Action("raise", state.legal.max_raise_to)
        prior_me = state.my_committed - int(gs.current_bets[gs.my_index])
        return clamp_to_legal(Action("raise", prior_me + int(resp.amount)), state.legal)
    return Action("call") if call > 0 else Action("check")


def safe_response(gs, response_cls):
    return response_cls.call() if int(getattr(gs, "call_amount", 0) or 0) == 0 else response_cls.fold()


class ArenaAdapter:
    """Wraps one of our `Bot`s for the arena. `act(their_state)` -> their Response."""

    def __init__(self, bot, response_cls, opponent: str | None = None):
        self.bot = bot
        self.response_cls = response_cls
        self.hand_id = -1
        self._last_hole = None
        self._last_request = None
        self._last_len = 0
        self._last_state: GameState | None = None
        self._last_action: Action | None = None
        self.errors = 0
        try:
            bot.new_match("arena", opponent)
            partial = getattr(bot, "set_partial_observations", None)
            if partial is not None:
                partial()
        except Exception:
            self.errors += 1

    def act(self, gs):
        try:
            self._track_hand(gs)
            state = to_game_state(gs, self.hand_id)
            action = self.bot.act(state)
            self._last_state, self._last_action = state, action
            return to_response(action, gs, state, self.response_cls)
        except Exception:
            self.errors += 1
            return safe_response(gs, self.response_cls)

    def observe(self, gs, response) -> None:
        """Record a decision another module made (e.g. a separate preflop strategy), so hand tracking and
        the wrapped bot's opponent model still see the whole hand."""
        try:
            self._track_hand(gs)
            state = to_game_state(gs, self.hand_id)
            observer = getattr(self.bot, "observe_state", None)
            if observer is not None:
                observer(state)
            self._last_state, self._last_action = state, from_response(response, gs, state)
        except Exception:
            self.errors += 1

    # ------------------------------------------------------------- hand tracking

    def _track_hand(self, gs) -> None:
        # A combined terminal preflop decision may call act() then observe()
        # with the same request. Do not count that bookkeeping as another turn.
        if gs is self._last_request:
            return
        self._last_request = gs
        hole = tuple(gs.hole_cards)
        n = len(gs.action_history)
        # The SDK creates a fresh object per turn; within a hand the preceding
        # action must grow history. Equal length can be a new deal with the
        # same hole cards, even though no hand ID is exposed by the arena.
        if hole != self._last_hole or n <= self._last_len:
            self._finish_hand()
            self.hand_id += 1
        self._last_hole, self._last_len = hole, n

    def _finish_hand(self) -> None:
        """Report the previous hand to the bot, as far as we saw it."""
        state, action = self._last_state, self._last_action
        self._last_state = self._last_action = None
        if state is None:
            return
        history = list(state.history)
        if action is not None:
            kind = action.kind
            if kind == "fold" and not state.legal.can_fold:
                kind = "check"
            raise_to = action.raise_to if kind == "raise" else None
            history.append(ActionRecord(state.street, "me", kind, raise_to))
            all_in = kind == "raise" and state.legal.max_raise_to is not None and raise_to is not None \
                and raise_to >= min(state.legal.max_raise_to, opponent_start(state))
            if kind == "raise" and not all_in and state.street < 4:
                history.append(ActionRecord(state.street, "opp", "fold", None))   # no further turn: they folded
        try:
            self.bot.hand_over(HandResult(
                hand_id=state.hand_id, was_button=state.is_button, my_delta=0, history=tuple(history),
                board=state.board, opp_hole_cards=None))
        except Exception:
            self.errors += 1
