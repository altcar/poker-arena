"""
Manchester Fried Chicken poker bot: heads-up no-limit hold'em.

  * handeval.py      fast integer hand evaluator (prime-product tables)
  * preflop_table.py 169x169 all-in equity table (precomputed, embedded)
  * this file        EV-maximising decisions against an opponent range that
                     is narrowed by each of their actions.

Opponent model: at every decision node the opponent is assumed to act by
hand strength u in [0, 1] (1 = strongest): aggressive if u > Ta, continue
(call/check) if u > Tc, else fold. Ta and Tc are fitted per opponent (by team
name) from every observed action together with the range they held at that
moment, so "folds a lot to raises" and "never folds a 3-bet range" are both
learned correctly. Fold equity of our bets follows from Tc and their range.

Everything is plain data; tables are computed in memory on import. No files,
no network, no engine internals.
"""

import math
import random
import time

from card_utils import Card
from game_state import GameStateView
from player_action import PlayerAction

import handeval as H
import river_solver as RS
from preflop_table import EQUITY_HEX
from preflop_nash import NASH

# Equilibrium preflop strategy: path -> (actions, [probs per action] per
# class). Solved offline by solve_preflop.py (CFR+, 25bb).
NASH_P = {}
for _path, (_acts, _hex) in NASH.items():
    _n = len(_acts)
    NASH_P[_path] = (_acts, [
        [int(_hex[(c * _n + k) * 2:(c * _n + k) * 2 + 2], 16) / 255.0
         for k in range(_n)] for c in range(169)])
LAMBDA_MAX = 0.9
RIVER_BUCKETS = 20
STEAL_FOLD = 0.55   # their fold rate that turns our limps into raises
LOOSE_RATIO = 1.5   # ... above which we punish it with the best response
MIN_OBS = 6         # observations before a measured exploit switches on
BARREL_FOLD = 0.6   # their fold-to-bet rate above which we always bet
STATION_FOLD = 0.15  # ... below which we never bluff
SIZE_BUCKETS = (0.45, 0.85)  # bet / pot edges: small | medium | large
TIGHT_RATIO = 0.8   # their aggression / equilibrium's below which we
                    # respect their raises with the best response
# Paths where we face their aggression -> (their node, equilibrium
# aggression share at that node).
FACING = {
    "r/open": ("sb_open", 0.18), "r/shove": ("sb_open", 0.18),
    "r/limp/raise": ("bb_limp", 0.36), "r/limp/shove": ("bb_limp", 0.36),
    "r/open/raise": ("bb_raise", 0.24), "r/open/shove": ("bb_raise", 0.24),
    "r/limp/raise/shove": ("pf_3b", 0.16),
    "r/open/raise/shove": ("pf_3b", 0.21),
}

# --- preflop tables (built on import, from the embedded hex string) --------

_HEX = "".join(EQUITY_HEX)
EQ = [[int(_HEX[(i * 169 + j) * 4:(i * 169 + j) * 4 + 4], 16) / 65535.0
       for j in range(169)] for i in range(169)]
del _HEX
_TOTAL = float(sum(H.CLASS_SIZE))
EQ_RANDOM = [sum(EQ[i][j] * H.CLASS_SIZE[j] for j in range(169)) / _TOTAL
             for i in range(169)]
# Strength order for preflop ranges: equity vs a random hand. x = share of
# combos stronger than a class (0 = aces), u = 1 - x.
ORDER = sorted(range(169), key=lambda i: -EQ_RANDOM[i])
CUM_LO = [0.0] * 169
CUM_HI = [0.0] * 169
_acc = 0.0
for _i in ORDER:
    CUM_LO[_i] = _acc / _TOTAL
    _acc += H.CLASS_SIZE[_i]
    CUM_HI[_i] = _acc / _TOTAL

# --- opponent model priors: node -> (Ta, Tc) in strength u -----------------
PRIORS = {
    "sb_open": (0.50, 0.30),    # first action as button/SB: raise/limp/fold
    "bb_limp": (0.65, 0.0),     # BB after a limp: raise / check
    "bb_raise": (0.90, 0.42),   # BB facing an open raise
    "pf_3b": (0.94, 0.70),      # facing a re-raise preflop
    "pf_allin": (1.0, 0.72),    # facing an all-in preflop: call / fold
    "post_open": (0.55, 0.0),   # postflop, nothing owed: bet / check
    "post_bet": (0.85, 0.40),   # facing a bet
    "post_raise": (0.90, 0.55),  # facing a raise
    "post_allin": (1.0, 0.50),  # facing an all-in postflop
}
PRIOR_N = 4.0
# Most often a sensible player can be aggressive for value at a node; more
# aggression than this is counted as bluffs (see Model._agg_eps).
VALUE_CAP = {
    "bb_raise": 0.2, "pf_3b": 0.25, "post_open": 0.5,
    "post_bet": 0.15, "post_raise": 0.12,
}
BLUFF = 0.15        # post_open: share of weak hands that bet anyway
NBINS = 20
MAX_OBS = 300
STREETS = ("preflop", "flop", "turn", "river")


def _sig(x):
    if x > 30:
        return 1.0
    if x < -30:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def band_weights(lo, hi, floor=0.02):
    """169 class weights (per combo) for the strength band x in [lo, hi]."""
    w = [floor] * 169
    for i in range(169):
        a, b = CUM_LO[i], CUM_HI[i]
        ov = min(b, hi) - max(a, lo)
        if ov > 0:
            w[i] += ov / (b - a)
    return w


def _cum_uniform(ulo, uhi):
    """Cumulative histogram (NBINS + 1 points) of uniform u on [ulo, uhi]."""
    width = max(1e-9, uhi - ulo)
    return tuple(min(1.0, max(0.0, (k / NBINS - ulo) / width))
                 for k in range(NBINS + 1))


def _cum_weighted(us, ws):
    bins = [0.0] * NBINS
    for u, w in zip(us, ws):
        k = int(u * NBINS)
        bins[k if k < NBINS else NBINS - 1] += w
    total = sum(bins) or 1.0
    cum = [0.0]
    acc = 0.0
    for b in bins:
        acc += b
        cum.append(acc / total)
    return tuple(cum)


def _below(cum, t):
    """Mass of the histogram below strength t."""
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0
    x = t * NBINS
    k = int(x)
    return cum[k] + (cum[k + 1] - cum[k]) * (x - k)


def _fit(obs, prior_t, want, bluff=0.0):
    """Threshold T so that the expected count of hands above T (each
    observation's histogram; plus PRIOR_N uniform pseudo-observations at
    prior_t) matches the observed count `want`."""
    target = want + PRIOR_N * (bluff + (1 - bluff) * (1 - prior_t))
    lo, hi = 0.0, 1.0
    for _ in range(22):
        mid = (lo + hi) / 2
        g = PRIOR_N * (bluff + (1 - bluff) * (1 - mid))
        for cum in obs:
            g += bluff + (1 - bluff) * (1 - _below(cum, mid))
        if g > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None
        self.ids = []
        self.hist = []           # every action seen this hand
        self.init_stacks = None  # stacks at the start of this hand
        self.opp = {}            # team name -> node -> model data
        self.rng = random.Random(20261107)
        self.hands_seen = 0
        self.time_budget = 1.5   # seconds of Monte Carlo per decision, max
        self.mc_samples = 2500
        self.last_info = None    # debugging: last decision's EVs
        self.debug = False       # True: re-raise errors (testing only)
        self.nash_k = 40.0       # (unused by the gate; kept for variants)
        self.exploit_c = 0.3     # exploitation margin, share of the pot
        self.targeted_exploit = True  # steal / isolate / respect rules
        self.river_solve = True  # re-solve river spots with CFR+
        self.river_iters = 200
        self.river_budget = 3.0  # seconds since the start of the decision

    # ------------------------------------------------------------------ API

    def seed(self, hand, player_id):
        self.hand = hand
        self.player_id = player_id
        self.ids = [H.to_id(c) for c in hand]
        self.hist = []
        self.init_stacks = None

    def run(self, actions, game_state):
        start = time.perf_counter()
        try:
            self.hist.extend(actions)
        except Exception:
            pass
        action = None
        try:
            action = self._decide(game_state, start)
        except Exception:
            if self.debug:
                raise
            action = None
        return self._safe(action, game_state)

    def end_game(self, actions, game_state):
        try:
            self.hist.extend(actions)
            self._learn(game_state)
        except Exception:
            if self.debug:
                raise
        self.hands_seen += 1

    # ------------------------------------------------------------ safety

    def _safe(self, action, gs):
        me = self.player_id
        if action is not None:
            try:
                gs.validate_action(action)
                return action
            except Exception:
                pass
        try:
            if gs.call_amount == 0:
                return PlayerAction.call(me)
        except Exception:
            pass
        return PlayerAction.fold(me)

    def _raise(self, gs, total):
        """A legal raise_to near total (int), or None if raising is not
        allowed. Totals close to all-in become all-in."""
        if not gs.can_raise:
            return None
        lo, hi = gs.raise_range
        total = int(round(total))
        if total >= hi - 0.25 * (hi - gs.current_bet) or total >= hi:
            total = hi
        total = max(lo, min(hi, total))
        return PlayerAction.raise_to(self.player_id, total)

    # --------------------------------------------------------- history

    def _replay(self, button, sb_amt, bb_amt, init=None):
        """Events for every player action this hand, with context."""
        bbp = 1 - button
        bets = [0, 0]
        bets[button] = sb_amt
        bets[bbp] = bb_amt
        contrib = list(bets)
        cur = bb_amt
        nraises = 0
        nboard = 0
        street = 0
        allin = [False, False]
        events = []
        for a in self.hist:
            kind = a.kind
            if kind == "reveal_card":
                if a.player_id == -1:
                    nboard += 1
                    if nboard in (1, 4, 5):
                        street = 1 if nboard == 1 else nboard - 2
                        bets = [0, 0]
                        cur = 0
                        nraises = 0
                continue
            if kind not in ("fold", "call", "raise"):
                continue
            p = a.player_id
            if p not in (0, 1):
                continue
            ev = {
                "p": p, "kind": kind, "street": street,
                "to_call": cur - bets[p], "nraises": nraises,
                "pot": contrib[0] + contrib[1], "nboard": nboard,
                "facing_allin": allin[1 - p] and cur - bets[p] > 0,
                "is_button": p == button, "amount": a.amount, "cur": cur,
            }
            if kind == "call":
                add = max(0, cur - bets[p])
                if init is not None:
                    add = min(add, init[p] - contrib[p])
                bets[p] += add
                contrib[p] += add
            elif kind == "raise":
                add = a.amount - bets[p]
                bets[p] = a.amount
                cur = a.amount
                contrib[p] += add
                nraises += 1
            if init is not None and contrib[p] >= init[p]:
                allin[p] = True
            ev["node"] = self._node(ev)
            events.append(ev)
        return events, contrib

    @staticmethod
    def _node(ev):
        if ev["street"] == 0:
            if ev["facing_allin"]:
                return "pf_allin"
            if ev["nraises"] == 0:
                return "sb_open" if ev["is_button"] else "bb_limp"
            if ev["nraises"] == 1 and not ev["is_button"]:
                return "bb_raise"
            return "pf_3b"
        if ev["facing_allin"]:
            return "post_allin"
        if ev["to_call"] == 0:
            return "post_open"
        if ev["nraises"] <= 1:
            return "post_bet"
        return "post_raise"

    # ------------------------------------------------------ opponent model

    def _stats(self, name):
        st = self.opp.get(name)
        if st is None:
            st = self._fresh_stats()
            self.opp[name] = st
        return st

    @staticmethod
    def _fresh_stats():
        st = {k: {"obs": [], "agg": 0, "cont": 0,
                  "Ta": PRIORS[k][0], "Tc": PRIORS[k][1]}
              for k in PRIORS}
        # Showdown checks: [actions seen, actions off-model] for
        # preflop raise, preflop call, postflop bet/raise, postflop call.
        st["noise"] = {"pa": [0, 0], "pc": [0, 0], "qa": [0, 0],
                       "qc": [0, 0]}
        return st

    @staticmethod
    def _eps(st, key):
        """Share of this kind of action that does not follow the
        threshold model (bluffs, random play), prior 0.1."""
        n, off = st["noise"][key]
        return (off + 0.5) / (n + 5.0)

    @staticmethod
    def _agg_eps(st, node):
        """Minimum bluff share of their aggressive actions at node implied
        by how often they are aggressive there: no sensible player has a
        value hand more often than VALUE_CAP[node]."""
        cap = VALUE_CAP.get(node)
        if cap is None:
            return 0.0
        a, n = st[node].get("an", (0, 0))
        prior = 1.0 - PRIORS[node][0]
        freq = (a + PRIOR_N * prior) / (n + PRIOR_N)
        return max(0.0, 1.0 - cap / max(cap, freq))

    def _loose_eps(self, st, node):
        """Raises are rarely shown down (we fold to them), so borrow from
        how often their calls turn out to be junk at showdown, scaled by
        how often they raise at node: a loose caller that raises a lot is
        likely raising loosely too."""
        n = st["noise"]["pc"][0] + st["noise"]["qc"][0]
        if n < 4:
            return 0.0
        loose = 0.5 * (self._eps(st, "pc") + self._eps(st, "qc"))
        a, m = st[node].get("an", (0, 0))
        freq = (a + PRIOR_N * (1.0 - PRIORS[node][0])) / (m + PRIOR_N)
        return 0.8 * loose * min(1.0, freq / 0.25)

    @staticmethod
    def _size_bucket(ev):
        """0 small, 1 medium, 2 large (incl. all-in) bet or raise, by the
        chips added beyond the current bet relative to the pot before."""
        frac = (ev["amount"] - ev["cur"]) / max(1.0, ev["pot"])
        if frac <= SIZE_BUCKETS[0]:
            return 0
        return 1 if frac <= SIZE_BUCKETS[1] else 2

    @staticmethod
    def _size_tell(st, bucket, ta, ea):
        """(threshold, bluff share) for a bet of this size, moved toward
        what the opponent has shown down after bets of this size."""
        seen = st.get("sizes", [[], [], []])[bucket]
        n = len(seen)
        if n < 3:
            return ta, ea
        mean = sum(seen) / n
        low = sum(1 for x in seen if x < 0.4) / n
        # Their value region starts a bit below the average shown hand.
        centre = max(0.05, min(0.97, mean - 0.15))
        w = n / (n + 4.0)
        ta2 = (1 - w) * ta + w * centre
        ea2 = (1 - w) * ea + w * max(0.03, min(0.9, low))
        return ta2, ea2

    @staticmethod
    def _thresholds(st, node):
        d = st[node]
        return d["Ta"], d["Tc"]

    def _refit(self, st, node):
        d = st[node]
        obs = [o[0] for o in d["obs"]]
        agg = sum(1 for o in d["obs"] if o[1] == 0)
        cont = sum(1 for o in d["obs"] if o[1] <= 1)
        d["an"] = (agg, len(obs))
        pa, pc = PRIORS[node]
        if node == "post_open":
            d["Ta"] = _fit(obs, pa, agg, BLUFF)
        elif pa < 1.0:
            d["Ta"] = _fit(obs, pa, agg)
        if pc > 0.0:
            d["Tc"] = min(d["Ta"], _fit(obs, pc, cont))

    def _learn(self, gs):
        if gs.num_players != 2:
            return
        me = self.player_id
        opp = 1 - me
        st = self._stats(gs.teams[opp])
        if self.init_stacks is None:
            self.init_stacks = [10 ** 9, 10 ** 9]
        events, contrib = self._replay(gs.button, gs.small_blind,
                                       gs.big_blind, self.init_stacks)
        shown = [H.to_id(a.card) for a in self.hist
                 if a.kind == "reveal_card" and a.player_id == opp]
        ctx = {"gs": gs, "me": me, "opp": opp, "st": st, "events": events,
               "opp_ids": shown if len(shown) == 2 else None}
        record = []
        self._pf_band(ctx, record)
        board = [H.to_id(c) for c in gs.board]
        if any(e["street"] > 0 and e["p"] == opp for e in events):
            self._opp_range(ctx, board, record)
        st["hands"] = st.get("hands", 0) + 1
        # Per-street fold-to-bet counts: [facing a bet, folded] by street.
        fs = st.setdefault("fold_street", [[0, 0] for _ in range(4)])
        for ev in events:
            if ev["p"] == opp and ev["node"] == "post_bet":
                fs[ev["street"]][0] += 1
                if ev["kind"] == "fold":
                    fs[ev["street"]][1] += 1
        sizes = st.setdefault("sizes", [[], [], []])
        for b, u_act in ctx.get("size_obs", ()):
            sizes[b].append(u_act)
            if len(sizes[b]) > 40:
                del sizes[b][0]
        touched = set()
        noise = st["noise"]
        for node, cum, idx, ta, tc, u_act in record:
            check = u_act is not None and (
                idx == 0 or (idx == 1 and node not in ("bb_limp",
                                                       "post_open")))
            if check:
                pre = node in ("sb_open", "bb_limp", "bb_raise", "pf_3b",
                               "pf_allin")
                key = ("p" if pre else "q") + ("a" if idx == 0 else "c")
                limit = ta if idx == 0 else tc
                noise[key][0] += 1
                if u_act < limit - 0.08:
                    noise[key][1] += 1
            d = st[node]
            d["obs"].append((cum, idx))
            if len(d["obs"]) > MAX_OBS:
                del d["obs"][0]
            touched.add(node)
        for node in touched:
            self._refit(st, node)

    # ----------------------------------------------------------- deciding

    def _decide(self, gs, start):
        me = self.player_id
        if gs.num_players != 2:
            return self._simple(gs)
        opp = 1 - me
        if self.init_stacks is None:
            _, contrib = self._replay(gs.button, gs.small_blind, gs.big_blind)
            self.init_stacks = [gs.stacks[i] + contrib[i] for i in range(2)]
        events, contrib = self._replay(gs.button, gs.small_blind,
                                       gs.big_blind, self.init_stacks)
        st = self._stats(gs.teams[opp])
        ctx = {
            "gs": gs, "me": me, "opp": opp, "st": st, "events": events,
            "contrib": contrib, "start": start,
            "E": min(gs.stacks[me] + contrib[me],
                     gs.stacks[opp] + contrib[opp]),
        }
        if gs.street == "preflop":
            return self._preflop(ctx)
        return self._postflop(ctx)

    def _choose(self, options, pot):
        """options: list of (ev, action). Picks the best, mixing between
        near-equal ones so play is not fully deterministic."""
        options = [o for o in options if o[1] is not None]
        weights = self._softmax(options, pot)
        self.last_info = [(round(ev, 1), a.kind, a.amount)
                          for ev, a in options]
        return self._sample(weights, [o[1] for o in options])

    @staticmethod
    def _softmax(options, pot):
        best = max(o[0] for o in options)
        temp = 0.015 * pot + 1.0
        w = [math.exp((ev - best) / temp) for ev, _ in options]
        s = sum(w)
        return [x / s for x in w]

    def _sample(self, weights, items):
        r = self.rng.random() * sum(weights)
        for w, it in zip(weights, items):
            r -= w
            if r <= 0:
                return it
        return items[-1]

    # -------------------------------------------- equilibrium baseline

    def _lambda(self, st):
        """Trust in the opponent model: 0 = play the equilibrium, toward
        LAMBDA_MAX = play the best response to the model. Grows with the
        hands seen against this opponent (confidence-scheduled restricted
        response)."""
        n = st.get("hands", 0)
        return LAMBDA_MAX * n / (n + self.nash_k)

    def _nash_path(self, ctx):
        """The equilibrium tree node matching this preflop history, or
        None when the hand has left the abstraction (other stack depth,
        a 5-bet, a fold...). Raises of any size map to the nearest
        abstract action (open / raise / shove)."""
        gs = ctx["gs"]
        bb = gs.big_blind
        if gs.small_blind * 2 != bb or abs(ctx["E"] - 25 * bb) > 5 * bb:
            return None
        init = self.init_stacks or [ctx["E"], ctx["E"]]
        path = "r"
        for ev in ctx["events"]:
            if ev["street"] != 0:
                return None
            k = ev["kind"]
            if k == "fold":
                return None
            if k == "call":
                a = {"r": "limp", "r/limp": "check"}.get(path, "call")
            elif ev["amount"] >= 0.6 * min(init):
                a = "shove"
            else:
                a = "open" if path == "r" else "raise"
            path = path + "/" + a
            if path not in NASH_P:
                return None
        return path

    def _blend_preflop(self, ctx, options, pot):
        """Mixes the equilibrium strategy with the best response to the
        opponent model, weighted by _lambda."""
        path = self._nash_path(ctx)
        if path is None:
            return self._choose(options, pot)
        acts, table = NASH_P[path]
        gs = ctx["gs"]
        h = H.class_of(self.ids[0], self.ids[1])
        mapped = {}
        for ev, act in options:
            if act is None:
                continue
            if act.kind == "fold":
                a = "fold"
            elif act.kind == "call":
                a = "limp" if "limp" in acts else (
                    "check" if "check" in acts else "call")
            elif act.amount >= gs.max_raise_to:
                a = "shove"
            else:
                a = "open" if "open" in acts else "raise"
            if a in acts and (a not in mapped or ev > mapped[a][0]):
                mapped[a] = (ev, act)
        if not mapped:
            return self._choose(options, pot)
        names = list(mapped)
        nash = [table[h][acts.index(a)] for a in names]
        s = sum(nash)
        if s <= 0:
            return self._choose(options, pot)
        probs = [p / s for p in nash]
        evs = [mapped[a][0] for a in names]
        st = ctx["st"]
        mode = "nash"
        # Exploit only deviations measured directly (data-biased response).
        if not self.targeted_exploit:
            pass
        elif path == "r" and "open" in names and "limp" in names and \
                (self._fold_rate(st, "bb_raise", 0.37) >= STEAL_FOLD or
                 self._fold_rate(st, "sb_open", 0.0) >= 0.5):
            i, j = names.index("limp"), names.index("open")
            probs[j] += probs[i]
            probs[i] = 0.0
            mode = "steal"
        elif path == "r/limp" and "raise" in names and "check" in names \
                and self._fold_rate(st, "pf_3b", 0.41) >= STEAL_FOLD:
            i, j = names.index("check"), names.index("raise")
            probs[j] += probs[i]
            probs[i] = 0.0
            mode = "isolate"
        elif path in FACING and self._agg_ratio(st, FACING[path]) < TIGHT_RATIO:
            probs = self._softmax([mapped[a] for a in names], pot)
            mode = "respect"
        elif path in FACING and self._agg_ratio(st, FACING[path]) > LOOSE_RATIO:
            # They raise far more than equilibrium: their range is wide, so
            # call / re-shove wider (best response to the fitted range).
            probs = self._softmax([mapped[a] for a in names], pot)
            mode = "punish"
        self.last_info = [(round(e, 1), a, round(p, 2))
                          for e, a, p in zip(evs, names, probs)] + [mode]
        return self._sample(probs, [mapped[a][1] for a in names])

    @staticmethod
    def _fold_rate(st, node, prior):
        """Observed fold share at node, smoothed toward the equilibrium's;
        0 until there are enough observations."""
        obs = st[node]["obs"]
        if len(obs) < MIN_OBS:
            return 0.0
        folds = sum(1 for o in obs if o[1] == 2)
        return (folds + 6 * prior) / (len(obs) + 6)

    @staticmethod
    def _agg_ratio(st, spec):
        """Observed aggression share at their node over the equilibrium's
        (1.0 until there are enough observations)."""
        node, nash_freq = spec
        obs = st[node]["obs"]
        if len(obs) < MIN_OBS + 2:
            return 1.0
        agg = sum(1 for o in obs if o[1] == 0)
        return (agg + 6 * nash_freq) / (len(obs) + 6) / nash_freq

    def _margin(self, st, pot):
        """Gated (safe) exploitation: leave the equilibrium only when the
        model's estimated gain beats this margin, which shrinks as hands
        against this opponent accumulate."""
        n = st.get("hands", 0)
        return self.exploit_c * pot * math.sqrt(30.0 / (n + 30.0))

    # ------------------------------------------------------------ preflop

    @staticmethod
    def _split(lo, hi, ta, tc):
        """Band [lo, hi] (x, 0 = strongest) split by thresholds in u:
        (raise share, call share, fold share, raise band, call band)."""
        w = max(1e-9, hi - lo)
        xa, xc = 1.0 - ta, 1.0 - tc
        ra = min(hi, max(lo, xa))
        rc = min(hi, max(lo, xc))
        a = (ra - lo) / w
        cont = (rc - lo) / w
        return a, max(0.0, cont - a), 1.0 - cont, (lo, ra), (ra, rc)

    def _pf_band(self, ctx, record=None):
        """Opponent's preflop range as a strength band [lo, hi] in x. Sets
        ctx["floor"]: weight of hands outside the band (bluffs, noise)."""
        lo, hi = 0.0, 1.0
        floor = 0.02
        for ev in ctx["events"]:
            if ev["p"] != ctx["opp"] or ev["street"] != 0:
                continue
            node = ev["node"]
            k = ev["kind"]
            ta, tc = self._thresholds(ctx["st"], node)
            if record is not None:
                idx = 0 if k == "raise" else (1 if k == "call" else 2)
                u_act = None
                if ctx.get("opp_ids"):
                    c = H.class_of(ctx["opp_ids"][0], ctx["opp_ids"][1])
                    u_act = 1.0 - (CUM_LO[c] + CUM_HI[c]) / 2
                record.append((node, _cum_uniform(1 - hi, 1 - lo), idx,
                               ta, tc, u_act))
            a, c, f, rband, cband = self._split(lo, hi, ta, tc)
            w = hi - lo
            if k in ("raise", "call") and node != "bb_limp":
                eps = self._eps(ctx["st"], "pa" if k == "raise" else "pc")
                if k == "raise":
                    eps = max(eps, self._agg_eps(ctx["st"], node),
                              self._loose_eps(ctx["st"], node))
                eps = min(0.9, eps)
                share = (rband[1] - rband[0]) if k == "raise" else \
                    (cband[1] - cband[0])
                share = max(0.02, share)
                floor = max(floor, min(1.0, eps * share /
                                       ((1 - eps) * max(0.05, 1 - share))))
            if k == "raise":
                if rband[1] - rband[0] < 0.02 * w:
                    rband = (lo, lo + 0.25 * w)  # off-model: assume strong
                lo, hi = rband
            elif k == "call":
                if cband[1] - cband[0] < 0.02 * w:
                    cband = (lo + 0.2 * w, lo + 0.7 * w)
                lo, hi = cband
        ctx["floor"] = floor
        return lo, max(hi, lo + 0.005)

    def _eq_vs_weights(self, w):
        h = H.class_of(self.ids[0], self.ids[1])
        dead = set(self.ids)
        num = 0.0
        den = 0.0
        row = EQ[h]
        for j in range(169):
            wj = w[j]
            if wj <= 0:
                continue
            n = 0
            for a, b in H.CLASS_COMBOS[j]:
                if a not in dead and b not in dead:
                    n += 1
            if n:
                num += wj * n * row[j]
                den += wj * n
        return num / den if den else 0.5

    def _eq_band(self, band, floor=0.02):
        lo, hi = band
        if hi - lo < 0.003:
            hi = lo + 0.003
        return self._eq_vs_weights(band_weights(lo, hi, floor))

    @staticmethod
    def _realize(eq, ip):
        r = (1.0 if ip else 0.88) + 0.35 * (eq - 0.5)
        return max(0.6, min(1.15, r))

    def _preflop(self, ctx):
        gs, me, opp = ctx["gs"], ctx["me"], ctx["opp"]
        st = ctx["st"]
        contrib = ctx["contrib"]
        E = ctx["E"]
        pot = gs.pot
        to_call = gs.call_amount
        ip = gs.button == me  # the button is in position after the flop
        c_me = contrib[me]
        lo, hi = self._pf_band(ctx)
        eq_all = self._eq_band((lo, hi), ctx["floor"])
        opp_allin = gs.stacks[opp] == 0
        options = [(0.0, PlayerAction.fold(me))]
        raised = any(e["street"] == 0 and e["kind"] == "raise"
                     for e in ctx["events"])

        # Calling (or checking).
        call = PlayerAction.call(me)
        if opp_allin or gs.stacks[me] <= to_call:
            ev_call = eq_all * min(2 * E, pot + to_call) - to_call
        elif ip and to_call > 0 and not raised:
            # Limp from the button: the big blind may raise.
            ta, tc = self._thresholds(st, "bb_limp")
            a, c, f, rband, cband = self._split(lo, hi, ta, tc)
            eq_chk = self._eq_band((rband[1], hi))
            eq_r = self._eq_band(rband)
            pot_l = pot + to_call
            extra = 3 * gs.big_blind
            v_face = max(0.0, eq_r * self._realize(eq_r, True) *
                         (pot_l + 2 * extra) - extra)
            ev_call = -to_call + (1 - a) * eq_chk * \
                self._realize(eq_chk, True) * pot_l + a * v_face
        else:
            ev_call = eq_all * self._realize(eq_all, ip) * (pot + to_call) \
                - to_call
        options.append((ev_call, call))

        if gs.can_raise and not opp_allin:
            # All-in.
            ta, tc = self._thresholds(st, "pf_allin")
            a, c, f, rband, cband = self._split(lo, hi, 1.0, tc)
            add = E - c_me
            if c > 0.001:
                ev_shove = f * pot + c * (self._eq_band(cband) * 2 * E - add)
            else:
                ev_shove = pot
            options.append((ev_shove, self._raise(gs, gs.max_raise_to)))
            # A normal raise.
            if not raised and ip:
                size, rnode = 2.5 * gs.big_blind, "bb_raise"
            elif not raised:
                size, rnode = 4 * gs.big_blind, "pf_3b"
            else:
                size, rnode = gs.current_bet * 3, "pf_3b"
            if size < 0.4 * E:
                act = self._raise(gs, size)
                if act is not None and act.amount < gs.max_raise_to:
                    X = act.amount
                    ta, tc = self._thresholds(st, rnode)
                    a, c, f, rband, cband = self._split(lo, hi, ta, tc)
                    add = X - gs.bets[me]
                    ev_raise = f * pot
                    if c > 0.001:
                        eq_cl = self._eq_band(cband)
                        pot_called = pot + add + (X - gs.bets[opp])
                        ev_raise += c * (eq_cl * self._realize(eq_cl, ip) *
                                         pot_called - add)
                    if a > 0.001:
                        # Re-raised: assume all-in; call or fold.
                        eq_rr = self._eq_band(rband)
                        ev_raise += a * max(-add, eq_rr * 2 * E - (E - c_me))
                    options.append((ev_raise, act))
        return self._blend_preflop(ctx, options, pot)

    # ----------------------------------------------------------- postflop

    def _opp_range(self, ctx, board_ids, record=None, exclude_hero=True):
        """Opponent's combos, weights and strength u on the current board.
        Starts from the preflop band; every postflop action of theirs is
        applied in order, on the board of its street. With record, appends
        (node, histogram of u, action) for each of their actions."""
        lo, hi = self._pf_band(ctx)
        cw = band_weights(lo, hi, floor=ctx["floor"])
        dead = set(board_ids)
        if exclude_hero:
            dead |= set(self.ids)
        combos = []
        weights = []
        for a, b in H.ALL_COMBOS:
            if a in dead or b in dead:
                continue
            combos.append((a, b))
            weights.append(cw[H.class_of(a, b)])
        base = list(weights)
        st = ctx["st"]
        opp = ctx["opp"]
        cache = {}
        for ev in ctx["events"]:
            if ev["p"] != opp or ev["street"] == 0:
                continue
            nb = ev["nboard"]
            if nb not in cache:
                cache[nb] = self._strength_u(combos, base, board_ids[:nb])
            u = cache[nb]
            node = ev["node"]
            k = ev["kind"]
            ta, tc = self._thresholds(st, node)
            if record is not None:
                idx = 0 if k == "raise" else (1 if k == "call" else 2)
                u_act = None
                if ctx.get("opp_ids"):
                    pair = tuple(sorted(ctx["opp_ids"]))
                    for i, cmb in enumerate(combos):
                        if cmb == pair:
                            u_act = u[i]
                            break
                record.append((node, _cum_weighted(u, weights), idx,
                               ta, tc, u_act))
                if k == "raise" and u_act is not None:
                    ctx.setdefault("size_obs", []).append(
                        (self._size_bucket(ev), u_act))
            ea = min(0.9, max(BLUFF if node == "post_open" else 0.05,
                              self._eps(st, "qa"), self._agg_eps(st, node),
                              self._loose_eps(st, node)))
            ec = max(0.04, self._eps(st, "qc"))
            if k == "raise":
                # Bet-size tell: what this opponent has shown down after
                # bets of this size moves the centre of their range.
                ta, ea = self._size_tell(st, self._size_bucket(ev), ta, ea)
            for i in range(len(combos)):
                s = u[i]
                pa = _sig((s - ta) / 0.05)
                if node == "post_open":
                    if k == "raise":
                        m = ea + (1 - ea) * pa
                    else:
                        m = 1.0 - (BLUFF + (1 - BLUFF) * pa)
                elif k == "raise":
                    m = ea + (1 - ea) * pa
                elif k == "call":
                    m = (ec + (1 - ec) * _sig((s - tc) / 0.06)) * \
                        (1.0 - 0.7 * (1 - ea) * pa)
                else:
                    m = 1.0
                weights[i] *= m
        nb = len(board_ids)
        if nb not in cache:
            cache[nb] = self._strength_u(combos, base, board_ids)
        return combos, weights, cache[nb]

    @staticmethod
    def _strength_u(combos, weights, board):
        """Strength u in [0, 1] of each combo on board: made-hand percentile
        within the preflop range, lifted for flush and straight draws."""
        ev = H.evaluate_ids
        n = len(combos)
        made = [ev([a, b] + board) for a, b in combos]
        draws = [0.0] * n
        if len(board) < 5:
            bmask = 0
            bsuits = [0, 0, 0, 0]
            for c in board:
                bmask |= H.CARD_RANKBIT[c]
                bsuits[c & 3] += 1
            sh = H.STRAIGHT_HIGH
            fd_val = 0.6 if len(board) == 3 else 0.45
            sd_val = 0.5 if len(board) == 3 else 0.4
            for i, (a, b) in enumerate(combos):
                d = 0.0
                sa, sb = a & 3, b & 3
                if (bsuits[sa] + 1 + (sb == sa) == 4) or \
                        (sb != sa and bsuits[sb] == 3):
                    d = fd_val
                mask = bmask | H.CARD_RANKBIT[a] | H.CARD_RANKBIT[b]
                if sh[mask] < 0:
                    outs = 0
                    for r in range(13):
                        if sh[mask | (1 << r)] >= 0:
                            outs += 1
                    if outs >= 2:
                        d = 0.8 if d else sd_val
                    elif outs == 1 and not d:
                        d = 0.3
                draws[i] = d
        order = sorted(range(n), key=made.__getitem__)
        total = sum(weights) or 1.0
        u = [0.0] * n
        acc = 0.0
        i = 0
        while i < n:
            j = i
            group = 0.0
            m0 = made[order[i]]
            while j < n and made[order[j]] == m0:
                group += weights[order[j]]
                j += 1
            mid = (acc + group / 2) / total
            for k in range(i, j):
                idx = order[k]
                u[idx] = mid if mid > draws[idx] else draws[idx]
            acc += group
            i = j
        return u

    def _mc(self, combos, weights, u, board, deadline):
        """Monte Carlo: list of (u, result) samples against the range."""
        rng = self.rng
        ev = H.evaluate_ids
        hero = self.ids
        need = 5 - len(board)
        dead = set(hero) | set(board)
        deck = [c for c in range(52) if c not in dead]
        n = len(combos)
        cum = []
        acc = 0.0
        for w in weights:
            acc += w
            cum.append(acc)
        picks = rng.choices(range(n), cum_weights=cum, k=self.mc_samples)
        out = []
        mine = ev(hero + board) if not need else 0
        for t, i in enumerate(picks):
            if t & 255 == 255 and time.perf_counter() > deadline:
                break
            a, b = combos[i]
            if need:
                run = []
                for c in rng.sample(deck, need + 2):
                    if c != a and c != b:
                        run.append(c)
                        if len(run) == need:
                            break
                full = board + run
                sh = ev(hero + full)
                so = ev([a, b] + full)
            else:
                sh = mine
                so = ev([a, b] + board)
            out.append((u[i], 1.0 if sh > so else (0.5 if sh == so else 0.0)))
        return out

    @staticmethod
    def _response(samples, ta, tc):
        """(fold share, equity when they call, equity when they raise,
        raise share) from samples, given their thresholds."""
        f = 0.0
        num_c = den_c = num_r = den_r = 0.0
        for s, r in samples:
            pc = _sig((s - tc) / 0.06)
            pa = _sig((s - ta) / 0.05) if ta < 1.0 else 0.0
            f += 1.0 - pc
            wc = max(0.0, pc - pa)
            num_c += wc * r
            den_c += wc
            num_r += pa * r
            den_r += pa
        n = max(1, len(samples))
        eq_c = num_c / den_c if den_c > 1e-6 else 0.5
        eq_r = num_r / den_r if den_r > 1e-6 else 0.5
        return f / n, eq_c, eq_r, den_r / n

    def _postflop(self, ctx):
        gs, me, opp, st = ctx["gs"], ctx["me"], ctx["opp"], ctx["st"]
        board = [H.to_id(c) for c in gs.board]
        combos, weights, u = self._opp_range(ctx, board)
        if sum(weights) <= 0:
            weights = [1.0] * len(combos)
        deadline = ctx["start"] + self.time_budget
        samples = self._mc(combos, weights, u, board, deadline)
        if not samples:
            return None
        eq = sum(r for _, r in samples) / len(samples)
        pot = gs.pot
        to_call = gs.call_amount
        E = ctx["E"]
        c_me = ctx["contrib"][me]
        river = gs.street == "river"
        ip = gs.button == me
        opp_allin = gs.stacks[opp] == 0
        R = 1.0 if river else self._realize(eq, ip)
        options = [(0.0, PlayerAction.fold(me))]
        call = PlayerAction.call(me)
        if to_call == 0:
            options.append((eq * R * pot, call))
        else:
            if opp_allin or gs.stacks[me] <= to_call:
                ev_call = eq * min(pot + to_call, 2 * E) - to_call
            else:
                ev_call = eq * R * (pot + to_call) - to_call
            options.append((ev_call, call))
        if gs.can_raise and not opp_allin:
            street = STREETS.index(gs.street)
            nraises = sum(1 for e in ctx["events"]
                          if e["street"] == street and e["kind"] == "raise")
            node = "post_bet" if nraises == 0 else "post_raise"
            ta, tc = self._thresholds(st, node)
            _, tc_ai = self._thresholds(st, "post_allin")
            if to_call == 0:
                sizes = [0.33 * pot, 0.66 * pot, 1.0 * pot, 1.6 * pot]
            else:
                sizes = [gs.current_bet * 2.5 + 0.5 * pot]
            totals = [gs.bets[me] + s for s in sizes] + [gs.max_raise_to]
            seen = set()
            for X in totals:
                act = self._raise(gs, X)
                if act is None or act.amount in seen:
                    continue
                seen.add(act.amount)
                X = act.amount
                add = X - gs.bets[me]
                allin = X >= gs.max_raise_to
                frac = (X - gs.current_bet) / max(1.0, pot)
                if allin:
                    f, eq_c, _, _ = self._response(samples, 1.0, tc_ai)
                    ev_r = f * pot + (1 - f) * (eq_c * 2 * E - (E - c_me))
                else:
                    # Bigger bets fold out more of their range.
                    shift = 0.12 * (frac - 0.66)
                    f, eq_c, eq_rr, a = self._response(
                        samples, min(1.0, ta + shift / 2),
                        max(0.0, min(0.98, tc + shift)))
                    pot_called = pot + add + (X - gs.bets[opp])
                    Rc = 1.0 if river else self._realize(eq_c, ip)
                    c = max(0.0, 1.0 - f - a)
                    ev_r = f * pot + c * (eq_c * Rc * pot_called - add)
                    if a > 0:
                        # Re-raised (all-in assumed): call or fold.
                        ev_r += a * max(-add, eq_rr * 2 * E - (E - c_me))
                options.append((ev_r, act))
        # Measured postflop exploits (counts, not model estimates).
        fold_bet = self._fold_rate(st, "post_bet", 0.4)
        # Prefer this street's own fold rate once it has enough data:
        # many bots call early and fold late, or the reverse.
        fs = st.get("fold_street")
        if fs is not None:
            n, f = fs[STREETS.index(gs.street)]
            if n >= MIN_OBS:
                fold_bet = (f + 4 * fold_bet) / (n + 4)
        if to_call == 0 and gs.can_raise and not opp_allin and \
                fold_bet >= BARREL_FOLD:
            # Over-folder: bet every time we may, smallest size that works
            # (value hands keep their best-EV size).
            bets = [o for o in options if o[1] is not None and
                    o[1].kind == "raise"]
            if bets:
                best = max(options, key=lambda o: o[0])
                if best[1].kind == "raise":
                    return best[1]
                small = min(bets, key=lambda o: o[1].amount)
                return small[1]
        if 0 < fold_bet <= STATION_FOLD and to_call == 0 and eq < 0.5:
            # Calling station: no bluffs; check behind with weak hands.
            return call
        if river and not opp_allin and self.river_solve and (
                gs.can_raise or to_call > 0):
            act = self._river_resolve(ctx, combos, weights, board, options)
            if act is not None:
                return act
        return self._choose(options, pot)

    # ------------------------------------------------ river re-solving

    def _river_resolve(self, ctx, combos, weights, board, options):
        """Re-solves the river as a range-vs-range game (CFR+ over strength
        buckets) and plays its strategy for our hand, unless the opponent
        model's best response is worth more than the exploitation margin.
        Our range is what the opponent could infer from our line (our
        actions through the default model); theirs is our current model."""
        gs, me, opp = ctx["gs"], ctx["me"], ctx["opp"]
        ctx2 = dict(ctx)
        ctx2["opp"] = me
        ctx2["st"] = self._fresh_stats()
        our_c, our_w, _ = self._opp_range(ctx2, board, exclude_hero=False)
        ev = H.evaluate_ids
        our_s = [ev([a, b] + board) for a, b in our_c]
        their_s = [ev([a, b] + board) for a, b in combos]
        edges = RS.buckets(our_s, our_w, their_s, weights, RIVER_BUCKETS)
        ours = RS.to_buckets(our_s, our_w, edges)
        theirs = RS.to_buckets(their_s, weights, edges)
        tot = sum(ours) or 1.0
        ours = [x / tot + 0.02 / len(ours) for x in ours]
        tt = sum(theirs) or 1.0
        theirs = [x / tt for x in theirs]
        mine = RS.bucket_of(ev(self.ids + board), edges)
        deadline = ctx["start"] + self.river_budget
        acts, root = RS.solve(ours, theirs, gs.pot, gs.call_amount,
                              gs.stacks[me], gs.stacks[opp],
                              self.river_iters, deadline)
        probs = root[mine]
        concrete = []
        for name, add in acts:
            if name == "fold":
                a = PlayerAction.fold(me)
            elif name in ("check", "call"):
                a = PlayerAction.call(me)
            elif name == "allin":
                a = self._raise(gs, gs.max_raise_to)
            else:
                a = self._raise(gs, gs.bets[me] + add)
            concrete.append(a)
        # Exploit EVs of the solver's actions (nearest exploit option).
        evs = []
        for a in concrete:
            best = None
            for e, o in options:
                if o is None or a is None or o.kind != a.kind:
                    continue
                d = abs(o.amount - a.amount)
                if best is None or d < best[0]:
                    best = (d, e)
            evs.append(best[1] if best else -1e9)
        keep = [i for i, a in enumerate(concrete)
                if a is not None and evs[i] > -1e8]
        if not keep:
            return None
        p = [probs[i] for i in keep]
        s = sum(p)
        if s <= 0:
            return None
        p = [x / s for x in p]
        ev_solver = sum(x * evs[i] for x, i in zip(p, keep))
        valid = [o for o in options if o[1] is not None]
        gain = max(o[0] for o in valid) - ev_solver
        margin = self._margin(ctx["st"], gs.pot)
        self.last_info = [(round(evs[i], 1), acts[i][0], acts[i][1],
                           round(x, 2)) for x, i in zip(p, keep)] + \
            [("river", round(gain, 1), round(margin, 1))]
        if gain > margin:
            return None  # the caller plays the best response
        return self._sample(p, [concrete[i] for i in keep])

    # ---------------------------------------------------- 3+ players

    def _simple(self, gs):
        """Fallback for tables of 3 or more: equity vs random hands."""
        me = self.player_id
        board = [H.to_id(c) for c in gs.board]
        dead = set(self.ids) | set(board)
        deck = [c for c in range(52) if c not in dead]
        opps = max(1, len(gs.players_in_hand) - 1)
        rng = self.rng
        won = 0.0
        n = 300
        for _ in range(n):
            cards = rng.sample(deck, 5 - len(board) + 2 * opps)
            full = board + cards[:5 - len(board)]
            mine = H.evaluate_ids(self.ids + full)
            rest = cards[5 - len(board):]
            best = max(H.evaluate_ids(rest[2 * k:2 * k + 2] + full)
                       for k in range(opps))
            won += 1.0 if mine > best else (0.5 if mine == best else 0.0)
        eq = won / n
        if eq > 0.75 and gs.can_raise:
            return self._raise(gs, gs.current_bet + gs.pot)
        if gs.call_amount == 0 or eq > gs.pot_odds + 0.05:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)
