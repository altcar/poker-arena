"""WhiteSpider, a heads-up no-limit Hold'em bot for Poker Arena.

Preflop: starting-hand chart. Postflop: weights the opponent's possible
hands by their betting history, then picks the check/call/fold/bet size
with the best expected value. Learns each team's fold and raise rates."""

from bisect import bisect_left, bisect_right
from itertools import combinations
from random import Random

from player_action import PlayerAction

from hunter_chart import STARTING
from hunter_eval import evaluate


SUITS = {"C": 0, "D": 1, "H": 2, "S": 3}
DECK = tuple(range(52))
COMBOS = tuple(combinations(DECK, 2))


def card_id(card):
    return 4 * (card.value - 2) + SUITS[card.suit]


def hand_class(hand):
    a, b = hand
    low, high = sorted((a // 4, b // 4))
    kind = 0 if low == high else 1 if a % 4 == b % 4 else 2
    return high, low, kind


def strength(hand, board):
    score = evaluate(board + hand)
    if len(board) == 5 or score >= 4 << 20:
        return score
    # Rank draws between adjacent portions of the made-hand distribution.
    cards = board + hand
    if any(sum(card % 4 == suit for card in cards) == 4 for suit in range(4)):
        score += 650000
    ranks = {card // 4 for card in cards}
    if 12 in ranks:
        ranks.add(-1)
    if any(sum(rank in ranks for rank in range(lo, lo + 5)) == 4
           for lo in range(-1, 9)):
        score += 220000
    return score


def opponent_equity(percentile, width, future):
    """Approximate equity against a strength-filtered range."""
    made = max(0.0, (percentile + width - 1.0) / width)
    return made * (1.0 - future) + future * (0.12 + 0.55 * percentile)


class Model:
    EDGE = 0.03  # extra EV a raise must show over the passive line (share of pot)
    def __init__(self):
        self.starting = STARTING
        self.profiles = {}
        self.hand_number = 0

    def seed(self, hand, player_id):
        self.hole = tuple(map(card_id, hand))
        self.me = player_id
        self.hand_number += 1
        self.board = ()
        self.bets = None
        self.history = []
        self.raises = 0
        self.own_calls = 0
        self.decision_number = 0

    def observe(self, actions, gs):
        if self.bets is None:
            self.bets = {gs.button: gs.small_blind, (gs.button + 1) % 2: gs.big_blind}
        profile = self.profiles.setdefault(gs.teams[1 - self.me], {
            "faced": 0,
            "folded": 0,
            "actions": 0,
            "raises": 0,
            "open_shoves": [0, 0],
            "reshoves": [0, 0],
        })
        for action in actions:
            seat, kind = action.player_id, action.kind
            if kind == "reveal_card" and seat == -1:
                self.board += (card_id(action.card),)
                if len(self.board) >= 3:
                    self.bets.clear()
            if kind not in ("call", "raise", "fold"):
                continue
            top = max(self.bets.values(), default=0)
            owed = top - self.bets.get(seat, 0)
            if kind == "raise":
                self.bets[seat] = action.amount
            elif kind == "call":
                self.bets[seat] = top
            if seat == self.me:
                self.raises += kind == "raise"
                self.own_calls += kind == "call"
                continue
            if owed:
                profile["faced"] += 1
                profile["folded"] += kind == "fold"
            profile["actions"] += 1
            profile["raises"] += kind == "raise"
            if not self.board:
                bucket = profile["reshoves" if self.raises else "open_shoves"]
                bucket[0] += 1
                bucket[1] += kind == "raise" and action.amount >= 8 * gs.big_blind
            self.history.append((self.board, kind, owed, action.amount, self.raises,
                                 self.own_calls))
        return profile

    def end_game(self, actions, game_state):
        if game_state.num_players == 2:
            self.observe(actions, game_state)

    def run(self, actions, game_state):
        gs = game_state
        if gs.num_players != 2:
            return PlayerAction.fold(self.me) if gs.call_amount else PlayerAction.call(self.me)
        profile = self.observe(actions, gs)
        self.decision_number += 1
        rng = Random(self.hand_number * 65537 + self.hole[0] * 53 + self.hole[1]
                     + self.decision_number * 1000003)
        if gs.street == "preflop":
            move = self.preflop(gs, profile, rng)
        else:
            move = self.postflop(gs, profile, rng)
        if move is None:
            return PlayerAction.fold(self.me) if gs.call_amount else PlayerAction.call(self.me)
        if move == 0:
            return PlayerAction.call(self.me)
        return PlayerAction.raise_to(self.me, move)

    def preflop(self, gs, profile, rng):
        bb = gs.big_blind
        equity, percentile = self.starting[hand_class(self.hole)]
        owed = gs.call_amount
        other = 1 - self.me
        effective = min(gs.stacks[self.me] + gs.bets[self.me],
                        gs.stacks[other] + gs.bets[other])
        low, high = gs.raise_range or (0, 0)
        high = min(high, max(low, effective))
        opponent_raises = [action for action in self.history if action[1] == "raise"]
        if not opponent_raises and gs.stacks[other] > 0:
            if low and percentile <= (0.88 if self.me == gs.button else 0.60):
                return min(high, max(low, 2 * bb if self.me == gs.button else 3 * bb))
            return 0 if not owed or equity >= 0.39 else None

        last = opponent_raises[-1] if opponent_raises else None
        opening = len(opponent_raises) == 1 and last[4] == 0 and last[3] <= 5 * bb
        if opening and low and percentile <= 0.16:
            return min(high, max(low, 3 * last[3]))

        if not owed:
            return 0
        excess = max(0, gs.bets[other] - gs.bets[self.me] - owed)
        odds = owed / (gs.pot - excess + owed)
        if opening:
            aggression = (profile["raises"] + 4) / (profile["actions"] + 15)
            width = max(0.22, min(0.85, 2 * aggression))
        else:
            width = 0.24 if last and last[4] else 0.55
            if last and last[3] >= 8 * bb:
                seen, shoves = profile["reshoves" if last[4] else "open_shoves"]
                width = max(width if last[4] else 0.25, (shoves + 1) / (seen + 4))
        eq = self.preflop_equity(width, rng) if width < 0.90 else equity
        all_in = not gs.stacks[other] or owed == gs.stacks[self.me]
        margin = 0.008 if all_in else (0.045 if self.me != gs.button else 0.015)
        if low and eq >= 0.61 and percentile <= 0.12:
            return high if effective <= 28 * bb else min(high, max(low, 3 * gs.current_bet))
        return 0 if eq >= odds + margin else None

    def preflop_equity(self, width, rng):
        pool = [hand for hand in COMBOS if not any(c in self.hole for c in hand)
                and self.starting[hand_class(hand)][1] <= width]
        wins = 0.0
        deck = [card for card in DECK if card not in self.hole]
        for _ in range(420):
            hand = rng.choice(pool)
            board = tuple(rng.sample([c for c in deck if c not in hand], 5))
            mine, theirs = evaluate(board + self.hole), evaluate(board + hand)
            wins += (mine > theirs) + 0.5 * (mine == theirs)
        return wins / 420

    def postflop(self, gs, profile, rng):
        board = tuple(map(card_id, gs.board))
        need = 5 - len(board)
        owed = gs.call_amount
        other = 1 - self.me
        excess = max(0, gs.bets[other] - gs.bets[self.me] - owed)
        pot = gs.pot - excess
        deck = [c for c in DECK if c not in board and c not in self.hole]
        pool = list(combinations(deck, 2))
        if need:
            pool = rng.sample(pool, 600)
        relevant = {action[0] for action in self.history if action[0]}
        relevant.add(board)
        percentiles = {}
        for street in relevant:
            scores = [strength(hand, street) for hand in pool]
            order = sorted(scores)
            percentiles[street] = [
                (bisect_left(order, score) + bisect_right(order, score)) / (2 * len(order))
                for score in scores
            ]

        low, high = gs.raise_range or (0, 0)
        if low:
            high = min(high, max(low, gs.bets[other] + gs.stacks[other]))
        top = gs.bets[self.me] + owed
        candidates = sorted({min(high, max(low, top + int(frac * (pot + owed))))
                             for frac in (0.0, 0.38, 0.72, 1.05)}) if low else []
        if low and high - top <= 2 * (pot + owed):
            candidates = sorted(set(candidates + [high]))
        weight_sum = win_sum = 0.0
        raise_ev = [0.0] * len(candidates)
        fold_rate = (profile["folded"] + 5) / (profile["faced"] + 12)
        for i, hand in enumerate(pool):
            _, pre_pct = self.starting[hand_class(hand)]
            weight = 1.0
            for street, action, facing, amount, hero_raises, hero_calls in self.history:
                if not street:
                    if action == "raise" and hero_raises:
                        cutoff = 0.30
                        weight *= 1.0 if pre_pct <= cutoff else 0.05
                    elif action == "raise":
                        aggression = (profile["raises"] + 4) / (profile["actions"] + 15)
                        cutoff = max(0.25, min(0.90, 2.0 * aggression))
                        if amount >= 8 * gs.big_blind:
                            seen, shoves = profile["open_shoves"]
                            cutoff = max(0.20, (shoves + 1) / (seen + 4))
                        weight *= 1.0 if pre_pct <= cutoff else 0.12
                    elif action == "call" and hero_raises:
                        weight *= 1.0 if pre_pct <= 0.65 else 0.45
                    continue
                if action == "fold":
                    continue
                p = percentiles[street][i]
                if action == "raise":
                    weight *= (0.17 + 0.83 * p ** (3 if facing else 2))
                elif facing:
                    weight *= 0.30 + 0.70 * p

            runout = tuple(rng.sample([c for c in deck if c not in hand], need)) if need else ()
            full = board + runout
            mine, theirs = evaluate(full + self.hole), evaluate(full + hand)
            win = (mine > theirs) + 0.5 * (mine == theirs)
            weight_sum += weight
            win_sum += weight * win
            p = percentiles[board][i]
            for j, size in enumerate(candidates):
                add = min(size - gs.bets[self.me], owed + gs.stacks[other])
                reply = add - owed
                odds = reply / (pot + add + reply)
                threshold = min(0.89, 0.38 + 1.05 * odds + 0.10 * bool(owed))
                call = min(0.98, max(0.10, 0.50 + (p - threshold) * 2.2))
                call = min(0.99, max(0.08, call + 0.40 - fold_rate))
                raise_ev[j] += weight * ((1 - call) * pot +
                                          call * (win * (pot + add + reply) - add))
        equity = win_sum / weight_sum
        passive = equity * (pot + owed) - owed
        if owed and need and gs.stacks[other] and owed < gs.stacks[self.me]:
            passive -= 0.025 * (pot + owed)
        best = max(0, passive)
        action = 0 if passive >= 0 or not owed else None
        for size, value in zip(candidates, raise_ev):
            risk = size - gs.bets[self.me]
            edge = self.EDGE * (pot + risk)
            if not need and not owed:
                edge *= 0.75
            adjusted = value / weight_sum - edge
            if adjusted > best:
                action, best = size, adjusted
        return action
