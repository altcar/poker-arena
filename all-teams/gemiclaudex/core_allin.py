"""Heads-up stack and pot accounting. Commitments are hand totals (docs/API.md)."""
from __future__ import annotations

from core_interface import GameState


def opponent_start(state: GameState) -> int:
    return state.starting_stack if state.opp_starting_stack is None else state.opp_starting_stack


def terminal_call(state: GameState) -> bool:
    """A call ends all betting when either player has no chips left afterwards."""
    behind = max(0, state.starting_stack - state.my_committed)
    return state.legal.call_amount > 0 and (
        state.legal.call_amount >= behind or state.opp_committed >= opponent_start(state))


def call_price(state: GameState) -> tuple[int, int]:
    """(chips to add, contestable pot after calling), excluding unmatched excess."""
    call = min(state.legal.call_amount, max(0, state.starting_stack - state.my_committed))
    final_me = state.my_committed + call
    return call, 2 * min(final_me, state.opp_committed)


def effective_raise_to(state: GameState) -> int | None:
    """Largest useful legal hand-total raise, without unmatched excess.

    If the full minimum exceeds the opponent's stack, use the minimum legal
    raise (the dealer returns the uncalled part); never emit an illegal underraise.
    """
    lg = state.legal
    if lg.min_raise_to is None or lg.max_raise_to is None:
        return None
    return min(lg.max_raise_to, max(lg.min_raise_to, opponent_start(state)))


def unbeatable(hole: tuple[str, str], board: tuple[str, ...], value: int) -> bool:
    """Prove a made straight-or-better cannot lose on any remaining runout.

    Test attainable higher five-card categories using board cards plus at most
    2 + remaining-board-card unknowns. Hero's cards are unavailable to villain.
    This avoids enumerating hundreds of opponent hands in the fast fallback.
    It is conservative: hero may improve, but only the current rank is used.
    """
    from core_equity import RANKS, SUITS

    category = value >> 25
    if category < 4:
        return False
    known = set(board)
    live = {r + s for r in RANKS for s in SUITS} - known - set(hole)
    budget = 7 - len(board)  # villain's two holes plus all remaining board cards
    board_counts = [sum(c[0] == r for c in board) for r in RANKS]
    live_counts = [sum(c[0] == r for c in live) for r in RANKS]
    windows = [(hi, list(range(hi - 4, hi + 1))) for hi in range(12, 3, -1)]
    windows.append((3, [12, 0, 1, 2, 3]))

    def possible(needs):
        missing = [max(0, n - board_counts[r]) for r, n in needs]
        return sum(missing) <= budget and all(
            n <= live_counts[r] for (r, _), n in zip(needs, missing))

    for hi, ranks in windows:
        if ((8 << 25) | (hi << 21)) <= value:
            continue
        for suit in SUITS:
            cards = {RANKS[r] + suit for r in ranks}
            if cards <= known | live and len(cards - known) <= budget:
                return False
    if category == 8:
        return True
    for q in range(13):
        for k in range(13):
            if k != q and ((7 << 25) | (q << 21) | (1 << k)) > value:
                if possible([(q, 4), (k, 1)]):
                    return False
    if category == 7:
        return True
    for t in range(13):
        for p in range(13):
            if p != t and ((6 << 25) | (t << 21) | (p << 17)) > value:
                if possible([(t, 3), (p, 2)]):
                    return False
    if category == 6:
        return True
    for suit in SUITS:
        ranks, spent = [], 0
        for r in range(12, -1, -1):
            card = RANKS[r] + suit
            if card in known or (card in live and spent < budget):
                ranks.append(r)
                spent += int(card not in known)
                if len(ranks) == 5:
                    break
        if len(ranks) == 5 and ((5 << 25) | sum(1 << r for r in ranks)) > value:
            return False
    if category == 5:
        return True
    for hi, ranks in windows:
        if ((4 << 25) | (hi << 21)) > value and possible([(r, 1) for r in ranks]):
            return False
    return True
