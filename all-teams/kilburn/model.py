"""
An exploitative equity bot for heads-up no-limit hold'em.

Every decision is the action with the most expected chips against a model
of this opponent, learned during the match:

* a range: a weight for each of the opponent's 1326 possible hands,
  narrowed by Bayes on every action they take ("they bet, and this bot
  bets 30% of the time here: mostly its strongest 30%");
* response frequencies: how often they fold, call or raise in each spot
  and to each bet size, starting from a sensible prior and updated after
  every action.

Equity comes from handeval (exact on the turn and river, sampled on the
flop) and preflop_tables (169 x 169 preflop equities). Every move is
checked against the rules before it is returned; anything unexpected
falls back to check-or-fold. Multiway pots (only in the validity tests)
use a simple equity rule.
"""

import math
import random
import time

import preflop_tables
from handeval import Evaluator, card_id, hand_class
from player_action import DEALER, PlayerAction

# Each spot the opponent acts in, with its actions weakest first, and the
# prior: (probabilities, weight in pseudo-observations).
PRIORS = {
    "pf_open": ((0.20, 0.15, 0.60, 0.05), 4.0),  # fold, limp, raise, shove
    "pf_vs_limp": ((0.65, 0.30, 0.05), 4.0),  # check, raise, shove
    "pf_vs_raise": ((0.30, 0.55, 0.15), 4.0),  # fold, call, raise
    "pf_vs_allin": ((0.62, 0.38), 4.0),  # fold, call
    "pf_vs_reshove": ((0.45, 0.55), 3.0),  # fold, call: all-in after a raise
    "post_open": ((0.60, 0.40), 4.0),  # check, bet
    "post_vs_bet": ((0.40, 0.45, 0.15), 4.0),  # fold, call, raise
    "post_vs_raise": ((0.35, 0.50, 0.15), 3.0),  # ... after betting
    "post_vs_allin": ((0.50, 0.50), 3.0),  # fold, call
}
AGGRESSIVE = {
    "pf_open": (2, 3), "pf_vs_limp": (1, 2), "pf_vs_raise": (2,),
    "post_open": (1,), "post_vs_bet": (2,), "post_vs_raise": (2,),
}
# Fold rate by bet size (to call / pot before it), relative to the average.
SIZE_OFFSET = (-0.10, -0.03, 0.03, 0.08)
TAU = 0.05  # how soft a range's edges are, in quantiles


def _sig(x):
    if x < -30.0:
        return 0.0
    if x > 30.0:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def _realize(equity, in_position):
    """How much of its equity a hand wins when more betting is to come:
    strong hands win more than their share, weak ones less, and the
    player acting last does better."""
    r = 1.0 + 0.5 * (equity - 0.5) + (0.04 if in_position else -0.06)
    return min(1.3, max(0.6, r))


def _realize_pf(equity, in_position):
    """_realize for a pot that goes to the flop with most chips still
    behind: strong hands win more of the stacks after the flop."""
    r = 1.0 + 0.8 * (equity - 0.5) + (0.05 if in_position else -0.05)
    return min(1.4, max(0.6, r))


def _flop_value(equity, pot, behind, in_position):
    """Chips back from a pot of `pot` that sees the flop with `behind`
    chips left in the smaller stack: realized equity, plus some of the
    chips behind for a hand that is ahead."""
    bonus = 0.3 * max(0.0, equity - 0.55) * behind
    return _realize_pf(equity, in_position) * equity * pot + bonus


def _bucket(ratio):
    if ratio < 0.45:
        return 0
    if ratio < 0.8:
        return 1
    if ratio < 1.2:
        return 2
    return 3


def _new_profile():
    profile = {ctx: [0.0] * len(p) for ctx, (p, _) in PRIORS.items()}
    profile["size"] = [[0.0, 0.0] for _ in range(4)]  # folds, decisions
    profile["showdown"] = [0.0, 0.0]  # weak hands shown after a bet, all
    return profile


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None
        self.ev = Evaluator()
        self.eq = [x / 10000.0 for x in preflop_tables.EQ]
        self.pf_strength = [
            preflop_tables.HS[c] / 10000.0 for c in self.ev.cls
        ]
        self.card_combos = [[] for _ in range(52)]
        for i, (a, b) in enumerate(self.ev.combos):
            self.card_combos[a].append(i)
            self.card_combos[b].append(i)
        self.rng = random.Random(20261107)
        self.profiles = {}  # team name -> what we learned about it
        self.slowest = 0.0
        self._new_hand()

    # -- the engine's calls --------------------------------------------------

    def seed(self, hand, player_id):
        self.hand = hand
        self.player_id = player_id
        self._new_hand()
        self.hero = [card_id(c) for c in hand]

    def run(self, actions, game_state):
        started = time.perf_counter()
        action = None
        try:
            action = self._decide(actions, game_state)
        except Exception:
            action = None
        if action is not None:
            try:
                game_state.validate_action(action)
            except Exception:
                action = None
        if action is None:
            action = self._fallback(game_state)
        self.slowest = max(self.slowest, time.perf_counter() - started)
        return action

    def end_game(self, actions, game_state):
        try:
            if not self.started:
                self._start(game_state)
            self._consume(actions, live=False)
            self._learn_showdown()
        except Exception:
            pass

    # -- following the hand ----------------------------------------------------

    def _new_hand(self):
        self.hero = []
        self.board = []
        self.started = False
        self.n = 0
        self.opp = None
        self.prof = None
        self.w = None
        self.tables = {}
        self.acted = []
        self.bets = []
        self.stk = []
        self.cur = 0
        self.last_raise = 0
        self.pot = 0
        self.street = 0
        self.button = 0
        self.bb = 0
        self.agg_boards = []  # board length at each opponent bet or raise
        self.opp_raised_pre = False
        self.shown = {}

    def _start(self, gs):
        """Replays the blinds; actions are then applied as they come."""
        n = gs.num_players
        self.n = n
        self.button = gs.button
        self.bb = gs.big_blind
        if gs.street == "preflop":
            start = [gs.stacks[p] + gs.bets[p] for p in range(n)]
        else:
            start = list(gs.stacks)
        small = gs.button if n == 2 else (gs.button + 1) % n
        big = (small + 1) % n
        self.stk = list(start)
        self.bets = [0] * n
        for p, blind in ((small, gs.small_blind), (big, gs.big_blind)):
            put = min(blind, self.stk[p])
            self.bets[p] += put
            self.stk[p] -= put
        self.pot = sum(self.bets)
        self.cur = max(self.bets)
        self.last_raise = gs.big_blind
        self.street = 0
        self.board = []
        self.acted = [0] * n
        self.started = True
        if n == 2 and self.player_id in (0, 1):
            self.opp = 1 - self.player_id
            name = gs.teams[self.opp]
            if name not in self.profiles:
                self.profiles[name] = _new_profile()
            self.prof = self.profiles[name]
            dead = 0
            for c in self.hero:
                dead |= 1 << c
            cbit = self.ev.cbit
            self.w = [0.0 if cbit[i] & dead else 1.0 for i in range(1326)]

    def _consume(self, actions, live):
        for a in actions:
            kind = a.kind
            if kind == "reveal_card":
                c = card_id(a.card)
                if a.player_id == DEALER:
                    self.board.append(c)
                    if self.w is not None:
                        for i in self.card_combos[c]:
                            self.w[i] = 0.0
                    if len(self.board) in (3, 4, 5):
                        self.street = len(self.board) - 2
                        self.bets = [0] * self.n
                        self.cur = 0
                        self.last_raise = self.bb
                else:
                    self.shown.setdefault(a.player_id, []).append(c)
                continue
            if kind not in ("fold", "call", "raise"):
                continue
            pid = a.player_id
            if pid == self.opp and self.prof is not None:
                self._observe(pid, kind, a.amount, live)
            self._apply(pid, kind, a.amount)

    def _apply(self, pid, kind, amount):
        if kind == "call":
            add = min(self.cur - self.bets[pid], self.stk[pid])
            if add > 0:
                self.bets[pid] += add
                self.stk[pid] -= add
                self.pot += add
        elif kind == "raise":
            add = amount - self.bets[pid]
            if add > 0:
                self.bets[pid] = amount
                self.stk[pid] -= add
                self.pot += add
            if amount - self.cur >= self.last_raise:
                self.last_raise = amount - self.cur
            self.cur = max(self.cur, amount)
        self.acted[pid] += 1

    def _observe(self, pid, kind, amount, live):
        """Counts the opponent's action in its spot, then narrows its
        range to the hands that act that way."""
        prof = self.prof
        me = self.player_id
        to_call = min(self.cur - self.bets[pid], self.stk[pid])
        facing_allin = to_call > 0 and self.stk[me] == 0
        shove = kind == "raise" and amount >= 0.85 * (
            self.bets[pid] + self.stk[pid])
        bucket = None
        if self.street == 0:
            if facing_allin:
                ctx = "pf_vs_reshove" if self.opp_raised_pre else "pf_vs_allin"
                idx = 0 if kind == "fold" else 1
            elif pid == self.button and self.acted[pid] == 0:
                ctx = "pf_open"
                idx = {"fold": 0, "call": 1}.get(kind, 3 if shove else 2)
            elif to_call == 0:
                ctx = "pf_vs_limp"
                idx = (2 if shove else 1) if kind == "raise" else 0
            else:
                ctx = "pf_vs_raise"
                idx = {"fold": 0, "call": 1}.get(kind, 2)
        elif facing_allin:
            ctx, idx = "post_vs_allin", (0 if kind == "fold" else 1)
        elif to_call == 0:
            ctx, idx = "post_open", (1 if kind == "raise" else 0)
        elif self.bets[pid] > 0:  # they bet this street; we raised
            ctx = "post_vs_raise"
            idx = {"fold": 0, "call": 1}.get(kind, 2)
        else:
            ctx = "post_vs_bet"
            idx = {"fold": 0, "call": 1}.get(kind, 2)
            bucket = _bucket(to_call / max(1.0, self.pot - to_call))
            cell = prof["size"][bucket]
            cell[1] += 1
            if kind == "fold":
                cell[0] += 1
        prof[ctx][idx] += 1
        if kind == "raise" and self.street == 0:
            self.opp_raised_pre = True
        if kind == "raise" and self.street > 0:
            self.agg_boards.append(len(self.board))
        if live and kind != "fold" and self.w is not None:
            self._narrow(ctx, idx, bucket)

    def _learn_showdown(self):
        """Did the opponent's bets and raises come with weak hands?"""
        cards = self.shown.get(self.opp)
        if self.prof is None or not cards or len(cards) != 2:
            return
        combo = self.ev.combo_index.get((cards[0], cards[1]))
        if combo is None:
            return
        for length in self.agg_boards:
            table = self.tables.get(tuple(self.board[:length]))
            if table is None:
                continue
            self.prof["showdown"][1] += 1
            if table[1][combo] < 0.45:
                self.prof["showdown"][0] += 1

    # -- the opponent model ----------------------------------------------------

    def _probs(self, ctx):
        prior, weight = PRIORS[ctx]
        counts = self.prof[ctx] if self.prof else [0.0] * len(prior)
        total = sum(counts) + weight
        return [(counts[j] + prior[j] * weight) / total
                for j in range(len(prior))]

    def _bucket_fold(self, bucket, average=None):
        if average is None:
            average = self._probs("post_vs_bet")[0]
        prior = min(0.95, max(0.02, average + SIZE_OFFSET[bucket]))
        folds, seen = self.prof["size"][bucket]
        return (folds + 2.5 * prior) / (seen + 2.5)

    def _bluff_rate(self):
        weak, seen = self.prof["showdown"]
        return min(0.6, max(0.06, (weak + 1.2) / (seen + 8.0)))

    def _narrow(self, ctx, idx, bucket):
        probs = self._probs(ctx)
        if bucket is not None:
            fold = self._bucket_fold(bucket, probs[0])
            rest = max(1e-9, probs[1] + probs[2])
            probs = [fold, (1 - fold) * probs[1] / rest,
                     (1 - fold) * probs[2] / rest]
        lo = sum(probs[:idx])
        hi = lo + probs[idx]
        if self.street == 0:
            strength = self.pf_strength
        else:
            strength = self._tables_for(self.board)[1]
        w = self.w
        live = [i for i in range(1326) if w[i] > 0.0]
        if not live:
            return
        q = self._quantiles(live, strength)
        aggressive = idx in AGGRESSIVE.get(ctx, ())
        if self.street == 0:
            eps = 0.04 if aggressive else 0.06
        else:
            eps = self._bluff_rate() if aggressive else 0.08
        for i in live:
            x = q[i]
            inside = 1.0 if lo <= 0.0 else _sig((x - lo) / TAU)
            if hi < 1.0:
                inside -= _sig((x - hi) / TAU)
            if inside < 0.0:
                inside = 0.0
            w[i] *= eps + (1.0 - eps) * inside
        total = sum(w[i] for i in live)
        if total > 0.0:
            scale = len(live) / total
            for i in live:
                w[i] *= scale

    def _tables_for(self, board):
        key = tuple(board)
        table = self.tables.get(key)
        if table is None:
            budget = 0.35 if len(board) == 3 else 0.25
            _, heq, hs, _ = self.ev.street(self.hero, board, self.rng,
                                           budget)
            table = (heq, hs)
            self.tables[key] = table
        return table

    def _quantiles(self, live, strength):
        """Each live combo's place in the opponent's range, 0 (weakest) to
        1 (strongest), weighted by how likely it is."""
        w = self.w
        order = sorted(live, key=lambda i: strength[i])
        total = 0.0
        for i in live:
            total += w[i]
        q = {}
        cum = 0.0
        for i in order:
            wi = w[i]
            q[i] = (cum + 0.5 * wi) / total
            cum += wi
        return q

    def _slice(self, live, heq, q, lo, hi, default):
        """Our equity against the part of their range between quantiles
        lo and hi (soft edges); default if that part is empty."""
        w = self.w
        num = 0.0
        den = 0.0
        for i in live:
            x = q[i]
            s = 1.0 if lo <= 0.0 else _sig((x - lo) / TAU)
            if hi < 1.0:
                s -= _sig((x - hi) / TAU)
            if s <= 0.0:
                continue
            ww = w[i] * s
            num += ww * heq[i]
            den += ww
        return num / den if den > 1e-9 else default

    # -- decisions -------------------------------------------------------------

    def _decide(self, actions, gs):
        if not self.started:
            self._start(gs)
        self._consume(actions, live=True)
        if len(self.hero) != 2:
            return None
        if self.n != 2 or self.opp is None:
            return self._multiway(gs)
        if sum(1 for x in self.w if x > 0.0) == 0:
            self._reset_range(gs)
        if gs.street == "preflop":
            return self._preflop(gs)
        return self._postflop(gs)

    def _reset_range(self, gs):
        dead = 0
        for c in self.hero + [card_id(c) for c in gs.board]:
            dead |= 1 << c
        cbit = self.ev.cbit
        self.w = [0.0 if cbit[i] & dead else 1.0 for i in range(1326)]

    def _preflop(self, gs):
        me, opp = self.player_id, self.opp
        pot, to_call, bb = gs.pot, gs.call_amount, gs.big_blind
        my_bet, opp_bet = gs.bets[me], gs.bets[opp]
        my_stack, opp_stack = gs.stacks[me], gs.stacks[opp]
        heq = self.tables.get("pf")
        if heq is None:
            row = hand_class(self.hero[0], self.hero[1]) * 169
            eq, cls = self.eq, self.ev.cls
            heq = [eq[row + cls[i]] for i in range(1326)]
            self.tables["pf"] = heq
        w = self.w
        live = [i for i in range(1326) if w[i] > 0.0]
        q = self._quantiles(live, self.pf_strength)
        ip = me == gs.button
        base = pot - my_bet - opp_bet
        eff = min(my_bet + my_stack, opp_bet + opp_stack)
        allin_pot = base + 2 * eff
        allin_add = eff - my_bet
        hi = gs.max_raise_to
        e_all = self._slice(live, heq, q, 0.0, 1.0, 0.5)
        fold_allin = self._probs("pf_vs_allin")[0]
        fold_reshove = self._probs("pf_vs_reshove")[0]

        def part(lo, top):
            return self._slice(live, heq, q, lo, top, e_all)

        def flop(e, total):
            # We both have `total` in and see a flop.
            return _flop_value(e, base + 2 * total, eff - total, ip)

        def shove_over(lo, top, fold, pot_then, spent):
            # We move all-in over their bet; their range is the slice
            # lo..top, of which the top (1 - fold) calls. spent: what we
            # have already added from now.
            e = part(lo + (top - lo) * fold, top)
            return (fold * pot_then - spent
                    + (1 - fold) * (e * allin_pot - allin_add))

        def raise_ev(total):
            # They fold, call (and we play on), or raise (we call it off
            # or fold, whichever is better).
            f, c, r = self._probs("pf_vs_raise")
            e_c, e_r = part(f, f + c), part(f + c, 1.0)
            add = total - my_bet
            called = flop(e_c, total) - add
            reraised = max(-add, e_r * allin_pot - allin_add)
            return f * pot + c * called + r * reraised

        cands = []
        first_sb = (me == gs.button and self.acted[me] == 0
                    and to_call > 0 and gs.current_bet <= bb)
        if first_sb:
            cands.append((0.0, "fold", 0))
            k, r2, s2 = self._probs("pf_vs_limp")
            e_k, e_r2, e_s2 = part(0.0, k), part(k, k + r2), part(k + r2, 1.0)
            iso = min(hi, 4 * bb)
            limp_total = my_bet + to_call
            vs_iso = max(
                -to_call,
                flop(e_r2, iso) - (iso - my_bet),
                shove_over(k, k + r2, fold_reshove,
                           base + limp_total + iso, to_call),
            )
            limp = (k * (flop(e_k, limp_total) - to_call)
                    + r2 * vs_iso
                    + s2 * max(-to_call, e_s2 * allin_pot - allin_add))
            cands.append((limp, "call", 0))
            if gs.can_raise:
                open_to = self._clamp(gs, 2 * bb)
                if open_to < hi:
                    cands.append((raise_ev(open_to), "raise", open_to))
                cands.append((shove_over(0.0, 1.0, fold_allin, pot, 0.0),
                              "raise", hi))
        elif to_call == 0:
            # The big blind after a limp: check, raise or shove.
            cands.append((flop(e_all, my_bet), "call", 0))
            if gs.can_raise:
                iso = self._clamp(gs, 4 * bb)
                if iso < hi:
                    cands.append((raise_ev(iso), "raise", iso))
                cands.append((shove_over(0.0, 1.0, fold_allin, pot, 0.0),
                              "raise", hi))
        else:
            # Facing a raise: fold, call or move all-in.
            cands.append((0.0, "fold", 0))
            if to_call >= my_stack or opp_stack == 0:
                call = e_all * allin_pot - allin_add
            else:
                call = flop(e_all, opp_bet) - to_call
            cands.append((call, "call", 0))
            if gs.can_raise and my_stack > to_call:
                fold = fold_reshove if self.opp_raised_pre else fold_allin
                cands.append((shove_over(0.0, 1.0, fold, pot, 0.0),
                              "raise", hi))
        return self._pick(gs, cands)

    def _postflop(self, gs):
        me, opp = self.player_id, self.opp
        board = [card_id(c) for c in gs.board]
        heq, hs = self._tables_for(board)
        w = self.w
        live = [i for i in range(1326) if w[i] > 0.0]
        q = self._quantiles(live, hs)
        pot, to_call = gs.pot, gs.call_amount
        my_bet, opp_bet = gs.bets[me], gs.bets[opp]
        my_stack, opp_stack = gs.stacks[me], gs.stacks[opp]
        river = gs.street == "river"
        ip = me == gs.button
        e_all = self._slice(live, heq, q, 0.0, 1.0, 0.5)
        cands = []
        if to_call > 0:
            cands.append((0.0, "fold", 0))
            terminal = river or to_call >= my_stack or opp_stack == 0
            mult = 1.0 if terminal else _realize(e_all, ip)
            cands.append((mult * e_all * (pot + to_call) - to_call, "call", 0))
        else:
            if river:
                mult = 1.0 if ip else 0.97
            else:
                mult = _realize(e_all, ip)
            cands.append((mult * e_all * pot, "call", 0))
        if gs.can_raise:
            opp_total = opp_bet + opp_stack
            my_total = my_bet + my_stack
            for x in self._sizes(gs):
                matched = min(x, opp_total)
                add = matched - my_bet  # any excess comes back
                their_add = matched - opp_bet
                allin = x >= my_total or matched >= opp_total
                f = self._fold_prob(gs, x, allin)
                r = 0.0 if allin else (1 - f) * self._raise_share(gs)
                e_c = self._slice(live, heq, q, f, 1.0 - r, e_all)
                newpot = pot + add + their_add
                mult = 1.0 if (allin or river) else _realize(e_c, ip)
                ev = f * pot + (1 - f - r) * (mult * e_c * newpot - add)
                if r > 0.0:
                    # They raise (treated as all-in): we call or fold.
                    e_r = self._slice(live, heq, q, 1.0 - r, 1.0, e_all)
                    eff = min(my_total, opp_total)
                    final = pot + (eff - my_bet) + (eff - opp_bet)
                    ev += r * max(-add, e_r * final - (eff - my_bet))
                # A bet must beat checking or calling by a clear margin:
                # fewer thin bluffs into the equity bots most teams write.
                margin = max(0.03 * pot, 0.10 * add * (1 - e_all))
                cands.append((ev - margin, "raise", x))
        return self._pick(gs, cands)

    def _raise_share(self, gs):
        """Of the times they do not fold to our bet, how often they raise."""
        ctx = "post_vs_raise" if gs.call_amount > 0 else "post_vs_bet"
        _, call, rais = self._probs(ctx)
        return rais / max(1e-9, call + rais)

    def _sizes(self, gs):
        me = self.player_id
        pot, to_call, cur = gs.pot, gs.call_amount, gs.current_bet
        lo, hi = gs.min_raise_to, gs.max_raise_to
        mine = gs.bets[me]
        if to_call == 0:
            raw = [mine + pot * s for s in (0.33, 0.66, 1.0)]
        else:
            raw = [cur + (pot + to_call) * s for s in (0.6, 1.0)]
        raw.append(hi)
        out = []
        for x in raw:
            x = int(min(hi, max(lo, x)))
            if hi - x < 0.35 * (x - mine):  # little would be left: shove
                x = hi
            if x not in out:
                out.append(x)
        return out

    def _fold_prob(self, gs, total, allin):
        me, opp = self.player_id, self.opp
        if gs.call_amount > 0:  # raising their bet
            f = self._probs("post_vs_raise")[0]
            return min(0.97, max(0.0, f * (0.9 if allin else 1.0)))
        average = self._probs("post_vs_bet")[0]
        if allin:
            prior = min(0.95, max(0.05, average + 0.10))
            fold, call = self.prof["post_vs_allin"]
            f = (fold + 3.0 * prior) / (fold + call + 3.0)
        else:
            theirs = min(total, gs.bets[opp] + gs.stacks[opp]) - gs.bets[opp]
            before = gs.pot + (total - gs.bets[me]) - theirs
            f = self._bucket_fold(_bucket(theirs / max(1.0, before)), average)
        return min(0.97, max(0.0, f))

    def _pick(self, gs, cands):
        """The best action; near-ties are mixed so we are harder to read."""
        if not cands:
            return None
        best = max(c[0] for c in cands)
        margin = max(1.0, 0.015 * gs.pot)
        near = [c for c in cands if c[0] >= best - margin]
        choice = near[0]
        if len(near) > 1:
            weights = [c[0] - (best - margin) + 1e-9 for c in near]
            r = self.rng.random() * sum(weights)
            for c, x in zip(near, weights):
                r -= x
                if r <= 0.0:
                    choice = c
                    break
        _, kind, total = choice
        me = self.player_id
        if kind == "fold":
            if gs.call_amount == 0:
                return PlayerAction.call(me)
            return PlayerAction.fold(me)
        if kind == "raise" and gs.can_raise:
            return PlayerAction.raise_to(me, self._clamp(gs, total))
        return PlayerAction.call(me)

    def _clamp(self, gs, total):
        return int(min(gs.max_raise_to, max(gs.min_raise_to, total)))

    def _multiway(self, gs):
        me = self.player_id
        board = [card_id(c) for c in gs.board]
        opponents = max(1, len(gs.players_in_hand) - 1)
        e = self.ev.equity_multi(self.hero, board, opponents, self.rng, 150)
        pot, to_call = gs.pot, gs.call_amount
        if gs.can_raise and e > max(0.5, 1.7 / (opponents + 1)):
            return PlayerAction.raise_to(
                me, self._clamp(gs, gs.current_bet + pot))
        if to_call == 0:
            return PlayerAction.call(me)
        if e > to_call / (pot + to_call) + 0.05:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)

    def _fallback(self, gs):
        me = self.player_id
        try:
            if gs.call_amount == 0:
                return PlayerAction.call(me)
        except Exception:
            pass
        return PlayerAction.fold(me)
