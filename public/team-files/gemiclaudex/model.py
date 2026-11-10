"""GemiClaudEx: the organisers' Model API in front of our combined bot (combo_bot.ComboBot).

The engine calls seed() / run() / end_game(). run() rebuilds the old arena's GameState (arena_sdk) from the
GameStateView plus the actions seen so far this hand, asks ComboBot, and turns its Response into a legal
PlayerAction. The inner bot is created on first use, so the pickle holds only plain data. Any error plays
the safe move (check, else fold).
"""
from card_utils import Card
from game_state import GameStateView
from player_action import PlayerAction

from arena_sdk import ActionRecord, GameState

DEALER = -1
STREETS = {3: "flop", 4: "turn", 5: "river"}


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None
        self.bot = None            # ComboBot, created after unpickling on the first decision
        self.errors = 0
        self._history = []         # old-arena ActionRecords for this hand
        self._bets = None          # chips each seat has put in on the current street
        self._street = "preflop"
        self._board = 0

    def seed(self, hand: list[Card], player_id: int):
        self.hand = hand
        self.player_id = player_id
        self._history = []
        self._bets = None
        self._street = "preflop"
        self._board = 0

    # ------------------------------------------------------------------ helpers

    def _start_hand(self, gs):
        button = gs.button
        bb = 1 - button
        sb_amt, bb_amt = gs.small_blind, gs.big_blind
        self._bets = {button: sb_amt, bb: bb_amt}
        self._history = [
            ActionRecord(f"p{button}", "post_small_blind", sb_amt, "preflop"),
            ActionRecord(f"p{bb}", "post_big_blind", bb_amt, "preflop"),
        ]

    def _ingest(self, actions):
        for a in actions:
            kind, pid = a.kind, a.player_id
            if kind == "reveal_card":
                if pid == DEALER:
                    self._board += 1
                    if self._board in STREETS:
                        self._street = STREETS[self._board]
                        self._bets = {0: 0, 1: 0}
                continue
            if kind not in ("fold", "call", "raise") or pid not in (0, 1):
                continue
            who = f"p{pid}"
            if kind == "fold":
                self._history.append(ActionRecord(who, "fold", None, self._street))
            elif kind == "call":
                owed = max(self._bets.values()) - self._bets[pid]
                if owed <= 0:
                    self._history.append(ActionRecord(who, "check", None, self._street))
                else:
                    self._bets[pid] += owed
                    self._history.append(ActionRecord(who, "call", owed, self._street))
            else:
                total = int(a.amount)
                name = "bet" if max(self._bets.values()) == 0 else "raise_to"
                self._bets[pid] = total
                self._history.append(ActionRecord(who, name, total, self._street))

    def _old_state(self, gs) -> GameState:
        me = self.player_id
        call = gs.call_amount
        can_raise = gs.can_raise
        legal = ["fold"]
        if call == 0:
            legal.append("check")
            if can_raise and gs.current_bet == 0:
                legal.append("bet")
        else:
            legal.append("call")
        if can_raise and gs.current_bet > 0:
            legal.append("raise_to")
        return GameState(
            hole_cards=tuple(str(c) for c in self.hand),
            community_cards=tuple(str(c) for c in gs.board),
            pot_size=gs.pot,
            stack_sizes=tuple(gs.stacks),
            current_bets=tuple(gs.bets),
            current_player_index=me,
            call_amount=call,
            minimum_raise=gs.last_raise,
            action_history=tuple(self._history),
            legal_actions=tuple(legal),
            my_index=me,
            street=gs.street,
            status="active",
            player_ids=tuple(f"p{i}" for i in range(gs.num_players)),
            folded=tuple(gs.folded),
            all_in=tuple(gs.status(i) == "all_in" for i in range(gs.num_players)),
            min_raise_to=gs.min_raise_to if can_raise else None,
            max_raise_to=gs.max_raise_to if can_raise else None,
            dealer_index=gs.button,
            small_blind=gs.small_blind,
            big_blind=gs.big_blind,
        )

    def _safe(self, gs) -> PlayerAction:
        if gs.call_amount == 0:
            return PlayerAction.call(self.player_id)
        return PlayerAction.fold(self.player_id)

    def _to_action(self, resp, gs) -> PlayerAction:
        me = self.player_id
        kind = resp.action_type
        if kind in ("raise", "all_in") and gs.can_raise:
            low, high = gs.min_raise_to, gs.max_raise_to
            total = high if kind == "all_in" or resp.amount is None else int(resp.amount)
            return PlayerAction.raise_to(me, max(low, min(total, high)))
        if kind == "fold" and gs.call_amount > 0:
            return PlayerAction.fold(me)
        return PlayerAction.call(me)       # call, check, or a raise we cannot make

    # ------------------------------------------------------------------ engine API

    def run(self, actions: list[PlayerAction], game_state: GameStateView) -> PlayerAction:
        gs = game_state
        try:
            if gs.num_players != 2:
                return self._safe(gs)
            if self.bot is None:
                from combo_bot import ComboBot
                self.bot = ComboBot()
            if self._bets is None:
                self._start_hand(gs)
            self._ingest(actions)
            resp = self.bot.act(self._old_state(gs))
            action = self._to_action(resp, gs)
            gs.validate_action(action)
            return action
        except Exception:
            self.errors += 1
            return self._safe(gs)

    def end_game(self, actions: list[PlayerAction], game_state: GameStateView):
        pass
