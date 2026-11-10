"""Combined prototype: lembzg's table-driven preflop (#32) + EulaBet's MainBot postflop and opponent model.
Build with `python -m adapters.arena.build --team NAME --strategy combo --preflop-dir PATH`.
PATH is the bots/myteam folder from #32. See docs/ENVIRONMENT.md for validate/pack commands."""
from __future__ import annotations

import random

from team_opponent import OpponentModel
from arena_sdk import PokerBot
from arena_sdk import Response
from team_preflop import preflop_action

from arena_translate import ArenaAdapter
from main_bot import MainBot


class ComboBot(PokerBot):
    def __init__(self) -> None:
        self.rng = random.Random(None)
        self.model = OpponentModel()            # lembzg's preflop model
        self._post = ArenaAdapter(MainBot(), Response)   # our postflop + opponent model
        self._started = False

    def act(self, gs):
        try:
            if not self._started:
                self.rng.seed()
                self._started = True
            self.model.observe(gs)
            terminal = gs.call_amount > 0 and (
                gs.call_amount >= gs.stack_sizes[gs.my_index]
                or gs.stack_sizes[1 - gs.my_index] == 0)
            if gs.is_preflop and not terminal:
                resp = self._legal(preflop_action(gs, self.model, self.rng), gs)
            else:
                resp = self._post.act(gs)
        except Exception as exc:  # noqa: BLE001
            print(f"act() failed, playing safe: {exc!r}")
            resp = Response.call() if gs.call_amount == 0 else Response.fold()
        if gs.is_preflop:
            # Record the actual returned action, including a strategy failure's safe response.
            self._post.observe(gs, resp)
        try:
            kind = "raise" if resp.action_type in ("raise", "all_in") else resp.action_type
            # A covering raise also ends betting if the shorter opponent calls.
            # The missing next turn cannot distinguish that call from a fold.
            effective_total = min(stack + bet for stack, bet in zip(gs.stack_sizes, gs.current_bets))
            terminal_raise = kind == "raise" and (
                resp.action_type == "all_in"
                or (resp.amount is not None and resp.amount >= effective_total))
            self.model.record_own(kind, terminal_raise, bool(gs.is_preflop))
        except Exception as exc:  # bookkeeping must not replace an already tracked action
            print(f"record_own() failed: {exc!r}")
        return resp

    @staticmethod
    def _legal(resp, gs):
        kind = resp.action_type
        if kind in ("raise", "all_in"):
            if not (gs.can_raise or gs.can_bet):
                return Response.call()
            if kind == "raise":
                low, high = gs.min_raise_to, gs.max_raise_to
                if low is None or high is None or high <= 0:
                    return Response.call() if gs.call_amount == 0 else Response.fold()
                return Response.raise_to(max(low, min(int(resp.amount), high)))
            return resp
        if kind == "fold" and gs.call_amount == 0:
            return Response.call()
        return resp
