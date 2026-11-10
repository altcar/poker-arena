"""Manchester heads-up 25bb hybrid. Public observations only; bounded work."""
import random
from bisect import bisect_left
from math import exp
from player_action import PlayerAction
from poker_math import encode, rank, hand_key, pre_strength, draw_strength, board_features


def clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def sigmoid(x):
    return 1.0 / (1.0 + exp(clip(x, -30, 30) * -1))


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = 0
        self.rng = random.Random(731993)
        self.stats = {}
        self.adapt = True
        self.use_ranges = True
        self.range_mix = 1.0
        self.samples = 320
        self.history = []
        self.policy = {}
        for a in range(52):
            for b in range(a+1, 52):
                k = hand_key(a, b)
                s = pre_strength(a, b)
                # Raise/limp/fold probabilities, all 169 classes. 25bb, no ante.
                raise_p = .90 if s >= .64 else .72 if s >= .55 else .48 if s >= .47 else .23
                fold_p = 0 if s >= .44 else .12 if s >= .39 else .38
                self.policy[k] = (raise_p, 1-raise_p-fold_p, fold_p)

    def seed(self, hand, player_id):
        self.hand = [encode(c) for c in hand]
        self.player_id = player_id
        self.history = []
        self.board = []
        self.track_bets = None
        self.track_pot = 0
        self.track_raises = 0
        self.pre_aggressor = None
        self.features = {}
        self.shown = {}
        self.hand_seen = set()
        self.ranges = {}

    def _stat(self, name):
        if name not in self.stats:
            self.stats[name] = {}
        return self.stats[name]

    def _record(self, name, key, yes):
        d = self._stat(name)
        a, n = d.get(key, (0, 0))
        d[key] = (a + int(yes), n + 1)

    def rate(self, name, key, prior, strength=12):
        if not self.adapt:
            return prior
        a, n = self._stat(name).get(key, (0, 0))
        return (a + prior * strength) / (n + strength)

    def _features(self, board):
        key = tuple(board)
        if key not in self.features:
            combos, ranks, strengths, draws = board_features(self.hand, board)
            self.features[key] = {h: (r, s, d) for h, r, s, d in zip(combos, ranks, strengths, draws)}
        return self.features[key]

    def _range_update(self, name, action, facing, raises, ratio, board):
        if not self.use_ranges or action.kind == 'fold':
            return
        if name not in self.ranges:
            deck = [c for c in range(52) if c not in self.hand]
            self.ranges[name] = {(a,b): 1.0 for i,a in enumerate(deck) for b in deck[i+1:]}
        weights = self.ranges[name]
        ag = self.rate(name, 'post_raise', .30)
        pfr = self.rate(name, 'pfr', .45)
        jam = self.rate(name, 'jam', .06)
        if board:
            features = self._features(board)
        for h in list(weights):
            if any(c in board for c in h):
                weights[h] = 0
                continue
            if not board:
                s = pre_strength(*h)
                if action.kind == 'raise':
                    cutoff = (.52 if raises == 0 else .64) + .065 * min(2, ratio)
                    if ratio > 3:
                        cutoff = .68
                    cutoff -= .25 * max(0, pfr-.45) + .30 * max(0, jam-.12)
                    value = sigmoid((s-cutoff)*27)
                    likelihood = .05 + .90 * value
                elif facing:
                    value = sigmoid((s-(.40 if raises < 2 else .54))*22)
                    likelihood = .12 + .85*value
                    # Flat calls mildly cap, never exclude, premiums.
                    if s > .72 and raises < 2:
                        likelihood *= .65
                else:
                    likelihood = .9 - .35 * sigmoid((s-.63)*25)
                # Frequent raisers/shovers can have almost any hand.
                noise = clip((pfr-.50)*1.4 + (jam-.15), 0, .85)
            else:
                _, s, d = features[h]
                if action.kind == 'raise':
                    threshold = .64 + .10*min(ratio, 2) + .07*(raises > 0)
                    value = sigmoid((s-threshold)*14)
                    bluff = (.10 + .38*ag) * (1-s)**2 + d * .8
                    likelihood = .025 + .90*value + bluff
                elif facing:
                    threshold = .25 + .14 * min(ratio, 2)
                    likelihood = .06 + .84*sigmoid((s+d-threshold)*12)
                    if s > .94:
                        likelihood *= .75
                else:
                    likelihood = .95 - .40*sigmoid((s-.79)*12)
                noise = clip((ag-.38)*1.5, 0, .85)
            weights[h] *= noise + (1-noise)*likelihood
        peak = max(weights.values(), default=1) or 1
        for h in weights:
            weights[h] /= peak

    def observe(self, actions, g):
        if self.track_bets is None:
            n = g.num_players
            self.track_bets = [0]*n
            sb = g.button if n == 2 else (g.button+1)%n
            bb = (sb+1)%n
            self.track_bets[sb] = g.small_blind
            self.track_bets[bb] = g.big_blind
            self.track_pot = g.small_blind + g.big_blind
        for a in actions:
            if a.kind == 'reveal_card':
                if a.player_id == -1:
                    self.board.append(encode(a.card))
                    if len(self.board) >= 3:
                        self.track_bets = [0]*g.num_players
                        self.track_raises = 0
                else:
                    self.shown.setdefault(a.player_id, []).append(encode(a.card))
                continue
            if a.kind == 'win':
                continue
            p = a.player_id
            current = max(self.track_bets)
            facing = current > self.track_bets[p]
            ratio = (a.amount-current if a.kind == 'raise' else current-self.track_bets[p]) / max(g.big_blind, self.track_pot)
            name = g.teams[p]
            if p != self.player_id:
                if not self.board:
                    if p not in self.hand_seen:
                        self._record(name, 'pfr', a.kind == 'raise')
                        self._record(name, 'limp', a.kind == 'call' and self.track_raises == 0)
                        self.hand_seen.add(p)
                    if self.track_raises == 1 and facing:
                        self._record(name, 'threebet', a.kind == 'raise')
                else:
                    self._record(name, 'post_raise', a.kind == 'raise')
                    if len(self.board) == 3 and self.pre_aggressor == p and self.track_raises == 0:
                        self._record(name, 'cbet', a.kind == 'raise')
                if facing:
                    self._record(name, 'fold', a.kind == 'fold')
                    self._record(name, 'fold_pre' if not self.board else 'fold_post', a.kind == 'fold')
                    if len(self.board) == 3 and self.pre_aggressor == self.player_id and self.track_raises == 1:
                        self._record(name, 'fold_cbet', a.kind == 'fold')
                self._record(name, 'jam', a.kind == 'raise' and a.amount >= 15*g.big_blind)
                if a.kind == 'raise':
                    d = self._stat(name)
                    total, n = d.get('sizing', (0,0))
                    d['sizing'] = (total + ratio, n+1)
                self._range_update(name, a, facing, self.track_raises, ratio, self.board)
            self.history.append((len(self.board), p, a.kind, a.amount, facing))
            if a.kind == 'raise':
                self.track_pot += a.amount-self.track_bets[p]
                self.track_bets[p] = a.amount
                self.track_raises += 1
                if not self.board:
                    self.pre_aggressor = p
            elif a.kind == 'call':
                self.track_pot += current-self.track_bets[p]
                self.track_bets[p] = current

    def end_game(self, actions, game_state):
        self.observe(actions, game_state)
        for p, cards in self.shown.items():
            if p != self.player_id and len(cards) == 2:
                name = game_state.teams[p]
                self._record(name, 'showdown_strong', pre_strength(*cards) > .60)

    def safe(self, g, kind, total=0):
        if kind == 'raise' and g.can_raise:
            lo, hi = g.raise_range
            return PlayerAction.raise_to(self.player_id, int(clip(int(total), lo, hi)))
        if kind == 'fold' and g.call_amount:
            return PlayerAction.fold(self.player_id)
        return PlayerAction.call(self.player_id)

    def equity(self, board, name, samples=None):
        blocked = set(self.hand) | set(board)
        deck = [c for c in range(52) if c not in blocked]
        weights = self.ranges.get(name, {}) if self.use_ranges else {}
        combos = [(a,b) for i,a in enumerate(deck) for b in deck[i+1:]]
        ws = [weights.get(h, 1.0) for h in combos]
        total = sum(ws)
        if self.range_mix < 1.0:
            prior = total / max(1, len(ws))
            ws = [self.range_mix*w + (1-self.range_mix)*prior for w in ws]
            total = sum(ws)
        if total < 1e-15:
            ws = [1.0]*len(combos)
            total = len(combos)
        features = self._features(board) if board else None
        rows = []
        if len(board) == 5:
            mine = rank(self.hand + board)
            for h,w in zip(combos, ws):
                other, s, d = features[h]
                rows.append((w/total, float(mine > other) + .5*(mine == other), s, d))
        else:
            cumulative = []
            t = 0
            for w in ws:
                t += w
                cumulative.append(t)
            n = samples or self.samples
            # Stratified draws reduce range sampling variance without peeking at cards.
            offset = self.rng.random()
            for i in range(n):
                h = combos[min(len(combos)-1, bisect_left(cumulative, (i+offset)/n*total))]
                remaining = [c for c in deck if c not in h]
                runout = board + self.rng.sample(remaining, 5-len(board))
                mine, other = rank(self.hand+runout), rank(list(h)+runout)
                s, d = (features[h][1], features[h][2]) if board else (pre_strength(*h), 0)
                rows.append((1/n, float(mine > other)+.5*(mine == other), s, d))
        return sum(w*e for w,e,s,d in rows), rows

    def preflop(self, g, name):
        a,b = self.hand
        strength = pre_strength(a,b)
        hi,lo = sorted((a//4+2,b//4+2), reverse=True)
        pair = hi == lo
        bb = g.big_blind
        cost = g.call_amount
        raises = sum(k == 'raise' for street,p,k,amt,f in self.history if street == 0)
        fold = self.rate(name, 'fold_pre', .38)
        jam = self.rate(name, 'jam', .06)
        eff = min(g.stacks[self.player_id]+g.bets[self.player_id], max(g.stacks[p]+g.bets[p] for p in g.players_in_hand if p != self.player_id))
        if raises == 0 and g.current_bet <= bb:
            if g.button == self.player_id:
                rp,lp,fp = self.policy[hand_key(a,b)]
                if fold > .67:
                    return self.safe(g, 'raise', 2*bb)
                if jam > .30:
                    # Keep marginal hands cheap against frequent re-shoves.
                    rp *= .30
                    if strength < .46:
                        return self.safe(g, 'fold')
                if eff <= 8*bb and strength > .55:
                    return self.safe(g, 'raise', g.max_raise_to)
                roll = self.rng.random()
                if roll < rp:
                    return self.safe(g, 'raise', 2*bb)
                if roll > 1-fp:
                    return self.safe(g, 'fold')
                return self.safe(g, 'call')
            if strength > .60 or (fold > .62 and self.rng.random() < .75):
                return self.safe(g, 'raise', 3*bb)
            return self.safe(g, 'call')
        eq, _ = self.equity([], name, 640)
        # Exclude the unmatched part of an oversized bet from the winnable pot.
        excess = max(0, g.current_bet-g.bets[self.player_id]-cost)
        pot = max(1,g.pot-excess)
        odds = cost/(pot+cost)
        allin = not g.can_raise or cost >= .65*g.stacks[self.player_id]
        if allin:
            return self.safe(g, 'call' if eq > odds+.018 else 'fold')
        if raises >= 2:
            if (pair and hi >= 11) or (hi == 14 and lo == 13) or (eq > .61 and jam > .25):
                return self.safe(g, 'raise', g.max_raise_to)
            return self.safe(g, 'call' if eq*.88 > odds+.025 else 'fold')
        if (pair and hi >= 9) or (hi == 14 and lo >= 12):
            return self.safe(g, 'raise', g.max_raise_to if eff <= 14*bb or g.current_bet >= 4*bb else 3*g.current_bet)
        if a%4 == b%4 and hi == 14 and lo <= 5 and fold > .48 and self.rng.random() < .23:
            return self.safe(g, 'raise', 3*g.current_bet)
        realization = .88 if g.button == self.player_id else .80
        if a%4 == b%4 or pair:
            realization += .05
        return self.safe(g, 'call' if eq*realization > odds+.025 else 'fold')

    def postflop(self, g, name):
        board = [encode(c) for c in g.board]
        eq, rows = self.equity(board, name)
        cost = g.call_amount
        excess = max(0, g.current_bet-g.bets[self.player_id]-cost)
        pot = max(1,g.pot-excess)
        fold = self.rate(name, 'fold_post', .35)
        aggression = self.rate(name, 'post_raise', .30)
        allin = not g.can_raise or cost >= g.stacks[self.player_id]
        odds = cost/(pot+cost)
        future = 0 if len(board) == 5 or allin else .035 + .02*(g.button != self.player_id)
        call_ev = eq*(pot+cost)-cost-future*pot*(1-eq)
        if cost and (eq < odds+.015 or call_ev < 0):
            return self.safe(g, 'fold')
        if not g.can_raise:
            return self.safe(g, 'call')
        own = g.bets[self.player_id]
        max_add = min(g.stacks[self.player_id], cost + g.effective_stack(self.player_id))
        check_ev = eq*pot*(1 if len(board) == 5 else .90)
        base_ev = call_ev if cost else check_ev
        best_ev, best_total = base_ev, None
        draw = draw_strength(self.hand, board)
        # Polar value / draw / low-showdown bluff candidates, not automatic pair bets.
        value = eq > (.66 if cost else .57)
        bluff = eq < .36 and (draw > .10 or self.rng.random() < .22)
        if fold > .67:
            bluff = True
        if not value and not bluff:
            return self.safe(g, 'call')
        fractions = [.33, .65, 1.0]
        if eq > .88:
            fractions.append(1.5)
        candidates = [own+cost+int((pot+cost)*f) for f in fractions]
        if max_add <= 1.5*(pot+cost) or eq > .90:
            candidates.append(own+max_add)
        lo,hi = g.raise_range
        for total in sorted(set(int(clip(t,lo,min(hi,own+max_add))) for t in candidates)):
            total = max(lo,total)
            add = total-own
            opp_cost = total-g.current_bet
            if opp_cost <= 0:
                continue
            ratio = opp_cost/(pot+cost)
            threshold = .35 + .20*min(ratio,2)
            # Posterior fold rate shifts continuation; stations continue widely.
            shift = (fold-.35)*1.8
            cp = 0.0
            ce = 0.0
            for w,e,s,d in rows:
                cont = clip(sigmoid((s+d-threshold-shift)*10), .02, .99)
                cp += w*cont
                ce += w*cont*e
            called_eq = ce/max(cp,1e-12)
            ev = (1-cp)*pot + cp*(called_eq*(pot+add+opp_cost)-add)
            # Model risk / opponent re-raises / unrealised future equity.
            ev -= (.025 if len(board)==5 else .055)*(pot+add)*cp
            if value and called_eq < .53:
                continue
            if bluff and fold < .28:
                continue
            if ev > best_ev + .025*pot:
                best_ev,best_total = ev,total
        if best_total is not None:
            return self.safe(g,'raise',best_total)
        return self.safe(g,'call')

    def run(self, actions, game_state):
        g = game_state
        self.observe(actions,g)
        others = [p for p in g.players_in_hand if p != self.player_id]
        if not others:
            return self.safe(g,'call')
        name = g.teams[others[0]]
        # Full-table validity fallback: deliberately conservative, HU is the target.
        if len(others) > 1:
            s = pre_strength(*self.hand)
            if not g.board:
                return self.safe(g,'raise',g.max_raise_to) if s > .77 else self.safe(g,'call' if not g.call_amount else 'fold')
            category = rank(self.hand+[encode(c) for c in g.board])[0]
            return self.safe(g,'call' if category >= 3 or not g.call_amount else 'fold')
        return self.preflop(g,name) if g.street == 'preflop' else self.postflop(g,name)
