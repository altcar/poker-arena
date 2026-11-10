# Built by tools/build_flat.py from bot/: policy_v1. Edit the source, not this.

from __future__ import annotations

"""Policy v1: equity against an estimated range, pot odds, value bets.

The opponent's range starts from their preflop action and is narrowed by the
size of their postflop aggression, less so the more aggressive they have been
this half. Every threshold is a first guess, not a measured value.
"""


from core import equity_exact, equity_mc
from core import evaluate
from core import (
    ALL_COMBOS,
    CLASSES,
    class_combos,
    class_equity,
    hand_class,
    preflop_equity,
)
from core import (
    OPP_FOLDED,
    PREFLOP,
    RIVER,
    BaseStrategy,
    Decision,
    HandSummary,
    View,
    all_in,
    fold,
    passive,
    raise_to,
)

AGGRESSIVE = ("bet", "raise_to")
BLINDS = ("post_small_blind", "post_big_blind")

SAMPLES = 1500
SAMPLES_NO_BUDGET = 100
SAMPLE_TIME_CAP = 0.03
SAMPLE_BUDGET_SHARE = 0.6

OPEN_VALUE = 0.58  # equity against a random hand
OPEN_STEAL = 0.47
OPEN_STEAL_STICKY = 0.54
OPTION_RAISE = 0.60
OPEN_SIZE_BB = 3.0
OPEN_STICKY_BB = 5.0
STEAL_SIZE_BB = 2.5
RERAISE_TIMES = 3.0
REPOP_EQUITY = 0.64  # equity against their raising range
REALISE_IN_POSITION = 0.95
REALISE_OUT_OF_POSITION = 0.85

BET_VALUE = {1: 0.62, 2: 0.62, 3: 0.60}
BET_VALUE_STICKY = {1: 0.55, 2: 0.55, 3: 0.54}
CALL_MARGIN = {1: 0.02, 2: 0.03, 3: 0.03}
RAISE_VALUE = 0.80
COMMIT_SHARE = 0.55  # a raise this close to all-in might as well be all-in

KEEP_LATER_STREET = 0.55  # share of range kept per earlier-street aggression
RANGE_TAIL = 0.2  # weight left on hands outside the kept share
CALLED_RAISE_WIDTH = 0.75
WIDTH_STEPS = 20

BLUFF_FOLD_RATE = 0.55
BLUFF_MIN_SAMPLES = 6
BLUFF_SHARE = 0.7
STICKY_FOLD_RATE = 0.2
STICKY_MIN_SAMPLES = 5


def _combo_percentiles() -> tuple[float, ...]:
    """Each combo's place in the ranking of hands by equity against random."""
    sizes = [len(class_combos(index)) for index in range(CLASSES)]
    total = sum(sizes)
    strength = [
        sum(
            sizes[other] * class_equity(index, other)
            for other in range(CLASSES)
            if sizes[other]
        )
        for index in range(CLASSES)
    ]
    place = [0.0] * CLASSES
    running = 0
    for index in sorted(range(CLASSES), key=lambda i: -strength[i]):
        if sizes[index]:
            running += sizes[index]
            place[index] = running / total
    return tuple(place[hand_class((a, b))] for a, b, _ in ALL_COMBOS)


COMBO_PLACE = _combo_percentiles()


def rate(hits: int, trials: int, prior: float, strength: int) -> float:
    """A frequency shrunk toward a prior worth ``strength`` observations."""
    return (hits + prior * strength) / (trials + strength)


class OppStats:
    """What this half has shown about the opponent."""

    def __init__(self) -> None:
        self.pf_hands = self.pf_raises = 0
        self.post_actions = self.post_aggressive = 0
        self.bets_answered = self.bets_folded = 0
        self.post_answered = self.post_folded = 0

    def update(self, summary: HandSummary) -> None:
        history = summary.history
        pf_seen = pf_raised = False
        for position, event in enumerate(history):
            if event.action in BLINDS:
                continue
            if event.mine:
                if event.action in AGGRESSIVE and position + 1 < len(history):
                    self.bets_answered += 1
                    self.post_answered += event.street > PREFLOP
            elif event.street == PREFLOP:
                pf_seen = True
                pf_raised = pf_raised or event.action in AGGRESSIVE
            else:
                self.post_actions += 1
                self.post_aggressive += event.action in AGGRESSIVE
        if summary.ending == OPP_FOLDED:
            self.bets_folded += 1
            self.post_folded += summary.my_last_street > PREFLOP
            pf_seen = pf_seen or summary.my_last_street == PREFLOP
        self.pf_hands += pf_seen
        self.pf_raises += pf_raised

    @property
    def pf_raise_rate(self) -> float:
        return rate(self.pf_raises, self.pf_hands, 0.4, 4)

    @property
    def aggression(self) -> float:
        return rate(self.post_aggressive, self.post_actions, 0.3, 6)

    @property
    def sticky(self) -> bool:
        """They almost never fold to a bet, so bet bigger and thinner."""
        trials = self.bets_answered + self.bets_folded
        return (
            trials >= STICKY_MIN_SAMPLES
            and rate(self.bets_folded, trials, 0.4, 4) < STICKY_FOLD_RATE
        )

    @property
    def folds_too_much(self) -> bool:
        trials = self.post_answered + self.post_folded
        return (
            trials >= BLUFF_MIN_SAMPLES and self.post_folded / trials >= BLUFF_FOLD_RATE
        )


def width_for_raise(total: int, big_blind: int) -> float:
    """Share of hands that raises to this preflop total, before adjustment."""
    size = total / big_blind
    if size <= 3:
        return 0.7
    if size <= 5:
        return 0.45
    if size <= 12:
        return 0.25
    if size <= 30:
        return 0.12
    return 0.08


def width_for_shove(total: int, big_blind: int) -> float:
    """Share of hands that moves all-in for this total: short shoves are wide."""
    depth = total / big_blind
    if depth <= 12:
        return 0.45
    if depth <= 20:
        return 0.35
    if depth <= 30:
        return 0.25
    if depth <= 50:
        return 0.15
    return 0.08


class PolicyV1(BaseStrategy):
    """A complete strategy: it always has an opinion."""

    def __init__(self) -> None:
        self.stats = OppStats()
        self.ranges: dict[int, list] = {}

    def on_half_start(self, rng) -> None:
        self.stats = OppStats()
        self.ranges = {}

    def on_hand_end(self, summary: HandSummary) -> None:
        self.stats.update(summary)

    def decide(self, view: View) -> Decision:
        if view.street == PREFLOP:
            return self._preflop(view)
        villain = self._villain_range(view)
        equity = self._equity(view, villain)
        if view.to_call:
            return self._facing_bet(view, equity)
        return self._open_street(view, equity)

    # -- ranges and equity --

    def _top_range(self, width: float) -> list:
        step = max(1, min(WIDTH_STEPS, round(width * WIDTH_STEPS)))
        cached = self.ranges.get(step)
        if cached is None:
            limit = step / WIDTH_STEPS
            cached = [
                combo for combo, place in zip(ALL_COMBOS, COMBO_PLACE) if place <= limit
            ]
            self.ranges[step] = cached
        return cached

    def _raise_width(self, view: View) -> float:
        """Their preflop raising range, from their largest raise this hand."""
        totals = [
            event.amount
            for event in view.history
            if not event.mine and event.street == PREFLOP and event.action in AGGRESSIVE
        ]
        if not totals:
            return 0.0
        size = width_for_shove if view.opp_all_in else width_for_raise
        width = size(max(totals), view.big_blind)
        return max(width, 0.9 * self.stats.pf_raise_rate)

    def _preflop_width(self, view: View) -> float:
        raised = self._raise_width(view)
        if raised:
            return raised
        i_raised = any(
            event.mine and event.street == PREFLOP and event.action in AGGRESSIVE
            for event in view.history
        )
        return CALLED_RAISE_WIDTH if i_raised and not self.stats.sticky else 1.0

    def _keep_share(self, view: View) -> float:
        """Share of their range still alive after their postflop aggression."""
        keep = 1.0
        for event in view.history:
            if event.mine or event.street == PREFLOP:
                continue
            if event.action in AGGRESSIVE and event.street < view.street:
                keep *= KEEP_LATER_STREET
        if view.to_call:
            ratio = view.to_call / max(1, view.pot - view.to_call)
            keep *= 0.6 if ratio <= 0.4 else 0.45 if ratio <= 0.8 else 0.35
        return keep ** (1.0 - min(self.stats.aggression, 0.9))

    def _villain_range(self, view: View) -> list:
        base = self._top_range(self._preflop_width(view))
        keep = self._keep_share(view)
        if keep > 0.98:
            return base
        return narrow(base, view.hole, view.board, keep)

    def _equity(self, view: View, villain: list) -> float:
        try:
            return estimate_equity(view, villain)
        except ValueError:  # card removal emptied the range
            return estimate_equity(view, None)

    # -- preflop --

    def _preflop(self, view: View) -> Decision:
        bb = view.big_blind
        versus_random = preflop_equity(view.hole)
        sticky = self.stats.sticky
        if view.opp_bet <= bb and view.to_call <= view.small_blind:
            if view.to_call:
                return self._open_button(view, versus_random, sticky)
            if versus_random >= OPTION_RAISE:
                return commit(view, open_size(bb, sticky), "option")
            return passive("check option")
        return self._facing_raise(view)

    def _open_button(self, view: View, strength: float, sticky: bool) -> Decision:
        bb = view.big_blind
        if strength >= OPEN_VALUE:
            return commit(view, open_size(bb, sticky), "open value")
        if strength >= (OPEN_STEAL_STICKY if sticky else OPEN_STEAL):
            return commit(view, int(STEAL_SIZE_BB * bb), "open steal")
        return passive("limp")  # folding would give up the blind for nothing

    def _facing_raise(self, view: View) -> Decision:
        width = self._raise_width(view) or 1.0
        equity = preflop_equity(view.hole, self._top_range(width))
        price = view.pot_odds
        if view.opp_all_in or view.to_call >= view.my_stack:
            return passive("call shove") if equity >= price else fold("fold shove")
        if equity >= REPOP_EQUITY and view.can_raise:
            return commit(view, int(view.opp_bet * RERAISE_TIMES), "reraise")
        realise = REALISE_IN_POSITION if view.is_button else REALISE_OUT_OF_POSITION
        return passive("call") if equity * realise >= price else fold("fold")

    # -- postflop --

    def _open_street(self, view: View, equity: float) -> Decision:
        if not view.can_raise:
            return passive("check")
        sticky = self.stats.sticky
        needed = (BET_VALUE_STICKY if sticky else BET_VALUE)[view.street]
        if equity >= needed:
            return commit(
                view, view.pot_fraction_total(bet_size(equity, sticky)), "value"
            )
        if self.stats.folds_too_much and view.rng.random() < BLUFF_SHARE:
            return raise_to(view.pot_fraction_total(0.5), "fold exploit")
        return passive("check")

    def _facing_bet(self, view: View, equity: float) -> Decision:
        price = view.pot_odds
        if view.opp_all_in or view.to_call >= view.my_stack:
            return passive("call shove") if equity >= price else fold("fold shove")
        if view.can_raise and equity >= RAISE_VALUE:
            return commit(view, view.pot_fraction_total(0.8), "value raise")
        if equity >= price + CALL_MARGIN[view.street]:
            return passive("call")
        return fold("fold")


def open_size(big_blind: int, sticky: bool) -> int:
    return int((OPEN_STICKY_BB if sticky else OPEN_SIZE_BB) * big_blind)


def bet_size(equity: float, sticky: bool) -> float:
    """Pot fraction for a value bet."""
    if sticky:
        return 2.0 if equity >= 0.85 else 1.5 if equity >= 0.72 else 0.9
    return 1.0 if equity >= 0.85 else 0.75 if equity >= 0.72 else 0.5


def commit(view: View, total: int, tag: str) -> Decision:
    """Raises to ``total``, or all-in when that leaves almost nothing behind."""
    if total >= COMMIT_SHARE * view.max_raise_to:
        return all_in(tag)
    return raise_to(total, tag)


def narrow(combos: list, hole: tuple, board: tuple, keep: float) -> list:
    """Reweights a range toward its best made hands on this board."""
    dead = (*hole, *board)
    scored = []
    total = 0.0
    for first, second, weight in combos:
        if first in dead or second in dead:
            continue
        scored.append((evaluate((first, second, *board)), first, second, weight))
        total += weight
    scored.sort(reverse=True)
    limit = keep * total
    seen = 0.0
    result = []
    for _, first, second, weight in scored:
        kept = seen < limit
        seen += weight
        result.append((first, second, weight if kept else weight * RANGE_TAIL))
    return result


def estimate_equity(view: View, villain: list | None) -> float:
    """Equity on this street: exact on the river, sampled before it."""
    if view.street == RIVER:
        return equity_exact(view.hole, view.board, villain).equity
    if view.budget_s <= 0.0:
        return equity_mc(
            view.hole, view.board, villain, SAMPLES_NO_BUDGET, rng=view.rng
        ).equity
    limit = min(view.budget_s * SAMPLE_BUDGET_SHARE, SAMPLE_TIME_CAP)
    return equity_mc(view.hole, view.board, villain, SAMPLES, limit, view.rng).equity
