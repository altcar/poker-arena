# Built by tools/build_flat.py from bot/: oppmodel.model. Edit the source, not this.

from __future__ import annotations

"""One opponent, learned from what end_game shows: its cards and its folds.

The engine shows hole cards at every showdown and every fold after the fact, so
strength is observed, not assumed. For each shown hand and each of its actions
this keeps the hand's strength (equity against a random hand on that street's
board; preflop, 1 minus its percentile) in a small histogram:

    (street, kind, size) -> (street, kind) -> (kind)

kind is bet, raise, call or check; size is the pot-fraction class of the bet made
or called. A cell borrows from its parents, then the whole estimate shrinks
towards whatever the generic model says with weight ``prior``. No evidence means
no change, exactly: every method returns its default.

Two uses:

* ``pull`` and ``pull_preflop`` move the range after an action of theirs towards
  the strength histogram of that action. On the river this is close to unbiased:
  a called bet always shows. Earlier, a hand that bets and folds later never
  shows, so such a hand counts as a weak pseudo-observation (FOLD_PROXY).
* ``fold_rate``, ``bluff_gate`` and ``overbets`` use the exact count of folds to
  my bets, by street and size. All plain data, so the object pickles.

One file on purpose: the flat build has a limit on modules.
"""


from random import Random

from core import card
from core import equity_exact, equity_mc
from core import ALL_COMBOS, preflop_equity
from policy_v1 import COMBO_PLACE

OBS_BINS = 16
KINDS = {"bet": "bet", "raise_to": "raise", "call": "call", "check": "check"}
AGGRESSIVE = ("bet", "raise_to")
BLINDS = ("post_small_blind", "post_big_blind")

SIZE_EDGES = (0.45, 0.85, 1.3)  # small, mid, big, over
STREET_WEIGHT = 0.4  # what the rest of a street and kind adds to a size cell
KIND_WEIGHT = 0.15  # same for the rest of a kind, all streets
SPREAD = 0.25  # share of an observation that goes to each neighbouring bin

BLUFF_EHS = 0.5  # a shown bet below this was not value
FOLD_PROXY = 0.35  # the strength a hand that folded later is taken to have had
PROXY_WEIGHT = 0.5
FLOP_SAMPLES = 400

GATE_FLOOR = 0.8  # bluffs stop at this share of the break-even fold rate
GATE_SLOPE = 2.5
GATE_TOP = 2.0
OVERBET_EVIDENCE = 4.0  # replies to big bets before an overbet is considered
OVERBET_FOLDS = 0.25  # they fold big bets less often than this
OVERBET_SIZES = (1.6,)
PREFLOP_TRUST = 0.3  # share of evidence before a preflop call or fold is overruled
PREFLOP_MARGIN = 0.06  # equity a call needs beyond the price when not all-in

COMBO_ID = {(a, b): i for i, (a, b, _) in enumerate(ALL_COMBOS)}
PLACE_BIN = tuple(
    min(OBS_BINS - 1, int((1.0 - place) * OBS_BINS)) for place in COMBO_PLACE
)


def size_class(fraction: float) -> int:
    for place, edge in enumerate(SIZE_EDGES):
        if fraction < edge:
            return place
    return len(SIZE_EDGES)


def action_size(action, amount, other, owed, pot) -> float:
    """Pot fraction of an action, from the state just before it.

    A bet or raise counts what it adds beyond a call, against the pot after that
    call. A call counts the bet it answers, against the pot before that bet.
    """
    if action in AGGRESSIVE:
        return (amount - other) / max(1, pot + owed)
    if action == "call":
        return owed / max(1, pot - owed)
    return 0.0


def walk(events):
    """Each event of a hand with its pot fraction, replaying the chips."""
    steps = []
    street = pot = 0
    bets = {True: 0, False: 0}
    for event in events:
        if event.street != street:
            street = event.street
            bets = {True: 0, False: 0}
        mine, action = event.mine, event.action
        before, other = bets[mine], bets[not mine]
        owed = max(0, other - before)
        if action in BLINDS or action == "call":
            added = event.amount or 0
        elif action in AGGRESSIVE:
            added = event.amount - before
        else:
            added = 0
        if action not in BLINDS:
            steps.append((event, action_size(action, event.amount, other, owed, pot)))
        pot += added
        bets[mine] = before + added
    return steps


def preflop_strength(hole) -> float:
    return 1.0 - COMBO_PLACE[COMBO_ID[(min(hole), max(hole))]]


def strength(hole, board) -> float:
    """Equity against a random hand; the same draw for the same cards."""
    size = len(board)
    if size >= 4:
        return equity_exact(hole, board).equity
    seed = hole[0] + 52 * hole[1] + 2704 * (board[0] + 52 * board[1] + 2704 * board[2])
    return equity_mc(hole, board, None, FLOP_SAMPLES, None, Random(seed)).equity


def shown_cards(actions, me: int):
    """Their hole cards from end_game's actions, or None. Board cards are the
    dealer's (player -1) and mine are mine: only the rest is theirs."""
    cards = [
        card(str(action.card))
        for action in actions
        if action.kind == "reveal_card" and action.player_id >= 0
        and action.player_id != me
    ]  # fmt: skip
    return (cards[0], cards[1]) if len(cards) == 2 else None


class OppModel:
    """Everything shown by one opponent. Rebuilt for each rotation, as the bot is."""

    def __init__(self) -> None:
        self.team = None
        self._clear()

    def _clear(self) -> None:
        self.hands = 0
        self.shown = 0
        self.cells = {}  # (street, kind, size), (street, kind), (kind,) -> counts
        self.replies = {}  # (street, size), (size,), () -> [folds, trials]
        self.preflop_replies = [0.0, 0.0]
        self.shown_bets = [0, 0, 0, 0]  # per street
        self.bluffs = [0, 0, 0, 0]
        self.pulls = 0  # range updates this changed, to prove it fires
        self.fold_uses = 0
        self.gates = 0

    def for_team(self, team) -> None:
        """A new opponent starts from nothing."""
        if team != self.team:
            self._clear()
            self.team = team

    # Learning

    def learn(self, events, board, shown) -> None:
        """One finished hand: all its Events, the final board, their hole cards
        as two core.cards codes, or None if they were not shown."""
        self.hands += 1
        steps = walk(events)
        if shown is not None:
            self.shown += 1
        they_folded = any(not e.mine and e.action == "fold" for e, _ in steps)
        strengths = {}
        for position, (event, fraction) in enumerate(steps):
            if event.mine:
                if event.action in AGGRESSIVE and position + 1 < len(steps):
                    self._reply(event.street, fraction, steps[position + 1][0])
                continue
            kind = KINDS.get(event.action)
            if kind is None:
                continue
            street, size = event.street, size_class(fraction)
            if shown is None:
                if they_folded:
                    self._observe(street, kind, size, FOLD_PROXY, PROXY_WEIGHT)
                continue
            value = strengths.get(street)
            if value is None:
                value = strengths[street] = (
                    strength(shown, board[: street + 2])
                    if street
                    else preflop_strength(shown)
                )
            self._observe(street, kind, size, value, 1.0)
            if kind == "bet" or kind == "raise":
                self.shown_bets[street] += 1
                self.bluffs[street] += value < BLUFF_EHS

    def _observe(self, street, kind, size, value, weight) -> None:
        place = min(OBS_BINS - 1, int(value * OBS_BINS))
        for key in ((street, kind, size), (street, kind), (kind,)):
            cell = self.cells.get(key)
            if cell is None:
                cell = self.cells[key] = [0.0] * OBS_BINS
            side = weight * SPREAD
            cell[place] += weight - 2.0 * side
            cell[max(0, place - 1)] += side
            cell[min(OBS_BINS - 1, place + 1)] += side

    def _reply(self, street, fraction, reply) -> None:
        if reply.mine:
            return
        folded = 1.0 if reply.action == "fold" else 0.0
        if street == 0:
            self.preflop_replies[0] += folded
            self.preflop_replies[1] += 1.0
            return
        size = size_class(fraction)
        for key in ((street, size), (size,), ()):
            cell = self.replies.get(key)
            if cell is None:
                cell = self.replies[key] = [0.0, 0.0]
            cell[0] += folded
            cell[1] += 1.0

    # Reading

    def _pooled(self, street, kind, size):
        """Counts for one cell with its parents mixed in, and their total."""
        empty = None
        fine = self.cells.get((street, kind, size), empty)
        mid = self.cells.get((street, kind), empty)
        top = self.cells.get((kind,), empty)
        if top is None:
            return None, 0.0
        pooled = []
        for place in range(OBS_BINS):
            a = fine[place] if fine else 0.0
            b = mid[place] if mid else 0.0
            pooled.append(
                a + STREET_WEIGHT * (b - a) + KIND_WEIGHT * (top[place] - b)
            )
        return pooled, sum(pooled)

    def _pooled_replies(self, street, size):
        fine = self.replies.get((street, size), (0.0, 0.0))
        mid = self.replies.get((size,), (0.0, 0.0))
        top = self.replies.get((), (0.0, 0.0))
        folds = fine[0] + STREET_WEIGHT * (mid[0] - fine[0])
        folds += KIND_WEIGHT * (top[0] - mid[0])
        trials = fine[1] + STREET_WEIGHT * (mid[1] - fine[1])
        trials += KIND_WEIGHT * (top[1] - mid[1])
        return folds, trials

    def trust(self, street, kind, fraction, prior: float) -> float:
        """Share of the estimate for this action that is evidence, 0 to 1."""
        if prior <= 0.0:
            return 0.0
        total = self._pooled(street, kind, size_class(fraction))[1]
        return total / (total + prior)

    def _targets(self, pooled, prior, default, live):
        """Per coarse bin: the default mix, moved towards what was shown."""
        seen = sum(pooled[place] for place in live)
        if seen <= 0.0:
            return None
        trust = seen / (seen + prior)
        result = [0.0] * OBS_BINS
        for place in live:
            result[place] = (
                trust * pooled[place] / seen + (1.0 - trust) * default[place]
            )
        return result

    def pull(self, likelihood, weights, table, street, kind, fraction, prior):
        """A likelihood over table bins that lands the range between the generic
        model's answer and the shown histogram. Default when nothing was shown."""
        if prior <= 0.0:
            return likelihood
        pooled, total = self._pooled(street, kind, size_class(fraction))
        if total <= 0.0:
            return likelihood
        fine = len(likelihood)
        ratio = fine // OBS_BINS
        before = [0.0] * fine
        bins = table.bins
        for position, combo in enumerate(table.index):
            before[bins[position]] += weights[combo]
        after = [mass * chance for mass, chance in zip(before, likelihood)]
        all_after = sum(after)
        if all_after <= 0.0:
            return likelihood
        coarse_before = [0.0] * OBS_BINS
        coarse_after = [0.0] * OBS_BINS
        for place in range(fine):
            coarse_before[place // ratio] += before[place]
            coarse_after[place // ratio] += after[place]
        live = [p for p in range(OBS_BINS) if coarse_before[p] > 0.0]
        default = [mass / all_after for mass in coarse_after]
        target = self._targets(pooled, prior, default, live)
        if target is None:
            return likelihood
        result = list(likelihood)
        for place in range(fine):
            if before[place] <= 0.0:
                continue
            coarse = place // ratio
            if coarse_after[coarse] > 1e-12:
                shape = after[place] / coarse_after[coarse]
            else:
                shape = before[place] / coarse_before[coarse]
            result[place] = target[coarse] * shape / before[place]
        self.pulls += 1
        return result

    def pull_preflop(self, before, after, kind, fraction, prior) -> None:
        """The same for a preflop action. ``after`` is changed in place."""
        if prior <= 0.0:
            return
        pooled, total = self._pooled(0, kind, size_class(fraction))
        if total <= 0.0:
            return
        coarse_before = [0.0] * OBS_BINS
        coarse_after = [0.0] * OBS_BINS
        for combo, place in enumerate(PLACE_BIN):
            coarse_before[place] += before[combo]
            coarse_after[place] += after[combo]
        all_after = sum(coarse_after)
        if all_after <= 0.0:
            return
        live = [p for p in range(OBS_BINS) if coarse_before[p] > 0.0]
        default = [mass / all_after for mass in coarse_after]
        target = self._targets(pooled, prior, default, live)
        if target is None:
            return
        for combo, place in enumerate(PLACE_BIN):
            if coarse_after[place] > 1e-12:
                after[combo] *= target[place] / coarse_after[place]
            elif coarse_before[place] > 0.0:
                after[combo] = before[combo] * target[place] / coarse_before[place]
        self.pulls += 1

    def preflop_call(self, hole, weights, kind, fraction, price, final, prior):
        """True: their shown range pays this call. False: it does not, at a final
        price. None: no opinion, too little evidence."""
        if self.trust(0, kind, fraction, prior) < PREFLOP_TRUST:
            return None
        combos = [(a, b, w) for (a, b, _), w in zip(ALL_COMBOS, weights)]
        equity = preflop_equity(hole, combos)
        if equity >= price + (0.0 if final else PREFLOP_MARGIN):
            return True
        return False if final else None

    def fold_rate(self, street, fraction, default: float, prior: float) -> float:
        """How often they fold to a bet of this size, shrunk towards ``default``."""
        if prior <= 0.0:
            return default
        folds, trials = self._pooled_replies(street, size_class(fraction))
        if trials <= 0.0:
            return default
        self.fold_uses += 1
        return (folds + prior * default) / (trials + prior)

    def bluff_gate(self, street, fraction, prior: float, base: float) -> float:
        """Multiplies a bluff: ``base`` with no evidence; with evidence 0 under
        the break-even fold rate, up to GATE_TOP well over it."""
        if prior <= 0.0:
            return 1.0
        folds, trials = self._pooled_replies(street, size_class(fraction))
        if trials <= 0.0:
            return base
        self.gates += 1
        trust = trials / (trials + prior)
        need = fraction / (1.0 + fraction) if fraction > 0.0 else 0.3
        gate = GATE_SLOPE * (folds / trials / need - GATE_FLOOR)
        gate = min(GATE_TOP, max(0.0, gate))
        return (1.0 - trust) * base + trust * gate

    def overbets(self, street, prior: float) -> tuple[float, ...]:
        """Extra sizes for the menu, against a bot that will not fold big bets."""
        if prior <= 0.0:
            return ()
        folds, trials = self._pooled_replies(street, 2)
        if trials < OVERBET_EVIDENCE or folds / trials > OVERBET_FOLDS:
            return ()
        return OVERBET_SIZES

    def bluff_share(self, street, prior_share: float, prior: float = 4.0) -> float:
        """Share of their shown bets on a street that were not value."""
        return (self.bluffs[street] + prior * prior_share) / (
            self.shown_bets[street] + prior
        )

    def report(self) -> dict:
        return {
            "hands": self.hands,
            "shown": self.shown,
            "shown_bets": list(self.shown_bets),
            "bluffs": list(self.bluffs),
            "folds": {str(k): list(v) for k, v in self.replies.items() if len(k) == 2},
            "preflop_replies": list(self.preflop_replies),
            "pulls": self.pulls,
            "fold_uses": self.fold_uses,
            "gates": self.gates,
        }
