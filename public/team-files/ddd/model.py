"""
An adaptive heads-up bot.

Every decision compares the expected value of fold, check/call and a few
raise sizes. The inputs are:

- my equity, by Monte Carlo against the opponent's estimated range;
- that range: every two-card combination has a weight, narrowed by each of
  the opponent's actions this hand. An action keeps a band of hands by
  absolute strength (e.g. "raises with the top 20%"), where the band comes
  from how often this opponent takes that action in that spot (learned per
  team name);
- how often the opponent folds, calls or re-raises when facing a bet of a
  given size (also per team name), turned into strength thresholds, so the
  fold chance depends on how strong their range already is;
- how honest the opponent's actions are, learned from the hands they show
  down: a bot that bets at random gets its range barely narrowed.
"""

import random
import time
from bisect import bisect_left

from handeval import STRAIGHT, card_int, evaluate
from player_action import DEALER, PlayerAction
from preflop_table import PREFLOP_EQUITY

RANKS = "23456789TJQKA"

# Every two-card combination, its preflop equity against a random hand and
# its preflop percentile (0 = best, 1 = worst).
COMBOS = [(a, b) for a in range(52) for b in range(a + 1, 52)]


def _class_name(a, b):
    ra, rb = a >> 2, b >> 2
    hi, lo = max(ra, rb), min(ra, rb)
    if hi == lo:
        return RANKS[hi] + RANKS[lo]
    return RANKS[hi] + RANKS[lo] + ("s" if (a & 3) == (b & 3) else "o")


COMBO_INDEX = {combo: i for i, combo in enumerate(COMBOS)}
COMBO_PRE_EQ = [PREFLOP_EQUITY[_class_name(a, b)] for a, b in COMBOS]


def _percentiles(keys, live):
    """Percentile per combination among the live ones, strongest first;
    ties share the middle of their block. Dead ones get 1.0."""
    order = sorted(live, key=lambda i: keys[i], reverse=True)
    pct = [1.0] * len(COMBOS)
    n = len(order)
    if n == 0:
        return pct
    start = 0
    while start < n:
        end = start
        k = keys[order[start]]
        while end + 1 < n and keys[order[end + 1]] == k:
            end += 1
        mid = (start + end + 1) / 2 / n
        for j in range(start, end + 1):
            pct[order[j]] = mid
        start = end + 1
    return pct


COMBO_PRE_PCT = _percentiles(COMBO_PRE_EQ, range(len(COMBOS)))

CONTEXTS = ("pre_open", "pre_option", "pre_vs_raise", "pre_vs_reraise",
            "post_free", "post_facing", "post_vs_raise")
FACING = ("pre_vs_raise", "pre_vs_reraise", "post_facing", "post_vs_raise")
# Prior action frequencies (fold, call, raise) per context.
PRIOR_FREQ = {
    "pre_open": (0.2, 0.3, 0.5),
    "pre_option": (0.0, 0.7, 0.3),
    "pre_vs_raise": (0.3, 0.5, 0.2),
    "pre_vs_reraise": (0.2, 0.5, 0.3),
    "post_free": (0.0, 0.55, 0.45),
    "post_facing": (0.3, 0.55, 0.15),
    "post_vs_raise": (0.25, 0.55, 0.2),
}
PRIOR_WEIGHT = 5.0
# Size buckets for fold-to-bet: owed / pot before calling.
BUCKET_EDGES = (0.25, 0.4, 0.55)
BUCKET_FOLD_SCALE = (0.75, 0.95, 1.1, 1.25)
BUCKET_WEIGHT = 4.0
HONESTY_PRIOR = 0.85
HONESTY_WEIGHT = 4.0

SAMPLES = 1200
REALIZE_IP = 0.95  # share of equity realised when not all-in, in position
REALIZE_OOP = 0.9
RAISE_FRACS = (0.4, 0.7, 1.0, 1.5)  # raise sizes, as fractions of the pot
RAISE_MARGIN = 0.5  # chips a raise must beat the passive option by
CHECK_MODEL = False  # value a check by the opponent's reply to it
LIMP_MODEL = True  # value a limp by the big blind's raise over it
REALIZE_PRIOR_CHIPS = 2000.0  # weight of the default realisation
REALIZE_MIN = 0.6
REALIZE_MAX = 1.5
TIME_BUDGET = 1.5  # seconds per decision, a safety guard


def _bucket(ratio):
    for i, edge in enumerate(BUCKET_EDGES):
        if ratio < edge:
            return i
    return len(BUCKET_EDGES)


def _strength(a, b, board):
    """Postflop strength key of a combination on this board: the made hand,
    with strong draws counted like a medium pair."""
    key = evaluate([a, b] + board)
    if len(board) >= 5 or key >= (2 << 20):
        return key
    suits = [0, 0, 0, 0]
    for c in board:
        suits[c & 3] += 1
    sa, sb = a & 3, b & 3
    if sa == sb:
        if suits[sa] + 2 == 4:
            return max(key, (1 << 20) | (9 << 16))
    elif suits[sa] + 1 == 4 or suits[sb] + 1 == 4:
        return max(key, (1 << 20) | (7 << 16))
    board_mask = 0
    for c in board:
        board_mask |= 1 << (c >> 2)
    full = board_mask | (1 << (a >> 2)) | (1 << (b >> 2))
    if STRAIGHT[full] < 0:
        outs = 0
        for r in range(13):
            if (STRAIGHT[full | (1 << r)] >= 0
                    and STRAIGHT[board_mask | (1 << r)] < 0):
                outs += 1
        if outs >= 2:
            return max(key, (1 << 20) | (4 << 16))
    return key


def _board_percentiles(board, dead):
    """Absolute percentile of every combination on this board (dead cards
    excluded); preflop the fixed preflop ranking."""
    if len(board) < 3:
        return COMBO_PRE_PCT
    keys = [0] * len(COMBOS)
    live = []
    for i, (a, b) in enumerate(COMBOS):
        if a in dead or b in dead:
            continue
        keys[i] = _strength(a, b, board)
        live.append(i)
    return _percentiles(keys, live)


class Model:
    learn_realize = True
    learn_realize_post = False

    def __init__(self):
        self.hand = []
        self.player_id = None
        self.cards = []
        self.rng = random.Random(20261107)
        self.stats = {}  # team name -> learned numbers
        self.started = False
        self.n = 2
        self.opp = None
        self.opp_team = ""
        self.board = []
        self.bets = []
        self.raises = []
        self.contrib = []
        self.street = 0
        self.weights = []
        self.events = []
        self.opp_cards = []
        self.pct_len = -1
        self.pct = []
        self.options = []
        self.pre_eq = None
        self.flop_pot = 0
        self.flop_contrib = 0

    # -- the engine's calls -------------------------------------------------

    def seed(self, hand, player_id):
        self.hand = hand
        self.player_id = player_id
        self.cards = [card_int(c) for c in hand]
        self.started = False

    def run(self, actions, game_state):
        me = self.player_id
        try:
            if not self.started:
                self._start(game_state)
            for action in actions:
                self._observe(action, game_state, True)
            if self.n == 2:
                action = self._decide(game_state)
            else:
                action = self._decide_multiway(game_state)
            game_state.validate_action(action)
            return action
        except Exception:
            try:
                if game_state.call_amount == 0:
                    return PlayerAction.call(me)
                if game_state.pot_odds < 0.2:
                    return PlayerAction.call(me)
            except Exception:
                pass
            return PlayerAction.fold(me)

    def end_game(self, actions, game_state):
        try:
            if not self.started:
                self._start(game_state)
            for action in actions:
                self._observe(action, game_state, False)
            self._learn_showdown()
            self._learn_realize(actions)
        except Exception:
            pass

    # -- tracking the hand --------------------------------------------------

    def _start(self, gs):
        self.started = True
        self.n = gs.num_players
        n = self.n
        self.board = []
        self.street = 0
        self.bets = [0] * n
        self.raises = [0] * n
        self.events = []
        self.opp_cards = []
        self.pct_len = -1
        self.pct = []
        self.options = []
        self.pre_eq = None
        self.flop_pot = 0
        self.flop_contrib = 0
        small = gs.button if n == 2 else (gs.button + 1) % n
        big = (small + 1) % n
        self.bets[small] = gs.small_blind
        self.bets[big] = gs.big_blind
        if gs.street == "preflop":
            # A short stack posts less: preflop every chip put in is in bets,
            # so stacks + bets is the stack at the start of the hand.
            for p in (small, big):
                self.bets[p] = min(self.bets[p], gs.stacks[p] + gs.bets[p])
        self.contrib = list(self.bets)
        self.opp = None
        if n == 2:
            self.opp = 1 - self.player_id
            self.opp_team = gs.teams[self.opp]
            if self.opp_team not in self.stats:
                self.stats[self.opp_team] = self._new_stats()
            self.stats[self.opp_team]["hands"] += 1
        dead = set(self.cards)
        self.weights = [
            0.0 if (a in dead or b in dead) else 1.0 for a, b in COMBOS
        ]

    def _new_stats(self):
        return {
            "hands": 0,
            "freq": {ctx: [0, 0, 0] for ctx in CONTEXTS},
            "fold_bucket": {
                ctx: [[0, 0] for _ in BUCKET_FOLD_SCALE] for ctx in FACING
            },
            "honesty": {"pre": [0.0, 0.0], "post": [0.0, 0.0]},
            "real": [0.0, 0.0],  # postflop chips won, chips expected
            "opt_raise": [0, 0],  # raises over our limp: all-in, all
        }

    def _observe(self, action, gs, update_range):
        kind = action.kind
        p = action.player_id
        if kind == "reveal_card":
            c = card_int(action.card)
            if p == DEALER:
                self.board.append(c)
                if update_range:
                    w = self.weights
                    for i, (a, b) in enumerate(COMBOS):
                        if a == c or b == c:
                            w[i] = 0.0
                if len(self.board) == 3:
                    self.flop_pot = sum(self.contrib)
                    self.flop_contrib = self.contrib[self.player_id]
                if len(self.board) >= 3:
                    self.street = len(self.board) - 2
                    self.bets = [0] * self.n
                    self.raises = [0] * self.n
            elif p == self.opp:
                self.opp_cards.append(c)
            return
        if kind == "win" or p < 0 or p >= self.n:
            return
        top = max(self.bets)
        owed = top - self.bets[p]
        pot_before = sum(self.contrib)
        if p == self.opp and self.n == 2:
            self._learn_action(gs, kind, owed, top, pot_before, update_range)
            if (kind == "raise" and update_range and self.street == 0
                    and owed == 0):
                counts = self.stats[self.opp_team]["opt_raise"]
                counts[1] += 1
                if gs.stacks[p] <= 0.2 * action.amount:
                    counts[0] += 1
        if kind == "call":
            add = owed
            self.bets[p] = top
        elif kind == "raise":
            add = action.amount - self.bets[p]
            self.bets[p] = action.amount
            self.raises[p] += 1
        else:
            add = 0
        self.contrib[p] += add

    def _context(self, p, owed, top, gs):
        if self.street == 0:
            if owed == 0:
                return "pre_option"
            if top <= gs.big_blind:
                return "pre_open"
            return "pre_vs_reraise" if self.raises[p] else "pre_vs_raise"
        if owed == 0:
            return "post_free"
        return "post_vs_raise" if self.raises[p] else "post_facing"

    def _freqs(self, stats, ctx):
        counts = stats["freq"][ctx]
        prior = PRIOR_FREQ[ctx]
        total = counts[0] + counts[1] + counts[2] + PRIOR_WEIGHT
        return [
            (counts[i] + PRIOR_WEIGHT * prior[i]) / total for i in range(3)
        ]

    def _fold_freq(self, stats, ctx, ratio):
        """Chance of folding to a bet of this size (owed / pot)."""
        f = self._freqs(stats, ctx)[0]
        b = _bucket(ratio)
        base = min(0.97, f * BUCKET_FOLD_SCALE[b])
        folds, seen = stats["fold_bucket"][ctx][b]
        return (folds + BUCKET_WEIGHT * base) / (seen + BUCKET_WEIGHT)

    def _honesty(self, stats, group):
        inside, total = stats["honesty"][group]
        return (inside + HONESTY_WEIGHT * HONESTY_PRIOR) / (
            total + HONESTY_WEIGHT)

    def _learn_action(self, gs, kind, owed, top, pot_before, update_range):
        stats = self.stats[self.opp_team]
        ctx = self._context(self.opp, owed, top, gs)
        index = {"fold": 0, "call": 1, "raise": 2}[kind]
        if update_range and index > 0:
            # Narrow with the frequencies as they were before this action.
            ratio = owed / pot_before if pot_before > 0 else 0.0
            self._narrow(stats, ctx, index, ratio)
        stats["freq"][ctx][index] += 1
        if ctx in stats["fold_bucket"] and pot_before > 0:
            b = _bucket(owed / pot_before)
            stats["fold_bucket"][ctx][b][1] += 1
            if index == 0:
                stats["fold_bucket"][ctx][b][0] += 1

    # -- the opponent's range -----------------------------------------------

    def _abs_pct(self):
        if self.pct_len != len(self.board):
            dead = set(self.cards) | set(self.board)
            self.pct = _board_percentiles(self.board, dead)
            self.pct_len = len(self.board)
        return self.pct

    def _narrow(self, stats, ctx, index, ratio):
        f, c, r = self._freqs(stats, ctx)
        if ctx in FACING:
            f = self._fold_freq(stats, ctx, ratio)
        if index == 2:
            lo, hi, floor = 0.0, max(r, 0.03), 0.0
        elif ctx in ("pre_option", "post_free"):
            lo, hi, floor = min(r, 0.9), 1.0, 0.35  # checked: slowplays
        else:
            lo, hi, floor = 0.0, max(1.0 - f, 0.05), 0.0
        group = "pre" if self.street == 0 else "post"
        h = self._honesty(stats, group)
        q = max(min(hi - lo, 0.99), 0.01)
        eps = q * (1 - h) / (h * (1 - q))
        eps = min(1.0, max(eps, 0.02, floor))
        pct = self._abs_pct()
        w = self.weights
        for i in range(len(w)):
            if w[i] > 0:
                p = pct[i]
                if p < lo or p >= hi:
                    w[i] *= eps
        self.events.append((group, len(self.board), lo, hi))

    def _learn_realize(self, actions):
        if self.n != 2 or self.pre_eq is None or self.flop_pot <= 0:
            return
        stats = self.stats.get(self.opp_team)
        if stats is None:
            return
        me = self.player_id
        won = 0
        for a in actions:
            if a.kind == "win" and a.player_id == me:
                won += a.amount
        gain = won - (self.contrib[me] - self.flop_contrib)
        stats["real"][0] += gain
        stats["real"][1] += self.pre_eq * self.flop_pot

    def _learn_showdown(self):
        if self.n != 2 or len(self.opp_cards) != 2 or not self.events:
            return
        stats = self.stats.get(self.opp_team)
        if stats is None:
            return
        a, b = sorted(self.opp_cards)
        i = COMBO_INDEX[(a, b)]
        cache = {}
        for group, board_len, lo, hi in self.events:
            if board_len not in cache:
                board = self.board[:board_len]
                dead = set(self.cards) | set(board)
                cache[board_len] = _board_percentiles(board, dead)[i]
            p = cache[board_len]
            inside = lo <= p < hi
            stats["honesty"][group][0] += 1.0 if inside else 0.0
            stats["honesty"][group][1] += 1.0

    # -- deciding -----------------------------------------------------------

    def _simulate(self):
        """Monte Carlo against the weighted range: a list of (opponent
        absolute percentile, my result), strongest opponent first."""
        board = self.board
        dead = set(self.cards) | set(board)
        w = self.weights
        idx = [i for i in range(len(w)) if w[i] > 0]
        if not idx:
            return [(0.5, 0.5)]
        weights = [w[i] for i in idx]
        pct = self._abs_pct()
        missing = 5 - len(board)
        deck = [c for c in range(52) if c not in dead]
        mine_base = self.cards
        rng = self.rng
        picks = rng.choices(idx, weights=weights, k=SAMPLES)
        out = []
        start = time.time()
        for n, i in enumerate(picks):
            a, b = COMBOS[i]
            if missing:
                drawn = rng.sample(deck, missing + 2)
                full = board + [c for c in drawn if c != a and c != b][:missing]
            else:
                full = board
            mine = evaluate(mine_base + full)
            theirs = evaluate([a, b] + full)
            res = 1.0 if mine > theirs else 0.5 if mine == theirs else 0.0
            out.append((pct[i], res))
            if n % 200 == 199 and time.time() - start > TIME_BUDGET:
                break
        out.sort()
        return out

    def _decide(self, gs):
        me, opp = self.player_id, self.opp
        stats = self.stats[self.opp_team]
        sims = self._simulate()
        n = len(sims)
        pcts = [p for p, _ in sims]
        prefix = [0.0]
        for _, res in sims:
            prefix.append(prefix[-1] + res)
        eq_all = prefix[n] / n

        def count_below(t):
            return bisect_left(pcts, t)

        def eq_range(k1, k2):
            if k2 <= k1:
                return eq_all
            return (prefix[k2] - prefix[k1]) / (k2 - k1)

        group = "pre" if self.street == 0 else "post"
        h = self._honesty(stats, group)
        pot = gs.pot
        to_call = gs.call_amount
        my_bet, opp_bet = gs.bets[me], gs.bets[opp]
        my_stack, opp_stack = gs.stacks[me], gs.stacks[opp]
        river = self.street == 3
        in_position = me == gs.button
        realize = 1.0 if river else (REALIZE_IP if in_position else REALIZE_OOP)
        if self.street == 0:
            self.pre_eq = eq_all
            if self.learn_realize:
                realize = self._learned_realize(stats, realize)
        elif self.learn_realize_post and not river:
            realize = self._learned_realize(stats, realize)
        options = []  # (ev, kind, amount): every option considered
        self.options = options

        best = PlayerAction.fold(me)
        if to_call == 0:
            best_ev = eq_all * pot * realize
            best = PlayerAction.call(me)
            if (CHECK_MODEL and self.street > 0 and not in_position
                    and not gs.acted[opp]):
                best_ev = self._check_ev(stats, h, pot, realize, n, eq_all,
                                         count_below, eq_range,
                                         min(my_stack, opp_stack))
            options.append((best_ev, "call", 0))
        else:
            best_ev = 0.0
            options.append((0.0, "fold", 0))
            all_in = to_call >= my_stack or opp_stack == 0
            r = 1.0 if all_in else realize
            ev_call = r * eq_all * (pot + to_call) - to_call
            if (LIMP_MODEL and self.street == 0 and not all_in
                    and gs.current_bet <= gs.big_blind
                    and my_bet < gs.big_blind):
                ev_call = self._limp_ev(gs, stats, h, n, eq_all, count_below,
                                        eq_range, r)
            options.append((ev_call, "call", 0))
            if ev_call > best_ev:
                best_ev = ev_call
                best = PlayerAction.call(me)

        if not gs.can_raise:
            return best

        if self.street == 0:
            ctx = "pre_vs_reraise" if self.raises[opp] else "pre_vs_raise"
        else:
            ctx = "post_vs_raise" if self.raises[opp] else "post_facing"
        _, _, r_ctx = self._freqs(stats, ctx)
        low, high = gs.raise_range
        for total in self._raise_sizes(gs, low, high):
            added = total - my_bet
            opp_owed = min(total - opp_bet, opp_stack)
            pot_after = pot + added
            f_b = self._fold_freq(stats, ctx, opp_owed / pot_after)
            all_in = total >= my_bet + my_stack or opp_owed >= opp_stack
            rr_d = 0.0 if all_in else min(r_ctx, 1.0 - f_b)
            final = pot_after + opp_owed
            r = 1.0 if all_in else realize
            opp_total = min(opp_stack + opp_bet, my_stack + my_bet)
            pot_allin = pot + my_stack + (opp_total - opp_bet)

            # Honest part: they continue with the top (1 - f_b) of all
            # hands and re-raise with the top rr_d.
            k_cont = count_below(1.0 - f_b)
            k_rr = min(count_below(rr_d), k_cont) if rr_d > 0 else 0
            fold_h = (n - k_cont) / n
            call_h = (k_cont - k_rr) / n
            rr_h = k_rr / n
            ev_h = fold_h * pot
            ev_h += call_h * (r * eq_range(k_rr, k_cont) * final - added)
            if rr_h > 0:
                ev_h += rr_h * max(-added,
                                   eq_range(0, k_rr) * pot_allin - my_stack)
            # Random part: same frequencies, no information.
            call_d = max(0.0, 1.0 - f_b - rr_d)
            ev_d = f_b * pot + call_d * (r * eq_all * final - added)
            if rr_d > 0:
                ev_d += rr_d * max(-added, eq_all * pot_allin - my_stack)
            ev = h * ev_h + (1 - h) * ev_d
            options.append((ev, "raise", total))
            if ev > best_ev + RAISE_MARGIN:
                best_ev = ev
                best = PlayerAction.raise_to(me, total)
        return best

    def _check_ev(self, stats, h, pot, realize, n, eq_all, count_below,
                  eq_range, behind):
        """Value of checking out of position: the opponent bets (about
        three quarters of the pot) with the top of their range, and then
        calling or folding, whichever is better; otherwise a free card."""
        pb = self._freqs(stats, "post_free")[2]
        bet = min(0.75 * pot, behind)
        k_b = count_below(pb)

        def reply(eq):
            return max(0.0, realize * eq * (pot + 2 * bet) - bet)

        share = k_b / n
        ev_h = share * reply(eq_range(0, k_b)) + (1 - share) * (
            realize * eq_range(k_b, n) * pot)
        ev_d = pb * reply(eq_all) + (1 - pb) * realize * eq_all * pot
        return h * ev_h + (1 - h) * ev_d

    def _limp_ev(self, gs, stats, h, n, eq_all, count_below, eq_range, r):
        """Value of limping from the small blind, counting the big blind's
        raise over the limp (learned per opponent: how often, and how often
        it is all-in), and our best reply to it: fold, or call."""
        me, opp = self.player_id, self.opp
        pot, to_call = gs.pot, gs.call_amount
        my_bet, opp_bet = gs.bets[me], gs.bets[opp]
        eff = min(my_bet + gs.stacks[me], opp_bet + gs.stacks[opp])
        pb = self._freqs(stats, "pre_option")[2]
        shoves, raises = stats["opt_raise"]
        shove_share = (shoves + 2.0 * 0.5) / (raises + 2.0)
        small = min(eff, 4 * gs.big_blind)  # a typical raise over a limp

        def reply(eq):
            fold = -to_call
            vs_shove = max(fold, eq * 2 * eff - (eff - my_bet))
            vs_small = max(fold, r * eq * 2 * small - (small - my_bet))
            return shove_share * vs_shove + (1 - shove_share) * vs_small

        def unraised(eq):
            return r * eq * (pot + to_call) - to_call

        k_b = count_below(pb)
        share = k_b / n
        ev_h = (share * reply(eq_range(0, k_b))
                + (1 - share) * unraised(eq_range(k_b, n)))
        ev_d = pb * reply(eq_all) + (1 - pb) * unraised(eq_all)
        return h * ev_h + (1 - h) * ev_d

    def _learned_realize(self, stats, default):
        """Preflop realisation learned from this opponent: chips we won
        after the flop against what our preflop equity predicted."""
        gain, expected = stats["real"]
        prior = REALIZE_PRIOR_CHIPS
        ratio = (gain + prior * default) / (expected + prior)
        return min(REALIZE_MAX, max(REALIZE_MIN, ratio))

    def _raise_sizes(self, gs, low, high):
        pot = gs.pot
        to_call = gs.call_amount
        top = gs.current_bet
        sizes = set()
        if self.street == 0 and top <= gs.big_blind:
            sizes.add(2 * gs.big_blind)
            sizes.add(int(2.5 * gs.big_blind))
        else:
            for frac in RAISE_FRACS:
                sizes.add(int(top + frac * (pot + to_call)))
        out = set()
        for s in sizes:
            s = min(high, max(low, s))
            # A raise leaving little behind is better as an all-in.
            if high - s < 0.3 * s:
                s = high
            out.add(s)
        out.add(high)
        return sorted(out)

    def _decide_multiway(self, gs):
        """More than two players: equity against random hands."""
        me = self.player_id
        opponents = len(gs.players_in_hand) - 1
        board = self.board
        dead = set(self.cards) | set(board)
        deck = [c for c in range(52) if c not in dead]
        missing = 5 - len(board)
        won = 0.0
        trials = 400
        for _ in range(trials):
            cs = self.rng.sample(deck, missing + 2 * opponents)
            full = board + cs[:missing]
            mine = evaluate(self.cards + full)
            best = max(
                evaluate(cs[missing + 2 * k:missing + 2 * k + 2] + full)
                for k in range(opponents)
            )
            won += 1.0 if mine > best else (0.5 if mine == best else 0.0)
        eq = won / trials
        if eq > 1.4 / (opponents + 1) and eq > 0.5 and gs.can_raise:
            low, high = gs.raise_range
            total = min(high, max(low, gs.current_bet + gs.pot))
            return PlayerAction.raise_to(me, total)
        if gs.call_amount == 0 or eq > gs.pot_odds:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)
