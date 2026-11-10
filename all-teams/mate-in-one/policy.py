# Built by tools/build_flat.py from bot/: policy_v2.features, policy_v2.knobs, policy_v2.reads, policy_v2.table, policy_v2.ranges, policy_v2.postflop, policy_v2.preflop_play, policy_v2.policy. Edit the source, not this.

from __future__ import annotations

"""What my two cards do on this board: draws and blockers. No simulation."""


from typing import NamedTuple

STRAIGHTS = tuple(0b11111 << low for low in range(9)) + (0b1000000001111,)


class Features(NamedTuple):
    outs: float  # clean-ish cards that make a strong hand next street
    blockers: float  # 1.0 is neutral; above 1 my cards block their strong hands


def rank_mask(cards) -> int:
    mask = 0
    for code in cards:
        mask |= 1 << (code >> 2)
    return mask


def straight_outs(hole: tuple[int, int], board: tuple[int, ...]) -> int:
    """Ranks that complete a straight using at least one of my cards."""
    board_mask = rank_mask(board)
    mask = board_mask | rank_mask(hole)
    outs = 0
    for rank in range(13):
        bit = 1 << rank
        if mask & bit:
            continue
        with_mine = mask | bit
        without = board_mask | bit
        made = any(s & with_mine == s for s in STRAIGHTS)
        if made and not any(s & without == s for s in STRAIGHTS):
            already = any(s & mask == s for s in STRAIGHTS)
            outs += not already
    return outs


def flush_cards(hole: tuple[int, int], board: tuple[int, ...]) -> int:
    """The most cards of one suit, counting only suits I hold a card of."""
    best = 0
    for suit in {hole[0] & 3, hole[1] & 3}:
        count = sum(code & 3 == suit for code in (*hole, *board))
        best = max(best, count)
    return best


def overcards(hole: tuple[int, int], board: tuple[int, ...]) -> int:
    top = max(code >> 2 for code in board)
    return sum(code >> 2 > top for code in hole)


def blocker_score(hole: tuple[int, int], board: tuple[int, ...]) -> float:
    """How much my cards cut the hands that would call a bluff."""
    score = 1.0
    suits = [code & 3 for code in board]
    flush_suit = max(range(4), key=suits.count)
    if suits.count(flush_suit) >= 3:
        mine = [code >> 2 for code in hole if code & 3 == flush_suit]
        # A flush I hold would not be a bluff; the top card blocks the most.
        if mine and max(mine) >= 11:
            score += 0.6
    ranks = sorted((code >> 2 for code in board), reverse=True)
    held = {code >> 2 for code in hole}
    if ranks[0] in held:
        score += 0.25
    if len(ranks) > 1 and ranks[1] in held:
        score += 0.1
    if straight_outs(hole, board) or 12 in held:
        score += 0.15
    return score


def features(hole: tuple[int, int], board: tuple[int, ...]) -> Features:
    outs = 0.0
    if len(board) < 5:
        suited = flush_cards(hole, board)
        if suited == 4:
            outs += 9.0
        elif suited == 3 and len(board) == 3:
            outs += 1.0  # backdoor
        outs += 4.0 * min(2, straight_outs(hole, board))
        outs += 1.5 * overcards(hole, board)
    return Features(outs, blocker_score(hole, board))

"""The knobs the exploit layer may turn. Defaults assume no opponent information.

See README.md for what each one does and which way to move it.
"""


from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Knobs:
    """All values are read at every decision, so they can change mid-half."""

    aggression: float = 0.0  # + bets thinner, - needs a stronger hand to bet
    bluff_freq: float = 1.0  # multiplies the balanced bluff rate; 0 = no bluffs
    call_threshold: float = 0.0  # added to the equity a call needs
    mdf_floor: float = 0.5  # share of minimum defence kept whatever the equity says
    range_looseness: float = 0.0  # + their actions say less about their hand
    bluff_belief: float = 0.25  # share of their bets that are not strength
    opp_stickiness: float = 0.8  # 0 they defend by MDF, 1 by pot odds vs a random hand
    size_scale: float = 1.0  # multiplies every postflop bet and raise size
    trap_freq: float = 0.08  # how often a very strong flop hand checks
    read_prior: float = 2.0  # actions of evidence worth as much as the generic model; 0 = no reads
    fold_prior: float = 0.0  # same, for how often they fold to my bets; 0 = no read
    show_prior: float = 0.5  # shown hands worth as much as the assumed range; 0 = off
    show_fold_prior: float = 2.0  # my bets worth as much as the assumed folds; 0 = off
    show_bluff_base: float = 1.0  # bluff rate while no fold has been measured

"""How often this opponent takes each kind of action, read within one half.

An action taken with frequency ``f`` cannot mean more than the top ``f`` of the
range that reached it, whatever its size says. So a bot that bets three times in
four is not showing strength, a bot that calls everything is not showing a made
hand, and a bot that bets once in twenty is showing the nuts. These are action
counts only, which is all a bot can ever see: no showdown reaches it
(docs/ENGINE.md section 1).

What can be counted, and how:

* Their first move on a postflop street when nothing is owed: check or bet. A
  river check behind me ends the hand without a turn of mine, so it comes from
  the hand summary.
* Their reply to my flop or turn bet: fold, call or raise. River replies are left
  out, as a fold and a call look the same there, and so are replies to all-ins.
"""


from core import (
    OPP_FOLDED,
    PASSIVE,
    PREFLOP,
    RAISE,
    RIVER,
    SHOWDOWN,
    Event,
    HandSummary,
)

DECAY = 0.985  # per counted action, so a change of style is noticed in a half
OPEN_PRIOR = 0.30  # used only to shrink rates: the Bayes weight is ``evidence``
RAISE_PRIOR = 0.10
STAY_PRIOR = 0.60


def shrunk(hits: float, total: float, prior: float, strength: float) -> float:
    return (hits + prior * strength) / (total + strength)


class Reads:
    """Decayed counts of one opponent's visible choices. One per half."""

    def __init__(self) -> None:
        self.bets = 0.0  # nothing owed: bets
        self.opens = 0.0  # nothing owed: decisions
        self.raises = 0.0  # facing my bet: raises
        self.calls = 0.0
        self.folds = 0.0
        self.price_sum = 0.0  # price of the bets those replies answered

    @property
    def replies(self) -> float:
        return self.raises + self.calls + self.folds

    def _decay_open(self) -> None:
        self.bets *= DECAY
        self.opens *= DECAY

    def _decay_reply(self) -> None:
        self.raises *= DECAY
        self.calls *= DECAY
        self.folds *= DECAY
        self.price_sum *= DECAY

    def note(self, event: Event, owed: int, pot: int = 0) -> None:
        """One action of theirs, seen in the history of a live hand."""
        if event.street == PREFLOP or event.mine:
            return
        action = event.action
        if owed == 0:
            if action in ("bet", "raise_to", "check"):
                self._decay_open()
                self.opens += 1.0
                self.bets += action != "check"
        elif event.street != RIVER:
            if action == "call":
                self._decay_reply()
                self.calls += 1.0
                self.price_sum += owed / max(1, pot + owed)
            elif action in ("bet", "raise_to"):
                self._decay_reply()
                self.raises += 1.0
                self.price_sum += owed / max(1, pot + owed)

    def finish(self, summary: HandSummary) -> None:
        """Their last reply, which only the way the hand ended can show."""
        last = summary.my_last
        street = summary.my_last_street
        if summary.ending == OPP_FOLDED:
            if last.kind == RAISE and PREFLOP < street < RIVER:
                self._decay_reply()
                self.folds += 1.0
                total = last.total or 0
                self.price_sum += total / max(1, summary.pot_at_last + 2 * total)
        elif (
            summary.ending == SHOWDOWN
            and street == RIVER
            and last.kind == PASSIVE
            and summary.seat == 1
            and _nothing_owed(summary)
        ):
            self._decay_open()
            self.opens += 1.0

    # Rates, shrunk only enough to stay away from 0 and 1

    def bet_rate(self) -> float:
        return shrunk(self.bets, self.opens, OPEN_PRIOR, 2.0)

    def raise_rate(self) -> float:
        return shrunk(self.raises, self.replies, RAISE_PRIOR, 2.0)

    def stay_rate(self) -> float:
        return shrunk(self.raises + self.calls, self.replies, STAY_PRIOR, 2.0)

    def seen_price(self) -> float:
        """The usual price of the bets behind ``stay_rate``."""
        return self.price_sum / self.replies if self.replies > 0.0 else 0.3


def _nothing_owed(summary: HandSummary) -> bool:
    """True if my last action on the river was a check, not a call of a bet."""
    return not any(
        event.street == RIVER and not event.mine and event.action in ("bet", "raise_to")
        for event in summary.history
    )

"""One street's card maths, shared by every decision on that street.

For each combo the opponent could hold, ``StreetTable`` samples runouts and
stores two numbers that do not depend on anyone's range:

* ``ehs``: its share of all live hands beaten by the river, the same quantity a
  pot-odds bot computes as equity against a random hand;
* ``heq``: my showdown equity against exactly that combo.

So a range change on the same street costs one weighted sum, not a new
simulation, and equity against any slice of a range is a prefix-sum lookup.
"""


from collections import Counter
from itertools import accumulate
from operator import add
from time import perf_counter

from core import (
    FLAG_SUIT,
    FLUSH,
    STEP,
    SUIT_BIT,
    SUIT_COUNT,
    SUIT_FLAGS,
    THREE_OF_A_SUIT,
)
from core import ALL_COMBOS

BINS = 64  # likelihood tables are indexed by ehs in this many steps

CARD_A = tuple(combo[0] for combo in ALL_COMBOS)
CARD_B = tuple(combo[1] for combo in ALL_COMBOS)
COMBO_COUNT = len(ALL_COMBOS)


def _runouts(deck: list[int], need: int, samples: int, rng) -> list[tuple[int, ...]]:
    """Returns ``samples`` runouts; disjoint within each shuffle of the deck."""
    if need == 0:
        return [()]
    if need == 1 and samples >= len(deck):
        return [(code,) for code in deck]
    runouts: list[tuple[int, ...]] = []
    shuffled = deck[:]
    while len(runouts) < samples:
        rng.shuffle(shuffled)
        for start in range(0, len(shuffled) - need + 1, need):
            runouts.append(tuple(shuffled[start : start + need]))
    return runouts[:samples]


def _board_state(cards: tuple[int, ...]) -> tuple[int, int, list[int]]:
    state = count = 0
    masks = [0, 0, 0, 0]
    for code in cards:
        state = STEP[state + (code >> 2)]
        count += SUIT_COUNT[code]
        masks[code & 3] |= 1 << (code >> 2)
    return state, count, masks


class StreetTable:
    """Per-combo strength on one board, from my seat (my cards are dead)."""

    __slots__ = ("index", "ehs", "heq", "bins", "order", "hero_ehs", "samples")

    def __init__(
        self,
        hole: tuple[int, int],
        board: tuple[int, ...],
        samples: int,
        rng,
        deadline: float | None = None,
    ):
        dead = {*hole, *board}
        index = [
            i
            for i in range(COMBO_COUNT)
            if CARD_A[i] not in dead and CARD_B[i] not in dead
        ]
        first = [CARD_A[i] for i in index]
        second = [CARD_B[i] for i in index]
        rank_a = [code >> 2 for code in first]
        rank_b = [code >> 2 for code in second]
        by_card: dict[int, list[int]] = {}
        for position, (a, b) in enumerate(zip(first, second)):
            by_card.setdefault(a, []).append(position)
            by_card.setdefault(b, []).append(position)

        deck = [code for code in range(52) if code not in dead]
        runouts = _runouts(deck, 5 - len(board), samples, rng)
        size = len(index)
        beaten = [0.0] * size
        wins = [0.0] * size
        seen = [len(runouts)] * size
        hero_total = 0.0
        h1, h2 = hole
        step = STEP
        done = 0
        for runout in runouts:
            state, count, masks = _board_state((*board, *runout))
            own = step[step[state + (h1 >> 2)] + (h2 >> 2)]
            flags = (count + THREE_OF_A_SUIT) & SUIT_FLAGS
            if flags:
                suit = FLAG_SUIT[flags]
                bits = SUIT_BIT[suit]
                mask = masks[suit]
                flush = FLUSH
                own = flush[mask | bits[h1] | bits[h2]] or own
                values = [
                    flush[mask | bits[a] | bits[b]] or step[step[state + x] + y]
                    for a, b, x, y in zip(first, second, rank_a, rank_b)
                ]
            else:
                values = [step[step[state + x] + y] for x, y in zip(rank_a, rank_b)]
            for code in runout:  # these combos do not exist on this runout
                for position in by_card.get(code, ()):
                    if values[position]:
                        values[position] = 0
                        seen[position] -= 1
            counts = Counter(values)
            blocked = counts.pop(0, 0)
            live = size - blocked
            share = {0: 0.0}
            result = {0: 0.0}
            below = 0
            hero_below = 0.0
            for value in sorted(counts):
                many = counts[value]
                share[value] = (below + many / 2) / live
                below += many
                if value < own:
                    result[value] = 1.0
                    hero_below += many
                elif value == own:
                    result[value] = 0.5
                    hero_below += many / 2
                else:
                    result[value] = 0.0
            hero_total += hero_below / live
            beaten = list(map(add, beaten, map(share.__getitem__, values)))
            wins = list(map(add, wins, map(result.__getitem__, values)))
            done += 1
            if deadline is not None and perf_counter() >= deadline:
                break  # a slow host: a coarser table beats a late move

        if done < len(runouts):
            skipped = len(runouts) - done
            seen = [n - skipped for n in seen]
        self.index = index
        self.ehs = [total / max(1, n) for total, n in zip(beaten, seen)]
        self.heq = [total / max(1, n) for total, n in zip(wins, seen)]
        top = BINS - 1
        self.bins = [min(top, int(value * BINS)) for value in self.ehs]
        self.order = sorted(range(size), key=self.ehs.__getitem__)
        self.hero_ehs = hero_total / done
        self.samples = done


class Snapshot:
    """A range laid out in ehs order, for prefix-sum questions about slices."""

    __slots__ = ("ehs", "mass", "value", "total", "equity")

    def __init__(self, table: StreetTable, weights: list[float]):
        order = table.order
        index = table.index
        ranked = [weights[index[j]] for j in order]
        self.ehs = [table.ehs[j] for j in order]
        heq = table.heq
        self.mass = list(accumulate(ranked, initial=0.0))
        self.value = list(
            accumulate((w * heq[j] for w, j in zip(ranked, order)), initial=0.0)
        )
        self.total = self.mass[-1]
        self.equity = self.value[-1] / self.total if self.total > 0.0 else 0.5

"""Both players' ranges through a hand, as weights over all 1326 combos.

Every action multiplies a range by the chance a hand of that strength would
have taken it (a Bayes update with a simple parametric action model), so a
range is never emptied and a bluff is never ruled out. Card removal needs no
step of its own: ``StreetTable`` only lists live combos.

The model is deliberately the one simple bots really use: strength against a
random hand compared with a threshold that rises with the size of the action.
"""


from bisect import bisect_left
from math import exp

from opp import KINDS, OppModel, action_size
from policy_v1 import COMBO_PLACE
from core import PREFLOP, Event


AGGRESSIVE = ("bet", "raise_to")
BLINDS = ("post_small_blind", "post_big_blind")

SOFTNESS = 0.07  # postflop width of the step from "no" to "yes", in ehs
PREFLOP_SOFTNESS = 0.05  # same, in hand percentile
PREFLOP_FLOOR = 0.02  # hands outside any preflop range keep this weight
TRAP_SHARE = 0.25  # very strong hands that still check or call
CALL_FLOOR = 0.03
RAISE_EXTRA = 0.08  # a raise needs this much more strength than a bet
CHECK_TAU = 0.62  # ehs where half of hands would have bet
MY_BLUFF_SHARE = 0.25  # floor on my own betting range, for balance


def sigmoid(value: float) -> float:
    if value < -30.0:
        return 0.0
    if value > 30.0:
        return 1.0
    return 1.0 / (1.0 + exp(-value))


def bet_tau(fraction: float) -> float:
    """The strength behind a bet of ``fraction`` of the pot: rises with size."""
    return min(0.9, 0.5 + 0.3 * max(0.0, fraction) ** (1.0 / 3.0))


def _bins(function) -> list[float]:
    return [function((b + 0.5) / BINS) for b in range(BINS)]


def value_tau(fraction: float, raised: bool) -> float:
    return bet_tau(fraction) + (RAISE_EXTRA if raised else 0.0)


def bet_likelihood(tau: float, soft: float):
    """The chance a hand bets for value; bluffs are added by ``with_bluffs``."""
    return _bins(lambda e: sigmoid((e - tau) / soft))


def check_likelihood(soft: float, trap: float = TRAP_SHARE, tau: float = CHECK_TAU):
    return _bins(lambda e: 1.0 - (1.0 - trap) * sigmoid((e - tau) / soft))


def call_likelihood(tau: float, soft: float):
    def chance(e: float) -> float:
        stays = CALL_FLOOR + (1.0 - CALL_FLOOR) * sigmoid((e - tau) / soft)
        return stays * (1.0 - (1.0 - TRAP_SHARE) * sigmoid((e - 0.92) / 0.03))

    return _bins(chance)


def with_bluffs(
    value: list[float], weights: list[float], table: StreetTable, share: float
) -> list[float]:
    """Adds a flat chance to bet so that ``share`` of all bets are not value.

    Flat in strength, and scaled to the value mass of this range, so a bigger
    bet keeps meaning a stronger range instead of a larger pile of air.
    """
    total = betting = 0.0
    bins = table.bins
    for position, combo in enumerate(table.index):
        weight = weights[combo]
        total += weight
        betting += weight * value[bins[position]]
    floor = share / (1.0 - share) * betting / total if total > 0.0 else share
    return [chance + floor for chance in value]


def top_tau(
    weights: list[float], table: StreetTable, share: float
) -> float:
    """The strength above which ``share`` of this range lies."""
    mass = [0.0] * BINS
    bins = table.bins
    for position, combo in enumerate(table.index):
        mass[bins[position]] += weights[combo]
    left = share * sum(mass)
    for place in range(BINS - 1, -1, -1):
        left -= mass[place]
        if left <= 0.0:
            return (place + 0.5) / BINS
    return 0.0


def raise_width(total: int, big_blind: int, raises_before: int) -> float:
    """Share of hands that make this preflop raise."""
    size = total / big_blind
    if size <= 2.6:
        width = 0.65
    elif size <= 3.6:
        width = 0.5
    elif size <= 5.5:
        width = 0.35
    elif size <= 12:
        width = 0.22
    elif size <= 30:
        width = 0.12
    else:
        width = 0.08
    return width * 0.7**raises_before


def apply_width(weights: list[float], width: float, top_cut: float, floor: float):
    """Keeps hands inside the top ``width``; ``top_cut`` of them are discounted."""
    soft = PREFLOP_SOFTNESS
    for i in range(COMBO_COUNT):
        place = COMBO_PLACE[i]
        keep = floor + (1.0 - floor) * sigmoid((width - place) / soft)
        if top_cut and place < top_cut:
            keep *= 0.4
        weights[i] *= keep


class HandRanges:
    """Replays a hand's events into both ranges, one new event at a time."""

    def __init__(self, hole: tuple[int, int]) -> None:
        self.hole = hole
        self.theirs = [1.0] * COMBO_COUNT
        self.mine = [1.0] * COMBO_COUNT
        self.tables: dict[int, StreetTable] = {}
        self.position = 0
        self.pot = 0
        self.street = PREFLOP
        self.bets = {True: 0, False: 0}  # by Event.mine, on this street
        self.raises = 0  # preflop raises so far
        self.big_blind = 20  # replaced by the post_big_blind event of the hand
        self.reads: Reads | None = None
        self.opp: OppModel | None = None  # shown hands; None plays the generic model

    def update(
        self, history: tuple[Event, ...], knobs: Knobs, reads: Reads | None = None
    ) -> None:
        self.reads = reads
        for event in history[self.position :]:
            self._apply(event, knobs)
        self.position = len(history)

    def _calibrated(
        self,
        tau: float,
        share: float,
        evidence: float,
        weights: list[float],
        table: StreetTable,
        knobs: Knobs,
    ) -> float:
        """Moves a generic threshold towards what this bot's frequency implies."""
        prior = knobs.read_prior
        if prior <= 0.0 or evidence <= 0.0:
            return tau
        trust = evidence / (evidence + prior)
        return (1.0 - trust) * tau + trust * top_tau(weights, table, share)

    def _apply(self, event: Event, knobs: Knobs) -> None:
        if event.street != self.street:
            self.street = event.street
            self.bets = {True: 0, False: 0}
        mine = event.mine
        action = event.action
        if action == "post_big_blind" and event.amount:
            self.big_blind = event.amount
        before = self.bets[mine]
        other = self.bets[not mine]
        owed = max(0, other - before)
        if action in BLINDS or action == "call":
            added = event.amount or 0
        elif action in AGGRESSIVE:
            added = event.amount - before
        else:
            added = 0
        if action not in BLINDS:
            self._update_range(event, knobs, owed, other)
            if self.reads is not None:
                self.reads.note(event, owed, self.pot)
        self.pot += added
        self.bets[mine] = before + added
        if event.street == PREFLOP and action in AGGRESSIVE:
            self.raises += 1

    def _update_range(self, event: Event, knobs: Knobs, owed: int, other: int) -> None:
        weights = self.mine if event.mine else self.theirs
        loose = 0.0 if event.mine else max(-0.5, knobs.range_looseness)
        action = event.action
        if action == "fold":
            return
        opp = None if event.mine else self.opp
        kind = KINDS.get(action)
        fraction = action_size(action, event.amount, other, owed, self.pot)
        if event.street == PREFLOP:
            before = weights[:] if opp is not None else None
            self._preflop(weights, event, owed, loose)
            if opp is not None:
                opp.pull_preflop(before, weights, kind, fraction, knobs.show_prior)
            return
        table = self.tables.get(event.street)
        if table is None:  # restarted mid-hand: that street's cards are gone
            return
        soft = SOFTNESS * (1.0 + loose)
        reads = None if event.mine else self.reads
        if action == "check":
            tau = CHECK_TAU
            if reads is not None:
                tau = self._calibrated(
                    tau, reads.bet_rate(), reads.opens, weights, table, knobs
                )
            likelihood = check_likelihood(
                soft, min(1.0, TRAP_SHARE * (1.0 + loose)), tau
            )
        elif action == "call":
            tau = owed / max(1, self.pot + owed) + 0.02
            if reads is not None:
                tau = self._calibrated(
                    tau, reads.stay_rate(), reads.replies, weights, table, knobs
                )
            likelihood = call_likelihood(tau, soft)
        else:
            base = self.pot + owed
            fraction = (event.amount - other) / max(1, base)
            if event.mine:
                bluffs = MY_BLUFF_SHARE
            else:
                bluffs = min(0.9, knobs.bluff_belief * (1.0 + loose))
            raised = owed > 0
            tau = value_tau(fraction, raised)
            if reads is not None:
                share, evidence = (
                    (reads.raise_rate(), reads.replies)
                    if raised
                    else (reads.bet_rate(), reads.opens)
                )
                tau = self._calibrated(tau, share, evidence, weights, table, knobs)
            likelihood = with_bluffs(
                bet_likelihood(tau, soft), weights, table, bluffs
            )
        if opp is not None:
            likelihood = opp.pull(
                likelihood, weights, table, event.street, kind, fraction,
                knobs.show_prior,
            )  # fmt: skip
        bins = table.bins
        for position, combo in enumerate(table.index):
            weights[combo] *= likelihood[bins[position]]

    def _preflop(self, weights: list[float], event: Event, owed: int, loose: float):
        floor = PREFLOP_FLOOR * (1.0 + 2.0 * max(0.0, loose))
        action = event.action
        if action in AGGRESSIVE:
            width = raise_width(event.amount, self.big_blind, self.raises)
            apply_width(weights, min(1.0, width * (1.0 + loose)), 0.0, floor)
        elif action == "check":  # the big blind did not raise a limp
            apply_width(weights, 1.5, 0.12, floor)
        elif action == "call":
            price = owed / max(1, self.pot + owed)
            if self.raises == 0:  # a limp
                apply_width(weights, min(1.5, 0.85 * (1.0 + loose)), 0.1, floor)
            else:  # wider when the price is good, cut at the top for no re-raise
                width = 1.15 - 1.8 * price
                width = max(0.1, width * 0.8**self.raises * (1.0 + loose))
                apply_width(weights, width, 0.04, floor)


def quantile(snapshot: Snapshot, share: float) -> float:
    """The ehs below which ``share`` of a range's mass lies."""
    mass = snapshot.mass
    position = bisect_left(mass, share * snapshot.total)
    ehs = snapshot.ehs
    return ehs[min(len(ehs) - 1, max(0, position - 1))]


def above(snapshot: Snapshot, tau: float) -> tuple[float, float]:
    """Returns (share of the range at or above ``tau``, my equity against it)."""
    start = bisect_left(snapshot.ehs, tau)
    mass = snapshot.total - snapshot.mass[start]
    if mass <= 1e-12 or snapshot.total <= 0.0:
        return 0.0, 0.5
    return mass / snapshot.total, (snapshot.value[-1] - snapshot.value[start]) / mass


def share_below(snapshot: Snapshot, tau: float) -> float:
    if snapshot.total <= 0.0:
        return 0.5
    return snapshot.mass[bisect_left(snapshot.ehs, tau)] / snapshot.total

"""Postflop decisions from equity against the tracked range.

Value: for each size, model which part of their range continues and take the
size with the best expected value; bet when it beats checking and I am ahead of
what continues. Bluffs: sized and counted against my own range, so a caller is
close to indifferent (the classic bluffs / (value + bluffs) = b / (pot + 2b)),
filled first by draws, then by blockers. Defence: pot odds against their range,
with a floor at a share of minimum defence so that pure aggression cannot print.
"""


from typing import NamedTuple

from opp import OppModel
from core import RIVER, Decision, View, all_in, fold, passive, raise_to


BET_SIZES = {1: (0.33, 0.66, 1.0), 2: (0.5, 0.75, 1.2), 3: (0.5, 0.8, 1.3)}
BLUFF_SIZES = {1: (0.5, 0.75), 2: (0.66, 1.0), 3: (0.66, 1.0)}
RAISE_SIZES = (0.6, 1.0)  # of the pot after my call
SHOVE_SPR = 4.0  # all-in joins the menu when the stack is under this many pots
COMMIT_SHARE = 0.55  # a bet this close to all-in might as well be all-in

VALUE_CUT = {1: 0.54, 2: 0.55, 3: 0.53}  # equity against what continues
RAISE_CUT = {1: 0.62, 2: 0.64, 3: 0.66}
TRAP_EQUITY = 0.8
REALISE_IP = {1: 1.0, 2: 1.0, 3: 1.0}
REALISE_OOP = {1: 0.9, 2: 0.94, 3: 1.0}
CALL_MARGIN = {1: 0.0, 2: 0.01, 3: 0.0}
DRAW_ODDS_PER_OUT = 0.004  # implied odds a draw adds to its equity
THEY_RAISE = 0.12  # share of what continues that raises my bet
RAISED_FOLD_EQUITY = 0.35  # against that slice, under this I give up

BLUFF_MAX_EQUITY = {1: 0.42, 2: 0.38, 3: 0.3}  # hands with showdown value check
BLUFF_STREET_SCALE = {1: 1.6, 2: 1.3, 3: 1.0}  # draws keep equity when called
STRONG_DRAW_OUTS = 8.0
STRONG_DRAW_SHARE = 3.0  # bluff rate of a strong draw over the base rate
WEAK_DRAW_SHARE = 1.0
AIR_SHARE = {1: 0.3, 2: 0.4, 3: 1.0}
WEAK_DRAW_OUTS = 3.0
RAISE_BLUFF_SHARE = 0.35  # semi-bluff raises against semi-bluff bets


class Spot(NamedTuple):
    """Everything one postflop decision reads."""

    view: View
    snap: Snapshot  # their range
    mine: Snapshot  # my range, for balance
    feats: Features
    knobs: Knobs
    hero_ehs: float
    reads: Reads | None = None
    opp: OppModel | None = None


class BetValue(NamedTuple):
    total: int
    ev: float
    equity: float  # against what continues
    fold_share: float


def size_total(view: View, fraction: float, knobs: Knobs) -> int:
    total = view.pot_fraction_total(fraction * knobs.size_scale)
    return max(view.min_raise_to, min(total, view.max_raise_to))


def as_bet(view: View, total: int, tag: str) -> Decision:
    if total >= COMMIT_SHARE * view.max_raise_to:
        return all_in(tag)
    return raise_to(total, tag)


def continue_tau(snap: Snapshot, price: float, knobs: Knobs) -> float:
    """The strength a generic opponent needs to continue at ``price``."""
    keep = 1.0 - price / (1.0 - price) if price < 0.5 else 0.0  # minimum defence
    stick = knobs.opp_stickiness
    return stick * (price + 0.02) + (1.0 - stick) * quantile(snap, 1.0 - keep)


def evaluate_bet(
    view: View,
    snap: Snapshot,
    total: int,
    knobs: Knobs,
    reads: Reads | None = None,
    opp: OppModel | None = None,
) -> BetValue:
    """Expected chips from betting to ``total``, relative to folding now.

    The generic response model is shifted by what this bot has really done: if
    it stays more than the model says at the prices it has been shown, the whole
    curve moves down, so sizes keep their order but the level is theirs.
    """
    pot = view.pot
    mine = total - view.my_bet
    theirs = total - view.opp_bet
    base = pot + view.to_call
    price = theirs / (pot + mine + theirs)
    keep = base / (base + max(1, theirs))  # minimum defence
    stick = knobs.opp_stickiness
    tau = stick * (price + 0.02) + (1.0 - stick) * quantile(snap, 1.0 - keep)
    if reads is not None and knobs.fold_prior > 0.0 and reads.replies > 0.0:
        trust = reads.replies / (reads.replies + knobs.fold_prior)
        seen = quantile(snap, 1.0 - reads.stay_rate())
        tau += trust * min(0.0, seen - continue_tau(snap, reads.seen_price(), knobs))
    share, equity = above(snap, tau)
    if opp is not None:
        seen = opp.fold_rate(
            view.street, theirs / max(1, base), 1.0 - share, knobs.show_fold_prior
        )
        if seen != 1.0 - share:
            share, equity = above(snap, quantile(snap, seen))
    fold_share = 1.0 - share
    final = pot + mine + theirs
    if total >= view.max_raise_to or share <= 0.0:
        return BetValue(total, fold_share * pot + share * (equity * final - mine),
                        equity, fold_share)  # fmt: skip
    top_tau = quantile(snap, 1.0 - share * THEY_RAISE)
    top_share, top_equity = above(snap, top_tau)
    rest = share - top_share
    if rest <= 1e-9:
        rest_equity = equity
    else:
        rest_equity = (equity * share - top_equity * top_share) / rest
    if top_equity < RAISED_FOLD_EQUITY:
        raised = -mine
    else:
        raised = top_equity * final * 1.3 - mine * 1.3
    ev = fold_share * pot + rest * (rest_equity * final - mine) + top_share * raised
    return BetValue(total, ev, equity, fold_share)


def bet_menu(view: View, knobs: Knobs, opp: OppModel | None = None) -> list[int]:
    sizes = BET_SIZES[view.street]
    if opp is not None:
        sizes = (*sizes, *opp.overbets(view.street, knobs.show_fold_prior))
    totals = {size_total(view, f, knobs) for f in sizes}
    if view.street == RIVER or view.my_stack <= SHOVE_SPR * view.pot:
        totals.add(view.max_raise_to)
    return sorted(totals)


def balanced_bluff_rate(spot: Spot, total: int) -> float:
    """The chance a hand from the bluffing region bets, from my own range."""
    view, snap, mine = spot.view, spot.snap, spot.mine
    bet = total - view.my_bet - view.to_call
    pot = view.pot + view.to_call
    alpha = bet / (pot + 2.0 * bet) * BLUFF_STREET_SCALE[view.street]
    alpha = min(0.6, alpha)
    cut = VALUE_CUT[view.street]
    value_share = above(mine, quantile(snap, cut))[0]
    bluff_share = share_below(mine, quantile(snap, BLUFF_MAX_EQUITY[view.street]))
    if bluff_share <= 0.0:
        return 0.0
    return alpha / (1.0 - alpha) * value_share / bluff_share


def fold_gate(spot: Spot, total: int, check_ev: float) -> float:
    """Scales a bluff by how close the modelled folds come to paying for it.

    Bluff where their range is weak, not at a fixed rate on every board: 1 when
    the bluff is as good as checking under the response model, falling with the
    square of the shortfall in folds.
    """
    view = spot.view
    result = evaluate_bet(view, spot.snap, total, spot.knobs, spot.reads, spot.opp)
    if result.ev >= check_ev:
        return 1.0
    mine = total - view.my_bet
    final = view.pot + mine + total - view.opp_bet
    called = result.equity * final - mine
    gap = view.pot - called
    needed = (check_ev - called) / gap if gap > 0.0 else 1.0
    if needed <= 0.0:
        return 1.0
    return min(1.0, result.fold_share / needed) ** 2


def measured_gate(spot: Spot, total: int) -> float:
    """1 unless shown folds say this bluff does or does not pay."""
    if spot.opp is None:
        return 1.0
    view, knobs = spot.view, spot.knobs
    fraction = (total - view.opp_bet) / max(1, view.pot + view.to_call)
    return spot.opp.bluff_gate(
        view.street, fraction, knobs.show_fold_prior, knobs.show_bluff_base
    )


def bluff_weight(view: View, equity: float, feats: Features) -> float:
    """How much of the base bluff rate this hand takes; 0 means never."""
    street = view.street
    if equity > BLUFF_MAX_EQUITY[street]:
        return 0.0
    if street == RIVER:
        return feats.blockers
    if feats.outs >= STRONG_DRAW_OUTS:
        return STRONG_DRAW_SHARE
    if feats.outs >= WEAK_DRAW_OUTS:
        return WEAK_DRAW_SHARE * feats.blockers
    return AIR_SHARE[street] * feats.blockers


def open_street(spot: Spot) -> Decision:
    """Nobody has bet: check, value bet or bluff."""
    view, snap, feats, knobs = spot.view, spot.snap, spot.feats, spot.knobs
    if not view.can_raise:
        return passive("check")
    street = view.street
    equity = snap.equity
    realise = (REALISE_IP if view.is_button else REALISE_OOP)[street]
    check_ev = equity * view.pot * realise
    best = max(
        (
            evaluate_bet(view, snap, total, knobs, spot.reads, spot.opp)
            for total in bet_menu(view, knobs, spot.opp)
        ),
        key=lambda item: item.ev,
    )
    cut = VALUE_CUT[street] - 0.05 * knobs.aggression
    if best.equity >= cut and best.ev > check_ev:
        if (
            street == 1
            and equity >= TRAP_EQUITY
            and view.rng.random() < knobs.trap_freq
        ):
            return passive("trap")
        return as_bet(view, best.total, "value")
    weight = bluff_weight(view, equity, feats)
    if weight > 0.0 and knobs.bluff_freq > 0.0:
        total = size_total(view, view.rng.choice(BLUFF_SIZES[street]), knobs)
        rate = balanced_bluff_rate(spot, total) * fold_gate(spot, total, check_ev)
        rate *= measured_gate(spot, total)
        if (
            total < COMMIT_SHARE * view.max_raise_to
            and view.rng.random() < rate * weight * knobs.bluff_freq
        ):
            tag = "bluff" if feats.outs < WEAK_DRAW_OUTS else "semi-bluff"
            return raise_to(total, tag)
    return passive("check")


def facing_bet(spot: Spot) -> Decision:
    """They bet: fold, call, or raise."""
    view, snap, feats, knobs = spot.view, spot.snap, spot.feats, spot.knobs
    street = view.street
    equity = snap.equity
    price = view.pot_odds
    realise = (REALISE_IP if view.is_button else REALISE_OOP)[street]
    final_price = view.to_call >= view.my_stack or view.opp_all_in or street == RIVER
    effective = equity if final_price else equity * realise
    if not final_price:
        effective += DRAW_ODDS_PER_OUT * feats.outs
    needed = (
        price + knobs.call_threshold + (0.0 if final_price else CALL_MARGIN[street])
    )
    call_ev = effective * (view.pot + view.to_call) - view.to_call

    if view.can_raise and not view.opp_all_in:
        raised = _try_raise(spot, max(0.0, call_ev))
        if raised is not None:
            return raised
    if effective >= needed:
        return passive("call")
    floor = knobs.mdf_floor
    if spot.opp is not None:  # shown hands replace the guess this floor insures
        bet = view.to_call / max(1, view.pot - view.to_call)
        kind = "raise" if view.my_bet else "bet"
        floor *= 1.0 - spot.opp.trust(street, kind, bet, knobs.show_prior)
    keep = (view.pot - view.to_call) / max(1, view.pot) * floor
    my_place = share_below(spot.mine, spot.hero_ehs)
    if my_place >= 1.0 - keep and equity >= 0.5 * price:
        return passive("mdf call")
    return fold("fold")


def _try_raise(spot: Spot, call_ev: float) -> Decision | None:
    view, snap, feats, knobs = spot.view, spot.snap, spot.feats, spot.knobs
    street = view.street
    base = view.pot + view.to_call
    totals = {
        min(view.max_raise_to, max(view.min_raise_to,
            view.opp_bet + int(f * knobs.size_scale * base)))
        for f in RAISE_SIZES
    }  # fmt: skip
    if view.my_stack <= SHOVE_SPR * base:
        totals.add(view.max_raise_to)
    best = max(
        (
            evaluate_bet(view, snap, total, knobs, spot.reads, spot.opp)
            for total in sorted(totals)
        ),
        key=lambda item: item.ev,
    )
    cut = RAISE_CUT[street] - 0.05 * knobs.aggression
    if best.equity >= cut and best.ev > call_ev:
        return as_bet(view, best.total, "value raise")
    if street < RIVER and feats.outs >= STRONG_DRAW_OUTS and knobs.bluff_freq > 0.0:
        total = min(best.total, min(totals))
        rate = balanced_bluff_rate(spot, total) * measured_gate(spot, total)
        if view.rng.random() < rate * RAISE_BLUFF_SHARE * knobs.bluff_freq:
            return as_bet(view, total, "semi-bluff raise")
    return None

"""Preflop: the solved table when it is there, else policy v1's rules."""


from policy_v1 import PolicyV1
from core import PREFLOP, Decision, View, all_in, fold, passive, raise_to


table_decide = None  # the solved 100 bb table is not in this build
hand_class = None


def line_of(view: View) -> tuple[tuple[int, ...], bool]:
    """Returns the raise totals so far and whether the small blind limped."""
    raises: list[int] = []
    limped = False
    for event in view.history:
        if event.street != PREFLOP or event.action in BLINDS:
            continue
        if event.action in AGGRESSIVE:
            raises.append(event.amount)
        elif event.action == "call" and not raises:
            limped = True
    return tuple(raises), limped


def from_table(view: View) -> Decision | None:
    if table_decide is None:
        return None
    raises, limped = line_of(view)
    answer = table_decide(
        hand_class(*view.hole),
        raises,
        limped,
        view.rng.random(),
        view.min_raise_to,
        view.max_raise_to,
    )
    if answer is None:
        return None
    kind, chips = answer
    if kind == "fold":
        return fold("pf table")
    if kind == "call" or not view.can_raise:
        return passive("pf table")
    if chips >= view.max_raise_to:
        return all_in("pf table")
    return raise_to(chips, "pf table")


def preflop_decision(
    view: View, fallback: PolicyV1, use_table: bool = True
) -> Decision:
    try:
        decision = from_table(view) if use_table else None
    except (KeyError, ValueError, IndexError, AttributeError):
        decision = None
    if decision is not None:
        return decision
    answer = fallback.decide(view)
    return Decision(answer.kind, answer.total, f"pf v1 {answer.tag}")

"""PolicyV2: the default strategy. Preflop table, then range-based postflop."""


from collections import Counter

from core import card
from opp import OppModel, shown_cards
from policy_v1 import PolicyV1
from core import (
    FOLD,
    PREFLOP,
    RAISE,
    BaseStrategy,
    Decision,
    HandSummary,
    View,
    fold,
    passive,
)


SAMPLES = {1: 48, 2: 46, 3: 1}  # runouts per street table
SAMPLES_NO_BUDGET = {1: 4, 2: 4, 3: 1}
TIGHT_BUDGET_S = 0.01


class PolicyV2(BaseStrategy):
    """A complete strategy: it always has an opinion.

    ``knobs`` may be replaced at any time, by the exploit layer for example.
    ``fired`` counts every decision tag, to prove which features are live.
    ``use_table=False`` plays policy v1's preflop even when the table is built.
    """

    def __init__(self, knobs: Knobs | None = None, use_table: bool = False) -> None:
        self.knobs = knobs or Knobs()
        self.use_table = use_table
        self.preflop_fallback = PolicyV1()
        self.hand: HandRanges | None = None
        self.reads = Reads()
        self.opp = OppModel()
        self.fired: Counter = Counter()

    def on_half_start(self, rng) -> None:
        self.preflop_fallback.on_half_start(rng)
        self.hand = None
        self.reads = Reads()
        self.opp = OppModel()
        self.fired = Counter()

    def on_end_game(self, events, actions, state, me: int) -> None:
        """Called by the host after every hand with this hand's Events and what
        end_game received. It is what the opponent model learns from."""
        teams = state.teams
        self.opp.for_team(teams[1 - me] if len(teams) == 2 else None)
        board = tuple(card(str(code)) for code in state.board)
        self.opp.learn(events, board, shown_cards(actions, me))

    def on_hand_end(self, summary: HandSummary) -> None:
        self.preflop_fallback.on_hand_end(summary)
        self.reads.finish(summary)

    def decide(self, view: View) -> Decision:
        hand = self._hand_for(view)
        if view.street == PREFLOP:
            hand.update(view.history, self.knobs, self.reads)
            decision = preflop_decision(view, self.preflop_fallback, self.use_table)
            decision = self._shown_preflop(view, hand, decision)
        else:
            decision = self._postflop(view, hand)
        self.fired[decision.tag] += 1
        return decision

    def _shown_preflop(self, view, hand, decision: Decision) -> Decision:
        """Facing a raise: their shown range may overrule folding or calling."""
        if hand.opp is None or view.opp_bet <= view.big_blind or decision.kind == RAISE:
            return decision
        final = view.opp_all_in or view.to_call >= view.my_stack
        fraction = view.to_call / max(1, view.pot - view.to_call)
        prior = self.knobs.show_prior
        verdict = hand.opp.preflop_call(
            view.hole, hand.theirs, "raise", fraction, view.pot_odds, final, prior
        )
        if verdict is False:  # only final prices say False
            # The assumed range is tight, so it alone must not fold a default call.
            verdict = hand.opp.preflop_call(
                view.hole, self._shown_range(hand, fraction), "raise", fraction,
                view.pot_odds, final, prior,
            )  # fmt: skip
        if verdict is True and decision.kind == FOLD:
            return passive("pf shown call")
        if verdict is False and decision.kind != FOLD:
            return fold("pf shown fold")
        return decision

    def _shown_range(self, hand, fraction: float) -> list[float]:
        """Any hand, moved towards what they showed for this action. All-ins always
        show when called, so this is unbiased, and with little evidence it stays wide
        instead of falling back on the assumed range."""
        weights = [1.0] * len(hand.theirs)
        hand.opp.pull_preflop(weights[:], weights, "raise", fraction, self.knobs.show_prior)
        return weights

    def _opp(self) -> OppModel | None:
        knobs = self.knobs
        return self.opp if knobs.show_prior or knobs.show_fold_prior else None

    def _hand_for(self, view: View) -> HandRanges:
        hand = self.hand
        if (
            hand is None
            or view.new_hand
            or hand.hole != view.hole
            or hand.position > len(view.history)
        ):
            hand = self.hand = HandRanges(view.hole)
            if self.knobs.show_prior > 0.0:
                hand.opp = self.opp
        return hand

    def _postflop(self, view: View, hand: HandRanges) -> Decision:
        table = hand.tables.get(view.street)
        if table is None:
            tight = view.budget_s < TIGHT_BUDGET_S
            samples = (SAMPLES_NO_BUDGET if tight else SAMPLES)[view.street]
            table = StreetTable(view.hole, view.board, samples, view.rng, view.deadline)
            hand.tables[view.street] = table
        hand.update(view.history, self.knobs, self.reads)
        spot = Spot(
            view,
            Snapshot(table, hand.theirs),
            Snapshot(table, hand.mine),
            features(view.hole, view.board),
            self.knobs,
            table.hero_ehs,
            self.reads,
            self._opp(),
        )
        return facing_bet(spot) if view.to_call else open_street(spot)
