"""Independent defensive range policy with public-information runtime guards."""

import bisect
import itertools
import math
import random
import time

from card_utils import full_deck
from player_action import PlayerAction


def _straight_high_mask(mask):
    mask |= (mask >> 13) & 2
    consecutive = mask & (mask << 1) & (mask << 2) & (mask << 3) & (mask << 4)
    return consecutive.bit_length() - 1 if consecutive else 0


def evaluate(cards):
    counts = [0] * 15
    suits = {}
    mask = 0
    for card in cards:
        value = card.value
        bit = 1 << value
        counts[value] += 1
        mask |= bit
        suits[card.suit] = suits.get(card.suit, 0) | bit
    flush = 0
    for suit_mask in suits.values():
        if suit_mask.bit_count() >= 5:
            high = _straight_high_mask(suit_mask)
            if high:
                return (8, high)
            flush = suit_mask
            break
    ranks = [rank for rank in range(14, 1, -1) if counts[rank]]
    quads = [rank for rank in ranks if counts[rank] == 4]
    if quads:
        quad = quads[0]
        return (7, quad, next(rank for rank in ranks if rank != quad))
    trips = [rank for rank in ranks if counts[rank] >= 3]
    pairs = [rank for rank in ranks if counts[rank] >= 2]
    if trips:
        trip = trips[0]
        pair = next((rank for rank in pairs if rank != trip), 0)
        if pair:
            return (6, trip, pair)
    if flush:
        return (5,) + tuple(rank for rank in ranks if flush & (1 << rank))[:5]
    high = _straight_high_mask(mask)
    if high:
        return (4, high)
    if trips:
        trip = trips[0]
        return (3, trip, *[rank for rank in ranks if rank != trip][:2])
    if len(pairs) >= 2:
        first, second = pairs[:2]
        return (2, first, second, next(rank for rank in ranks if rank not in pairs[:2]))
    if pairs:
        pair = pairs[0]
        return (1, pair, *[rank for rank in ranks if rank != pair][:3])
    return (0, *ranks[:5])


def _chen(hand):
    high, low = sorted((c.value for c in hand), reverse=True)
    value = {14: 10, 13: 8, 12: 7, 11: 6}.get(high, high / 2)
    if high == low:
        return max(5, value * 2)
    value += 2 * (hand[0].suit == hand[1].suit)
    gap = high - low - 1
    value -= (0, 1, 2, 4, 5)[min(4, gap)]
    if gap <= 1 and high < 12:
        value += 1
    # Distinguish otherwise tied weak ace hands and broadway kickers.
    return value + low * 0.025 + (0.3 if high == 14 else 0)


_DECK = full_deck()
_PREFLOP_ORDER = sorted(_chen(h) for h in itertools.combinations(_DECK, 2))


def _preflop_strength(hand):
    value = _chen(hand)
    return (
        bisect.bisect_left(_PREFLOP_ORDER, value)
        + bisect.bisect_right(_PREFLOP_ORDER, value)
    ) / (2 * len(_PREFLOP_ORDER))


def _sigmoid(value):
    return 1 / (1 + math.exp(max(-30, min(30, -value))))


def _draw(hand, board):
    if not 3 <= len(board) < 5:
        return 0.0
    all_cards = list(hand) + list(board)
    suits = [c.suit for c in all_cards]
    flush = any(
        suits.count(c.suit) == 4 and sum(x.suit == c.suit for x in board) < 4
        for c in hand
    )
    ranks = {c.value for c in all_cards}
    if 14 in ranks:
        ranks.add(1)
    best = 0
    for high in range(5, 15):
        run = set(range(high - 4, high + 1))
        if not any(c.value in run or (c.value == 14 and 1 in run) for c in hand):
            continue
        overlap = len(run & ranks)
        if overlap == 4:
            missing = next(iter(run - ranks))
            best = max(best, 2 if missing in (high, high - 4) else 1)
    return max(0.58 if flush else 0, 0.48 if best == 2 else 0.36 if best else 0)


class _BudgetExceeded(Exception):
    pass


class Model:
    """Opponent-range tracker with position-aware opens and sampled action EV."""

    STYLE = "defensive"

    def __init__(self, seed=314159, samples=160):
        self.rng = random.Random(seed)
        self.samples = max(1, int(samples))
        self.stats = {}
        self.hand = []
        self.player_id = None
        self.hands_played = 0
        self.budget_fallbacks = 0
        self.ledger_errors = 0
        self._deadline = 0.0

    def seed(self, hand, player_id):
        self._deadline = time.perf_counter() + 1.75
        self._desynced = False
        self._done = False
        self.hand = list(hand)
        self.player_id = player_id
        self.ready = False
        self.board = []
        self.last_raise_player = None
        self.raises = 0
        self.shown = {}
        self.last_bet = None
        self.pending_response = None

    def _check_budget(self):
        if time.perf_counter() > self._deadline:
            raise _BudgetExceeded

    def _fallback(self, g):
        return (
            PlayerAction.call(self.player_id)
            if g.call_amount == 0
            else PlayerAction.fold(self.player_id)
        )

    def _start(self, g, awards=None):
        self._check_budget()
        if g.street != "preflop":
            return False
        self.other = 1 - self.player_id
        self.name = g.teams[self.other]
        self.stat = self.stats.setdefault(
            self.name,
            {
                "raise": 3.0,
                "actions": 10.0,
                "fold": 3.0,
                "faced": 8.0,
                "show_bluff": 1.0,
                "show_bet": 5.0,
                "responses": {},
            },
        )
        self.bb = g.big_blind
        awards = awards or [0, 0]
        self.stacks = [g.stacks[i] + g.bets[i] - awards[i] for i in range(2)]
        self.bets = [0, 0]
        for seat, blind in ((g.button, g.small_blind), (1 - g.button, g.big_blind)):
            paid = min(blind, self.stacks[seat])
            self.bets[seat] = paid
            self.stacks[seat] -= paid
        self.total = self.bets[:]
        self.holes = list(
            itertools.combinations([c for c in _DECK if c not in self.hand], 2)
        )
        self.weights = [1.0] * len(self.holes)
        self.features = []
        for i, hand in enumerate(self.holes):
            if i % 64 == 0:
                self._check_budget()
            self.features.append(_preflop_strength(hand))
        self.draws = [0.0] * len(self.holes)
        self.ready = True
        return True

    def _board_features(self):
        known = set(self.hand + self.board)
        possible = [i for i, h in enumerate(self.holes) if not known.intersection(h)]
        for i, h in enumerate(self.holes):
            if known.intersection(h):
                self.weights[i] = 0
        if len(self.board) >= 3:
            ranked = []
            for count, i in enumerate(possible):
                if count % 64 == 0:
                    self._check_budget()
                ranked.append((evaluate(self.holes[i] + tuple(self.board)), i))
            ranked.sort()
            start = 0
            while start < len(ranked):
                if start % 64 == 0:
                    self._check_budget()
                end = start + 1
                while end < len(ranked) and ranked[end][0] == ranked[start][0]:
                    end += 1
                strength = (start + end) / (2 * len(ranked))
                for _, i in ranked[start:end]:
                    self.features[i] = strength
                    self.draws[i] = _draw(self.holes[i], self.board)
                start = end

    def _replay(self, actions, g):
        if self._desynced:
            return False
        if not self.ready and not self._start(g):
            return False
        for action in actions:
            self._check_budget()
            who = action.player_id
            if action.kind == "reveal_card":
                if who == -1:
                    self.board.append(action.card)
                    if len(self.board) in (3, 4, 5):
                        self.bets = [0, 0]
                        self.raises = 0
                        self.last_raise_player = None
                        self._board_features()
                else:
                    self.shown.setdefault(who, []).append(action.card)
                continue
            if action.kind == "win":
                self.stacks[who] += action.amount
                continue
            if who == -1:
                continue
            facing = max(self.bets) > self.bets[who]
            cost = min(self.stacks[who], max(self.bets) - self.bets[who])
            pot = sum(self.total)
            if who == self.other and action.kind in ("fold", "call", "raise"):
                if self.pending_response is not None:
                    context, predicted = self.pending_response
                    counts = self.stat["responses"].setdefault(context, [0.0, 0.0, 0])
                    counts[0] += action.kind == "fold"
                    counts[1] += predicted
                    counts[2] += 1
                    self.pending_response = None
                self.stat["actions"] += 1
                if action.kind == "raise":
                    self.stat["raise"] += 1
                if facing:
                    self.stat["faced"] += 1
                    self.stat["fold"] += action.kind == "fold"
                self._condition(action, cost, pot, facing)
            if action.kind == "raise":
                addition = max(0, action.amount - self.bets[who])
                self.bets[who] += addition
                self.total[who] += addition
                self.stacks[who] -= addition
                self.raises += 1
                self.last_raise_player = who
                if who == self.other:
                    self.last_bet = (tuple(self.board), action.amount / max(1, pot))
            elif action.kind == "call":
                self.bets[who] += cost
                self.total[who] += cost
                self.stacks[who] -= cost
        correct = (
            tuple(self.bets) == tuple(g.bets)
            and tuple(self.stacks) == tuple(g.stacks)
            and tuple(self.board) == tuple(g.board)
            and sum(self.total) == g.pot
        )
        if not correct:
            self.ledger_errors += 1
            self._desynced = True
        return correct

    def _condition(self, action, cost, pot, facing):
        if action.kind == "fold":
            return
        aggressive = self.stat["raise"] / self.stat["actions"]
        bluff = self.stat["show_bluff"] / self.stat["show_bet"]
        preflop = not self.board
        if action.kind == "raise":
            size = (
                action.amount / max(1, self.bb)
                if preflop
                else (action.amount - self.bets[self.other]) / max(1, pot)
            )
            cutoff = (
                (
                    0.25
                    if self.raises == 0 and size <= 3.5
                    else 0.72
                    if size <= 9
                    else 0.88
                )
                if preflop
                else 0.65 + 0.12 * min(2, size)
            )
            cutoff -= max(0, aggressive - 0.28) * 0.9
            floor = 0.035 + 0.13 * bluff + max(0, aggressive - 0.4) * 0.3
            for i, made in enumerate(self.features):
                if i % 64 == 0:
                    self._check_budget()
                if self.weights[i]:
                    strength = max(made, self.draws[i] + 0.15)
                    self.weights[i] *= floor + (1 - floor) * _sigmoid(
                        (strength - cutoff) / 0.10
                    )
        elif facing:
            odds = cost / max(1, pot + cost)
            cutoff = 0.12 + 0.9 * odds if preflop else 0.15 + odds
            for i, made in enumerate(self.features):
                if i % 64 == 0:
                    self._check_budget()
                if self.weights[i]:
                    strength = max(made, self.draws[i])
                    # Calls retain strong traps and weak bluff-catchers.
                    self.weights[i] *= 0.06 + 0.94 * _sigmoid(
                        (strength - cutoff) / 0.15
                    )
        else:
            for i, made in enumerate(self.features):
                if i % 64 == 0:
                    self._check_budget()
                self.weights[i] *= 1.0 - 0.45 * made**3
        total = sum(self.weights)
        if total:
            self.weights = [w / total for w in self.weights]

    def _samples(self):
        total = sum(self.weights)
        if total <= 0:
            return []
        cdf = []
        mass = 0
        for w in self.weights:
            mass += w
            cdf.append(mass)
        known = set(self.hand + self.board)
        remaining = [c for c in _DECK if c not in known]
        rows = []
        for sample in range(self.samples):
            if sample % 16 == 0:
                self._check_budget()
            i = min(len(cdf) - 1, bisect.bisect_left(cdf, self.rng.random() * total))
            hole = self.holes[i]
            if len(self.board) == 5:
                board = self.board
            else:
                future = self.rng.sample(
                    [c for c in remaining if c not in hole], 5 - len(self.board)
                )
                board = self.board + future
            mine = evaluate(self.hand + board)
            theirs = evaluate(list(hole) + board)
            equity = 1.0 if mine > theirs else 0.5 if mine == theirs else 0.0
            rows.append((equity, max(self.features[i], self.draws[i])))
        return rows

    def _raise(self, g, amount):
        if not g.can_raise:
            return PlayerAction.call(self.player_id)
        low, high = g.raise_range
        return PlayerAction.raise_to(self.player_id, int(min(high, max(low, amount))))

    def run(self, actions, g):
        self._deadline = time.perf_counter() + 1.75
        try:
            return self._decide(actions, g)
        except _BudgetExceeded:
            self.budget_fallbacks += 1
            self._desynced = True
            return self._fallback(g)

    def _decide(self, actions, g):
        if g.num_players != 2:
            return PlayerAction.call(self.player_id)
        if not self._replay(actions, g):
            return self._fallback(g)
        me = self.player_id
        call = PlayerAction.call(me)
        fold = PlayerAction.fold(me)
        style = self.STYLE
        strength = _preflop_strength(self.hand)
        ip = me == g.button
        # Broad but structured heads-up opening ranges, independent of MC noise.
        if not self.board and g.current_bet <= g.big_blind and g.can_raise:
            if ip:
                cutoff = (
                    0.12
                    if style == "pressure"
                    else 0.25
                    if style == "defensive"
                    else 0.18
                )
                if strength > cutoff:
                    return self._raise(
                        g, (2.5 if style == "defensive" else 2.25) * g.big_blind
                    )
                return call if strength > 0.07 or not g.call_amount else fold
            if strength > (0.46 if style == "pressure" else 0.60):
                return self._raise(g, 4 * g.big_blind)
        rows = self._samples()
        if not rows:
            return call if not g.call_amount else fold
        equity = sum(x[0] for x in rows) / len(rows)
        cost = g.call_amount
        effective = min(g.stacks[me], g.stacks[self.other] + cost)
        # Remove the other player's uncallable excess from the contested pot.
        unmatched = max(0, g.bets[self.other] - (g.bets[me] + g.stacks[me]))
        pot = g.pot - unmatched
        terminal = (
            len(self.board) == 5 or g.stacks[self.other] == 0 or cost == g.stacks[me]
        )
        realization = 1 if terminal else (0.97 if ip else 0.92)
        if not self.board and not terminal:
            realization -= 0.025
        call_value = realization * equity * (pot + cost) - cost
        values = [(call_value, None)]
        if cost:
            values.append((0, "fold"))
        if g.can_raise:
            low, high = g.raise_range
            fractions = (0.33, 0.67, 1.0) if style != "pressure" else (0.5, 1.0, 1.5)
            targets = {
                min(high, max(low, g.bets[me] + cost + round(f * (pot + cost))))
                for f in fractions
            }
            if effective <= 2.5 * (pot + cost) or equity > 0.83:
                targets.add(high)
            fold_rate = self.stat["fold"] / self.stat["faced"]
            # Calibrate fold forecasts against public replies to our own bets.
            # Separate preflop/postflop and initial raises/re-raises.
            context = (bool(self.board), self.raises >= 2)
            observed, predicted, count = self.stat["responses"].get(context, (0, 0, 0))
            fold_scale = min(1.5, max(0.1, (observed + 3) / (predicted + 3)))
            predictions = {}
            for target in targets:
                self._check_budget()
                invested = target - g.bets[me]
                opponent_cost = min(
                    g.stacks[self.other], max(0, target - g.bets[self.other])
                )
                risk = min(invested, effective)
                fraction = opponent_cost / max(1, pot + invested)
                cutoff = 0.17 + 1.1 * fraction + 0.36 * (fold_rate - 0.37)
                if self.raises >= 1:
                    cutoff += 0.06
                base_folds = 0
                aggregate = 0
                called_mass = 0
                called_equity = 0
                for sample, (outcome, made) in enumerate(rows):
                    if sample % 64 == 0:
                        self._check_budget()
                    continuing = 0.05 + 0.95 * _sigmoid((made - cutoff) / 0.14)
                    base_folds += 1 - continuing
                    continuing = 1 - min(0.95, max(0.01, (1 - continuing) * fold_scale))
                    called_mass += continuing
                    called_equity += continuing * outcome
                    leaf_r = (
                        1
                        if invested >= effective or len(self.board) == 5
                        else realization
                    )
                    aggregate += (1 - continuing) * pot + continuing * (
                        leaf_r * outcome * (pot + risk + opponent_cost) - risk
                    )
                predictions[target] = base_folds / len(rows)
                score = aggregate / len(rows)
                conditional = called_equity / max(1e-9, called_mass)
                # Avoid blindly overvaluing marginal raises that permit reraises.
                if invested < effective:
                    score -= 0.07 * invested * max(0, 0.67 - conditional)
                if style == "defensive" and conditional < 0.55:
                    score -= 0.08 * risk
                if style == "pressure" and self._has_blocker() and conditional < 0.55:
                    score += 0.025 * pot
                # Require a small edge for voluntary large bets; calling remains
                # available and protects medium-strength showdown hands.
                score -= 0.012 * risk
                values.append((score, target))
        _, target = max(values, key=lambda x: x[0])
        if target == "fold":
            return fold
        if target is None:
            return call
        self.pending_response = (context, predictions[target])
        return self._raise(g, target)

    def _has_blocker(self):
        if not self.board:
            return any(c.value == 14 for c in self.hand)
        suit_counts = {s: sum(c.suit == s for c in self.board) for s in "CDHS"}
        return any(c.value >= 13 and suit_counts[c.suit] >= 2 for c in self.hand)

    def end_game(self, actions, g):
        if self._done:
            return
        self._deadline = time.perf_counter() + 1.75
        try:
            self._finish_hand(actions, g)
        except _BudgetExceeded:
            self.budget_fallbacks += 1
            self._desynced = True
        finally:
            self.hands_played += 1
            self._done = True

    def _finish_hand(self, actions, g):
        if g.num_players != 2 or self._desynced:
            return
        if not self.ready:
            if g.street != "preflop":
                return
            awards = [0, 0]
            for action in actions:
                if action.kind == "win":
                    awards[action.player_id] += action.amount
            if not self._start(g, awards):
                return
        if not self._replay(actions, g):
            return
        shown = self.shown.get(self.other, [])
        if len(shown) == 2 and self.last_bet is not None:
            board, size = self.last_bet
            if len(board) >= 3:
                rank = evaluate(shown + list(board))
                self.stat["show_bet"] += 1
                self.stat["show_bluff"] += rank[0] == 0
