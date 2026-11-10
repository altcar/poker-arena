"""

Submission by Liam and Jacob.
Team Name: FoldToGPT

"""
from bisect import bisect_left, bisect_right
from itertools import combinations
from math import exp, log
import random

from player_action import PlayerAction


def _tables():
    straights, top = [], []
    for mask in range(8192):
        high = 0
        for r in range(12, 3, -1):
            if mask & (31 << (r - 4)) == 31 << (r - 4):
                high = r + 2
                break
        if not high and mask & 0x100F == 0x100F:
            high = 5
        straights.append(high)
        values = [r + 2 for r in range(12, -1, -1) if mask & (1 << r)][:5]
        score = 0
        for i in range(5):
            score = (score << 4) | (values[i] if i < len(values) else 0)
        top.append(score)
    return tuple(straights), tuple(top)


_STRAIGHT, _TOP = _tables()


def _rank(cards):
    one = two = three = four = 0
    suits = [0, 0, 0, 0]
    for c in cards:
        bit = 1 << (c >> 2)
        four |= three & bit
        three |= two & bit
        two |= one & bit
        one |= bit
        suits[c & 3] |= bit
    flush = 0
    for mask in suits:
        if mask.bit_count() >= 5:
            sh = _STRAIGHT[mask]
            if sh:
                return (8 << 20) | (sh << 16)
            flush = _TOP[mask]
            break
    if four:
        bit = 1 << (four.bit_length() - 1)
        return (7 << 20) | ((four.bit_length() + 1) << 16) | (((one ^ bit).bit_length() + 1) << 12)
    if three:
        tr = three.bit_length() + 1
        rest = two ^ (1 << (tr - 2))
        if rest:
            return (6 << 20) | (tr << 16) | ((rest.bit_length() + 1) << 12)
    if flush:
        return (5 << 20) | flush
    sh = _STRAIGHT[one]
    if sh:
        return (4 << 20) | (sh << 16)
    if three:
        rest = one ^ (1 << (tr - 2))
        return (3 << 20) | (tr << 16) | ((_TOP[rest] >> 4) & 0x0FF00)
    if two:
        hi = two.bit_length() + 1
        rest_pairs = two ^ (1 << (hi - 2))
        if rest_pairs:
            lo = rest_pairs.bit_length() + 1
            rest = one ^ (1 << (hi - 2)) ^ (1 << (lo - 2))
            return (2 << 20) | (hi << 16) | (lo << 12) | ((rest.bit_length() + 1) << 8)
        rest = one ^ (1 << (hi - 2))
        return (1 << 20) | (hi << 16) | ((_TOP[rest] >> 4) & 0x0FFF0)
    return _TOP[one]


def _card(card):
    return (card.value - 2) * 4 + 'CDHS'.index(card.suit)


def _key(a, b):
    h, l = sorted((a >> 2, b >> 2), reverse=True)
    return h * 13 + l if a % 4 == b % 4 or h == l else l * 13 + h

_PREFLOP = (0.503, 0.32583, 0.33142, 0.33883, 0.34108, 0.34658, 0.37667, 0.37717, 0.41458, 0.44792, 0.47275, 0.49925, 0.55333, 0.34675, 0.53708, 0.36042, 0.37042, 0.35683, 0.37533, 0.3765, 0.3965, 0.43008, 0.444, 0.48083, 0.50083, 0.56108, 0.3625, 0.39567, 0.57683, 0.37767, 0.37608, 0.38292, 0.39767, 0.41192, 0.44075, 0.45533, 0.49642, 0.52158, 0.56833, 0.388, 0.39608, 0.41817, 0.60483, 0.38892, 0.40408, 0.4235, 0.42325, 0.44242, 0.47108, 0.498, 0.53533, 0.57625, 0.384, 0.39292, 0.41517, 0.42442, 0.62975, 0.41992, 0.43592, 0.44892, 0.46483, 0.47767, 0.50133, 0.53442, 0.5855, 0.39075, 0.39933, 0.41408, 0.445, 0.456, 0.66242, 0.44925, 0.46442, 0.49217, 0.49283, 0.51408, 0.54375, 0.58975, 0.40225, 0.39775, 0.42942, 0.45167, 0.46583, 0.47342, 0.68908, 0.48083, 0.50833, 0.51725, 0.53733, 0.55892, 0.5885, 0.43208, 0.44108, 0.43683, 0.4655, 0.48175, 0.4915, 0.50175, 0.72067, 0.5155, 0.53483, 0.56392, 0.58325, 0.60692, 0.447, 0.45417, 0.46142, 0.47925, 0.4855, 0.51492, 0.52525, 0.53633, 0.75225, 0.55808, 0.57483, 0.60333, 0.62742, 0.47558, 0.48867, 0.49333, 0.49433, 0.51033, 0.52675, 0.54325, 0.55017, 0.586, 0.77958, 0.57533, 0.61683, 0.63775, 0.50375, 0.51325, 0.50858, 0.52317, 0.54017, 0.53142, 0.56525, 0.578, 0.59142, 0.60467, 0.79967, 0.611, 0.64408, 0.53775, 0.53942, 0.54583, 0.56625, 0.56275, 0.57625, 0.58117, 0.59942, 0.61933, 0.6185, 0.64358, 0.82783, 0.65083, 0.58417, 0.58442, 0.596, 0.60067, 0.595, 0.60275, 0.61983, 0.64033, 0.64075, 0.65692, 0.65883, 0.66442, 0.847)


def _sigmoid(x):
    return 1.0 / (1.0 + exp(max(-30.0, min(30.0, -x))))


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))

_ORDER = sorted((e, i, 6 if i // 13 == i % 13 else (4 if i // 13 > i % 13 else 12))
                for i, e in enumerate(_PREFLOP))
_QUALITY_LIST = [0.0] * 169
_running = 0
for _eq, _idx, _weight in _ORDER:
    _QUALITY_LIST[_idx] = (_running + _weight / 2) / 1326
    _running += _weight
_QUALITY = tuple(_QUALITY_LIST)
_COMBOS = tuple(combinations(range(52), 2))


def _draw(a, b, board):
    if len(board) >= 5:
        return 0.0
    cards = (a, b) + tuple(board)
    suits = [0] * 4
    mask = 0
    for c in cards:
        suits[c & 3] += 1
        mask |= 1 << (c >> 2)
    flush = 0.0
    for s, count in enumerate(suits):
        if count == 4 and (a & 3 == s or b & 3 == s):
            flush = 0.34 if len(board) == 3 else 0.19
            if sum(c & 3 == s for c in board) >= 3:
                flush *= 0.75
    outs = set()
    if not _STRAIGHT[mask]:
        for straight in [0x100F] + [31 << r for r in range(9)]:
            if (mask & straight).bit_count() == 4:
                outs.add(straight & ~mask)
    straight = min(8, 4 * len(outs)) * (0.041 if len(board) == 3 else 0.022)
    return 1.0 - (1.0 - flush) * (1.0 - straight)


class Model:
    def __init__(self, samples=256, random_seed=742193):
        self.samples = max(64, min(2048, int(samples)))
        self.random_state = int(random_seed)
        self.profiles = {}
        self.hands_played = 0
        self.hand = []
        self.player_id = None
        self._clear_hand()

    def _clear_hand(self):
        self.board_seen = []
        self.history = {}
        self.ranges = {}
        self.feature_cache = {}
        self.shown = {}
        self.hand_flags = {}
        self.names = []
        self.paid = []
        self.street_bets = []
        self.behind = []
        self.replay_pot = 0
        self.street_raises = 0
        self.street_index = 0
        self.initialized = False
        self.finished = False
        self.last_diagnostics = {}

    def seed(self, hand, player_id):
        self._clear_hand()
        # Store encoded cards: the persistent object contains only plain data.
        self.hand = sorted(_card(c) for c in hand)
        self.player_id = int(player_id)
        self.hands_played += 1
        self.random_state = (self.random_state * 6364136223846793005
                             + 1442695040888963407 + self.hand[0] * 53
                             + self.hand[1] + self.hands_played) & ((1 << 64) - 1)

    def _profile(self, p):
        s = self.profiles[self.names[p]]
        heads_up = len(self.names) == 2
        h = s.get('hands', 0.0)
        vpip = (s.get('vpip', 0.0) + (3.8 if heads_up else 2.0)) / (h + 7.0)
        pfr = (s.get('pfr', 0.0) + (2.2 if heads_up else 1.2)) / (h + 7.0)
        faced = s.get('faced', 0.0)
        fold = (s.get('fold', 0.0) + 2.0) / (faced + 5.5)
        pf = (s.get('post_fold', 0.0) + 1.8) / (s.get('post_faced', 0.0) + 5.0)
        ag = (s.get('post_raise', 0.0) + 2.0) / (s.get('post_moves', 0.0) + 6.0)
        big_rate = s.get('big_pre_raise', 0.0) / (h + 5.0)
        wild = _clip((big_rate - 0.25) * 1.7, 0.0, 0.90)
        wild = max(wild, _clip(s.get('weak_raise', 0.0) / (s.get('shown_raise', 0.0) + 3.0) * 2.5, 0.0, 0.85))
        station = _clip((vpip - 0.68) * 3.0) * _clip(1.0 - ag * 2.5)
        post_wild = _clip(s.get('weak_post_raise', 0.0) / (s.get('shown_post_raise', 0.0) + 5.0) * 2.0, 0.0, 0.85)
        value_cut = _clip((s.get('post_strength', 0.0) + 4.6) /
                          (s.get('shown_post_raise', 0.0) + 6.0) - 0.11, 0.55, 0.88)
        return vpip, pfr, fold, pf, ag, wild, station, h, value_cut, post_wild

    def _initialize(self, state):
        self.names = list(state.teams)
        n = len(self.names)
        for name in self.names:
            self.profiles.setdefault(name, {})
        self.history = {p: [] for p in range(n)}
        self.hand_flags = {p: [False, False, False] for p in range(n)}
        # On the first preflop turn, all contributions are this street's bets.
        # A player all-in from the blinds might only be called at end_game;
        # that case needs no betting decision or chip reconstruction.
        if state.board:
            self.behind = [0] * n
        else:
            self.behind = [state.stacks[p] + state.bets[p] for p in range(n)]
        self.paid = [0] * n
        self.street_bets = [0] * n
        sb = state.button if n == 2 else (state.button + 1) % n
        bb = (sb + 1) % n
        for p, amount in ((sb, state.small_blind), (bb, state.big_blind)):
            cost = min(amount, self.behind[p])
            self.paid[p] += cost
            self.street_bets[p] += cost
            self.behind[p] -= cost
            self.replay_pot += cost
        self.initialized = True

    def _observe(self, actions, state):
        if not self.initialized:
            self._initialize(state)
        for action in actions:
            p, kind = action.player_id, action.kind
            if kind == 'reveal_card':
                c = _card(action.card)
                if p == -1:
                    self.board_seen.append(c)
                    if len(self.board_seen) in (3, 4, 5):
                        self.street_index = len(self.board_seen) - 2
                        self.street_bets = [0] * len(self.names)
                        self.street_raises = 0
                else:
                    self.shown.setdefault(p, []).append(c)
                continue
            if kind not in ('call', 'raise', 'fold') or p < 0:
                continue
            current = max(self.street_bets)
            owed = max(0, current - self.street_bets[p])
            cost = 0
            if kind == 'call':
                cost = min(owed, self.behind[p])
            elif kind == 'raise':
                cost = max(0, action.amount - self.street_bets[p])
            # Event records use public information at the time, not the later
            # board or showdown. This is important to prevent look-ahead bias.
            event = (kind, tuple(self.board_seen), owed, self.replay_pot,
                     max(0, (action.amount or 0) - current),
                     self.street_raises, state.big_blind)
            self.history[p].append(event)
            if p != self.player_id:
                stats = self.profiles[self.names[p]]
                if owed:
                    stats['faced'] = stats.get('faced', 0.0) + 1
                    stats['fold'] = stats.get('fold', 0.0) + (kind == 'fold')
                    if self.street_index:
                        stats['post_faced'] = stats.get('post_faced', 0.0) + 1
                        stats['post_fold'] = stats.get('post_fold', 0.0) + (kind == 'fold')
                if self.street_index:
                    stats['post_moves'] = stats.get('post_moves', 0.0) + 1
                    stats['post_raise'] = stats.get('post_raise', 0.0) + (kind == 'raise')
                else:
                    if cost:
                        self.hand_flags[p][0] = True
                    if kind == 'raise':
                        self.hand_flags[p][1] = True
                        if event[4] >= 8 * state.big_blind:
                            self.hand_flags[p][2] = True
            if kind == 'raise':
                self.street_raises += 1
            self.paid[p] += cost
            self.behind[p] = max(0, self.behind[p] - cost)
            self.street_bets[p] += cost
            self.replay_pot += cost

    def _features(self, board):
        board = tuple(board)
        if board in self.feature_cache:
            return self.feature_cache[board]
        blocked = set(board) | set(self.hand)
        combos = [(a, b) for a, b in _COMBOS if a not in blocked and b not in blocked]
        ranks = [_rank((a, b) + board) for a, b in combos]
        ordered = sorted(ranks)
        n = len(ordered)
        out = {}
        hero = tuple(self.hand)
        for (a, b), rank in list(zip(combos, ranks)) + [(hero, _rank(hero + board))]:
            hs = (bisect_left(ordered, rank) + bisect_right(ordered, rank)) / (2.0 * n)
            draw = _draw(a, b, board)
            if len(board) == 3:
                proxy = 0.08 + 0.80 * hs + 0.72 * draw * (1.0 - hs)
            elif len(board) == 4:
                proxy = 0.04 + 0.90 * hs + 0.80 * draw * (1.0 - hs)
            else:
                proxy = hs
            out[(a, b)] = (_clip(proxy, 0.005, 0.995), hs, draw, rank)
        self.feature_cache[board] = out
        return out

    def _likelihood(self, a, b, event, profile, features):
        kind, board, owed, pot, increment, raises, bb = event
        vpip, pfr, fold, post_fold, aggression, wild, station, hands, value_cut, post_wild = profile
        q = _QUALITY[_key(a, b)]
        if not board:
            if kind == 'raise':
                size_tightness = 0.05 * max(0.0, log(max(1.0, increment / (3 * bb))))
                cutoff = _clip(1.0 - pfr + 0.09 * min(raises, 2) + size_tightness * (1.0 - wild), 0.02, 0.97)
                informative = 0.025 + 0.975 * _sigmoid((q - cutoff) / 0.075)
                return wild + (1.0 - wild) * informative
            if kind == 'call' and owed:
                cutoff = _clip(1.0 - vpip + 0.07 * max(0.0, log(max(1.0, owed / bb))), 0.0, 0.90)
                return 0.07 + 0.93 * (station + (1.0 - station) * _sigmoid((q - cutoff) / 0.13))
            return 1.0
        feature = features.get((a, b))
        if feature is None:
            return 0.0
        proxy, hs, draw, rank = feature
        wild = post_wild
        pressure = increment / max(1, pot + owed)
        if kind == 'raise':
            cutoff = _clip(0.78 - 0.28 * aggression + 0.07 * min(raises, 2)
                           + 0.045 * max(0.0, log(max(1.0, pressure))), 0.48, 0.93)
            cutoff = max(cutoff, value_cut)
            value = _sigmoid((proxy - cutoff) / 0.075)
            bluff = (0.035 + 0.10 * aggression) * (1.0 - hs) + 0.30 * draw
            informative = min(1.0, 0.015 + value + bluff)
            return wild + (1.0 - wild) * informative
        if kind == 'call' and owed:
            odds = owed / max(1, pot + owed)
            cutoff = odds + 0.10 + 0.16 * post_fold - 0.15 * station
            return 0.04 + 0.96 * (station + (1.0 - station) * _sigmoid((proxy - cutoff) / 0.12))
        if kind == 'call':  # A check is informative, but does not rule out traps.
            return 1.0 - (1.0 - wild) * min(0.65, aggression * 1.3) * _sigmoid((proxy - 0.69) / 0.08)
        return 1.0

    def _range(self, p, board):
        if p not in self.ranges:
            blocked = set(self.hand)
            self.ranges[p] = {'weights': [(a, b, 1.0) for a, b in _COMBOS
                                         if a not in blocked and b not in blocked], 'done': 0}
        cache = self.ranges[p]
        weights = cache['weights']
        profile = self._profile(p)
        for event in self.history[p][cache['done']:]:
            features = self._features(event[1]) if event[1] else None
            weights = [(a, b, w * self._likelihood(a, b, event, profile, features))
                       for a, b, w in weights]
            # Renormalize after each event to avoid numerical underflow.
            peak = max((w for _, _, w in weights), default=1.0)
            if peak > 0:
                weights = [(a, b, w / peak) for a, b, w in weights]
        blocked = set(board)
        weights = [(a, b, max(w, 1e-7)) for a, b, w in weights
                   if a not in blocked and b not in blocked]
        cache['weights'] = weights
        cache['done'] = len(self.history[p])
        cumulative, total = [], 0.0
        for _, _, w in weights:
            total += w
            cumulative.append(total)
        return weights, cumulative, total

    def _worlds(self, state, opponents, rng, samples):
        board = tuple(_card(c) for c in state.board)
        features = self._features(board) if board else None
        ranges = [self._range(p, board) for p in opponents]
        hero = tuple(self.hand)
        # Heads-up river: integrate every legal opponent hand exactly under
        # the inferred range, instead of introducing Monte Carlo river noise.
        if len(board) == 5 and len(opponents) == 1:
            weights, _, total = ranges[0]
            mine = _rank(hero + board)
            return [(w / total, (mine, features[(a, b)][3]),
                     (features[(a, b)][0],), (0.5,), (_QUALITY[_key(a, b)],))
                    for a, b, w in weights]
        known = set(hero + board)
        unseen = [c for c in range(52) if c not in known]
        worlds = []
        for _ in range(samples):
            chosen = []
            used = set(known)
            # Joint rejection samples the product of ranges conditioned on
            # no duplicate cards, rather than favouring a fixed seat order.
            valid = False
            for attempt in range(32):
                chosen = []
                used = set(known)
                valid = True
                for weights, cumulative, total in ranges:
                    a, b, _w = weights[bisect_left(cumulative, rng.random() * total)]
                    if a in used or b in used:
                        valid = False
                        break
                    chosen.append((a, b))
                    used.add(a)
                    used.add(b)
                if valid:
                    break
            if not valid:
                # Bounded fallback for unusually crowded/narrow ranges. It
                # preserves legality, but is an approximation to joint sampling.
                chosen = []
                used = set(known)
                order = list(range(len(opponents)))
                rng.shuffle(order)
                assigned = {}
                for i in order:
                    legal = [(a, b, w) for a, b, w in ranges[i][0]
                             if a not in used and b not in used]
                    total = sum(w for _, _, w in legal)
                    draw = rng.random() * total
                    a, b = legal[-1][:2]
                    for x, y, w in legal:
                        draw -= w
                        if draw <= 0:
                            a, b = x, y
                            break
                    assigned[i] = (a, b)
                    used.update((a, b))
                chosen = [assigned[i] for i in range(len(opponents))]
            runout = rng.sample([c for c in unseen if c not in used], 5 - len(board))
            full = board + tuple(runout)
            ranks = (_rank(hero + full),) + tuple(_rank(pair + full) for pair in chosen)
            quality = tuple(_QUALITY[_key(a, b)] for a, b in chosen)
            proxies = (tuple(features[pair][0] for pair in chosen) if board
                       else tuple(_PREFLOP[_key(a, b)] for a, b in chosen))
            worlds.append((1.0 / samples, ranks, proxies,
                           tuple(rng.random() for _ in opponents), quality))
        return worlds

    def _fold_probability(self, profile, proxy, quality, extra, pot, bb, preflop, opponents):
        if extra <= 0:
            return 0.0
        vpip, pfr, fold, pf, aggression, wild, station, hands, value_cut, post_wild = profile
        if not preflop:
            wild = post_wild
        if preflop:
            cutoff = _clip(1.0 - vpip + 0.13 * log(1.0 + extra / (2.0 * bb)), 0.02, 0.98)
            normal = 1.0 - _sigmoid((quality - cutoff) / 0.085)
        else:
            odds = extra / max(1.0, pot + extra)
            cutoff = odds + 0.11 + 0.50 * (pf - 0.36) + 0.035 * max(0, opponents - 1)
            normal = 1.0 - _sigmoid((proxy - cutoff) / 0.105)
        # Learned calling stations and habitual folders need very different
        # sizes; a high aggression rate alone does not imply a random caller.
        f = (1.0 - wild) * normal + wild * fold
        f = (1.0 - 0.9 * station) * f + 0.9 * station * min(0.12, fold)
        if fold > 0.70 and hands >= 8:
            f = max(f, fold * 0.96)
        # Avoid risking a stack on an overconfident fold-equity estimate.
        return _clip(f * 0.91, 0.005, 0.97)

    @staticmethod
    def _payout(contributions, live, ranks, me):
        """Expected chips returned to hero for a sampled showdown (side pots)."""
        if len(live) == 1:
            return float(sum(contributions)) if me in live else 0.0
        hero_total = contributions[me]
        previous = 0
        result = 0.0
        for level in sorted(set(contributions)):
            if level <= 0:
                continue
            if level > hero_total:
                break
            size = sum(max(0, min(c, level) - previous) for c in contributions)
            eligible = [p for p in live if contributions[p] >= level]
            best = max(ranks[p] for p in eligible)
            if ranks[me] == best:
                result += size / sum(ranks[p] == best for p in eligible)
            previous = level
        return result

    def _raise(self, state, total):
        bounds = state.raise_range
        if bounds is None:
            return PlayerAction.call(self.player_id)
        low, high = bounds
        return PlayerAction.raise_to(self.player_id, int(max(low, min(high, round(total)))))

    def _opening(self, state, opponents, rng):
        """Position-aware opening policy. Return None for a simulated decision."""
        if state.current_bet > state.big_blind or any(
                e[0] == 'raise' for hist in self.history.values() for e in hist):
            return None
        me, bb = self.player_id, state.big_blind
        q = _QUALITY[_key(*self.hand)]
        effective = min(state.stacks[me] + state.bets[me],
                        max(state.stacks[p] + state.bets[p] for p in opponents))
        if effective < 12 * bb:
            return None
        profiles = [self._profile(p) for p in opponents]
        folder = all(pr[2] > 0.70 and pr[7] >= 8 for pr in profiles)
        station = sum(pr[6] for pr in profiles) / len(profiles)
        aggression = max(pr[1] for pr in profiles)
        if len(self.names) == 2:
            if folder:
                return self._raise(state, 2 * bb)
            if me == state.button:
                if aggression > 0.65:
                    return PlayerAction.call(me) if q > 0.24 else PlayerAction.fold(me)
                if q > 0.16:
                    size = (3.5 if station > 0.45 and q > 0.65 else 2.2) * bb
                    return self._raise(state, size)
                if q > 0.07 and (station > 0.40 or rng.random() < 0.40):
                    return PlayerAction.call(me)
                return PlayerAction.fold(me)
            if q > (0.63 if station > 0.35 else 0.72):
                return self._raise(state, (4.5 if station > 0.35 else 3.5) * bb)
            return PlayerAction.call(me)
        # More players means tighter opening ranges, especially early position.
        n = len(self.names)
        position = ((me - state.button - 3) % n) / max(1, n - 1)
        cutoff = 0.80 - 0.21 * position
        limpers = sum(any(e[0] == 'call' and e[2] > 0 for e in self.history[p]) for p in opponents)
        if q > cutoff or folder:
            return self._raise(state, (2.5 + 0.8 * limpers) * bb)
        pair = self.hand[0] >> 2 == self.hand[1] >> 2
        suited = self.hand[0] % 4 == self.hand[1] % 4
        if not state.call_amount or (state.pot_odds < 0.22 and (pair or (suited and q > 0.40))):
            return PlayerAction.call(me)
        return PlayerAction.fold(me)

    def run(self, actions, game_state):
        state = game_state
        self._observe(actions, state)
        me = self.player_id
        opponents = [p for p in state.players_in_hand if p != me]
        if not opponents:
            return PlayerAction.call(me)
        rng = random.Random(self.random_state)
        self.random_state = rng.getrandbits(64)
        preflop = not state.board
        if preflop:
            opening = self._opening(state, opponents, rng)
            if opening is not None:
                return opening
        profiles = [self._profile(p) for p in opponents]
        board = tuple(_card(c) for c in state.board)
        feature = self._features(board)[tuple(self.hand)] if board else None
        q = _QUALITY[_key(*self.hand)]
        draw = feature[2] if feature else 0.0
        pot, cost, bb = state.pot, state.call_amount, state.big_blind
        ip = all((me - state.button - 1) % len(self.names) >
                 (p - state.button - 1) % len(self.names) for p in opponents)
        worlds = self._worlds(state, opponents, rng, self.samples)
        def equity_of(ws):
            eq = 0.0
            for w, ranks, _, _, _ in ws:
                best = max(ranks)
                if ranks[0] == best:
                    eq += w / ranks.count(best)
            return eq
        equity = equity_of(worlds)
        odds = cost / max(1, pot + cost)
        if len(board) != 5 and cost > 2 * bb and abs(equity - odds) < 0.075:
            extra = self._worlds(state, opponents, rng, self.samples)
            worlds = [(w * 0.5, r, p, u, ql) for w, r, p, u, ql in worlds + extra]
            equity = equity_of(worlds)
        active_other = [p for p in opponents if state.stacks[p] > 0]
        future = bool(active_other and cost < state.stacks[me] and len(board) < 5)
        realization = 1.0
        if future:
            if preflop:
                realization = 0.88 if ip else 0.78
            elif len(board) == 3:
                realization = 0.94 if ip else 0.87
            else:
                realization = 0.97 if ip else 0.93
            realization -= 0.035 * max(0, len(opponents) - 1)
            if max(pr[6] for pr in profiles) > 0.65:
                realization += 0.06
            if equity > 0.80:
                realization += 0.10
            remaining = (state.stacks[me] - cost) / max(1, pot + cost)
            realization = 1.0 - (1.0 - min(1.0, realization)) * min(1.0, remaining)
        # Replayed public contributions allow correct main-/side-pot valuation.
        # In a multiway hand, an unacted opponent is provisionally assumed to
        # match the current bet. Future reraises are handled only heuristically.
        contributions = list(self.paid)
        contributions[me] += cost
        for p in opponents:
            contributions[p] += min(state.stacks[p], max(0, state.current_bet - state.bets[p]))
        call_return = 0.0
        for w, ranks, _, _, _ in worlds:
            rank_map = {p: ranks[i] for i, p in enumerate([me] + opponents)}
            call_return += w * self._payout(contributions, [me] + opponents, rank_map, me)
        call_ev = realization * call_return - cost
        best_ev = max(0.0, call_ev)
        best_total = None
        bounds = state.raise_range
        if bounds is not None:
            lo, hi = bounds
            effective_total = min(hi, max(state.bets[p] + state.stacks[p] for p in opponents))
            # A legally required minimum may exceed every opponent's stack;
            # any excess is returned, never counted as a contestable pot.
            cap = max(lo, effective_total)
            if preflop:
                if state.current_bet <= bb:
                    totals = [2.2 * bb, 3.5 * bb, cap]
                else:
                    totals = [(2.8 if ip else 3.3) * state.current_bet, cap]
            else:
                after_call = pot + cost
                totals = [state.current_bet + frac * after_call for frac in (0.33, 0.65, 1.0)]
                if cap - state.current_bet <= 3.5 * after_call:
                    totals.append(cap)
            totals = sorted(set(int(_clip(round(t), lo, cap)) for t in totals))
            mean_fold = sum(pr[2] for pr in profiles) / len(profiles)
            bluff_chance = _clip(0.25 + 1.3 * max(0, mean_fold - 0.40) + draw * 0.8, 0.15, 0.90)
            # Randomise marginal bluffs, not obvious calls or folds. This keeps
            # a generic one-step EV approximation from betting every weak hand.
            bluff_allowed = rng.random() < bluff_chance
            for total in totals:
                add = total - state.bets[me]
                is_jam = add >= state.stacks[me] or all(
                    total >= state.bets[p] + state.stacks[p] for p in active_other)
                if preflop:
                    wild = max(pr[5] for pr in profiles)
                    if state.current_bet > bb and q < 0.80 and not (wild > 0.45 and equity > 0.56):
                        continue
                    if is_jam and q < 0.76 and not (wild > 0.45 and equity > 0.55) and cap > 12 * bb:
                        continue
                elif equity < 0.52 and draw < 0.18 and not bluff_allowed:
                    continue
                if len(opponents) > 1 and equity < 0.38 and draw < 0.24:
                    continue
                ev = 0.0
                if len(opponents) == 1:
                    p = opponents[0]
                    extra = min(state.stacks[p], max(0, total - state.bets[p]))
                    if state.stacks[p] == 0:
                        continue
                    # Effective cap excludes uncalled chips from both risk and reward.
                    risk = min(add, max(0, state.bets[p] + state.stacks[p] - state.bets[me]))
                    rr = 1.0 if is_jam or len(board) == 5 else max(realization, 0.94)
                    for w, ranks, proxies, _, qualities in worlds:
                        f = self._fold_probability(profiles[0], proxies[0], qualities[0], extra,
                                                   pot + risk, bb, preflop, 1)
                        share = float(ranks[0] > ranks[1]) + 0.5 * (ranks[0] == ranks[1])
                        ev += w * (f * pot + (1.0 - f) * (rr * share * (pot + risk + extra) - risk))
                else:
                    for w, ranks, proxies, uniforms, qualities in worlds:
                        contrib = list(self.paid)
                        contrib[me] += add
                        live = [me]
                        for i, p in enumerate(opponents):
                            extra = min(state.stacks[p], max(0, total - state.bets[p]))
                            f = 0.0 if state.stacks[p] == 0 else self._fold_probability(
                                profiles[i], proxies[i], qualities[i], extra, pot + add,
                                bb, preflop, len(opponents))
                            if uniforms[i] >= f:
                                live.append(p)
                                contrib[p] += extra
                        rank_map = {p: ranks[i] for i, p in enumerate([me] + opponents)}
                        ev += w * (self._payout(contrib, live, rank_map, me) - add)
                # Approximate cost of reopening the betting against a reraise.
                if not is_jam and len(board) < 5:
                    ev -= 0.035 * add * (1.0 - equity) * (1.0 + max(pr[4] for pr in profiles))
                margin = max(0.04 * pot, 0.08 * bb)
                if equity < 0.45:
                    margin += 0.04 * pot
                if ev > best_ev + margin:
                    best_ev, best_total = ev, total
        self.last_diagnostics = {'equity': equity, 'pot_odds': odds, 'call_ev': call_ev,
                                 'chosen_ev': best_ev, 'samples': len(worlds),
                                 'realization': realization}
        if best_total is not None:
            return self._raise(state, best_total)
        if cost == 0 or call_ev > 0.0:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)

    def end_game(self, actions, game_state):
        if self.finished:
            return
        if not self.initialized and game_state.board:
            # Forced all-in from the blinds: no decision was ever requested.
            # Initial stacks cannot be reconstructed from the final view, so
            # do not invent chip histories or train on incorrect observations.
            self.finished = True
            return
        self._observe(actions, game_state)
        self.finished = True
        for p, name in enumerate(self.names):
            if p == self.player_id:
                continue
            stats = self.profiles[name]
            # A roughly 230-hand half-life lets the bot adapt to changing play.
            for key in list(stats):
                stats[key] *= 0.997
            stats['hands'] = stats.get('hands', 0.0) + 1.0
            stats['vpip'] = stats.get('vpip', 0.0) + self.hand_flags[p][0]
            stats['pfr'] = stats.get('pfr', 0.0) + self.hand_flags[p][1]
            stats['big_pre_raise'] = stats.get('big_pre_raise', 0.0) + self.hand_flags[p][2]
            shown = self.shown.get(p, [])
            if len(shown) == 2 and any(e[0] == 'raise' and not e[1] for e in self.history[p]):
                q = _QUALITY[_key(*shown)]
                stats['shown_raise'] = stats.get('shown_raise', 0.0) + 1
                stats['weak_raise'] = stats.get('weak_raise', 0.0) + (q < 0.38)

            if len(shown) == 2:
                # Learn only from actually revealed hands; never impute a fold's
                # cards. One postflop sample per hand limits correlated evidence.
                observed = [e for e in self.history[p] if e[0] == 'raise' and e[1]]
                if observed:
                    event = observed[-1]
                    feature = self._features(event[1]).get(tuple(sorted(shown)))
                    if feature is not None:
                        proxy, hs, draw, rank = feature
                        stats['shown_post_raise'] = stats.get('shown_post_raise', 0.0) + 1
                        stats['post_strength'] = stats.get('post_strength', 0.0) + proxy
                        stats['weak_post_raise'] = stats.get('weak_post_raise', 0.0) + (proxy < 0.45 and draw < 0.16)