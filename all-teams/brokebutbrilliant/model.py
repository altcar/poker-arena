"""
Team bot: an equity and expected-value player with opponent modelling.

How it decides:
- Every opponent gets a range: a band of the 1326 starting hands, ordered
  by preflop strength, narrowed by each preflop action using that team's
  own observed frequencies (Bayesian averages with sensible priors), then
  weighted after the flop by how well each hand fits the board and the
  opponent's bets, calls and checks.
- Monte Carlo equity against those ranges (fast evaluator in botcore.py),
  capped by a time budget far under the 10 s limit.
- Each option (fold, call, bet or raise, jam) is scored by expected value
  using the opponent's measured fold rates, and the best is played.
- Everything is wrapped so a bug can never crash, time out or play an
  illegal move: the fallback is a check or a fold.
"""

import random
import time

from botcore import (
    all_combos,
    card_to_int,
    class_key,
    evaluate,
    hand_class,
)
from bot_tables import PREFLOP_EQUITY
from card_utils import Card
from game_state import GameStateView
from player_action import DEALER, PlayerAction

# All 1326 starting hands, strongest first.
RANKED = sorted(all_combos(),
                key=lambda ab: -PREFLOP_EQUITY[class_key(ab[0], ab[1])])
NCOMBOS = len(RANKED)

# Share of starting hands stronger than each class (0 = the best hand).
PERCENTILE = {}
_seen = 0
for _i, (_a, _b) in enumerate(RANKED):
    _k = class_key(_a, _b)
    if _k not in PERCENTILE:
        PERCENTILE[_k] = _i / NCOMBOS

STREETS = {"preflop": 0, "flop": 1, "turn": 2, "river": 3}
BOARD_STREET = {3: 1, 4: 2, 5: 3}

# Seconds of simulation per decision (the limit is 10 s per move).
DECISION_BUDGET = 1.2
OUT_OF_BAND = 0.03  # weight of hands outside a modelled range: robustness

# Priors: (expected rate, weight in pseudo-observations).
PRIORS = {
    "pf_owe_raise": (0.60, 8), "pf_owe_call": (0.15, 8),
    "pf_free_raise": (0.30, 8),
    "pf_face_raise": (0.15, 8), "pf_face_call": (0.40, 8),
    "pf_jamA_call": (0.35, 6), "pf_jamB_call": (0.75, 6),
    "pf_face2_raise": (0.30, 6), "pf_face2_call": (0.45, 6),
    "post_free_bet": (0.40, 10),
    "post_face_fold": (0.45, 10), "post_face_raise": (0.12, 10),
}
DENOM = {
    "pf_owe_raise": "pf_owe_n", "pf_owe_call": "pf_owe_n",
    "pf_free_raise": "pf_free_n",
    "pf_face_raise": "pf_face_n", "pf_face_call": "pf_face_n",
    "pf_jamA_call": "pf_jamA_n", "pf_jamB_call": "pf_jamB_n",
    "pf_face2_raise": "pf_face2_n", "pf_face2_call": "pf_face2_n",
    "post_free_bet": "post_free_n",
    "post_face_fold": "post_face_n", "post_face_raise": "post_face_n",
}
COUNTERS = (
    "hands", "pf_owe_n", "pf_owe_raise", "pf_owe_call", "pf_free_n",
    "pf_free_raise", "pf_face_n", "pf_face_raise", "pf_face_call",
    "pf_jamA_n", "pf_jamA_call", "pf_jamB_n", "pf_jamB_call",
    "pf_face2_n", "pf_face2_raise", "pf_face2_call", "post_free_n", "post_free_bet",
    "post_face_n", "post_face_fold", "post_face_call", "post_face_raise",
    "sd_agg_n", "sd_agg_weak",
)


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None
        self.my = []  # hole cards as ints
        self.history = []  # every action of this hand seen so far
        self.stats = {}  # team name -> counters
        self.rng = random.Random(918273645)
        self.faults_avoided = 0

    # -- engine hooks --------------------------------------------------------

    def seed(self, hand, player_id):
        self.hand = list(hand)
        self.player_id = player_id
        self.history = []
        try:
            self.my = [card_to_int(c) for c in self.hand]
        except Exception:
            self.my = []

    def run(self, actions, game_state):
        me = self.player_id
        try:
            self.history.extend(actions)
            action = self._decide(game_state)
            if action is None or action.player_id != me:
                raise ValueError("no action")
            game_state.validate_action(action)
            return action
        except Exception:
            self.faults_avoided += 1
            return self._safe(game_state)

    def end_game(self, actions, game_state):
        try:
            self.history.extend(actions)
            self._learn(game_state)
        except Exception:
            pass

    # -- safety --------------------------------------------------------------

    def _safe(self, gs):
        me = self.player_id
        try:
            if gs.call_amount == 0:
                return PlayerAction.call(me)
        except Exception:
            pass
        return PlayerAction.fold(me)

    def _check_fold(self, gs):
        if gs.call_amount == 0:
            return PlayerAction.call(self.player_id)
        return PlayerAction.fold(self.player_id)

    def _raise(self, gs, total):
        """Raise to about total, inside the legal range; all-in when the
        rest would be a small part of the pot. A call if raising is not
        allowed."""
        me = self.player_id
        if not gs.can_raise or gs.raise_range is None:
            return PlayerAction.call(me)
        low, high = gs.raise_range
        t = int(max(low, min(high, int(total))))
        pot_after = gs.pot + (t - gs.bets[me])
        if high - t < 0.3 * pot_after:
            t = high
        return PlayerAction.raise_to(me, int(t))

    def _jam(self, gs):
        if not gs.can_raise or gs.raise_range is None:
            return PlayerAction.call(self.player_id)
        return PlayerAction.raise_to(self.player_id, int(gs.raise_range[1]))

    # -- stats ---------------------------------------------------------------

    def _team_stats(self, team):
        st = self.stats.get(team)
        if st is None:
            st = {name: 0 for name in COUNTERS}
            self.stats[team] = st
        return st

    def _rate(self, team, key):
        st = self._team_stats(team)
        prior, weight = PRIORS[key]
        return (st[key] + prior * weight) / (st[DENOM[key]] + weight)

    def _fold_to_bet(self, team):
        return self._rate(team, "post_face_fold")

    def _pf_fold_to_raise(self, team):
        return _clamp(1.0 - self._rate(team, "pf_face_raise")
                      - self._rate(team, "pf_face_call"), 0.0, 0.95)

    def _bluff_factor(self, team):
        bf = _clamp(0.1 + 0.6 * self._rate(team, "post_free_bet"), 0.1, 0.75)
        st = self._team_stats(team)
        if st["sd_agg_n"] >= 3:
            weak = (st["sd_agg_weak"] + 1.0) / (st["sd_agg_n"] + 3.0)
            bf = 0.5 * bf + 0.5 * _clamp(weak * 1.5, 0.05, 0.9)
        return bf

    def _call_air(self, team):
        return _clamp(1.2 * (1.0 - self._fold_to_bet(team)) - 0.35,
                      0.05, 1.0)

    # -- replaying the hand --------------------------------------------------

    def _replay(self, gs, starts=None):
        """Events of this hand from the history: per player move, the
        street, whether chips were owed, the raises before it this street
        and whether it faced an all-in."""
        n = gs.num_players
        button = gs.button
        if n == 2:
            sbp, bbp = button, (button + 1) % 2
        else:
            sbp, bbp = (button + 1) % n, (button + 2) % n
        bets = [0] * n
        bets[sbp] = gs.small_blind
        bets[bbp] = gs.big_blind
        contrib = list(bets)
        current = gs.big_blind
        street = 0
        nraises = 0
        facing_jam = False
        raised = set()
        board = []
        shown = {}
        wins = {}
        events = []
        for a in self.history:
            kind = a.kind
            p = a.player_id
            if kind == "reveal_card":
                c = card_to_int(a.card)
                if p == DEALER:
                    board.append(c)
                    s = BOARD_STREET.get(len(board))
                    if s is not None:
                        street = s
                        bets = [0] * n
                        current = 0
                        nraises = 0
                        facing_jam = False
                        raised = set()
                else:
                    shown.setdefault(p, []).append(c)
                continue
            if kind == "win":
                wins[p] = wins.get(p, 0) + a.amount
                continue
            if not 0 <= p < n:
                continue
            owed = current - bets[p]
            events.append((p, street, kind, owed > 0, nraises, facing_jam,
                           a.amount, p in raised))
            if kind == "call":
                if owed > 0:
                    bets[p] += owed
                    contrib[p] += owed
            elif kind == "raise":
                add = a.amount - bets[p]
                bets[p] = a.amount
                contrib[p] += add
                if a.amount > current:
                    current = a.amount
                nraises += 1
                raised.add(p)
                if starts is not None:
                    facing_jam = contrib[p] >= starts[p] - 1
        return events, board, shown, wins, contrib

    # -- learning ------------------------------------------------------------

    def _learn(self, gs):
        me = self.player_id
        events, board, shown, wins, contrib = self._replay(gs)
        n = gs.num_players
        stacks = gs.stacks
        starts = [stacks[p] - wins.get(p, 0) + contrib[p] for p in range(n)]
        events, board, shown, wins, contrib = self._replay(gs, starts)
        teams = gs.teams
        aggressive_post = set()
        for p in range(n):
            if p != me:
                self._team_stats(teams[p])["hands"] += 1
        for (p, street, kind, owed, nraises, facing_jam, _amt, had) in events:
            if p == me:
                continue
            st = self._team_stats(teams[p])
            if street == 0:
                if nraises == 0:
                    if owed:
                        st["pf_owe_n"] += 1
                        if kind == "raise":
                            st["pf_owe_raise"] += 1
                        elif kind == "call":
                            st["pf_owe_call"] += 1
                    else:
                        st["pf_free_n"] += 1
                        if kind == "raise":
                            st["pf_free_raise"] += 1
                elif facing_jam:
                    tag = "B" if had else "A"
                    st["pf_jam" + tag + "_n"] += 1
                    if kind == "call":
                        st["pf_jam" + tag + "_call"] += 1
                elif had:
                    st["pf_face2_n"] += 1
                    if kind == "raise":
                        st["pf_face2_raise"] += 1
                    elif kind == "call":
                        st["pf_face2_call"] += 1
                else:
                    st["pf_face_n"] += 1
                    if kind == "raise":
                        st["pf_face_raise"] += 1
                    elif kind == "call":
                        st["pf_face_call"] += 1
            else:
                if kind == "raise":
                    aggressive_post.add(p)
                if not owed:
                    st["post_free_n"] += 1
                    if kind == "raise":
                        st["post_free_bet"] += 1
                else:
                    st["post_face_n"] += 1
                    if kind == "fold":
                        st["post_face_fold"] += 1
                    elif kind == "call":
                        st["post_face_call"] += 1
                    else:
                        st["post_face_raise"] += 1
        if len(board) == 5:
            for p, cards in shown.items():
                if p == me or p not in aggressive_post or len(cards) != 2:
                    continue
                st = self._team_stats(teams[p])
                st["sd_agg_n"] += 1
                if hand_class(cards, board) <= 1:
                    st["sd_agg_weak"] += 1

    # -- ranges --------------------------------------------------------------

    def _preflop_band(self, team, events, p):
        lo, hi = 0.0, 1.0
        for (q, street, kind, owed, nraises, facing_jam, _amt, had) in events:
            if q != p or street != 0 or kind == "fold":
                continue
            w = hi - lo
            if nraises == 0 and not owed:
                f = self._rate(team, "pf_free_raise")
                if kind == "raise":
                    hi = lo + w * f
                else:
                    lo = lo + w * f * 0.6
            elif nraises == 0:
                fr = self._rate(team, "pf_owe_raise")
                fc = self._rate(team, "pf_owe_call")
                if kind == "raise":
                    hi = lo + w * fr
                else:
                    lo, hi = lo + w * fr * 0.5, lo + w * min(1.0, fr + fc)
            else:
                pre = "pf_face2_" if had else "pf_face_"
                fr = self._rate(team, pre + "raise")
                fc = self._rate(team, pre + "call")
                if facing_jam and kind == "call":
                    key = "pf_jamB_call" if had else "pf_jamA_call"
                    hi = lo + w * self._rate(team, key)
                elif kind == "raise":
                    hi = lo + w * fr
                else:
                    lo, hi = lo + w * fr * 0.5, lo + w * min(1.0, fr + fc)
            if hi - lo < 0.04:
                mid = (hi + lo) / 2
                lo, hi = max(0.0, mid - 0.02), min(1.0, mid + 0.02)
                if hi - lo < 0.04:
                    lo, hi = (0.0, 0.04) if lo == 0.0 else (0.96, 1.0)
        return lo, hi

    def _range(self, team, events, p, board, known, extra=None,
               band=None):
        """(combos, cumulative weights) for player p."""
        if band is None:
            band = self._preflop_band(team, events, p)
        lo_i = int(band[0] * NCOMBOS)
        hi_i = max(lo_i + 1, int(band[1] * NCOMBOS))
        post = []
        if board:
            bf = self._bluff_factor(team)
            ca = self._call_air(team)
            bet_rate = self._rate(team, "post_free_bet")
            agg_w = (bf, 0.45, 0.75, 1.0)
            call_w = (ca, 0.75, 1.0, 0.8)
            chk_w = (1.0, 1.0, 0.85, _clamp(1.1 - bet_rate, 0.3, 1.0))
            for (q, street, kind, owed, _nr, _fj, _amt, _had) in events:
                if q != p or street == 0 or kind == "fold":
                    continue
                if kind == "raise":
                    post.append(agg_w)
                elif owed:
                    post.append(call_w)
                else:
                    post.append(chk_w)
            if extra is not None:
                post.append(extra)
        combos = []
        cum = []
        total = 0.0
        for i in range(NCOMBOS):
            a, b = RANKED[i]
            if a in known or b in known:
                continue
            w = 1.0 if lo_i <= i < hi_i else OUT_OF_BAND
            if post and w > 0.0:
                cls = hand_class([a, b], board)
                for table in post:
                    w *= table[cls]
            if w <= 0.0:
                continue
            total += w
            combos.append((a, b))
            cum.append(total)
        return combos, cum

    # -- equity --------------------------------------------------------------

    def _equity(self, ranges, board, trials, deadline):
        """Share of the pot won against one hand from each range."""
        my = self.my
        known = set(my) | set(board)
        deck = [c for c in range(52) if c not in known]
        need = 5 - len(board)
        k = len(ranges)
        rng = self.rng
        picks = []
        for combos, cum in ranges:
            if not combos:
                return 0.5
            picks.append(rng.choices(combos, cum_weights=cum, k=trials))
        won = 0.0
        done = 0
        for t in range(trials):
            if t & 63 == 63 and time.perf_counter() > deadline:
                break
            used = set()
            hands = []
            ok = True
            for j in range(k):
                a, b = picks[j][t]
                if a in used or b in used:
                    ok = False
                    break
                used.add(a)
                used.add(b)
                hands.append([a, b])
            if not ok:
                continue
            if need:
                runout = []
                for c in rng.sample(deck, need + 2 * k):
                    if c not in used:
                        runout.append(c)
                        if len(runout) == need:
                            break
                full = board + runout
            else:
                full = board
            mine = evaluate(my + full)
            best = -1
            ties = 0
            for h in hands:
                s = evaluate(h + full)
                if s > best:
                    best, ties = s, 1
                elif s == best:
                    ties += 1
            if mine > best:
                won += 1.0
            elif mine == best:
                won += 1.0 / (ties + 1)
            done += 1
        if done == 0:
            return PREFLOP_EQUITY[class_key(my[0], my[1])]
        return won / done

    # -- decisions -----------------------------------------------------------

    def _decide(self, gs):
        me = self.player_id
        if len(self.my) != 2:
            return self._check_fold(gs)
        start = time.perf_counter()
        self.deadline = start + DECISION_BUDGET
        n = gs.num_players
        stacks = gs.stacks
        events, board, _shown, _wins, contrib = self._replay(gs)
        starts = [stacks[p] + contrib[p] for p in range(n)]
        events, board, _shown, _wins, contrib = self._replay(gs, starts)
        board = [card_to_int(c) for c in gs.board]
        opps = [p for p in gs.players_in_hand if p != me]
        if not opps:
            return self._check_fold(gs)
        teams = gs.teams
        ctx = {
            "events": events, "board": board, "opps": opps,
            "teams": teams, "known": set(self.my) | set(board),
        }
        if gs.street == "preflop":
            return self._preflop(gs, ctx)
        return self._postflop(gs, ctx)

    def _ranges(self, ctx, extra=None, bands=None):
        out = []
        for p in ctx["opps"]:
            band = bands.get(p) if bands else None
            out.append(self._range(ctx["teams"][p], ctx["events"], p,
                                   ctx["board"], ctx["known"], extra, band))
        return out

    def _bands(self, ctx):
        return {p: self._preflop_band(ctx["teams"][p], ctx["events"], p)
                for p in ctx["opps"]}

    def _eq(self, ranges, ctx, trials=2500):
        return self._equity(ranges, ctx["board"], trials, self.deadline)

    def _contested(self, gs, opp):
        """Chips I put in by going all-in that this opponent can match."""
        me = self.player_id
        return max(0, min(gs.stacks[me],
                          gs.bets[opp] + gs.stacks[opp] - gs.bets[me]))

    def _jam_ev(self, gs, ctx, opp, fold_rate, bands, extra=None):
        """EV (chips, folding now = 0) of moving all-in heads-up."""
        me = self.player_id
        my_add = self._contested(gs, opp)
        if my_add <= 0:
            return -1e9
        my_total = gs.bets[me] + my_add
        opp_add = max(0, my_total - gs.bets[opp])
        pot = gs.pot
        odds = opp_add / float(pot + my_add + opp_add)
        fe = fold_rate * _clamp((odds - 0.22) / 0.15, 0.0, 1.0)
        lo, hi = bands[opp]
        call_hi = lo + (hi - lo) * _clamp(1.0 - fold_rate, 0.05, 1.0)
        call_band = {opp: (lo, max(call_hi, lo + 0.04))}
        if ctx["board"]:
            ranges = self._ranges(ctx, extra=extra)
        else:
            ranges = self._ranges(ctx, bands=call_band)
        e = self._eq(ranges, ctx, 1500)
        return fe * pot + (1 - fe) * (e * (pot + my_add + opp_add) - my_add)

    def _call_ev(self, gs, e, realization):
        me = self.player_id
        call = gs.call_amount
        # Chips of the bet I cannot match go back to the bettor.
        excess = max(0, gs.current_bet - (gs.bets[me] + gs.stacks[me]))
        pot = gs.pot - excess
        if call >= gs.stacks[me]:
            realization = 1.0
        return realization * e * (pot + call) - call

    def _in_position(self, gs):
        me = self.player_id
        if gs.num_players == 2:
            return gs.button == me
        return False

    def _preflop(self, gs, ctx):
        me = self.player_id
        bb = gs.big_blind
        call = gs.call_amount
        events = ctx["events"]
        opps = ctx["opps"]
        heads_up = len(opps) == 1
        nraises = sum(1 for ev in events if ev[1] == 0 and ev[2] == "raise")
        pct = PERCENTILE[class_key(self.my[0], self.my[1])]
        eff_bb = gs.effective_stack(me) / float(bb)
        rng = self.rng
        bands = self._bands(ctx)

        if not heads_up:
            ranges = self._ranges(ctx, bands=bands)
            e = self._eq(ranges, ctx, 1500)
            fair = 1.0 / (len(opps) + 1)
            if e > fair * 1.7 and gs.can_raise:
                return self._raise(gs, gs.current_bet * 3 + gs.pot)
            if call == 0:
                return PlayerAction.call(me)
            if self._call_ev(gs, e, 0.85) > 0:
                return PlayerAction.call(me)
            return PlayerAction.fold(me)

        opp = opps[0]
        team = ctx["teams"][opp]
        opp_folds_pf = self._pf_fold_to_raise(team)

        if nraises == 0 and call > 0:
            # First in (heads-up: the small blind on the button).
            jam_ev = None
            if eff_bb <= 13 and gs.can_raise:
                jam_ev = self._jam_ev(gs, ctx, opp,
                                      1.0 - self._rate(team, "pf_jamA_call"),
                                      bands)
                limp_ev = self._call_ev(
                    gs, self._eq(self._ranges(ctx, bands=bands), ctx, 800),
                    0.9)
                if jam_ev > max(0.0, limp_ev):
                    return self._jam(gs)
                if limp_ev > 0 and pct < 0.75:
                    return PlayerAction.call(me)
                return PlayerAction.fold(me)
            open_frac = 0.72
            if opp_folds_pf > 0.55:
                open_frac = 1.0
            elif opp_folds_pf > 0.45:
                open_frac = 0.85
            if self._rate(team, "pf_face_raise") > 0.30:
                open_frac = min(open_frac, 0.55)
            if pct <= open_frac and gs.can_raise:
                return self._raise(gs, int(2.5 * bb))
            return PlayerAction.fold(me)

        if nraises == 0 and call == 0:
            # Big blind, the small blind limped.
            raise_frac = 0.32
            if opp_folds_pf > 0.5:
                raise_frac = 0.6
            if gs.can_raise and (pct <= raise_frac
                                 or (pct > 0.75 and rng.random() < 0.10)):
                if eff_bb <= 10:
                    return self._jam(gs)
                return self._raise(gs, 4 * bb)
            return PlayerAction.call(me)

        # Facing a raise.
        ranges = self._ranges(ctx, bands=bands)
        e = self._eq(ranges, ctx, 2000)
        realization = 0.95 if self._in_position(gs) else 0.82
        best_kind, best_ev = "fold", 0.0
        if call == 0:
            best_kind, best_ev = "call", e * gs.pot * realization
        else:
            ev_call = self._call_ev(gs, e, realization)
            if ev_call > best_ev:
                best_kind, best_ev = "call", ev_call
        if gs.can_raise and self._contested(gs, opp) > 0:
            deep = eff_bb > 40
            if deep:
                if e > 0.62 or (pct > 0.85 and rng.random() < 0.06):
                    return self._raise(gs, gs.current_bet * 3)
            else:
                fold_rate = 1.0 - self._rate(team, "pf_jamB_call")
                if gs.status(opp) == "all_in":
                    fold_rate = 0.0
                ev_jam = self._jam_ev(gs, ctx, opp, fold_rate, bands)
                if ev_jam > best_ev:
                    best_kind, best_ev = "jam", ev_jam
        if best_kind == "jam":
            return self._jam(gs)
        if best_kind == "call":
            return PlayerAction.call(me)
        return self._check_fold(gs)

    def _postflop(self, gs, ctx):
        me = self.player_id
        call = gs.call_amount
        pot = gs.pot
        street = STREETS.get(gs.street, 1)
        opps = ctx["opps"]
        teams = ctx["teams"]
        rng = self.rng
        ip = self._in_position(gs)
        my_stack = gs.stacks[me]
        eff = gs.effective_stack(me)
        active_opps = [p for p in opps if gs.status(p) == "active"]

        ranges = self._ranges(ctx)
        e = self._eq(ranges, ctx, 2500)

        fold_rates = [self._fold_to_bet(teams[p]) for p in active_opps]
        fe_all = 1.0
        for f in fold_rates:
            fe_all *= f
        if not active_opps:
            fe_all = 0.0
        stations = [p for p in active_opps if self._fold_to_bet(teams[p]) < 0.28]
        if street == 3:
            realization = 1.0
        elif street == 2:
            realization = 0.97 if ip else 0.92
        else:
            realization = 0.94 if ip else 0.88

        # Calling range: how a range continues against a bet.
        call_table = (self._call_air(teams[opps[0]]), 0.75, 1.0, 1.0)

        if call > 0:
            ev_call = self._call_ev(gs, e, realization)
            can_raise = gs.can_raise and active_opps
            if can_raise and eff > 0:
                spr_after = eff / float(pot + call)
                strong_table = (0.15, 0.4, 0.9, 1.0)
                e_strong = self._eq(self._ranges(ctx, extra=strong_table),
                                    ctx, 1200)
                if len(opps) == 1 and spr_after < 2.5:
                    fold_rate = 0.6 * self._fold_to_bet(teams[opps[0]])
                    bands = self._bands(ctx)
                    ev_jam = self._jam_ev(gs, ctx, opps[0], fold_rate, bands,
                                          extra=strong_table)
                    if ev_jam > max(0.0, ev_call) and (e > 0.5
                                                       or e_strong > 0.45):
                        return self._jam(gs)
                elif e > 0.72 and e_strong > 0.5:
                    total = gs.current_bet + int(0.85 * (pot + call))
                    return self._raise(gs, total)
                elif (street < 3 and 0.3 < e < 0.45 and len(opps) == 1
                      and self._fold_to_bet(teams[opps[0]]) > 0.55
                      and rng.random() < 0.15):
                    total = gs.current_bet + int(0.85 * (pot + call))
                    return self._raise(gs, total)
            if ev_call > 0:
                return PlayerAction.call(me)
            return PlayerAction.fold(me)

        # Nothing owed: check, bet or jam.
        if not active_opps or not gs.can_raise:
            return PlayerAction.call(me)
        e_call = self._eq(self._ranges(ctx, extra=call_table), ctx, 1500)
        spr = eff / float(pot) if pot else 10.0
        if spr <= 1.0 and (e > 0.55 or e_call > 0.5):
            return self._jam(gs)

        size = 0.66
        if stations:
            size = 0.9
        if street == 3 and e_call > 0.75:
            size = 0.9 if not stations else 1.1
        bet = max(gs.big_blind, int(size * pot))
        ev_check = e * pot * (realization if street < 3 else 1.0)
        # Bigger bets fold out more of a range.
        fe = _clamp(fe_all * (0.8 + 0.35 * size), 0.0, 0.92)
        ev_bet = fe * pot + (1 - fe) * (
            e_call * (pot + 2 * bet) * realization - bet)

        # Trap: check strong hands against opponents who bet when checked to.
        aggressive = (len(active_opps) == 1 and not ip and street < 3
                      and self._rate(teams[active_opps[0]],
                                     "post_free_bet") > 0.6)
        if aggressive and e > 0.85 and rng.random() < 0.4:
            return PlayerAction.call(me)

        value = e_call > 0.55
        if ev_bet > ev_check and (value or rng.random() < 0.85):
            return self._raise(gs, gs.current_bet + bet)
        return PlayerAction.call(me)
