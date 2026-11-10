"""
Poker bot: equity against modelled ranges + expected-value action choice.

Each decision:
  1. Replays this hand's actions through a private GameState to know what
     every opponent faced at each of their decisions.
  2. Rebuilds each opponent's range: starting from all hands, every action
     they took re-weights it by P(action | hand), from their learned action
     frequencies in that spot and how strongly their actions track hand
     strength (learned from showdowns).
  3. Gets our equity against that range as a function of where their hand
     sits in it (169x169 table preflop, Monte Carlo on flop and turn,
     exact enumeration on the river).
  4. Scores fold, check/call and several raise sizes by expected value:
     fold equity from their observed fold rates by bet size, equity
     against the hands that continue, and a re-raise branch; picks the best.

end_game updates per-team statistics (Bayesian-smoothed), keyed by
game_state.teams so they follow the team across seats.
"""

import random

import pk_eval as E
import pk_range as R
from game_state import GameState
from player_action import DEALER, PlayerAction
from pk_pretable import PRE_EQ, PRE_STRENGTH, PRE_VS_RANDOM

# -- opponent model priors -----------------------------------------------------

# (fold, call/check, raise) frequencies of a "typical" opponent per spot.
PRIOR = {
    "pre_open": (0.25, 0.25, 0.50),     # first in, unopened pot
    "pre_vslimp": (0.0, 0.65, 0.35),    # big blind after a limp
    "pre_vsraise": (0.42, 0.43, 0.15),  # facing a raise preflop
    "post_nobet": (0.0, 0.55, 0.45),    # check or bet
    "post_vsbet": (0.40, 0.45, 0.15),   # facing a bet
    "pre_vs3bet": (0.30, 0.45, 0.25),   # re-raised after raising
    "post_vsraise": (0.20, 0.60, 0.20),  # raised after betting
}
PRIOR_K = 6.0  # pseudo-observations for the prior
CHILD_K = 6.0  # pseudo-observations pulling a street towards all streets
TRAP = {
    "pre_open": 0.15, "pre_vslimp": 0.25, "pre_vsraise": 0.30,
    "pre_vs3bet": 0.40, "nobet": 0.25, "vsbet": 0.30, "vsraise": 0.40,
}
# Fold rate by the price the opponent is offered (call / final pot).
BUCKETS = (0.20, 0.28, 0.36, 0.43)
BUCKET_DEV = (-0.12, -0.05, 0.0, 0.10, 0.25)
BUCKET_K = 5.0
LEVELS = ("pre_vsraise", "pre_vs3bet", "post_vsbet", "post_vsraise")


def _level(ctx):
    if ctx.startswith("pre"):
        return ctx
    return "post_" + ctx.split("_", 1)[1]
ALPHA_PRIOR = 0.7
ALPHA_K = 1.2

MC_SAMPLES = {"flop": 1400, "turn": 1400, "river": 1200, "preflop": 900}


def _bucket(po):
    for i, edge in enumerate(BUCKETS):
        if po < edge:
            return i
    return len(BUCKETS)


def _smooth(counts, base, k):
    if not counts:
        return base
    n = counts[0] + counts[1] + counts[2]
    return tuple((counts[i] + k * base[i]) / (n + k) for i in range(3))


def _new_stats():
    return {
        "hands": 0,
        "ctx": {},
        "fb": {lv: [[0, 0] for _ in range(5)] for lv in LEVELS},
        "alpha": {"pre": [0.0, 0.0], "post": [0.0, 0.0]},
        "size": {},  # kind: [sum, n]
        "vpip": [0, 0],
        "pfr": [0, 0],
    }


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None
        self.rng = random.Random(20261107)
        self.opp = {}  # team name -> statistics (plain dicts and lists)
        self.my = []
        self.hand_actions = []
        self.start_stacks = None
        self.cache = {}

    # -- the engine's protocol -----------------------------------------------

    def seed(self, hand, player_id):
        self.hand = hand
        self.player_id = player_id
        self.my = [E.card_to_int(c) for c in hand]
        self.hand_actions = []
        self.start_stacks = None
        self.cache = {}

    def run(self, actions, game_state):
        try:
            self.hand_actions.extend(actions)
            action = self._decide(game_state)
            return self._legal(action, game_state)
        except Exception:
            return self._fallback(game_state)

    def end_game(self, actions, game_state):
        try:
            self.hand_actions.extend(actions)
            self._learn(game_state)
        except Exception:
            pass
        self.hand_actions = []
        self.cache = {}
        self.start_stacks = None

    # -- safety ----------------------------------------------------------------

    def _legal(self, action, gs):
        me = self.player_id
        if action.kind == "raise":
            rr = gs.raise_range
            if rr is None:
                return PlayerAction.call(me)
            total = int(action.amount)
            total = max(rr[0], min(rr[1], total))
            return PlayerAction.raise_to(me, total)
        if action.kind == "fold" and gs.call_amount == 0:
            return PlayerAction.call(me)
        return action

    def _fallback(self, gs):
        me = self.player_id
        try:
            if gs.call_amount == 0:
                return PlayerAction.call(me)
            cls = E.class_index(self.my[0], self.my[1])
            if PRE_VS_RANDOM[cls] > 0.5 + gs.pot_odds * 0.3:
                return PlayerAction.call(me)
            return PlayerAction.fold(me)
        except Exception:
            return PlayerAction.call(me)

    # -- replaying the hand ----------------------------------------------------

    def _replay(self, gs, actions, start):
        """Plays actions through a private GameState; returns the state and
        one record per betting decision, with what the player faced."""
        teams = list(gs.teams)
        st = GameState(list(start), teams)
        st.setup_hand(list(start), gs.button, gs.small_blind, gs.big_blind,
                      teams)
        decisions = []
        raised = set()  # players who raised on this street
        for a in actions:
            if a.player_id == DEALER:
                st.process_action(a)
                if st.street != "preflop" and len(st.board) in (3, 4, 5):
                    raised = set()
                continue
            if a.kind not in ("fold", "call", "raise"):
                continue
            p = a.player_id
            to_call = min(st.current_bet - st.bets[p], st.stacks[p])
            street = st.street
            cur = st.current_bet
            ctx = self._context(street, cur, st.big_blind, to_call,
                                p in raised)
            if a.kind == "raise":
                raised.add(p)
            pot = st.pot
            decisions.append({
                "p": p, "street": street, "ctx": ctx, "kind": a.kind,
                "amount": a.amount, "to_call": to_call, "pot": pot,
                "cur": cur, "bet": st.bets[p], "stack": st.stacks[p],
                "board": [E.card_to_int(c) for c in st.board],
                "po": to_call / (pot + to_call) if to_call > 0 else 0.0,
            })
            st.process_action(a)
        return st, decisions

    @staticmethod
    def _context(street, cur, bb, to_call, has_raised):
        if street == "preflop":
            if cur <= bb:
                return "pre_open" if to_call > 0 else "pre_vslimp"
            return "pre_vs3bet" if has_raised else "pre_vsraise"
        if to_call <= 0:
            return street + "_nobet"
        return street + ("_vsraise" if has_raised else "_vsbet")

    def _decisions_now(self, gs):
        me = self.player_id
        if self.start_stacks is None:
            if gs.street != "preflop":
                return None
            self.start_stacks = [
                gs.stacks[p] + gs.bets[p] for p in range(gs.num_players)
            ]
        try:
            st, decisions = self._replay(gs, self.hand_actions,
                                         self.start_stacks)
        except Exception:
            return None
        if st.pot != gs.pot or tuple(st.stacks) != tuple(gs.stacks) \
                or st.to_act != me:
            return None
        return decisions

    # -- statistics --------------------------------------------------------------

    def _stats(self, team):
        s = self.opp.get(team)
        if s is None:
            s = _new_stats()
            self.opp[team] = s
        return s

    def _freq(self, s, ctx):
        if ctx.startswith("pre"):
            return _smooth(s["ctx"].get(ctx), PRIOR[ctx], PRIOR_K)
        phase = "post_" + ctx.split("_", 1)[1]
        base = _smooth(s["ctx"].get(phase), PRIOR[phase], PRIOR_K)
        return _smooth(s["ctx"].get(ctx), base, CHILD_K)

    def _fold_prob(self, s, ctx, po):
        base = self._freq(s, ctx)[0]
        b = _bucket(po)
        level = _level(ctx)
        overall = self._freq(s, level)[0]
        folds, n = s["fb"][level][b]
        target = min(0.97, max(0.0, overall + BUCKET_DEV[b]))
        rate = (folds + BUCKET_K * target) / (n + BUCKET_K)
        return min(0.97, max(0.01, base + rate - overall))

    def _alpha(self, s, phase):
        num, den = s["alpha"][phase]
        a = (num + ALPHA_PRIOR * ALPHA_K) / (den + ALPHA_K)
        return min(1.0, max(0.05, a))

    def _size(self, s, kind, default):
        v = s["size"].get(kind)
        if not v or v[1] == 0:
            return default
        return (v[0] + default * 3) / (v[1] + 3)

    # -- ranges ------------------------------------------------------------------

    def _strengths(self, board):
        key = tuple(board)
        got = self.cache.get(key)
        if got is None:
            got = R.board_strengths(list(board))
            self.cache[key] = got
        return got

    def _opp_range(self, p, team, decisions, board, dead):
        """(class weights after preflop, combo weights, combo q now)."""
        s = self._stats(team)
        ncombos = [0] * 169
        mine = set(self.my)
        for i, (a, b) in enumerate(E.COMBOS):
            if a not in mine and b not in mine:
                ncombos[E.CLASS_OF_COMBO[i]] += 1
        cw = [1.0] * 169
        alpha = self._alpha(s, "pre")
        for d in decisions:
            if d["p"] != p or d["street"] != "preflop" or d["kind"] == "fold":
                continue
            pf, pc, pr = self._freq(s, d["ctx"])
            if d["ctx"] == "pre_vslimp":
                pf = 0.0
            q = R.class_percentiles(cw, ncombos)
            trap = TRAP[d["ctx"]]
            kind = d["kind"]
            for c in range(169):
                cw[c] *= R.action_likelihood(kind, q[c], pf, pc, pr, alpha,
                                             trap)
        if not board:
            q = R.class_percentiles(cw, ncombos)
            w = R.preflop_class_weights_to_combos(cw, dead)
            qc = [q[E.CLASS_OF_COMBO[i]] for i in range(1326)]
            return cw, w, qc
        w = R.preflop_class_weights_to_combos(cw, dead)
        alpha = self._alpha(s, "post")
        for street, n in (("flop", 3), ("turn", 4), ("river", 5)):
            if len(board) < n:
                break
            ds = [d for d in decisions
                  if d["p"] == p and d["street"] == street
                  and d["kind"] != "fold"]
            if not ds:
                continue
            strengths = self._strengths(board[:n])
            for d in ds:
                q = R.weighted_percentiles(w, strengths)
                pf, pc, pr = self._freq(s, d["ctx"])
                if d["ctx"].endswith("nobet"):
                    pf = 0.0
                    trap = TRAP["nobet"]
                else:
                    trap = TRAP[d["ctx"].split("_", 1)[1]]
                kind = d["kind"]
                for i in range(1326):
                    if w[i] > 0:
                        w[i] *= R.action_likelihood(kind, q[i], pf, pc, pr,
                                                    alpha, trap)
        q = R.weighted_percentiles(w, self._strengths(board))
        return cw, w, q

    # -- deciding ----------------------------------------------------------------

    def _realize(self, eq, street, ip, allin, cls=None):
        """Equity we expect to turn into pot share before showdown, given
        position and the streets still to play."""
        if allin or street == "river":
            return eq
        if street == "preflop":
            k = -0.04 if ip else 0.20
            if cls is not None:
                hi, lo, suited = E.class_ranks(cls)
                gap = hi - lo
                if hi == lo:
                    k -= 0.02
                else:
                    if suited:
                        k -= 0.05
                    if gap == 1:
                        k -= 0.03
                    elif gap == 2:
                        k -= 0.015
                    if not suited and gap >= 4 and hi < 10:
                        k += 0.05
        elif street == "flop":
            k = 0.04 if ip else 0.16
        else:
            k = 0.02 if ip else 0.08
        return eq - k * eq * (1.0 - eq)

    def _decide(self, gs):
        me = self.player_id
        decisions = self._decisions_now(gs)
        if decisions is None:
            decisions = []
        street = gs.street
        board = [E.card_to_int(c) for c in gs.board]
        dead = set(self.my) | set(board)
        teams = gs.teams
        opps = [p for p in gs.players_in_hand if p != me]
        if not opps:
            return PlayerAction.call(me)
        ranges = {}
        for p in opps:
            ranges[p] = self._opp_range(p, teams[p], decisions, board, dead)
        hu = len(opps) == 1
        my_cls = E.class_index(self.my[0], self.my[1])
        if hu and street == "preflop":
            profile = R.profile_preflop(my_cls, self.my, ranges[opps[0]][0])
        elif hu and street == "river":
            _, w, q = ranges[opps[0]]
            profile = R.profile_river(self.my, board, w, q)
        else:
            n = MC_SAMPLES[street]
            if not hu:
                n = n // 2
            profile = R.profile_mc(
                self.my, board, [(ranges[p][1], ranges[p][2]) for p in opps],
                n, self.rng,
            )
        if not profile:
            return self._fallback(gs)
        options = self._options(gs, decisions, opps, profile, my_cls)
        return self._choose(options, gs)

    def _position(self, gs, opps):
        """True if we act last after the flop."""
        me = self.player_id
        n = gs.num_players
        order = [(gs.button + k) % n for k in range(1, n + 1)]
        live = [p for p in order if p == me or p in opps]
        return live[-1] == me

    def _choose(self, options, gs):
        options.sort(key=lambda o: -o[1])
        best = options[0]
        eps = 0.01 * gs.pot + 0.5
        close = [o for o in options[1:3] if o[1] >= best[1] - eps
                 and o[0].kind != "fold"]
        if close and best[0].kind != "fold":
            pool = [best] + close
            weights = [o[1] - (best[1] - eps) + 0.01 for o in pool]
            x = self.rng.random() * sum(weights)
            for o, wt in zip(pool, weights):
                x -= wt
                if x <= 0:
                    return o[0]
        return best[0]

    def _options(self, gs, decisions, opps, profile, my_cls):
        me = self.player_id
        street = gs.street
        pot = gs.pot
        to_call = gs.call_amount
        my_bet = gs.bets[me]
        cur = gs.current_bet
        my_stack = gs.stacks[me]
        bb = gs.big_blind
        teams = gs.teams
        ip = self._position(gs, opps)
        hu = len(opps) == 1
        cls = my_cls if street == "preflop" else None
        pre = street == "preflop"
        phase = "pre" if pre else "post"
        opts = []

        def real(eq, allin):
            return self._realize(eq, street, ip, allin, cls)

        eq_all, _ = R.profile_equity(profile)
        # Who can still act on our bet.
        live_opps = [p for p in opps if gs.status(p) == "active"]
        stats = {p: self._stats(teams[p]) for p in opps}
        alpha = {p: self._alpha(stats[p], phase) for p in opps}

        # -- fold / check / call --
        if to_call > 0:
            opts.append((PlayerAction.fold(me), 0.0))
        call_allin = to_call >= my_stack or all(
            gs.stacks[p] - max(0, my_bet + to_call - gs.bets[p]) <= 0
            for p in live_opps) or not live_opps
        if to_call == 0:
            ev = self._ev_check(gs, opps, live_opps, profile, stats, alpha,
                                ip, real)
        elif pre and hu and cur <= bb and live_opps:
            ev = self._ev_limp(gs, opps[0], profile, stats, alpha, real)
        else:
            ev = real(eq_all, call_allin) * (pot + to_call) - to_call
        opts.append((PlayerAction.call(me), ev))

        # -- raises --
        rr = gs.raise_range
        if rr is not None and live_opps:
            lo, hi = rr
            cap = max(gs.bets[p] + gs.stacks[p] for p in live_opps)
            hi_eff = max(lo, min(hi, cap))
            sizes = []
            if pre:
                if cur <= bb:
                    if to_call > 0:
                        sizes = [2.0 * bb, 2.5 * bb, 3.0 * bb]
                    else:
                        sizes = [3.0 * bb, 4.0 * bb]
                elif eq_all >= 0.5:
                    sizes = [2.3 * cur, 3.0 * cur]  # small re-raises: value only
            else:
                base = pot + to_call
                if to_call == 0:
                    sizes = [f * pot for f in (0.33, 0.5, 0.75, 1.0, 1.5)]
                else:
                    sizes = [cur + f * base for f in (0.6, 1.0)] + [lo]
            totals = set()
            for t in sizes:
                t = int(round(t))
                if t < lo:
                    t = lo
                if t > hi_eff:
                    t = hi_eff
                totals.add(t)
            totals.add(hi_eff)
            for t in sorted(totals):
                ev = self._ev_raise(gs, t, opps, live_opps, profile, stats,
                                    alpha, real, decisions)
                opts.append((PlayerAction.raise_to(me, t), ev))
        return opts

    def _ev_check(self, gs, opps, live_opps, profile, stats, alpha, ip, real):
        pot = gs.pot
        eq_all, _ = R.profile_equity(profile)
        street = gs.street
        if ip or not live_opps or street == "preflop" or len(opps) > 1:
            return real(eq_all, False) * pot
        o = opps[0]
        s = stats[o]
        ctx = street + "_nobet"
        _, pc, pr = self._freq(s, ctx)
        beta = pr / (pc + pr) if pc + pr > 0 else 0.4
        a = alpha[o]
        ratio = self._size(s, "post_bet", 0.66)
        b = int(max(gs.big_blind, ratio * pot))
        b = min(b, gs.stacks[o], gs.stacks[self.player_id])
        if b <= 0:
            return real(eq_all, False) * pot
        bet_band = (1.0 - beta, None, a, beta, 0.0)
        chk_band = (None, 1.0 - beta, a, 1.0 - beta, TRAP["nobet"])
        eq_b, _ = R.profile_equity(profile, [bet_band])
        eq_x, _ = R.profile_equity(profile, [chk_band])
        allin = b >= gs.stacks[self.player_id] or b >= gs.stacks[o]
        facing = max(0.0, real(eq_b, allin) * (pot + 2 * b) - b)
        # A check-raise all-in option against their betting range.
        my_max = gs.stacks[self.player_id]
        their = gs.stacks[o]
        shove = min(my_max, their)
        if shove > b:
            pf2 = self._fold_prob(s, street + "_vsraise",
                                  (shove - b) / (pot + b + shove * 2 - b))
            band2 = (pf2, None, a, 1.0 - pf2, 0.0)
            # Their betting range, then the part of it that calls.
            eq2, _ = R.profile_equity(profile, [self._compose(bet_band, band2)])
            ev2 = pf2 * (pot + b) + (1 - pf2) * (
                eq2 * (pot + 2 * shove) - shove)
            facing = max(facing, ev2)
        return beta * facing + (1.0 - beta) * real(eq_x, False) * pot

    def _compose(self, first, second):
        """Approximate band for 'in first, and then in the top of second'."""
        lo1, _, a1, b1, _ = first
        lo2, _, a2, b2, _ = second
        lo1 = lo1 or 0.0
        lo2 = lo2 or 0.0
        lo = lo1 + (1.0 - lo1) * lo2
        a = min(a1, a2)
        return (lo, None, a, b1 * b2, 0.0)

    def _ev_limp(self, gs, o, profile, stats, alpha, real):
        """SB completes; the big blind may check or raise."""
        pot = gs.pot
        c = gs.call_amount
        s = stats[o]
        a = alpha[o]
        _, pc, pr = self._freq(s, "pre_vslimp")
        beta = pr / (pc + pr) if pc + pr > 0 else 0.35
        chk_band = (None, 1.0 - beta, a, 1.0 - beta, TRAP["pre_vslimp"])
        eq_x, _ = R.profile_equity(profile, [chk_band])
        ev_check = real(eq_x, False) * (pot + c) - c
        if beta < 0.01:
            return ev_check
        me = self.player_id
        open_bb = self._size(s, "pre_raise_bb", 4.0)
        t2 = min(gs.bets[o] + gs.stacks[o], int(open_bb * gs.big_blind))
        t2 = max(t2, 2 * gs.big_blind)
        my_total = gs.bets[me] + gs.stacks[me]
        t2 = min(t2, my_total)
        raise_band = (1.0 - beta, None, a, beta, 0.0)
        eq_r, _ = R.profile_equity(profile, [raise_band])
        my_now = gs.bets[me] + c
        extra = t2 - my_now
        pot2 = pot + c + (t2 - gs.bets[o])
        allin = t2 >= my_total or t2 >= gs.bets[o] + gs.stacks[o]
        respond = max(0.0, real(eq_r, allin) * (pot2 + extra) - extra)
        # Re-shove over their raise.
        if my_total > t2 and gs.bets[o] + gs.stacks[o] > t2:
            shove = min(my_total, gs.bets[o] + gs.stacks[o])
            add = shove - my_now
            opp_add = shove - t2
            po = opp_add / (pot2 + add + opp_add)
            pf2 = self._fold_prob(s, "pre_vs3bet", po)
            band2 = self._compose(raise_band, (pf2, None, a, 1 - pf2, 0.0))
            eq2, _ = R.profile_equity(profile, [band2])
            ev2 = pf2 * pot2 + (1 - pf2) * (eq2 * (pot2 + add + opp_add) - add)
            respond = max(respond, ev2)
        return (1.0 - beta) * ev_check + beta * (respond - c)

    @staticmethod
    def _facing_ctx(decisions, p, street):
        """The spot player p will be in when facing our raise."""
        raised = any(d["p"] == p and d["street"] == street
                     and d["kind"] == "raise" for d in decisions)
        if street == "preflop":
            return "pre_vs3bet" if raised else "pre_vsraise"
        return street + ("_vsraise" if raised else "_vsbet")

    def _ev_raise(self, gs, t, opps, live_opps, profile, stats, alpha, real,
                  decisions):
        me = self.player_id
        street = gs.street
        pre = street == "preflop"
        pot = gs.pot
        my_bet = gs.bets[me]
        my_total = my_bet + gs.stacks[me]
        add = t - my_bet
        we_allin = t >= my_total
        if len(opps) > 1:
            pf_all = 1.0
            bands = []
            called_pot = pot + add
            for p in opps:
                if p not in live_opps:
                    bands.append(None)
                    continue
                a_op = min(t - gs.bets[p], gs.stacks[p])
                po = a_op / (pot + add + a_op)
                ctx = self._facing_ctx(decisions, p, street)
                pf = self._fold_prob(stats[p], ctx, po)
                pf_all *= pf
                bands.append((pf, None, alpha[p], 1.0 - pf, 0.0))
                called_pot += (1.0 - pf) * a_op
            eq_c, _ = R.profile_equity(profile, bands)
            return pf_all * pot + (1.0 - pf_all) * (
                real(eq_c, we_allin) * called_pot - add)
        o = opps[0]
        s = stats[o]
        a = alpha[o]
        opp_total = gs.bets[o] + gs.stacks[o]
        a_op = min(t, opp_total) - gs.bets[o]
        if a_op <= 0:
            eq, _ = R.profile_equity(profile)
            return real(eq, True) * (pot + add) - add
        po = a_op / (pot + add + a_op)
        ctx = self._facing_ctx(decisions, o, street)
        pf = self._fold_prob(s, ctx, po)
        opp_allin = t >= opp_total
        allin = we_allin or opp_allin
        if allin:
            rshare = 0.0
        else:
            _, pc, pr = self._freq(s, ctx)
            rshare = pr / (pc + pr) if pc + pr > 0 else 0.2
        pr_tot = (1.0 - pf) * rshare
        if rshare > 0:
            call_band = (pf, 1.0 - pr_tot, a, (1.0 - pf) * (1.0 - rshare), 0.3)
        else:
            call_band = (pf, None, a, 1.0 - pf, 0.0)
        eq_c, _ = R.profile_equity(profile, [call_band])
        called_pot = pot + add + a_op
        ev_called = real(eq_c, allin) * called_pot - add
        ev = pf * pot + (1.0 - pf) * (1.0 - rshare) * ev_called
        if rshare > 0:
            rr_band = (1.0 - pr_tot, None, a, pr_tot, 0.0)
            eq_r, _ = R.profile_equity(profile, [rr_band])
            kind = "pre_3bet" if pre else "post_raise"
            mult = self._size(s, kind, 3.0)
            t2 = int(max(t + (t - gs.current_bet), mult * t))
            if opp_total - t2 < 0.35 * (pot + add + (t2 - gs.bets[o])):
                t2 = opp_total
            t2 = min(t2, opp_total)
            t2 = max(t2, t + 1)
            extra = min(t2, my_total) - t
            pot_rr = pot + add + (t2 - gs.bets[o])
            rr_allin = t2 >= opp_total or t2 >= my_total
            ev_rr_call = real(eq_r, rr_allin) * (pot_rr + extra) - add - extra
            ev_rr = max(-add, ev_rr_call)
            ev += (1.0 - pf) * rshare * ev_rr
        return ev

    # -- learning ----------------------------------------------------------------

    def _learn(self, gs):
        actions = self.hand_actions
        n = gs.num_players
        me = self.player_id
        start = self.start_stacks
        if start is None:
            big = [10 ** 7] * n
            st, _ = self._replay(gs, actions, big)
            won = [0] * n
            for a in actions:
                if a.kind == "win" and 0 <= a.player_id < n:
                    won[a.player_id] += a.amount
            start = [gs.stacks[p] - won[p] + (big[p] - st.stacks[p])
                     for p in range(n)]
            if min(start) < 0:
                return
        _, decisions = self._replay(gs, actions, start)
        shown = {}
        for a in actions:
            if a.kind == "reveal_card" and a.player_id != DEALER:
                shown.setdefault(a.player_id, []).append(
                    E.card_to_int(a.card))
        teams = gs.teams
        seen = set()
        for d in decisions:
            p = d["p"]
            if p == me:
                continue
            s = self._stats(teams[p])
            if p not in seen:
                seen.add(p)
                s["hands"] += 1
            ctx = d["ctx"]
            k = {"fold": 0, "call": 1, "raise": 2}[d["kind"]]
            if ctx.endswith("nobet") or ctx == "pre_vslimp":
                if k == 0:
                    k = 1
            for key in (ctx, None if ctx.startswith("pre")
                        else "post_" + ctx.split("_", 1)[1]):
                if key is None:
                    continue
                c = s["ctx"].setdefault(key, [0, 0, 0])
                c[k] += 1
            if d["to_call"] > 0 and _level(ctx) in LEVELS:
                fb = s["fb"][_level(ctx)][_bucket(d["po"])]
                fb[1] += 1
                if k == 0:
                    fb[0] += 1
            if k == 2:
                self._learn_size(s, d, gs.big_blind)
        # VPIP / PFR
        for p in seen:
            s = self._stats(teams[p])
            pre = [d for d in decisions if d["p"] == p
                   and d["street"] == "preflop"]
            if pre:
                s["vpip"][1] += 1
                s["pfr"][1] += 1
                if any(d["kind"] == "raise" or
                       (d["kind"] == "call" and d["to_call"] > 0)
                       for d in pre):
                    s["vpip"][0] += 1
                if any(d["kind"] == "raise" for d in pre):
                    s["pfr"][0] += 1
        # Showdowns: do their raises track hand strength?
        for p, cards in shown.items():
            if p == me or len(cards) != 2:
                continue
            s = self._stats(teams[p])
            a, b = min(cards), max(cards)
            combo = E.COMBOS.index((a, b))
            for d in decisions:
                if d["p"] != p or d["kind"] != "raise":
                    continue
                pr = self._freq(s, d["ctx"])[2]
                if pr > 0.85:
                    continue
                if d["street"] == "preflop":
                    strength = PRE_STRENGTH[E.class_index(a, b)]
                    phase = "pre"
                else:
                    strength = self._strengths(d["board"])[combo]
                    phase = "post"
                    if strength is None:
                        continue
                acc = s["alpha"][phase]
                acc[0] += strength - 0.5
                acc[1] += 0.5 - pr / 2.0

    def _learn_size(self, s, d, bb):
        if d["street"] == "preflop":
            if d["ctx"] == "pre_vsraise":
                kind, value = "pre_3bet", d["amount"] / max(1, d["cur"])
            else:
                kind, value = "pre_raise_bb", d["amount"] / bb
        else:
            if d["to_call"] == 0:
                kind, value = "post_bet", d["amount"] / max(1, d["pot"])
            else:
                kind, value = "post_raise", d["amount"] / max(1, d["cur"])
        v = s["size"].setdefault(kind, [0.0, 0])
        v[0] += min(value, 20.0)
        v[1] += 1
