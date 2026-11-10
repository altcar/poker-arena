"""An adaptive, range-aware Hold'em bot for the Manchester AI hackathon.

Only public game information is used. Persistent state is plain pickleable
Python data; no files, network, external models or runtime dependencies.
"""
import math
import random

from river_policy import choose_river_bet
from player_action import PlayerAction
from poker_math import (all_hands, draw_strength, encode, made_strength,
                        preflop_strength, sample_equity)


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None
        self.opponents = {}
        self.random_state = random.Random(257).getstate()
        self.samples = 480
        self._reset_hand()

    def _reset_hand(self):
        self.cards = []
        self.seen_board = []
        self.tracked_bets = []
        self.tracked_pot = 0
        self.ranges = {}
        self.preflop_raises = 0
        self.last_aggressor = None
        self.hand_players = set()
        self.hand_raisers = set()
        self.ready = False

    def seed(self, hand, player_id):
        self._reset_hand()
        self.hand = list(hand)
        self.cards = [encode(card) for card in hand]
        self.player_id = player_id

    def _stats(self, team):
        if team not in self.opponents:
            self.opponents[team] = dict(hands=0, entered=0, raised_hands=0,
                                       decisions=0, raises=0, faced=0,
                                       folds=0, calls=0, pre_faced=0, pre_folds=0,
                                       river_faced=0, river_folds=0)
        return self.opponents[team]

    def _profile(self, team):
        s = self._stats(team)
        # Shrink towards ordinary play until there is enough evidence.
        aggression = (s['raises'] + 2) / (s['decisions'] + 8)
        folding = (s['folds'] + 3) / (s['faced'] + 8)
        loose = (s['entered'] + 5) / (s['hands'] + 10)
        return aggression, folding, loose

    def _prepare(self, state):
        if self.ready:
            return
        self.ready = True
        self.tracked_bets = [0] * state.num_players
        small = state.button if state.num_players == 2 else (state.button + 1) % state.num_players
        big = (small + 1) % state.num_players
        for p, blind in ((small, state.small_blind), (big, state.big_blind)):
            # On the first preflop turn, stacks + bets reconstructs the
            # starting stack even if someone has already raised all-in.
            chips = state.stacks[p] + state.bets[p] if state.street == 'preflop' else blind
            self.tracked_bets[p] = min(blind, chips)
        self.tracked_pot = sum(self.tracked_bets)
        hands = all_hands(set(self.cards))
        for p in range(state.num_players):
            if p != self.player_id:
                self.ranges[p] = (hands, [1.0] * len(hands))
                self._stats(state.teams[p])

    def _update_range(self, player, kind, owed, amount, state):
        if kind == 'fold' or (kind == 'call' and owed == 0):
            return
        hands, weights = self.ranges[player]
        aggression, _, loose = self._profile(state.teams[player])
        # A player who bets almost every turn supplies little hand evidence.
        uninformative = min(0.97, max(0.0, (aggression - 0.25) / 0.65))
        board = tuple(self.seen_board)
        blocked = set(board)
        ratio = amount / max(self.tracked_pot, state.big_blind)
        if not board:
            cutoff = 0.56 + (0.045 if self.preflop_raises > 0 else 0.0)
            cutoff += min(0.06, max(0.0, amount / state.big_blind - 4) * 0.003)
            cutoff -= max(0.0, loose - 0.5) * 0.08
        else:
            cutoff = 0.57 + min(0.17, max(0.0, ratio - 0.5) * 0.10)
        maximum = 0.0
        for i, pair in enumerate(hands):
            if pair[0] in blocked or pair[1] in blocked:
                weights[i] = 0.0
                continue
            if weights[i] == 0:
                continue
            strength = preflop_strength(pair) if not board else made_strength(pair, board)
            if kind == 'raise':
                width = 0.035 if not board else 0.09
                value = 1.0 / (1.0 + math.exp(max(-30.0, min(30.0, (cutoff - strength) / width))))
                draw = draw_strength(pair, board) if board else 0.0
                likelihood = 0.07 + 0.93 * min(1.0, value + draw)
            else:
                threshold = cutoff - (0.08 if not board else 0.22)
                value = 1.0 / (1.0 + math.exp(max(-30.0, min(30.0, (threshold - strength) / 0.07))))
                likelihood = 0.30 + 0.70 * value
                # Frequent callers continue with weak holdings too.
                uninformative = max(uninformative, max(0.0, loose - 0.5))
            weights[i] *= uninformative + (1.0 - uninformative) * likelihood
            maximum = max(maximum, weights[i])
        if maximum:
            for i in range(len(weights)):
                weights[i] /= maximum

    def _observe(self, actions, state, update_ranges=True):
        self._prepare(state)
        for action in actions:
            p, kind = action.player_id, action.kind
            if kind == 'reveal_card':
                if p == -1:
                    self.seen_board.append(encode(action.card))
                    if len(self.seen_board) in (3, 4, 5):
                        self.tracked_bets = [0] * state.num_players
                continue
            if kind not in ('fold', 'call', 'raise'):
                continue
            current = max(self.tracked_bets)
            owed = current - self.tracked_bets[p]
            amount = action.amount - current if kind == 'raise' else owed
            if p != self.player_id:
                s = self._stats(state.teams[p])
                if update_ranges:
                    self._update_range(p, kind, owed, amount, state)
                s['decisions'] += 1
                if owed > 0:
                    s['faced'] += 1
                    s['folds'] += kind == 'fold'
                    s['calls'] += kind == 'call'
                    if not self.seen_board:
                        s['pre_faced'] += 1
                        s['pre_folds'] += kind == 'fold'
                    if len(self.seen_board) == 5:
                        s['river_faced'] += 1
                        s['river_folds'] += kind == 'fold'
                s['raises'] += kind == 'raise'
                if not self.seen_board and (kind == 'raise' or (kind == 'call' and owed > 0)):
                    self.hand_players.add(p)
                if not self.seen_board and kind == 'raise':
                    self.hand_raisers.add(p)
            if kind == 'raise':
                self.tracked_pot += action.amount - self.tracked_bets[p]
                self.tracked_bets[p] = action.amount
                self.last_aggressor = p
                if not self.seen_board:
                    self.preflop_raises += 1
            elif kind == 'call':
                self.tracked_pot += owed
                self.tracked_bets[p] = current

    def _raise(self, state, total):
        if not state.can_raise:
            return PlayerAction.call(self.player_id)
        low, high = state.raise_range
        return PlayerAction.raise_to(self.player_id, int(min(high, max(low, round(total)))))

    def run(self, actions, game_state):
        state = game_state
        self._observe(actions, state)
        rng = random.Random()
        rng.setstate(self.random_state)
        opponents = [p for p in state.players_in_hand if p != self.player_id]
        board = [encode(card) for card in state.board]
        samples = self.samples if len(opponents) == 1 else 240
        equity = sample_equity(self.cards, board, [self.ranges[p] for p in opponents], rng, samples)
        action = self._decide(state, opponents, board, equity, rng)
        self.random_state = rng.getstate()
        return action

    def _decide(self, state, opponents, board, equity, rng):
        me = self.player_id
        call, pot, bb = state.call_amount, state.pot, state.big_blind
        heads_up = len(opponents) == 1
        in_position = state.button == me
        profiles = [self._profile(state.teams[p]) for p in opponents]
        aggression = sum(p[0] for p in profiles) / max(1, len(profiles))
        fold_rate = sum(p[1] for p in profiles) / max(1, len(profiles))
        # Exclude an opponent's uncallable overbet from heads-up pot odds.
        contestable = pot
        if heads_up:
            opp = opponents[0]
            contestable -= max(0, state.bets[opp] - state.bets[me] - state.stacks[me])
        odds = call / max(1, contestable + call)
        all_in_call = call == state.stacks[me] or all(state.stacks[p] == 0 for p in opponents)
        station = False
        if heads_up:
            stats = self._stats(state.teams[opponents[0]])
            station = (stats['faced'] >= 12
                       and stats['folds'] / stats['faced'] < 0.08
                       and stats['raises'] / max(1, stats['decisions']) < 0.08)
        if not board:
            unopened = self.preflop_raises == 0
            if unopened and heads_up:
                if station and equity > 0.61:
                    return self._raise(state, state.max_raise_to)
                if (in_position and stats['pre_faced'] >= 8
                        and stats['pre_folds'] / stats['pre_faced'] > 0.70):
                    return self._raise(state, 2 * bb)
                # Open a wide button range; expand against habitual folders.
                threshold = 0.44 - max(0.0, fold_rate - 0.5) * 0.15
                if in_position:
                    if equity >= threshold:
                        return self._raise(state, 2.5 * bb)
                    return PlayerAction.call(me) if equity > 0.37 and fold_rate < 0.4 else PlayerAction.fold(me)
                # Big blind: take the free flop, or isolate a limp for value.
                if equity > 0.61 or (fold_rate > 0.65 and equity > 0.43):
                    return self._raise(state, 3.5 * bb)
                return PlayerAction.call(me)
            if state.can_raise and equity > (0.70 if heads_up else 0.78):
                total = state.current_bet + max(2 * bb, 0.85 * (pot + call))
                if equity > 0.79 or state.max_raise_to < total * 1.4:
                    total = state.max_raise_to
                return self._raise(state, total)
            margin = 0.015 if all_in_call else (0.045 if in_position else 0.075)
            if aggression > 0.6:
                margin *= 0.4
            if call == 0 or equity >= odds + margin:
                return PlayerAction.call(me)
            return PlayerAction.fold(me)

        if call:
            # Equity is measured against the betting range, not a random hand.
            value_threshold = 0.78 if heads_up else 0.86
            if state.can_raise and equity > value_threshold:
                total = state.current_bet + 0.85 * (pot + call)
                if equity > 0.90 or state.max_raise_to < total * 1.35:
                    total = state.max_raise_to
                return self._raise(state, total)
            margin = 0.012 if all_in_call or len(board) == 5 else (0.035 if in_position else 0.055)
            if call <= 0.15 * pot:
                margin *= 0.5
            if equity >= odds + margin:
                return PlayerAction.call(me)
            return PlayerAction.fold(me)

        if not state.can_raise:
            return PlayerAction.call(me)
        if heads_up and len(board) == 5:
            river_fold = (stats['river_folds'] + 6 * fold_rate) / (stats['river_faced'] + 6)
            bet = choose_river_bet(
                self.cards, board, self.ranges[opponents[0]], pot,
                min(state.min_raise_to - state.bets[me],
                    state.stacks[opponents[0]]),
                min(state.stacks[me], state.stacks[opponents[0]]),
                river_fold, aggression, bb,
            )
            return self._raise(state, state.bets[me] + bet) if bet else PlayerAction.call(me)
        if station and equity > 0.70:
            return self._raise(state, state.max_raise_to)
        value_threshold = (0.59 if len(board) == 5 else 0.63) if heads_up else 0.74
        if equity > value_threshold:
            fraction = 0.85 if equity > 0.83 else 0.55
            if fold_rate < 0.25:
                fraction = 1.0 if equity > 0.78 else 0.70
            total = state.bets[me] + max(bb, fraction * pot)
            if equity > 0.86 and state.stacks[me] < 1.4 * pot:
                total = state.max_raise_to
            return self._raise(state, total)
        # Small selective bluffs; never bluff a known calling station or a
        # multiway pot. Draws provide equity when our semi-bluff gets called.
        if heads_up and fold_rate > 0.32:
            draw = draw_strength(self.cards, board)
            chance = 0.10 + max(0.0, fold_rate - 0.45) * 0.7
            chance += 0.13 if draw > 0.1 else 0.0
            chance += 0.08 if in_position else 0.0
            if (equity < 0.40 or draw > 0.1) and rng.random() < chance:
                return self._raise(state, state.bets[me] + max(bb, 0.45 * pot))
        return PlayerAction.call(me)

    def end_game(self, actions, game_state):
        self._observe(actions, game_state, update_ranges=False)
        for p, team in enumerate(game_state.teams):
            if p == self.player_id:
                continue
            s = self._stats(team)
            s['hands'] += 1
            s['entered'] += p in self.hand_players
            s['raised_hands'] += p in self.hand_raisers
