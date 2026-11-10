"""Preflop decisions: node detection, table lookup, sizing and mixing."""

from __future__ import annotations

import team_ranges as ranges
from team_equity_table import HANDS, equity_vs_weights
from arena_sdk import Response

SB_FIRST_IN = "SB_FIRST_IN"
BB_VS_LIMP = "BB_VS_LIMP"
BB_VS_OPEN = "BB_VS_OPEN"
SB_VS_3BET = "SB_VS_3BET"
SB_VS_ISO = "SB_VS_ISO"
BB_VS_4BET = "BB_VS_4BET"
BB_VS_LIMP_RERAISE = "BB_VS_LIMP_RERAISE"
FACING_BIG = "FACING_BIG"
UNKNOWN = "UNKNOWN"

BLINDS = ("post_small_blind", "post_big_blind")
RAISES = ("raise_to", "bet")
OPEN_BUCKETS = tuple(sorted(ranges.BB_VS_OPEN))

BIG_RAISE_SHARE = 1 / 3  # of the effective stack: decided by equity alone.
JAM_INSTEAD_SHARE = 0.4  # a planned raise this big becomes all-in (8.4).
RERAISE_JAM_EQUITY = 0.60
NOT_ALL_IN_MARGIN = 0.03  # realisation allowance when chips remain behind.
VALUE_EQUITY = 0.57  # versus any two: strong enough to raise big at a station.

REF_PRICE_VS_3BET = 0.375  # 150 to call, 400 final pot.
REF_PRICE_VS_4BET = 0.273  # 240 to call, 880 final pot.
PRICE_GAIN = 2.5

_range_cache: dict[int, list[float]] = {}


def hand_key(hole) -> str:
    """Canonical class such as 'AA', 'AKs' or 'AKo' from two card codes."""
    order = "AKQJT98765432"
    first, second = hole[0], hole[1]
    if order.index(first[0]) > order.index(second[0]):
        first, second = second, first
    if first[0] == second[0]:
        return first[0] + second[0]
    suffix = "s" if first[1] == second[1] else "o"
    return first[0] + second[0] + suffix


def preflop_actions(state) -> list:
    return [
        record
        for record in state.action_history
        if record.street == "preflop" and record.action not in BLINDS
    ]


def effective_stack(state) -> int:
    """Smaller of the two starting stacks for this street, in chips."""
    return min(
        stack + bet
        for stack, bet in zip(state.stack_sizes, state.current_bets)
    )


def detect_node(state) -> str:
    """Works out the betting node from history and the current bets."""
    if state.call_amount >= max(
        1, effective_stack(state) * BIG_RAISE_SHARE
    ):
        return FACING_BIG
    actions = [
        "r" if record.action in RAISES else record.action[0]
        for record in preflop_actions(state)
    ]
    is_sb = state.my_index == state.dealer_index
    sequence = "".join(actions)
    if is_sb:
        return {
            "": SB_FIRST_IN,
            "rr": SB_VS_3BET,
            "cr": SB_VS_ISO,
        }.get(sequence, FACING_BIG if state.call_amount else UNKNOWN)
    return {
        "c": BB_VS_LIMP,
        "r": BB_VS_OPEN,
        "rrr": BB_VS_4BET,
        "crr": BB_VS_LIMP_RERAISE,
    }.get(sequence, FACING_BIG if state.call_amount else UNKNOWN)


def top_range(share: float) -> list[float]:
    """One weight per hand class: the top ``share`` of combos by RANKING."""
    key = max(1, min(100, round(share * 100)))
    cached = _range_cache.get(key)
    if cached is not None:
        return cached
    index = {hand: i for i, hand in enumerate(HANDS)}
    target = key / 100 * 1326
    weights = [0.0] * 169
    cumulative = 0
    for hand in ranges.RANKING:
        combos = 6 if len(hand) == 2 else 4 if hand[2] == "s" else 12
        if cumulative >= target:
            break
        weights[index[hand]] = min(1.0, (target - cumulative) / combos)
        cumulative += combos
    _range_cache[key] = weights
    return weights


def can_raise(state) -> bool:
    return bool(
        (state.can_raise or state.can_bet) and state.max_raise_to
    )


def raise_or_jam(state, total: int) -> Response:
    """A legal raise near ``total``, or the best legal alternative."""
    if not can_raise(state):
        return Response.call()
    high = state.max_raise_to
    if total >= high * JAM_INSTEAD_SHARE:
        return Response.all_in()
    low = state.min_raise_to if state.min_raise_to is not None else total
    return Response.raise_to(int(max(low, min(total, high))))


def passive(state, wants_call: bool) -> Response:
    """Check when free. Otherwise call or fold."""
    if state.call_amount == 0 or wants_call:
        return Response.call()
    return Response.fold()


def pick(rng, row) -> int:
    """Samples an index from a row of probabilities."""
    roll = rng.random()
    total = 0.0
    for index, probability in enumerate(row):
        total += probability
        if roll < total:
            return index
    return len(row) - 1


def required_equity(state) -> float:
    call = state.call_amount
    return call / (state.pot_size + call) if call else 0.0


def nearest_open(amount: int) -> int:
    return min(OPEN_BUCKETS, key=lambda size: abs(size - amount))


def price_shift(row, price: float, reference: float, wider: float = 0.0):
    """Moves fold <-> call as the price differs from the table's reference."""
    value, bluff, call, fold = row
    shift = (reference - price) * PRICE_GAIN + wider
    if shift > 0:
        moved = fold * min(1.0, shift)
        return value, bluff, call + moved, fold - moved
    moved = call * min(1.0, -shift)
    return value, bluff, call - moved, fold + moved


def scale_bluff(row, mult: float):
    """Scales bluff mass, taking from or returning to the calls (bounded)."""
    value, bluff, call, fold = row
    new_bluff = bluff * mult
    if new_bluff <= bluff:
        return value, new_bluff, call + bluff - new_bluff, fold
    extra = min(new_bluff - bluff, call)
    return value, bluff + extra, call - extra, fold


def blend(first, second, amount: float):
    return tuple(a + (b - a) * amount for a, b in zip(first, second))


def _facing_big(state, hand: str, model, context: str) -> Response:
    share = model.jam_range(context)
    equity = equity_vs_weights(hand, top_range(share))
    required = required_equity(state)
    chips_behind = state.call_amount < (state.my_stack or 0)
    margin = NOT_ALL_IN_MARGIN if chips_behind else 0.0
    if can_raise(state) and equity >= RERAISE_JAM_EQUITY:
        return Response.all_in()
    return passive(state, equity >= required + margin)


def _sb_first_in(state, hand: str, model, rng) -> Response:
    knobs = model.sb_knobs()
    row = ranges.SB_FIRST_IN[hand]
    if knobs.open_shift > 0:
        row = blend(row, ranges.SB_FIRST_IN_WIDE[hand], knobs.open_shift)
    elif knobs.open_shift < 0:
        row = blend(row, ranges.SB_FIRST_IN_TIGHT[hand], -knobs.open_shift)
    if knobs.fold_non_callers:
        # Against a bot that jams over opens, opening a hand that must then
        # fold loses 2.5bb where folding now loses 0.5bb.
        share = model.jam_range("vs_open")
        equity = equity_vs_weights(hand, top_range(share))
        needed = (effective_stack(state) - knobs.open_size) / (
            2 * effective_stack(state)
        )
        if equity < needed:
            return passive(state, False)
    is_value = ranges.EQ_ANY[hand] >= VALUE_EQUITY
    if knobs.limp_junk and not is_value:
        return Response.call()
    if pick(rng, row) == 0:
        size = knobs.value_size if is_value else knobs.open_size
        return raise_or_jam(state, size)
    return passive(state, False)


def _bb_vs_limp(state, hand: str, model, rng) -> Response:
    knobs = model.bb_knobs()
    row = ranges.BB_VS_LIMP[hand]
    if knobs.iso_shift > 0:
        row = blend(row, ranges.BB_VS_LIMP_WIDE[hand], knobs.iso_shift)
    value, bluff, check = row
    bluff_scaled = bluff * knobs.iso_bluff_mult
    choice = pick(rng, (value, bluff_scaled, check + bluff - bluff_scaled))
    if choice < 2:
        return raise_or_jam(state, knobs.iso_size)
    return Response.call()


def _bb_vs_open(state, hand: str, model, rng) -> Response:
    knobs = model.bb_knobs()
    open_total = max(state.current_bets)
    row = ranges.BB_VS_OPEN[nearest_open(open_total)][hand]
    row = scale_bluff(row, knobs.bluff_mult)
    choice = pick(rng, row)
    if choice < 2:
        return raise_or_jam(state, round(open_total * knobs.three_bet_mult))
    return passive(state, choice == 2)


def _sb_vs_3bet(state, hand: str, model, rng) -> Response:
    knobs = model.sb_knobs()
    row = ranges.SB_VS_3BET[hand]
    if knobs.wide_value > 0:
        row = blend(row, ranges.SB_VS_3BET_WIDE[hand], knobs.wide_value)
    row = scale_bluff(row, knobs.bluff_mult)
    row = price_shift(
        row, required_equity(state), REF_PRICE_VS_3BET, knobs.call_wider
    )
    choice = pick(rng, row)
    if choice < 2:
        return raise_or_jam(state, round(max(state.current_bets) * 2.2))
    return passive(state, choice == 2)


def _bb_vs_4bet(state, hand: str, model, rng) -> Response:
    knobs = model.bb_knobs()
    row = ranges.BB_VS_4BET[hand]
    row = price_shift(
        row, required_equity(state), REF_PRICE_VS_4BET, 0.4 * knobs.jam_lighter
    )
    jam, _, call, fold = row
    if knobs.jam_lighter > 0:
        moved = call * 0.3 * knobs.jam_lighter
        jam, call = jam + moved, call - moved
    choice = pick(rng, (jam, 0.0, call, fold))
    if choice == 0:
        return Response.all_in() if can_raise(state) else Response.call()
    return passive(state, choice == 2)


def _sb_vs_iso(state, hand: str, rng) -> Response:
    row = price_shift(
        ranges.SB_VS_ISO[hand], required_equity(state), 0.357
    )
    choice = pick(rng, row)
    if choice == 0:
        return raise_or_jam(state, round(max(state.current_bets) * 3))
    return passive(state, choice == 2)


def big_context(state) -> str:
    """Which jam prior applies, from how many raises came before."""
    raises = sum(
        1 for record in preflop_actions(state) if record.action in RAISES
    )
    if raises <= 1:
        return "open"
    if raises == 2:
        return "vs_open"
    return "deep"


def preflop_action(state, model, rng) -> Response:
    """The preflop decision. Always returns a legal Response."""
    hand = hand_key(state.hole_cards)
    node = detect_node(state)
    if node == FACING_BIG:
        return _facing_big(state, hand, model, big_context(state))
    if node == SB_FIRST_IN:
        return _sb_first_in(state, hand, model, rng)
    if node == BB_VS_LIMP:
        return _bb_vs_limp(state, hand, model, rng)
    if node == BB_VS_OPEN:
        return _bb_vs_open(state, hand, model, rng)
    if node == SB_VS_3BET:
        return _sb_vs_3bet(state, hand, model, rng)
    if node in (BB_VS_4BET, BB_VS_LIMP_RERAISE):
        return _bb_vs_4bet(state, hand, model, rng)
    if node == SB_VS_ISO:
        return _sb_vs_iso(state, hand, rng)
    return passive(state, False)
