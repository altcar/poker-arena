"""Adaptive heads-up bot using public actions and sampled range equity.

All persistent state is plain data. Runtime needs only the standard library,
the public Card/PlayerAction API, and poker_strength.py.
"""

from bisect import bisect_left, bisect_right
from math import exp
import random

from player_action import PlayerAction
from poker_strength import (
    COMBOS, PREFLOP_PERCENTILES, card_id, draw_potential, rank_hand,
)


def _sigmoid(x):
    return 1.0 / (1.0 + exp(-max(-25.0, min(25.0, x))))


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None
        self.profiles = {}
        self.random_state = random.Random(917431).getstate()
        self.samples = 1000
        self.ready = False

    def seed(self, hand, player_id):
        self.hand = list(hand)
        self.player_id = player_id
        self.hole = tuple(card_id(c) for c in hand)
        self.ready = False
        self.board = []
        self.ranges = {}
        self.first_preflop = set()
        self.voluntary_preflop = set()
        self.raising_preflop = set()
        self.preflop_raises = 0
        self.street_raises = 0
        self.our_raises = 0
        self.shown = {}

    def _begin(self, state):
        if self.ready:
            return
        self.ready = True
        self.names = list(state.teams)
        self.bets = [0] * state.num_players
        small = state.button if state.num_players == 2 else (state.button + 1) % state.num_players
        self.bets[small] = state.small_blind
        self.bets[(small + 1) % state.num_players] = state.big_blind
        self.pot = sum(self.bets)
        for p, name in enumerate(self.names):
            if p == self.player_id:
                continue
            if name not in self.profiles:
                self.profiles[name] = dict(pre=0, vpip=0, pfr=0, overbet=0,
                                           decisions=0, bets=0, faced=0,
                                           folds=0, calls=0, checks=0, hands=0,
                                           river_faced=0, river_folds=0)
            self.profiles[name]["hands"] += 1
            self.ranges[p] = [0.0 if a in self.hole or b in self.hole else 1.0
                              for a, b in COMBOS]

    def _style(self, p):
        stats = self.profiles[self.names[p]]
        n = stats["pre"]
        vpip = (stats["vpip"] + 4) / (n + 6)
        pfr = (stats["pfr"] + 1.5) / (n + 6)
        aggression = (stats["bets"] + 2) / (stats["decisions"] + 8)
        folding = (stats["folds"] + 2) / (stats["faced"] + 5)
        # Large raises observed on many different hands imply a wider range.
        wild = n >= 8 and stats["overbet"] / max(1, n) > 0.22
        station = stats["faced"] >= 10 and folding < 0.17 and pfr < 0.18
        return vpip, pfr, aggression, folding, wild, station

    def _observe(self, actions, state):
        self._begin(state)
        for action in actions:
            p = action.player_id
            if action.kind == "reveal_card":
                c = card_id(action.card)
                if p == -1:
                    self.board.append(c)
                    for weights in self.ranges.values():
                        for i, hand in enumerate(COMBOS):
                            if c in hand:
                                weights[i] = 0.0
                    if len(self.board) >= 3:
                        self.bets = [0] * len(self.bets)
                        self.street_raises = 0
                        self.our_raises = 0
                else:
                    self.shown.setdefault(p, []).append(c)
                continue
            if action.kind == "win":
                continue
            current = max(self.bets)
            owed = max(0, current - self.bets[p])
            price = owed / max(1, self.pot + owed)
            raise_ratio = max(0, action.amount - current) / max(1, self.pot + owed)
            preflop = len(self.board) < 3
            if p != self.player_id:
                stats = self.profiles[self.names[p]]
                if preflop and p not in self.first_preflop:
                    stats["pre"] += 1
                    self.first_preflop.add(p)
                if preflop:
                    if p not in self.voluntary_preflop and (action.kind == "raise" or (action.kind == "call" and owed > 0)):
                        stats["vpip"] += 1
                        self.voluntary_preflop.add(p)
                    if action.kind == "raise" and p not in self.raising_preflop:
                        stats["pfr"] += 1
                        stats["overbet"] += int(raise_ratio > 2.5)
                        self.raising_preflop.add(p)
                if owed:
                    stats["faced"] += 1
                    stats["folds"] += int(action.kind == "fold")
                    stats["calls"] += int(action.kind == "call")
                    if len(self.board) == 5:
                        stats["river_faced"] = stats.get("river_faced", 0) + 1
                        stats["river_folds"] = stats.get("river_folds", 0) + int(action.kind == "fold")
                elif action.kind == "call":
                    stats["checks"] += 1
                if not preflop:
                    stats["decisions"] += 1
                    stats["bets"] += int(action.kind == "raise")
                if action.kind != "fold":
                    self._update_range(p, action.kind, owed, price, raise_ratio)
            if action.kind == "raise":
                self.pot += max(0, action.amount - self.bets[p])
                self.bets[p] = action.amount
                self.street_raises += 1
                if preflop:
                    self.preflop_raises += 1
                if p == self.player_id:
                    self.our_raises += 1
            elif action.kind == "call":
                self.pot += owed
                self.bets[p] += owed

    def _update_range(self, p, kind, owed, price, raise_ratio):
        weights = self.ranges[p]
        vpip, pfr, aggression, folding, wild, station = self._style(p)
        preflop = len(self.board) < 3
        if kind == "call" and not owed:
            # A check is weak evidence: protect slowplays and drawing hands.
            return
        if not preflop:
            ranks = [(rank_hand(hand + tuple(self.board)), i)
                     for i, hand in enumerate(COMBOS) if weights[i] > 0]
            # Compare against ALL available hands, not just our posterior.
            ordered = sorted(rank_hand(hand + tuple(self.board))
                             for hand in COMBOS if not any(c in self.hole or c in self.board for c in hand))
            strengths = {i: (bisect_left(ordered, r) + bisect_right(ordered, r)) / (2 * len(ordered))
                         for r, i in ranks}
        for i, hand in enumerate(COMBOS):
            if weights[i] <= 0:
                continue
            if preflop:
                q = PREFLOP_PERCENTILES[i]
                if kind == "raise":
                    width = max(0.07, min(0.90, pfr))
                    if self.preflop_raises:
                        width *= 0.48
                    if raise_ratio > 2.5 and not wild:
                        width *= 0.6
                    likelihood = 0.035 + 0.965 * _sigmoid((q - (1 - width)) / 0.065)
                    if wild:
                        likelihood = 0.65 + 0.35 * q
                else:
                    width = min(0.98, max(vpip, 0.40))
                    threshold = 1 - width + max(0.0, price - 0.28) * 0.6
                    likelihood = 0.08 + 0.92 * _sigmoid((q - threshold) / 0.12)
                    if station or wild:
                        likelihood = 0.8 + 0.2 * q
            else:
                q = strengths[i]
                draws = draw_potential(hand, self.board)
                if kind == "raise":
                    threshold = 0.81 - 0.38 * aggression
                    threshold += 0.06 * (raise_ratio > 1.2)
                    threshold += 0.07 * (self.street_raises > 0)
                    bluff = 0.025 + 0.16 * aggression
                    likelihood = bluff + (1 - bluff) * _sigmoid((q + draws - threshold) / 0.07)
                    if wild:
                        likelihood = 0.65 + 0.35 * q
                else:
                    threshold = 0.22 + price * 0.75
                    likelihood = 0.10 + 0.90 * _sigmoid((q + draws - threshold) / 0.12)
                    if station:
                        likelihood = 0.85 + 0.15 * q
            weights[i] *= likelihood
        peak = max(weights)
        if peak > 0:
            self.ranges[p] = [w / peak for w in weights]

    def _equity(self, opponents, board, rng, samples):
        """Sample blocked, weighted opponent ranges and possible runouts."""
        distributions = []
        for p in opponents:
            hands, cdf, total = [], [], 0.0
            for hand, weight in zip(COMBOS, self.ranges[p]):
                if weight > 0 and not any(c in self.hole or c in board for c in hand):
                    hands.append(hand)
                    total += weight
                    cdf.append(total)
            distributions.append((hands, cdf, total))
        if any(total <= 0 for _, _, total in distributions):
            return 0.0  # no possible hand left for someone: nothing to sample
        if len(board) == 5 and len(opponents) == 1:
            # On the river heads-up equity can be exact, including all ties.
            mine = rank_hand(self.hole + tuple(board))
            wins = 0.0
            hands, cdf, total = distributions[0]
            previous = 0.0
            for hand, cumulative in zip(hands, cdf):
                weight = cumulative - previous
                previous = cumulative
                theirs = rank_hand(hand + tuple(board))
                wins += weight * (int(mine > theirs) + 0.5 * int(mine == theirs))
            return wins / total if total else 0.0
        wins = 0.0
        completed = 0
        for _ in range(samples):
            used = set(self.hole + tuple(board))
            holes = []
            for hands, cdf, total in distributions:
                chosen = None
                for attempt in range(40):
                    h = hands[min(len(hands) - 1, bisect_left(cdf, rng.random() * total))]
                    if h[0] not in used and h[1] not in used:
                        chosen = h
                        break
                if chosen is None:
                    break
                used.update(chosen)
                holes.append(chosen)
            if len(holes) != len(opponents):
                continue
            runout = rng.sample([c for c in range(52) if c not in used], 5 - len(board))
            final_board = tuple(board) + tuple(runout)
            mine = rank_hand(self.hole + final_board)
            theirs = [rank_hand(h + final_board) for h in holes]
            best = max(theirs)
            wins += int(mine > best) + (1 / (1 + theirs.count(best)) if mine == best else 0)
            completed += 1
        return wins / completed if completed else 0.0

    def _river_action(self, state, opponent, equity, roll):
        """Compare check/call/fold with bets against a modeled calling range.

        This is an approximate response model, not a solved equilibrium.
        All hand ranks and card removal are exact on the heads-up river.
        """
        me = self.player_id
        call = state.call_amount
        pot = state.pot - max(0, state.bets[opponent] - state.bets[me] - call)
        call_ev = equity * (pot + call) - call
        best_ev = max(0.0, call_ev)
        best = PlayerAction.call(me) if call == 0 or call_ev >= 0 else PlayerAction.fold(me)
        if not state.can_raise:
            return best
        vpip, pfr, aggression, folding, wild, station = self._style(opponent)
        stats = self.profiles[self.names[opponent]]
        # Shrink sparse river observations towards the broader fold history.
        faced = stats.get("river_faced", 0)
        folding = (stats.get("river_folds", 0) + 10 * folding) / (faced + 10)
        low, high = state.raise_range
        cap = min(high, state.bets[opponent] + state.stacks[opponent])
        if cap < low:
            return best
        # Restrict unproven bluffs; known folders can be attacked more often.
        if equity < 0.50 and roll > min(0.85, 0.10 + max(0, folding - 0.40) * 1.5):
            return best
        if equity < 0.50 and (station or wild):
            return best
        board = tuple(card_id(c) for c in state.board)
        mine = rank_hand(self.hole + board)
        hands = [(rank_hand(hand + board), weight)
                 for hand, weight in zip(COMBOS, self.ranges[opponent])
                 if weight > 0 and not any(c in self.hole or c in board for c in hand)]
        ordered = sorted(rank for rank, _ in hands)
        total_weight = sum(weight for _, weight in hands)
        if not total_weight:
            return best
        ranked = [(rank, weight,
                   (bisect_left(ordered, rank) + bisect_right(ordered, rank)) / (2 * len(ordered)))
                  for rank, weight in hands]
        sizes = {low}
        for fraction in (0.33, 0.60, 1.0, 1.5):
            sizes.add(min(cap, max(low, int(state.current_bet + fraction * (pot + call)))))
        if equity > 0.90 or station:
            sizes.add(cap)
        minimum_bet_ev = best_ev + max(0.025 * pot, 0.20 * state.big_blind)
        for total in sorted(sizes):
            cost = total - state.bets[me]
            other_cost = min(state.stacks[opponent], total - state.bets[opponent])
            odds = other_cost / max(1, pot + cost + other_cost)
            threshold = max(folding, odds + 0.08)
            # Increasing prices should never make a fixed hand call more often.
            threshold += 0.10 * max(0, cost / max(1, pot + call) - 0.60)
            ev = 0.0
            for rank, weight, strength in ranked:
                response = 0.025 + 0.975 * _sigmoid((strength - threshold) / 0.10)
                if station:
                    response = 0.98
                elif wild:
                    response = 0.80 + 0.20 * response
                share = int(mine > rank) + 0.5 * int(mine == rank)
                called_ev = share * (pot + cost + other_cost) - cost
                ev += weight * ((1 - response) * pot + response * called_ev)
            ev /= total_weight
            # Demand an edge to offset uncertainty in predicted calls/folds.
            if ev > minimum_bet_ev and ev > best_ev:
                best_ev = ev
                best = PlayerAction.raise_to(me, total)
        return best

    def run(self, actions, game_state):
        state = game_state
        self._observe(actions, state)
        rng = random.Random()
        rng.setstate(self.random_state)
        opponents = [p for p in state.players_in_hand if p != self.player_id]
        board = tuple(card_id(c) for c in state.board)
        samples = self.samples if len(opponents) == 1 else 250
        equity = self._equity(opponents, board, rng, samples)
        roll = rng.random()
        self.random_state = rng.getstate()
        me = self.player_id
        if state.street == "river" and len(opponents) == 1:
            return self._river_action(state, opponents[0], equity, roll)
        call = state.call_amount
        pot = state.pot
        bb = state.big_blind
        preflop = state.street == "preflop"
        in_position = me == state.button
        styles = [self._style(p) for p in opponents]
        wild = any(s[4] for s in styles)
        station = all(s[5] for s in styles)
        folding = sum(s[3] for s in styles) / len(styles)
        aggression = sum(s[2] for s in styles) / len(styles)
        # Cap the pot at chips we can contest, excluding an uncalled overbet.
        effective_pot = pot
        if len(opponents) == 1:
            effective_pot -= max(0, state.bets[opponents[0]] - state.bets[me] - call)
        odds = call / max(1, effective_pot + call)
        all_in_call = call >= state.stacks[me] or all(state.stacks[p] == 0 for p in opponents)
        # Future betting reduces realization for weak hands out of position.
        margin = 0.015 if all_in_call or state.street == "river" else 0.035
        if not in_position and not all_in_call:
            margin += 0.025
        if wild or station:
            margin *= 0.4
        if len(opponents) > 1:
            margin += 0.025
        can_call = call == 0 or equity > odds + margin
        if not can_call:
            return PlayerAction.fold(me)
        if not state.can_raise:
            return PlayerAction.call(me)
        low, high = state.raise_range
        # Never put chips beyond the largest opponent's reachable total.
        effective_high = min(high, max(state.bets[p] + state.stacks[p] for p in opponents))
        effective_high = max(low, effective_high)
        remaining = effective_high - state.bets[me]
        spr = remaining / max(1, pot + call)
        if preflop:
            # Steal frequently on the button; size up for value against callers.
            if self.preflop_raises == 0:
                threshold = 0.48 if in_position else 0.57
                if folding > 0.65:
                    threshold = 0.0
                if len(opponents) > 1:
                    threshold += 0.12
                if equity >= threshold:
                    total = (2.5 if in_position else 3.5) * bb
                    if station and equity > 0.62:
                        total = 5 * bb
                    if wild and equity < 0.65:
                        return PlayerAction.call(me)
                    return self._raise(state, total, effective_high)
            # Three-bet with strong range equity, avoid speculative raise wars.
            value_threshold = 0.67 if wild else 0.69
            if equity > value_threshold and self.our_raises < 2:
                if spr < 2.8 or equity > 0.82 or wild:
                    return self._raise(state, effective_high, effective_high)
                return self._raise(state, state.current_bet + 0.9 * (pot + call), effective_high)
            return PlayerAction.call(me)
        # Strong hands build a pot. Calling stations pay larger value bets.
        threshold = 0.61 if call == 0 else 0.70
        if state.street == "river":
            threshold += 0.035
        if len(opponents) > 1:
            threshold += 0.06
        if station:
            threshold -= 0.045
        if wild and call and equity < 0.82:
            # Let frequent bluffers continue betting rather than fold them out.
            return PlayerAction.call(me)
        if equity > threshold and self.our_raises < 2:
            fraction = 0.60
            if equity > 0.80:
                fraction = 0.95
            if station:
                fraction = 1.2 if equity > 0.78 else 0.85
            if call:
                fraction = 0.80
            total = state.current_bet + fraction * (pot + call)
            if spr < 1.5 and equity > 0.73:
                total = effective_high
            if station and equity > 0.90:
                total = effective_high
            return self._raise(state, total, effective_high)
        # Small, selective bluffs with fold equity; do not bluff calling stations.
        if not call and len(opponents) == 1 and not station and not wild and self.our_raises == 0:
            draw = draw_potential(self.hole, board)
            bluff_frequency = 0.10 + max(0, folding - 0.4) * 0.7
            if state.street == "flop" and self.preflop_raises and in_position:
                bluff_frequency += 0.12
            if draw and state.street != "river":
                bluff_frequency += 0.18
            if equity < 0.48 and roll < bluff_frequency and folding > 0.32 and aggression < 0.55:
                return self._raise(state, state.current_bet + 0.5 * pot, effective_high)
        return PlayerAction.call(me)

    def _raise(self, state, total, effective_high):
        low, high = state.raise_range
        total = int(min(high, effective_high, max(low, round(total))))
        return PlayerAction.raise_to(self.player_id, total)

    def end_game(self, actions, game_state):
        self._observe(actions, game_state)
