# Built by tools/build_flat.py from bot/: core.cards, core.evaluator, core.preflop, core.equity, shell.strategy. Edit the source, not this.

from __future__ import annotations

"""Card encoding: a card is an int 0..51 equal to rank * 4 + suit.

Ranks run 0 (deuce) to 12 (ace) and suits follow the organiser's order S, H,
D, C. ``card >> 2`` is the rank and ``card & 3`` the suit, so the hot loops
never touch strings.
"""

from collections.abc import Iterable

RANKS = "23456789TJQKA"
SUITS = "SHDC"

_NAMES = tuple(rank + suit for rank in RANKS for suit in SUITS)
_CODES = {name: code for code, name in enumerate(_NAMES)}


def card(name: str) -> int:
    """Returns the code of a card written like 'AS' or 'TD'."""
    try:
        return _CODES[name]
    except KeyError:
        raise ValueError(f"not a card: {name!r}") from None


def parse_cards(names: Iterable[str] | str) -> tuple[int, ...]:
    """Returns the codes of several cards, given as a list or 'AS TD 2C'."""
    if isinstance(names, str):
        names = names.split()
    return tuple(card(name) for name in names)


def card_name(code: int) -> str:
    """Returns the 'AS' style name of a card code."""
    return _NAMES[code]

"""Hand evaluator for 5, 6 and 7 cards.

A hand's strength is an int from 1 to 7462, one per distinct five-card hand
class. Higher is better and equal means a tie.

Suits only matter for flushes, so the rest of the work is a walk through a
state machine over rank multisets:

* ``STEP[state + rank]`` is the next state. States are row offsets, so one
  step is one add and one list index, and a board's state is shared by every
  hole-card pair played on it. The step from a six-card state returns the
  strength of the seven cards directly.
* ``STEP[state + RANK_COLUMN]`` is the strength of a five- or six-card state.
* ``FLUSH[mask]`` is the strength of the best flush inside a 13-bit rank mask,
  or 0 when the mask has fewer than five bits.

Seven cards cannot hold both a flush and a full house, so a flush strength,
when there is one, always decides. See docs/CORE_BENCH.md for the measurements
behind this design.

The tables are built when this module is imported, which happens at pack time;
they then travel inside the bot's pickle as plain lists.
"""

from bisect import bisect_right
from collections.abc import Sequence
from itertools import combinations_with_replacement

CATEGORY_NAMES = (
    "High Card",
    "One Pair",
    "Two Pair",
    "Three of a Kind",
    "Straight",
    "Flush",
    "Full House",
    "Four of a Kind",
    "Straight Flush",
)

RANK_COLUMN = 13
ROW_WIDTH = 14
MAX_SAME_RANK = 4

# Per-suit card counters, four bits each. Starting a counter at 3 makes its top
# bit fire at five cards of a suit; starting at 5 makes it fire at three.
SUIT_COUNT = tuple(1 << (4 * (code & 3)) for code in range(52))
FIVE_OF_A_SUIT = 0x3333
THREE_OF_A_SUIT = 0x5555
SUIT_FLAGS = 0x8888
FLAG_SUIT = {8 << (4 * suit): suit for suit in range(4)}

# SUIT_BIT[suit][card] is the card's rank bit if it has that suit, else 0.
SUIT_BIT = tuple(
    tuple(1 << (c >> 2) if c & 3 == suit else 0 for c in range(52)) for suit in range(4)
)


def _class_key(ranks: tuple[int, ...], flush: bool) -> tuple[int, ...]:
    """Returns a sortable (category, tiebreak...) key for five ranks."""
    ordered = sorted(
        set(ranks), key=lambda rank: (ranks.count(rank), rank), reverse=True
    )
    counts = sorted((ranks.count(rank) for rank in ordered), reverse=True)
    straight_high = None
    if len(ordered) == 5:
        if ordered[0] - ordered[4] == 4:
            straight_high = ordered[0]
        elif ordered == [12, 3, 2, 1, 0]:
            straight_high = 3
    if flush and straight_high is not None:
        return (8, straight_high)
    if counts == [4, 1]:
        return (7, *ordered)
    if counts == [3, 2]:
        return (6, *ordered)
    if flush:
        return (5, *ordered)
    if straight_high is not None:
        return (4, straight_high)
    if counts == [3, 1, 1]:
        return (3, *ordered)
    if counts == [2, 2, 1]:
        return (2, *ordered)
    if counts == [2, 1, 1, 1]:
        return (1, *ordered)
    return (0, *ordered)


def _multisets(size: int) -> list[tuple[int, ...]]:
    """Returns every sorted rank multiset of ``size`` cards in one deck."""
    return [
        ranks
        for ranks in combinations_with_replacement(range(13), size)
        if all(
            ranks[i] != ranks[i + MAX_SAME_RANK] for i in range(size - MAX_SAME_RANK)
        )
    ]


def _build_strengths() -> tuple[dict, dict, list[int]]:
    """Ranks all five-card classes.

    Returns the strength of each plain and each flush rank tuple, and the
    lowest strength of each category.
    """
    fives = _multisets(5)
    suited = [ranks for ranks in fives if len(set(ranks)) == 5]
    plain_keys = {ranks: _class_key(ranks, False) for ranks in fives}
    flush_keys = {ranks: _class_key(ranks, True) for ranks in suited}
    classes = sorted({*plain_keys.values(), *flush_keys.values()})
    strength = {key: index for index, key in enumerate(classes, start=1)}
    starts = [
        next(strength[key] for key in classes if key[0] == category)
        for category in range(len(CATEGORY_NAMES))
    ]
    plain = {ranks: strength[key] for ranks, key in plain_keys.items()}
    flush = {ranks: strength[key] for ranks, key in flush_keys.items()}
    return plain, flush, starts


def _build_step(plain: dict) -> list[int]:
    """Builds the rank state machine from the five-card strengths."""
    best = dict(plain)
    for size in (6, 7):
        for ranks in _multisets(size):
            best[ranks] = max(best[ranks[:i] + ranks[i + 1 :]] for i in range(size))

    offsets = {}
    for size in range(7):
        for ranks in _multisets(size):
            offsets[ranks] = len(offsets) * ROW_WIDTH

    step = [0] * (len(offsets) * ROW_WIDTH)
    for ranks, offset in offsets.items():
        if len(ranks) >= 5:
            step[offset + RANK_COLUMN] = best[ranks]
        for rank in range(13):
            if ranks.count(rank) == MAX_SAME_RANK:
                continue
            child = tuple(sorted((*ranks, rank)))
            step[offset + rank] = best[child] if len(child) == 7 else offsets[child]
    return step


def _build_flush(flush: dict) -> list[int]:
    """Builds the 13-bit rank mask to flush strength table."""
    table = [0] * (1 << 13)
    for mask in range(1 << 13):
        bits = [rank for rank in range(13) if mask >> rank & 1]
        if len(bits) == 5:
            table[mask] = flush[tuple(bits)]
        elif len(bits) > 5:
            table[mask] = max(table[mask ^ (1 << rank)] for rank in bits)
    return table


_plain, _flush, _CATEGORY_STARTS = _build_strengths()
STEP = _build_step(_plain)
FLUSH = _build_flush(_flush)
del _plain, _flush


def _flush_strength(cards: Sequence[int], flags: int) -> int:
    """Returns the flush strength of cards whose suit counter has fired."""
    bits = SUIT_BIT[FLAG_SUIT[flags]]
    mask = 0
    for code in cards:
        mask |= bits[code]
    return FLUSH[mask]


def evaluate7(a: int, b: int, c: int, d: int, e: int, f: int, g: int) -> int:
    """Returns the strength of exactly seven cards."""
    count = SUIT_COUNT
    flags = SUIT_FLAGS & (
        FIVE_OF_A_SUIT
        + count[a] + count[b] + count[c] + count[d]
        + count[e] + count[f] + count[g]
    )  # fmt: skip
    if flags:
        return _flush_strength((a, b, c, d, e, f, g), flags)
    step = STEP
    return step[
        step[
            step[
                step[step[step[step[a >> 2] + (b >> 2)] + (c >> 2)] + (d >> 2)]
                + (e >> 2)
            ]
            + (f >> 2)
        ]
        + (g >> 2)
    ]


def evaluate5(a: int, b: int, c: int, d: int, e: int) -> int:
    """Returns the strength of exactly five cards."""
    if a & 3 == b & 3 == c & 3 == d & 3 == e & 3:
        bits = SUIT_BIT[a & 3]
        return FLUSH[bits[a] | bits[b] | bits[c] | bits[d] | bits[e]]
    step = STEP
    state = step[step[step[step[a >> 2] + (b >> 2)] + (c >> 2)] + (d >> 2)]
    return step[step[state + (e >> 2)] + RANK_COLUMN]


def evaluate(cards: Sequence[int]) -> int:
    """Returns the strength of the best five of 5, 6 or 7 cards."""
    flags = FIVE_OF_A_SUIT
    for code in cards:
        flags += SUIT_COUNT[code]
    flags &= SUIT_FLAGS
    if flags:
        return _flush_strength(cards, flags)
    state = 0
    for code in cards:
        state = STEP[state + (code >> 2)]
    return state if len(cards) == 7 else STEP[state + RANK_COLUMN]


def category(strength: int) -> int:
    """Returns the hand category, an index into ``CATEGORY_NAMES``."""
    return bisect_right(_CATEGORY_STARTS, strength) - 1


def category_name(strength: int) -> str:
    """Returns the name of a strength's hand category."""
    return CATEGORY_NAMES[category(strength)]

"""Starting-hand classes and the exact preflop all-in equity table.

The 169 classes sit on a 13 x 13 grid: index ``high * 13 + low`` for suited
hands and pairs, ``low * 13 + high`` for offsuit hands. The table gives the
exact all-in equity of any hand of one class against a uniformly random
unblocked hand of another, averaged over all boards.
"""

from array import array
from collections.abc import Iterable, Mapping
from itertools import combinations

from tables import TABLE_HEX

CLASSES = 169
SCALE = 65535

Hand = tuple[int, int]
Combo = tuple[int, int, float]

_TABLE = array("H", bytes.fromhex(TABLE_HEX))
ALL_COMBOS: tuple[Combo, ...] = tuple(
    (a, b, 1.0) for a, b in combinations(range(52), 2)
)


def hand_class(hand: Hand) -> int:
    """Returns the class index of two hole cards."""
    a, b = hand
    high, low = a >> 2, b >> 2
    if high < low:
        high, low = low, high
    return high * 13 + low if a & 3 == b & 3 else low * 13 + high


def class_index(name: str) -> int:
    """Returns the class index of a name like 'AA', 'AKs' or 'T9o'."""
    high, low = RANKS.index(name[0]), RANKS.index(name[1])
    if high == low and len(name) == 2:
        return high * 13 + low
    if high > low and name[2:] == "s":
        return high * 13 + low
    if high > low and name[2:] == "o":
        return low * 13 + high
    raise ValueError(f"not a starting hand class: {name!r}")


def class_name(index: int) -> str:
    """Returns the name of a class index."""
    row, column = divmod(index, 13)
    if row == column:
        return RANKS[row] * 2
    if row > column:
        return RANKS[row] + RANKS[column] + "s"
    return RANKS[column] + RANKS[row] + "o"


def class_combos(index: int) -> list[Hand]:
    """Returns the hands of a class: 6 pairs, 4 suited or 12 offsuit."""
    return [(a, b) for a, b, _ in ALL_COMBOS if hand_class((a, b)) == index]


def range_from_classes(weights: Mapping[str, float]) -> list[Combo]:
    """Builds a weighted range from class names, like {'AA': 1, 'KQs': 0.5}."""
    by_class = {class_index(name): w for name, w in weights.items()}
    combos = []
    for a, b, _ in ALL_COMBOS:
        weight = by_class.get(hand_class((a, b)), 0.0)
        if weight > 0.0:
            combos.append((a, b, weight))
    return combos


def class_equity(hero_class: int, villain_class: int) -> float:
    """Returns the exact all-in equity of one class against another."""
    return _TABLE[hero_class * CLASSES + villain_class] / SCALE


def preflop_equity(hero: Hand, villain_range: Iterable[Combo] | None = None) -> float:
    """Returns the all-in equity of a hand against a weighted range.

    Each unblocked combo counts with its class's equity, so the result is
    exact for ranges uniform within each class (including the default, a
    random hand) and within about 1% for ranges that single out suits.
    """
    row = hand_class(hero) * CLASSES
    h1, h2 = hero
    total = weighted = 0.0
    for a, b, weight in ALL_COMBOS if villain_range is None else villain_range:
        if a != h1 and a != h2 and b != h1 and b != h2:
            total += weight
            weighted += weight * _TABLE[row + hand_class((a, b))]
    if total == 0.0:
        raise ValueError("the range has no combo left after card removal")
    return weighted / (total * SCALE)

"""Equity against a random hand or a weighted range.

A range is a sequence of ``(card, card, weight)`` combos, ``None`` meaning a
uniformly random hand; blocked combos are dropped by every function here.
``equity`` is the entry point for play: it uses the preflop table, exact
enumeration on the turn and river, and Monte Carlo on the flop.

Monte Carlo deals one shuffled deck into disjoint runouts, which costs far
less per sample than drawing each runout separately, and rejects a runout that
clashes with the sampled villain hand. Rejection keeps the joint distribution
exact, because every unblocked hand has the same number of runouts.
"""

import random
from collections.abc import Sequence
from itertools import combinations
from time import perf_counter
from typing import NamedTuple


Range = Sequence[Combo] | None

DEFAULT_SAMPLES = 2000

_RANDOM = random.Random()
_NO_SUIT = (0,) * 52


class Outcome(NamedTuple):
    """Win, tie and lose fractions.

    ``samples`` counts sampled deals, or the runouts enumerated by an exact call.
    """

    win: float
    tie: float
    lose: float
    samples: int

    @property
    def equity(self) -> float:
        """Share of the pot: wins plus half of the ties."""
        return self.win + self.tie / 2


class Potential(NamedTuple):
    """Billings' hand potential from one deal sample.

    ``strength`` is the chance of being ahead now, ``positive`` of going from
    behind to ahead by the river, ``negative`` of going from ahead to behind,
    and ``effective`` is the resulting expected hand strength (EHS).
    """

    strength: float
    positive: float
    negative: float
    effective: float
    samples: int


def _live_combos(villain_range: Range, dead: Sequence[int]) -> list[Combo]:
    """Returns the combos free of dead cards; raises if there are none."""
    combos = ALL_COMBOS if villain_range is None else villain_range
    live = [
        combo
        for combo in combos
        if combo[0] not in dead and combo[1] not in dead and combo[2] > 0.0
    ]
    if not live:
        raise ValueError("the range has no combo left after card removal")
    return live


def _board_summary(board: Sequence[int]) -> tuple[int, int, list[int]]:
    """Returns the board's rank state, suit counters and per-suit rank masks."""
    state = count = 0
    masks = [0, 0, 0, 0]
    for code in board:
        state = STEP[state + (code >> 2)]
        count += SUIT_COUNT[code]
        masks[code & 3] |= 1 << (code >> 2)
    return state, count, masks


def _tally_showdowns(
    hero: Hand,
    board: Sequence[int],
    live: Sequence[Combo],
    blocked: bytearray,
) -> tuple[float, float, float]:
    """Returns the win, tie and lose weight on a complete board."""
    h1, h2 = hero
    state, count, masks = _board_summary(board)
    step = STEP
    win = tie = lose = 0.0
    own = step[step[state + (h1 >> 2)] + (h2 >> 2)]
    flags = (count + THREE_OF_A_SUIT) & SUIT_FLAGS
    if not flags:
        for v1, v2, weight in live:
            if blocked[v1] or blocked[v2]:
                continue
            other = step[step[state + (v1 >> 2)] + (v2 >> 2)]
            if own > other:
                win += weight
            elif own == other:
                tie += weight
            else:
                lose += weight
        return win, tie, lose

    suit = FLAG_SUIT[flags]
    bits = SUIT_BIT[suit]
    mask = masks[suit]
    flush = FLUSH
    own = flush[mask | bits[h1] | bits[h2]] or own
    for v1, v2, weight in live:
        if blocked[v1] or blocked[v2]:
            continue
        other = (
            flush[mask | bits[v1] | bits[v2]]
            or step[step[state + (v1 >> 2)] + (v2 >> 2)]
        )
        if own > other:
            win += weight
        elif own == other:
            tie += weight
        else:
            lose += weight
    return win, tie, lose


def equity_exact(
    hero: Hand, board: Sequence[int], villain_range: Range = None
) -> Outcome:
    """Enumerates every runout and villain hand.

    On a five-card board this is the exact river hand strength. A turn costs
    about 46 times a river and a flop about 1000 times, so a flop belongs
    offline or in tests; use ``equity_mc`` for it during play.
    """
    dead = (*hero, *board)
    live = _live_combos(villain_range, dead)
    deck = [code for code in range(52) if code not in dead]
    blocked = bytearray(52)
    win = tie = lose = 0.0
    deals = 0
    for runout in combinations(deck, 5 - len(board)):
        for code in runout:
            blocked[code] = 1
        won, tied, lost = _tally_showdowns(hero, (*board, *runout), live, blocked)
        for code in runout:
            blocked[code] = 0
        win += won
        tie += tied
        lose += lost
        deals += 1
    total = win + tie + lose
    if total == 0.0:
        raise ValueError("no deal is possible against this range")
    return Outcome(win / total, tie / total, lose / total, deals)


def _deal_plan(
    hero: Hand,
    board: Sequence[int],
    villain_range: Range,
) -> tuple[list[int], list[Combo], list[float], int]:
    """Returns the deck, live combos, cumulative weights and runout length."""
    dead = (*hero, *board)
    live = _live_combos(villain_range, dead)
    deck = [code for code in range(52) if code not in dead]
    cumulative = []
    running = 0.0
    for combo in live:
        running += combo[2]
        cumulative.append(running)
    return deck, live, cumulative, 5 - len(board)


def equity_mc(
    hero: Hand,
    board: Sequence[int] = (),
    villain_range: Range = None,
    samples: int = DEFAULT_SAMPLES,
    time_limit: float | None = None,
    rng: random.Random = _RANDOM,
) -> Outcome:
    """Estimates equity by sampling, stopping at ``samples`` or ``time_limit``.

    ``time_limit`` is in seconds; at least one shuffled deck is always dealt,
    so the stop can overrun by a few microseconds. A complete board has
    nothing to sample and is answered exactly.
    """
    if len(board) == 5:
        return equity_exact(hero, board, villain_range)
    deck, live, cumulative, need = _deal_plan(hero, board, villain_range)
    h1, h2 = hero
    r1, r2 = h1 >> 2, h2 >> 2
    base_state, base_count, base_masks = _board_summary(board)
    runouts = len(deck) // need
    starts = range(0, runouts * need, need)
    step, flush, suit_count = STEP, FLUSH, SUIT_COUNT
    shuffle, choices = rng.shuffle, rng.choices
    deadline = None if time_limit is None else perf_counter() + time_limit

    win = tie = lose = 0
    while win + tie + lose < samples:
        shuffle(deck)
        villains = choices(live, cum_weights=cumulative, k=runouts)
        for start, (v1, v2, _) in zip(starts, villains):
            runout = deck[start : start + need]
            if v1 in runout or v2 in runout:
                continue
            state = base_state
            count = base_count
            for code in runout:
                state = step[state + (code >> 2)]
                count += suit_count[code]
            own = step[step[state + r1] + r2]
            other = step[step[state + (v1 >> 2)] + (v2 >> 2)]
            flags = (count + THREE_OF_A_SUIT) & SUIT_FLAGS
            if flags:
                suit = FLAG_SUIT[flags]
                bits = SUIT_BIT[suit]
                mask = base_masks[suit]
                for code in runout:
                    mask |= bits[code]
                own = flush[mask | bits[h1] | bits[h2]] or own
                other = flush[mask | bits[v1] | bits[v2]] or other
            if own > other:
                win += 1
            elif own == other:
                tie += 1
            else:
                lose += 1
        if deadline is not None and perf_counter() >= deadline:
            break
    total = win + tie + lose
    if total == 0:
        raise ValueError("no deal was sampled in the time allowed")
    return Outcome(win / total, tie / total, lose / total, total)


def equity(
    hero: Hand,
    board: Sequence[int] = (),
    villain_range: Range = None,
    samples: int = DEFAULT_SAMPLES,
    time_limit: float | None = None,
) -> float:
    """Returns the best-value equity estimate for a street.

    Preflop is the exact table, turn and river are exact enumeration (a few
    milliseconds at worst), and the flop is Monte Carlo under the given caps.
    """
    if not board:
        return preflop_equity(hero, villain_range)
    if len(board) == 3:
        return equity_mc(hero, board, villain_range, samples, time_limit).equity
    return equity_exact(hero, board, villain_range).equity


def _made_strength(state: int, mask: int, bits: Sequence[int], c1: int, c2: int) -> int:
    """Returns the strength of two cards on an incomplete board's state.

    ``mask`` and ``bits`` describe the board's flush suit, if it has one.
    """
    return (
        FLUSH[mask | bits[c1] | bits[c2]]
        or STEP[STEP[STEP[state + (c1 >> 2)] + (c2 >> 2)] + RANK_COLUMN]
    )


def hand_potential(
    hero: Hand,
    board: Sequence[int],
    villain_range: Range = None,
    samples: int = DEFAULT_SAMPLES,
    time_limit: float | None = None,
    rng: random.Random = _RANDOM,
) -> Potential:
    """Estimates hand strength and potential on a flop or turn by sampling.

    Each sample pairs a villain hand with a runout to the river and records
    who is ahead now and who is ahead at showdown, so ties count half, as in
    Billings et al. The river has no potential: strength is exact there.
    """
    if len(board) == 5:
        strength = equity_exact(hero, board, villain_range)
        return Potential(strength.equity, 0.0, 0.0, strength.equity, strength.samples)
    deck, live, cumulative, need = _deal_plan(hero, board, villain_range)
    h1, h2 = hero
    base_state, base_count, base_masks = _board_summary(board)
    now_flags = (base_count + THREE_OF_A_SUIT) & SUIT_FLAGS
    now_suit = FLAG_SUIT.get(now_flags, 0)
    now_mask = base_masks[now_suit] if now_flags else 0
    now_bits = SUIT_BIT[now_suit] if now_flags else _NO_SUIT
    own_now = _made_strength(base_state, now_mask, now_bits, h1, h2)
    runouts = len(deck) // need
    starts = range(0, runouts * need, need)
    step, flush, suit_count = STEP, FLUSH, SUIT_COUNT
    deadline = None if time_limit is None else perf_counter() + time_limit

    # table[now][final], where 0 is ahead, 1 tied and 2 behind.
    table = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
    total = 0
    while total < samples:
        rng.shuffle(deck)
        villains = rng.choices(live, cum_weights=cumulative, k=runouts)
        for start, (v1, v2, _) in zip(starts, villains):
            runout = deck[start : start + need]
            if v1 in runout or v2 in runout:
                continue
            other_now = _made_strength(base_state, now_mask, now_bits, v1, v2)
            state = base_state
            count = base_count
            for code in runout:
                state = step[state + (code >> 2)]
                count += suit_count[code]
            own = step[step[state + (h1 >> 2)] + (h2 >> 2)]
            other = step[step[state + (v1 >> 2)] + (v2 >> 2)]
            flags = (count + THREE_OF_A_SUIT) & SUIT_FLAGS
            if flags:
                suit = FLAG_SUIT[flags]
                bits = SUIT_BIT[suit]
                mask = base_masks[suit]
                for code in runout:
                    mask |= bits[code]
                own = flush[mask | bits[h1] | bits[h2]] or own
                other = flush[mask | bits[v1] | bits[v2]] or other
            now = 0 if own_now > other_now else 1 if own_now == other_now else 2
            final = 0 if own > other else 1 if own == other else 2
            table[now][final] += 1
            total += 1
        if deadline is not None and perf_counter() >= deadline:
            break
    if total == 0:
        raise ValueError("no deal was sampled in the time allowed")
    return _potential_from_table(table, total)


def _potential_from_table(table: list[list[int]], total: int) -> Potential:
    """Turns the now-versus-final counts into the Billings formulas."""
    ahead, tied, behind = (sum(row) for row in table)
    strength = (ahead + tied / 2) / total
    gain_base = behind + tied / 2
    loss_base = ahead + tied / 2
    positive = (
        (table[2][0] + table[2][1] / 2 + table[1][0] / 2) / gain_base
        if gain_base
        else 0.0
    )
    negative = (
        (table[0][2] + table[0][1] / 2 + table[1][2] / 2) / loss_base
        if loss_base
        else 0.0
    )
    effective = strength * (1 - negative) + (1 - strength) * positive
    return Potential(strength, positive, negative, effective, total)

"""The stable contract between the shell and every strategy layer.

A strategy reads a ``View`` and answers with a ``Decision`` or ``None``. It
never sees a ``GameState`` and never builds a ``Response``, so it cannot make
an illegal move: the action layer turns any decision into a legal one.

See shell/README.md for the rules a strategy must follow.
"""


import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

PREFLOP, FLOP, TURN, RIVER = 0, 1, 2, 3

FOLD = "fold"  # give up; becomes a check when checking is free
PASSIVE = "passive"  # check, or call what is owed
RAISE = "raise"  # bet or raise to a street total, clamped to the legal range
ALL_IN = "all_in"  # the largest legal raise; a call when no raise is legal

# How a hand ended, as far as the bot can ever know (docs/ENGINE.md section 1).
I_FOLDED = "i_folded"
OPP_FOLDED = "opp_folded"  # my live bet got no further turn: certain
SHOWDOWN = "showdown"  # the betting closed; the cards are never seen
UNKNOWN = "unknown"  # my river or all-in bet: fold and call look the same


@dataclass(frozen=True, slots=True)
class Decision:
    """What a strategy wants to do. Build one with the helpers below."""

    kind: str
    total: int | None = None  # street total, for RAISE only
    tag: str = ""  # free label for logs and tests


def fold(tag: str = "") -> Decision:
    return Decision(FOLD, None, tag)


def passive(tag: str = "") -> Decision:
    """Checks, or calls whatever is owed."""
    return Decision(PASSIVE, None, tag)


def raise_to(total: int, tag: str = "") -> Decision:
    """Bets or raises to ``total`` chips on this street."""
    return Decision(RAISE, total, tag)


def all_in(tag: str = "") -> Decision:
    return Decision(ALL_IN, None, tag)


@dataclass(frozen=True, slots=True)
class Event:
    """One action of the current hand."""

    mine: bool
    action: str  # post_small_blind, post_big_blind, fold, check, call, bet, raise_to
    amount: int | None  # call: chips added; bet, raise_to: street total
    street: int


@dataclass(frozen=True, slots=True)
class View:
    """Everything a strategy may read for one decision. All chips are ints."""

    hole: tuple[int, int]  # core.cards codes
    board: tuple[int, ...]
    street: int  # PREFLOP, FLOP, TURN or RIVER
    seat: int  # 0 is button and small blind for the whole half
    is_button: bool
    pot: int  # everything in the middle, this street's bets included
    to_call: int
    my_stack: int  # chips behind
    opp_stack: int
    my_bet: int  # chips in on this street
    opp_bet: int
    min_raise_to: int  # street totals; 0 when no raise is legal
    max_raise_to: int
    can_raise: bool  # a bet or a raise is legal
    can_check: bool
    small_blind: int
    big_blind: int
    history: tuple[Event, ...]  # this hand, blinds included
    hand_index: int  # hands I have been dealt into this half, from 1
    new_hand: bool  # first decision of this hand
    budget_s: float  # think time this decision may use; 0.0 means none
    deadline: float  # time.perf_counter() value not to pass
    rng: random.Random  # seeded per decision from the visible state; use this

    @property
    def pot_odds(self) -> float:
        """Share of the final pot I must pay; the equity a call needs."""
        return self.to_call / (self.pot + self.to_call) if self.to_call else 0.0

    @property
    def opp_all_in(self) -> bool:
        return self.opp_stack == 0

    def pot_fraction_total(self, fraction: float) -> int:
        """The street total of a raise worth ``fraction`` of the pot after I call.

        ``pot_fraction_total(1.0)`` is the classic pot-sized bet or raise.
        """
        return self.my_bet + self.to_call + int((self.pot + self.to_call) * fraction)


@dataclass(frozen=True, slots=True)
class HandSummary:
    """A finished hand, delivered at the start of the next one I am dealt.

    The last hand of a half has no summary, and hands where the opponent folds
    my big blind are never seen at all.
    """

    hand_index: int
    seat: int
    hole: tuple[int, int]
    board: tuple[int, ...]  # the board at my last turn, not the final one
    history: tuple[Event, ...]  # up to my last turn, without my last action
    my_last: Decision  # what I actually played last, after legalising
    my_last_street: int
    my_last_all_in: bool
    pot_at_last: int  # pot before my last action
    ending: str  # I_FOLDED, OPP_FOLDED, SHOWDOWN or UNKNOWN


@runtime_checkable
class Strategy(Protocol):
    """One layer of the bot.

    ``decide`` returns ``None`` to pass the decision to the next layer. It may
    raise: the shell catches everything and plays a safe move.
    """

    def on_half_start(self, rng: random.Random) -> None:
        """Called once per process, at the first decision of a half."""

    def on_hand_end(self, summary: HandSummary) -> None:
        """Called once per finished hand, before the next hand's ``observe``."""

    def observe(self, view: View) -> None:
        """Called at every decision, for every layer, before any ``decide``."""

    def decide(self, view: View) -> Decision | None:
        """Returns the move, or ``None`` for no opinion."""


class BaseStrategy:
    """No-op hooks, so a layer only writes what it needs."""

    def on_half_start(self, rng: random.Random) -> None:
        pass

    def on_hand_end(self, summary: HandSummary) -> None:
        pass

    def observe(self, view: View) -> None:
        pass

    def decide(self, view: View) -> Decision | None:
        return None


class Stack(BaseStrategy):
    """Layers in priority order: the first one with an opinion plays.

    Every layer gets every hook, even when an earlier layer decides. One layer
    failing costs only that layer's say on this decision.
    """

    def __init__(self, layers: Sequence[Any]) -> None:
        self.layers = tuple(layers)
        self.errors = 0

    def on_half_start(self, rng: random.Random) -> None:
        for layer in self.layers:
            try:
                layer.on_half_start(rng)
            except Exception:
                self.errors += 1

    def on_hand_end(self, summary: HandSummary) -> None:
        for layer in self.layers:
            try:
                layer.on_hand_end(summary)
            except Exception:
                self.errors += 1

    def observe(self, view: View) -> None:
        for layer in self.layers:
            try:
                layer.observe(view)
            except Exception:
                self.errors += 1

    def decide(self, view: View) -> Decision | None:
        for layer in self.layers:
            try:
                decision = layer.decide(view)
            except Exception:
                self.errors += 1
                continue
            if decision is not None:
                return decision
        return None
