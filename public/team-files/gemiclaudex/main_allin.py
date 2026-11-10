"""Chip-EV terminal calls; jam frequencies are range assumptions, not a Nash solution.

See docs/ALL_IN.md. No realization/stack-off premium remains once calling ends
betting. Preflop uses a blocker-filtered top-fraction range, learned separately
for opens, reraises and later raises, and shallow/deep effective stacks.
"""
from __future__ import annotations

from dataclasses import replace

from core_allin import call_price, opponent_start, terminal_call
from core_equity import Range, equity, top_percent_range
from core_interface import Action, GameState

PRIOR_HANDS = 8.0
# Arena BB half: the opponent's SB folds are hidden, so visible jams / visible decisions overstates their open-jam
# share by 1 / (SB entry rate). Scale by an assumed entry rate instead of discarding the evidence: discarding it
# kept a 10% jam range all half even vs a bot that shoved every hand (-3.7 bb/hand vs @shove, #45 review).
HIDDEN_SB_ENTRY = 0.80
DEEP_PRIORS = (0.10, 0.06, 0.03)
SHALLOW_PRIORS = (0.50, 0.35, 0.20)


class JamModel:
    """Count observed opponent decisions, never treat our unobserved SB folds as opportunities.

    Every observed decision is counted once, including repeated act() snapshots.
    Call finish_hand() on the real hand boundary, even when hand IDs repeat.
    Hidden actions/outcomes are not invented. A match reset creates a new model.
    """
    def __init__(self):
        self.counts: dict[tuple[bool, bool, int], list[int]] = {}
        self._seen: set[int] = set()
        self._hand_id = None
        self._last_state = None
        self.complete_hands = True

    def finish_hand(self, result=None):
        if self._last_state is not None and getattr(result, "history", None) is not None:
            snapshot = replace(self._last_state, hand_id=result.hand_id,
                               is_button=result.was_button, history=result.history)
            self.observe(snapshot)
        self._seen.clear()
        self._hand_id = None

    def _key(self, state, raises):
        shallow = min(state.starting_stack, opponent_start(state)) <= 15 * state.big_blind
        return shallow, not state.is_button, min(raises, 2)

    def observe(self, state: GameState):
        if self._hand_id != state.hand_id:
            self.finish_hand()
            self._hand_id = state.hand_id
        self._last_state = state
        raises = 0
        for i, rec in enumerate(state.history):
            if rec.street != 1:
                continue
            if rec.actor == "opp" and i not in self._seen:
                key = self._key(state, raises)
                pair = self.counts.setdefault(key, [0, 0])
                pair[0] += int(rec.kind == "raise" and rec.raise_to is not None
                               and rec.raise_to >= min(state.starting_stack, opponent_start(state)))
                pair[1] += 1
                self._seen.add(i)
            raises += int(rec.kind == "raise")

    def share(self, state: GameState) -> float:
        raises = 0
        context = 0
        for rec in state.history:
            if rec.street == 1:
                if rec.actor == "opp" and rec.kind == "raise":
                    context = raises
                raises += int(rec.kind == "raise")
        key = self._key(state, context)
        jams, total = self.counts.get(key, (0, 0))
        # The arena hides SB folds before our first turn. Their open frequency
        # is unidentifiable; never interpret only the visible raises as all hands.
        prior = (SHALLOW_PRIORS if key[0] else DEEP_PRIORS)[key[2]]
        if not self.complete_hands and key[1] and key[2] == 0:
            jams = jams * HIDDEN_SB_ENTRY
        return max(0.02, min(1.0, (jams + PRIOR_HANDS * prior) / (total + PRIOR_HANDS)))

    def range(self, state: GameState) -> Range:
        return top_percent_range(self.share(state))


def all_in_call_action(state: GameState, opp_range: Range | None,
                       time_budget_ms: float = 60.0) -> Action | None:
    if not terminal_call(state):
        return None
    call, final_pot = call_price(state)
    eq = equity(state.hole_cards, state.board, opp_range, samples=4000,
                time_budget_ms=time_budget_ms, seed=state.hand_id)
    return Action("call" if eq * final_pot >= call else "fold")
