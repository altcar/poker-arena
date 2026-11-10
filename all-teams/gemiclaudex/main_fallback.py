"""Fallback strategy (#5): simple, fast (< 1 ms), always legal, imports only `core`.

MainBot uses it whenever a strategy module is missing, raises, or runs over its time budget.
  Preflop: raise small with the top ~15% of hands, call cheaply with the top ~50%, else check/fold.
  Postflop: with a pair or better that uses a hole card, bet small when checked to and call a bet up to
  pot size; with two pair or better, raise small. Otherwise check/fold.
"""
from __future__ import annotations

from core_allin import unbeatable
from core_equity import _preflop_ranking, rank7
from core_interface import Action, GameState, clamp_to_legal


def _class_percentiles() -> dict[str, float]:
    """Hand class -> share of all 1326 combos at least as strong (AA = 0.0045, 32o = 1.0)."""
    out, acc = {}, 0
    for cls in _preflop_ranking():
        acc += 6 if len(cls) == 2 else 4 if cls[2] == "s" else 12   # pairs, suited, offsuit
        out[cls] = acc / 1326
    return out


_PCT = _class_percentiles()
_RANKS = "23456789TJQKA"


def _hand_class(hole: tuple[str, str]) -> str:
    a, b = hole
    hi, lo = (a, b) if _RANKS.index(a[0]) >= _RANKS.index(b[0]) else (b, a)
    if hi[0] == lo[0]:
        return hi[0] + lo[0]
    return hi[0] + lo[0] + ("s" if hi[1] == lo[1] else "o")


def _small_raise(state: GameState) -> Action:
    lg = state.legal
    if lg.min_raise_to is None:
        return Action("check") if lg.can_check else Action("call")
    pot = state.my_committed + state.opp_committed + lg.call_amount
    target = state.opp_committed + max(state.big_blind, pot // 2)
    return Action("raise", max(lg.min_raise_to, min(target, lg.max_raise_to)))


def fallback_action(state: GameState) -> Action:
    try:
        return clamp_to_legal(_decide(state), state.legal)
    except Exception:
        lg = state.legal
        return Action("check") if lg.can_check else Action("fold")


def _decide(state: GameState) -> Action:
    lg = state.legal
    call = lg.call_amount
    if state.street == 1:
        pct = _PCT.get(_hand_class(state.hole_cards), 1.0)
        if pct <= 0.15:
            return _small_raise(state)
        if lg.can_check:
            return Action("check")
        if pct <= 0.50 and call <= 3 * state.big_blind:
            return Action("call")
        return Action("fold")

    value = rank7(tuple(state.hole_cards) + tuple(state.board))
    category = value >> 25
    pair_rank = _RANKS[(value >> 21) & 0xF]
    uses_hole = category >= 2 or (category == 1 and pair_rank in (state.hole_cards[0][0], state.hole_cards[1][0]))
    if not uses_hole:
        return Action("check") if lg.can_check else Action("fold")
    if lg.can_check:
        return _small_raise(state)
    if category >= 2 and lg.min_raise_to is not None:
        return _small_raise(state)
    pot_before_bet = state.my_committed + state.opp_committed - call
    if call <= pot_before_bet or unbeatable(state.hole_cards, state.board, value):
        return Action("call")
    return Action("fold")
