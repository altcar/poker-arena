import itertools
import random
import time

from game_state import GameStateView
from player_action import DEALER, PlayerAction

RANKS = "23456789TJQKA"
SUIT_IDX = {"C": 0, "D": 1, "H": 2, "S": 3}

_EVAL_CACHE = {}


def _card_int(card):
    return card.value * 4 + SUIT_IDX[card.suit]


def _rank_five(r0, r1, r2, r3, r4, flush):
    r = [r0, r1, r2, r3, r4]
    r.sort(reverse=True)
    a, b, c, d, e = r
    distinct = len({a, b, c, d, e}) == 5
    straight_high = None
    if distinct:
        if a - e == 4:
            straight_high = a
        elif a == 14 and b == 5 and c == 4 and d == 3 and e == 2:
            straight_high = 5
    if straight_high is not None and flush:
        return (8, straight_high)
    # counts from sorted run
    if a == b == c == d:
        return (7, a, e)
    if b == c == d == e:
        return (7, b, a)
    if a == b == c and d == e:
        return (6, a, d)
    if a == b and c == d == e:
        return (6, c, a)
    if flush:
        return (5, a, b, c, d, e)
    if straight_high is not None:
        return (4, straight_high)
    if a == b == c:
        return (3, a, d, e)
    if b == c == d:
        return (3, b, a, e)
    if c == d == e:
        return (3, c, a, b)
    if a == b and c == d:
        return (2, a, c, e)
    if a == b and d == e:
        return (2, a, d, c)
    if b == c and d == e:
        return (2, b, d, a)
    if a == b:
        return (1, a, c, d, e)
    if b == c:
        return (1, b, a, d, e)
    if c == d:
        return (1, c, a, b, e)
    if d == e:
        return (1, d, a, b, c)
    return (0, a, b, c, d, e)


def _best_rank(ints):
    n = len(ints)
    if n == 5:
        key = tuple(sorted(ints))
        hit = _EVAL_CACHE.get(key)
        if hit is not None:
            return hit
        ranks = [c // 4 for c in ints]
        suits = [c % 4 for c in ints]
        flush = suits[0] == suits[1] == suits[2] == suits[3] == suits[4]
        out = _rank_five(ranks[0], ranks[1], ranks[2], ranks[3], ranks[4], flush)
        if len(_EVAL_CACHE) < 60000:
            _EVAL_CACHE[key] = out
        return out
    best = None
    # iterate 21 combos; build key per combo via sorted tuple for cache
    for idx in itertools.combinations(range(n), 5):
        combo = (ints[idx[0]], ints[idx[1]], ints[idx[2]], ints[idx[3]], ints[idx[4]])
        key = tuple(sorted(combo))
        hit = _EVAL_CACHE.get(key)
        if hit is None:
            ranks = [co // 4 for co in combo]
            suits = [co % 4 for co in combo]
            flush = suits[0] == suits[1] == suits[2] == suits[3] == suits[4]
            hit = _rank_five(ranks[0], ranks[1], ranks[2], ranks[3], ranks[4], flush)
            if len(_EVAL_CACHE) < 60000:
                _EVAL_CACHE[key] = hit
        if best is None or hit > best:
            best = hit
    return best


def _hand_key(hole):
    c1, c2 = hole
    v1, v2 = c1.value, c2.value
    s1, s2 = c1.suit, c2.suit
    if v1 == v2:
        ch = RANKS[v1 - 2]
        return ch + ch
    hi, lo = (c1, c2) if v1 > v2 else (c2, c1)
    hs = RANKS[hi.value - 2] + RANKS[lo.value - 2]
    return hs + ("s" if hi.suit == lo.suit else "o")


# GTO Wizard 20bb SB first-in raise frequencies (pixel-extracted, noisy hands
# smoothed into tiers). Missing hands fall back to heuristic in _raise_prob.
GTOW_RAISE = {
    "AA": 1.0, "KK": 1.0, "AKs": 1.0, "AQs": 0.97, "K6s": 0.96,
    "KTs": 0.95, "AQs_": 0.0, "AJs": 0.87, "88": 0.85, "K9s": 0.84,
    "A6s": 0.82, "K7s": 0.80, "QQ": 0.79, "KJs": 0.78, "ATs": 0.71,
    "A8s": 0.70, "TT": 0.65, "99": 0.65, "K8o": 0.64, "A9s": 0.64,
    "JJ": 0.62, "AKo": 0.62, "A7s": 0.61, "KTo": 0.58, "66": 0.54,
    "KQs": 0.54, "44": 0.47, "AQo": 0.46, "K6o": 0.45, "K9o": 0.42,
    "A6o": 0.39, "KQo": 0.38, "77": 0.36, "A5s": 0.35, "A4o": 0.80,
    "A3o": 0.32, "A5o": 0.32, "22": 0.32, "A8o": 0.30, "A9o": 0.29,
    "K4o": 0.28, "K3o": 0.28, "ATo": 0.24, "K7o": 0.23, "33": 0.22,
    "KJo": 0.22, "K5o": 0.21, "K8s": 0.20, "AJo": 0.18, "A7o": 0.15,
    "55": 0.13, "A4s": 0.08, "A2o": 0.04, "A3s": 0.0, "A2s": 0.0,
    "K2o": 0.0,
}

FOLD_ALWAYS = {"72o", "82o", "62o", "42o", "32o"}
FOLD_MIX = {"83o", "92o", "52o", "93o", "T2o"}
# Extra trash BB folds vs an open (defend ~80%: fold worst ~20% offsuit).
BB_FOLD_EXTRA = {
    "94o", "95o", "T3o", "T4o", "T5o", "J2o", "J3o", "Q2o", "Q3o",
    "K2o", "84o", "85o", "73o", "74o", "63o", "53o", "43o",
}

# SB calling range vs BB jam at ~25bb (needs ~46% equity).
CALL_VS_JAM = {
    "AA", "KK", "QQ", "JJ", "TT", "99", "88", "77", "66", "55", "44",
    "AKs", "AQs", "AJs", "ATs", "A9s", "A8s", "A7s", "A6s", "A5s",
    "AKo", "AQo", "AJo", "ATo", "A9o", "A8o", "A7o",
    "KQs", "KJs", "KTs", "KQo",
    "QJs", "QTs", "JTs",
}

# BB 3-bet jam candidates vs min-raise (value + Ax blocker / suited bluffs).
BB_JAM_VALUE = {"TT", "JJ", "QQ", "KK", "AA", "AKs", "AKo", "AQs", "AQo"}
BB_JAM_BLUFF = {
    "A2o", "A3o", "A4o", "A5o", "A6o", "K3o", "K4o", "K5o",
    "A2s", "A3s", "A4s", "A5s", "54s", "65s", "76s", "86s", "T7s",
}
BB_RAISE_VALUE = {"AA", "KK", "QQ", "AKs", "AKo", "AQs"}


def _raise_prob(key):
    if key in GTOW_RAISE:
        return GTOW_RAISE[key]
    if len(key) == 2:  # pair not listed (should not happen)
        return 0.4
    suited = key.endswith("s")
    r1, r2 = RANKS.index(key[0]), RANKS.index(key[1])
    bw = r1 >= 8 and r2 >= 8  # T+ broadway
    if suited:
        if bw:
            return 0.55
        if key[0] == "A" or key[0] == "K":
            return 0.08
        # suited connectors / one-gappers
        if abs(r1 - r2) <= 3 and r1 >= 3:
            return 0.15
        return 0.03
    else:
        if bw:
            return 0.30
        if key[0] == "A":
            return 0.10
        if key[0] == "K":
            return 0.05
        return 0.0


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None
        self.rng = random.Random(7)
        self.opp = {}
        self._hand_log = []
        self._my_team = None

    def seed(self, hand, player_id):
        self.hand = list(hand)
        self.player_id = player_id
        self._hand_log = []

    def run(self, actions, game_state):
        try:
            self._hand_log.extend(actions)
            return self._decide(game_state)
        except Exception:
            try:
                if game_state.call_amount == 0:
                    return PlayerAction.call(self.player_id)
                return PlayerAction.fold(self.player_id)
            except Exception:
                return PlayerAction.fold(self.player_id)

    def end_game(self, actions, game_state):
        try:
            full = list(self._hand_log) + list(actions)
            self._learn(full, game_state)
        except Exception:
            pass
        self._hand_log = []

    # ---------- learning ----------
    def _opp_rec(self, team):
        rec = self.opp.get(team)
        if rec is None:
            rec = {"hands": 0, "vpip": 0, "pfr": 0, "agg": 0, "pass": 0,
                   "pre_fold": 0}
            self.opp[team] = rec
        return rec

    def _learn(self, full, game_state):
        try:
            teams = list(game_state.teams)
        except Exception:
            return
        me = self.player_id
        if me is None or me >= len(teams):
            return
        my_team = teams[me]
        self._my_team = my_team
        opps = [t for i, t in enumerate(teams) if i != me]
        if not opps:
            return
        # credit activity to seats seen acting; attribute team-level loosely:
        # if any opp voluntarily entered preflop, mark vpip for all opps
        # (heads-up this is exact; multiway approximate but fine).
        saw_vpip = {t: False for t in opps}
        saw_pfr = {t: False for t in opps}
        saw_agg = {t: 0 for t in opps}
        saw_pass = {t: 0 for t in opps}
        saw_pre_fold = {t: False for t in opps}
        n_board = 0
        for a in full:
            try:
                kind = a.kind
                pid = a.player_id
            except Exception:
                continue
            if pid == DEALER:
                if kind == "reveal_card":
                    n_board += 1
                continue
            street = "preflop" if n_board < 3 else "postflop"
            if pid < 0 or pid >= len(teams):
                continue
            t = teams[pid]
            if t == my_team:
                if kind in ("raise", "call") and street == "preflop":
                    pass
                continue
            if kind == "raise":
                saw_vpip[t] = True
                saw_pfr[t] = True
                if street != "preflop":
                    saw_agg[t] += 1
            elif kind == "call":
                saw_vpip[t] = True
                if street != "preflop":
                    saw_pass[t] += 1
            elif kind == "fold":
                if street != "preflop":
                    saw_pass[t] += 1
                else:
                    saw_pre_fold[t] = True
        for t in opps:
            rec = self._opp_rec(t)
            rec["hands"] += 1
            if saw_vpip[t]:
                rec["vpip"] += 1
            if saw_pfr[t]:
                rec["pfr"] += 1
            rec["agg"] += saw_agg[t]
            rec["pass"] += saw_pass[t]
            if saw_pre_fold[t]:
                rec["pre_fold"] += 1

    def _opp_rates(self, game_state):
        try:
            teams = list(game_state.teams)
        except Exception:
            return None
        me = self.player_id
        if me is None or me >= len(teams):
            return None
        names = [t for i, t in enumerate(teams) if i != me]
        if not names:
            return None
        # heads-up: single opp; multiway: average
        vpip = pfr = agg = hands = 0
        n = 0
        for t in names:
            rec = self.opp.get(t)
            if not rec or rec["hands"] < 4:
                continue
            n += 1
            hands += rec["hands"]
            vpip += rec["vpip"] / max(1, rec["hands"])
            pfr += rec["pfr"] / max(1, rec["hands"])
            denom = max(1, rec["agg"] + rec["pass"])
            agg += rec["agg"] / denom
        if n == 0:
            return None
        pre_fold = sum((self.opp[t]["pre_fold"] / max(1, self.opp[t]["hands"]))
                       for t in names if self.opp.get(t) and self.opp[t]["hands"] >= 4) / n
        return {"vpip": vpip / n, "pfr": pfr / n, "agg": agg / n,
                "hands": hands, "pre_fold": pre_fold}

    # ---------- decision ----------
    def _decide(self, gs):
        me = self.player_id
        hole = self.hand
        board = list(gs.board)
        street = gs.street
        pot = gs.pot
        to_call = gs.call_amount
        stacks = list(gs.stacks)
        my_stack = stacks[me] if me < len(stacks) else 0
        opp_stacks = [s for i, s in enumerate(stacks) if i != me]
        eff = min(my_stack, max(opp_stacks)) if opp_stacks else my_stack
        bb = gs.big_blind
        n_in = len(gs.players_in_hand)
        n_opp = max(1, n_in - 1)

        key = _hand_key(hole)
        hole_ints = [_card_int(hole[0]), _card_int(hole[1])]
        board_ints = [_card_int(c) for c in board]
        rates = self._opp_rates(gs)
        # did an opponent already raise this street? (their raises are
        # value-heavy in this field: tight only bets 2pair+, equity only
        # raises with >75% equity)
        opp_raised = False
        try:
            n_board = 0
            for a in self._hand_log:
                if a.player_id == DEALER:
                    if a.kind == "reveal_card":
                        n_board += 1
                    continue
                if n_board >= 3:
                    if a.player_id != me and a.kind == "raise":
                        opp_raised = True
        except Exception:
            opp_raised = False
        maniac = rates is not None and rates.get("hands", 0) >= 4 and rates.get("agg", 0) > 0.55
        faced_nuts = opp_raised and not maniac and street != "preflop"

        # all-in facing check: if we cannot raise and must call/fold
        if street == "preflop":
            return self._preflop(gs, key, hole_ints, board_ints, eff, bb,
                                 pot, to_call, my_stack, rates, n_opp)
        return self._postflop(gs, key, hole_ints, board_ints, eff, bb,
                              pot, to_call, my_stack, rates, n_opp,
                              faced_nuts)

    def _legal_raise(self, gs, desired):
        me = self.player_id
        if not gs.can_raise:
            return None
        low, high = gs.raise_range
        total = int(desired)
        if total < low:
            total = low
        if total > high:
            total = high
        return PlayerAction.raise_to(me, total)

    def _call_or_check(self):
        return PlayerAction.call(self.player_id)

    def _preflop(self, gs, key, hole_ints, board_ints, eff, bb, pot,
                 to_call, my_stack, rates, n_opp):
        me = self.player_id
        button = gs.button
        n = gs.num_players
        # position: heads-up button == SB
        if n == 2:
            is_sb = (me == button)
        else:
            # multiway: treat button/CO-ish as late; simplify: first-in raiser
            is_sb = (me == button)
        opp_jam = to_call >= eff * 0.7 and to_call > 0 and eff > 0
        # ---- facing all-in / near all-in ----
        if opp_jam or (to_call >= my_stack and to_call > 0):
            if key in CALL_VS_JAM:
                return self._call_or_check()
            # small pairs below 44, weak Ax/Kx: fold; marginal suited mix
            if key in ("33", "22", "A4s", "A3s", "A2s", "K9s", "QJs"):
                if self.rng.random() < 0.35:
                    return self._call_or_check()
                return PlayerAction.fold(me)
            return PlayerAction.fold(me)
        # ---- short stack push/fold (<=10bb) ----
        if eff <= 10 * bb and eff > 0:
            push = self._nash_push(key)
            if to_call == 0:
                if push and self.rng.random() < 0.9:
                    r = self._legal_raise(gs, gs.max_raise_to)
                    return r if r is not None else self._call_or_check()
                # open-shove strong, else min-raise/limp mix for cats
                if key in CALL_VS_JAM and self.rng.random() < 0.5:
                    r = self._legal_raise(gs, gs.max_raise_to)
                    return r if r is not None else self._call_or_check()
                return self._call_or_check()
            # facing raise short: jam or fold
            if key in CALL_VS_JAM or (push and self.rng.random() < 0.6):
                if gs.can_raise:
                    r = self._legal_raise(gs, gs.max_raise_to)
                    return r if r is not None else self._call_or_check()
                return self._call_or_check()
            # pot odds fallback short
            if to_call <= 0:
                return self._call_or_check()
            if to_call / max(1, pot + to_call) < 0.30:
                return self._call_or_check()
            return PlayerAction.fold(me)
        # ---- multiway: tighten up ----
        if n_opp >= 2:
            return self._preflop_multiway(gs, key, to_call, pot, rates)
        # ---- heads-up ----
        current_bet = gs.current_bet
        my_bet = gs.bets[me] if me < len(gs.bets) else 0
        # first-in: SB acting with only blinds posted
        if is_sb and to_call <= bb and current_bet <= bb:
            # exploit: vs frequent 3-bettor, tighten raises
            raise_p = _raise_prob(key)
            overfolder = (rates and rates.get("hands", 0) >= 4
                          and rates.get("pre_fold", 0) > 0.35)
            if rates and rates.get("hands", 0) >= 4:
                if rates.get("pfr", 0) > 0.20:
                    raise_p *= 0.7
                if overfolder:
                    # vs overfolder: steal relentlessly
                    raise_p = min(0.85, raise_p + 0.40)
                elif rates.get("vpip", 0.5) > 0.65:
                    # vs station: raise value heavier
                    if key in CALL_VS_JAM:
                        raise_p = min(1.0, raise_p + 0.15)
            if key in FOLD_ALWAYS:
                if overfolder and self.rng.random() < 0.5:
                    pass  # steal even trash sometimes vs overfolder
                else:
                    return PlayerAction.fold(me)
            if key in FOLD_MIX and self.rng.random() < 0.5:
                if not overfolder:
                    return PlayerAction.fold(me)
            roll = self.rng.random()
            if roll < raise_p:
                r = self._legal_raise(gs, min(gs.max_raise_to, 2 * bb))
                return r if r is not None else self._call_or_check()
            # limp (call)
            return self._call_or_check()
        # BB facing limp (free option, current bet == bb, nothing to call)
        if to_call == 0:
            # iso with ~30%: premiums + some Ax/Kx/suited
            iso = key in BB_RAISE_VALUE or key in (
                "TT", "99", "AQs", "AQo", "AJs", "AJo", "KQs", "KQo")
            if key in ("AA", "KK", "QQ", "AKs", "AKo"):
                iso = self.rng.random() < 0.85
            elif iso:
                iso = self.rng.random() < 0.55
            else:
                # bluff iso ~12% with blockers
                iso = key in BB_JAM_BLUFF and self.rng.random() < 0.18
            # exploit: vs limper who folds a lot, iso wider
            if (rates and rates.get("hands", 0) >= 4
                    and rates.get("pre_fold", 0) > 0.35):
                if key in CALL_VS_JAM and self.rng.random() < 0.3:
                    iso = True
            if iso and gs.can_raise:
                # 3bb iso
                r = self._legal_raise(gs, min(gs.max_raise_to, 3 * bb))
                return r if r is not None else self._call_or_check()
            return self._call_or_check()
        # facing raise (SB open or 3-bet)
        # fold trash OOP: defend ~80% baseline
        if key in BB_FOLD_EXTRA and self.rng.random() < 0.85:
            return PlayerAction.fold(me)
        # detect 3-bet jam vs small raise via size
        raise_size = to_call
        # tiny raise relative to stack: standard open
        if raise_size <= 3 * bb + 5:
            return self._defend_vs_open(gs, key, to_call, pot, my_stack,
                                        eff, bb, rates)
        # bigger: treat as 3-bet; call tighter, 4-bet premiums
        if key in ("QQ", "KK", "AA", "AKs", "AKo"):
            if gs.can_raise and self.rng.random() < 0.55:
                # 4-bet small or jam if shallow
                if eff <= 20 * bb:
                    r = self._legal_raise(gs, gs.max_raise_to)
                    return r if r is not None else self._call_or_check()
                r = self._legal_raise(gs, min(gs.max_raise_to,
                                             gs.current_bet + 4 * bb))
                return r if r is not None else self._call_or_check()
            return self._call_or_check()
        if key in CALL_VS_JAM and self.rng.random() < 0.35:
            # flat some medium strength vs 3-bet if pot odds ok
            if to_call / max(1, pot + to_call) < 0.35:
                return self._call_or_check()
        return PlayerAction.fold(me)

    def _defend_vs_open(self, gs, key, to_call, pot, my_stack, eff, bb,
                        rates):
        me = self.player_id
        # folds: worst offsuit junk
        if key in FOLD_ALWAYS or key in FOLD_MIX:
            # BB getting ~3:1 vs min-raise; still fold worst
            if key in FOLD_ALWAYS:
                return PlayerAction.fold(me)
            if self.rng.random() < 0.6:
                return PlayerAction.fold(me)
        # 3-bet: value + bluffs
        if key in BB_JAM_VALUE:
            if self.rng.random() < 0.70:
                if eff <= 22 * bb:
                    r = self._legal_raise(gs, gs.max_raise_to)
                    return r if r is not None else self._call_or_check()
                r = self._legal_raise(gs, min(gs.max_raise_to, 5 * bb + 10))
                return r if r is not None else self._call_or_check()
            return self._call_or_check()
        if key in BB_JAM_BLUFF:
            freq = 0.30
            if rates and rates.get("pfr", 0) > 0.5:
                freq = 0.18  # less vs wide opener? actually jam more; keep low
            if self.rng.random() < freq:
                if eff <= 22 * bb:
                    r = self._legal_raise(gs, gs.max_raise_to)
                    return r if r is not None else self._call_or_check()
                r = self._legal_raise(gs, min(gs.max_raise_to, 5 * bb + 10))
                return r if r is not None else self._call_or_check()
        if key in BB_RAISE_VALUE and self.rng.random() < 0.5:
            r = self._legal_raise(gs, min(gs.max_raise_to, 5 * bb + 10))
            return r if r is not None else self._call_or_check()
        # exploit vs 100% opener: jam wider handled by bluff set; call wide
        odds = to_call / max(1, pot + to_call)
        tight_opener = rates is not None and rates.get("hands", 0) >= 4 and rates.get("pfr", 0.2) < 0.13
        if tight_opener:
            # vs tight range: only continue with real hands
            if key in CALL_VS_JAM or key in BB_JAM_VALUE:
                return self._call_or_check()
            if _raise_prob(key) > 0.5 and odds <= 0.25:
                return self._call_or_check()
            return PlayerAction.fold(me)
        if odds <= 0.32:
            return self._call_or_check()
        if key in CALL_VS_JAM or _raise_prob(key) > 0.25:
            return self._call_or_check()
        # marginal suited / connectors call if cheap
        if odds < 0.28:
            return self._call_or_check()
        return PlayerAction.fold(me)

    def _preflop_multiway(self, gs, key, to_call, pot, rates):
        me = self.player_id
        premium = key in ("AA", "KK", "QQ", "JJ", "TT", "AKs", "AKo",
                          "AQs", "AQo", "AJs", "KQs")
        good = premium or key in ("99", "88", "AJo", "ATs", "KJs", "QJs",
                                  "JTs", "AJo")
        if to_call == 0:
            if premium:
                r = self._legal_raise(gs, min(gs.max_raise_to, gs.pot + 20))
                return r if r is not None else self._call_or_check()
            if good and self.rng.random() < 0.5:
                r = self._legal_raise(gs, min(gs.max_raise_to, gs.pot + 20))
                return r if r is not None else self._call_or_check()
            return self._call_or_check()
        odds = to_call / max(1, pot + to_call)
        if premium:
            if gs.can_raise and self.rng.random() < 0.5:
                return self._legal_raise(gs, min(gs.max_raise_to,
                                                 gs.current_bet * 3)) or self._call_or_check()
            return self._call_or_check()
        if good and odds < 0.25:
            return self._call_or_check()
        return PlayerAction.fold(me)

    def _nash_push(self, key):
        # ~10-12bb push: pairs, Ax, K2s+, K6o+, Q4s+, Q9o+, J6s+, J9o+,
        # T6s+, T8o+, 96s+, 98o+, 85s+, 75s+, 64s+, 54s (approx 55%)
        if len(key) == 2:
            return True
        suited = key.endswith("s")
        r1, r2 = RANKS.index(key[0]), RANKS.index(key[1])
        if key[0] == "A":
            return True
        if suited:
            if key[0] == "K" and r2 >= 0:
                return r2 >= 0  # K2s+
            if key[0] == "Q" and r2 >= 2:
                return True  # Q4s+
            if key[0] == "J" and r2 >= 4:
                return True
            if key[0] == "T" and r2 >= 4:
                return True
            if abs(r1 - r2) <= 4 and r1 >= 3:
                return True
            return False
        # offsuit
        if key[0] == "K":
            return r2 >= 4  # K6o+
        if key[0] == "Q":
            return r2 >= 7  # Q9o+
        if key[0] == "J":
            return r2 >= 7
        if key[0] == "T":
            return r2 >= 6
        if r1 >= 7 and r2 >= 7:
            return True  # 98o+
        return False

    # ---------- postflop ----------
    def _postflop(self, gs, key, hole_ints, board_ints, eff, bb, pot,
                  to_call, my_stack, rates, n_opp, faced_nuts=False):
        me = self.player_id
        missing = 5 - len(board_ints)
        if n_opp >= 2:
            sims = 160
        elif len(board_ints) == 3:
            sims = 700
        elif len(board_ints) == 4:
            sims = 500
        else:
            sims = 0  # river: exact-ish via enumeration w/ sampling cap
        t0 = time.perf_counter()
        eq = self._equity(hole_ints, board_ints, n_opp, sims, t0)
        odds = to_call / (pot + to_call) if to_call else 0.0
        # commitment: shallow stacks
        if to_call > 0 and eff > 0 and to_call >= 0.4 * eff and eq > 0.45:
            if gs.can_raise and eq > 0.55:
                r = self._legal_raise(gs, gs.max_raise_to)
                return r if r is not None else self._call_or_check()
            return self._call_or_check()
        if to_call == 0:
            return self._aggress(gs, eq, pot, rates, board_ints)
        # facing bet (equity is vs RANDOM hand: overestimates vs tight
        # value ranges, so demand a bigger margin by default and vs tights)
        margin = 0.05
        if faced_nuts:
            # opp already raised this street and is not a maniac: nutted.
            # equity-vs-random overstates badly here.
            if eq < 0.60 and to_call / max(1, pot + to_call) > 0.15:
                return PlayerAction.fold(self.player_id)
            margin = 0.15
        if rates is not None and rates.get("hands", 0) >= 4:
            if rates.get("vpip", 0.5) < 0.45:
                margin = 0.09  # vs tight: their range is nutted
            elif rates.get("vpip", 0.5) > 0.60:
                margin = 0.03  # vs station: range is wide, equity honest
            elif rates.get("agg", 0.5) > 0.6:
                margin = 0.03  # vs maniac: call down wider
        if eq > odds + 0.16 and gs.can_raise:
            # value raise to ~2.5-3x their bet (raise_to is street total)
            size = int(max(gs.current_bet * 2.5, pot * 0.7))
            low, high = gs.raise_range
            size = min(high, max(low, size))
            # exploit: vs calling station, raise thinner with eq>0.58
            if rates and rates.get("vpip", 0) > 0.6 and eq > 0.58:
                return self._legal_raise(gs, size) or self._call_or_check()
            if eq > 0.62 or self.rng.random() < 0.7:
                return self._legal_raise(gs, size) or self._call_or_check()
            return self._call_or_check()
        if eq >= odds + margin:
            return self._call_or_check()
        # semi-bluff raise only with evidence opp folds
        if gs.can_raise and 0.40 <= eq < odds + margin:
            if rates is not None and rates.get("hands", 0) >= 6 and rates.get("vpip", 0.5) < 0.45:
                if self.rng.random() < 0.12:
                    size = int(max(gs.current_bet * 2.5, pot * 0.6))
                    low, high = gs.raise_range
                    size = min(high, max(low, size))
                    return self._legal_raise(gs, size) or self._call_or_check()
        return PlayerAction.fold(me)

    def _aggress(self, gs, eq, pot, rates, board_ints):
        # check/raise node (to_call == 0)
        if not gs.can_raise:
            return self._call_or_check()
        low, high = gs.raise_range
        # exploit: vs folder c-bet relentlessly; vs station only value
        station = rates is not None and rates.get("vpip", 0.5) > 0.62
        if eq > 0.62:
            size = int(max(low, min(high, pot * 0.5)))
            if self.rng.random() < 0.85:
                return self._legal_raise(gs, size) or self._call_or_check()
            return self._call_or_check()
        if 0.50 < eq <= 0.62:
            freq = 0.55 if not station else 0.35
            if self.rng.random() < freq:
                size = int(max(low, min(high, pot * 0.4)))
                return self._legal_raise(gs, size) or self._call_or_check()
            return self._call_or_check()
        # weak: tiny stab only with evidence opp overfolds, else check
        if rates is not None and rates.get("hands", 0) >= 6 and rates.get("vpip", 0.5) < 0.42:
            if self.rng.random() < 0.08:
                size = int(max(low, min(high, pot * 0.33 if pot else low)))
                return self._legal_raise(gs, size) or self._call_or_check()
        return self._call_or_check()

    def _equity(self, hole_ints, board_ints, n_opp, sims, t0):
        if sims <= 0:
            # river: enumerate up to 300 opp combos (fast exact-ish)
            return self._river_equity(hole_ints, board_ints, n_opp, t0)
        deck = [r * 4 + s for r in range(2, 15) for s in range(4)]
        known = set(hole_ints + board_ints)
        deck = [c for c in deck if c not in known]
        missing = 5 - len(board_ints)
        need = missing + 2 * n_opp
        won = 0.0
        done = 0
        batch = 64
        rng = self.rng
        mine_base = hole_ints + board_ints
        while done < sims:
            n = min(batch, sims - done)
            for _ in range(n):
                if time.perf_counter() - t0 > 6.0:
                    return won / max(1, done)
                sample = rng.sample(deck, need)
                runout = board_ints + sample[:missing]
                mine = _best_rank(hole_ints + runout)
                best_opp = None
                tie = 0
                for i in range(n_opp):
                    oh = sample[missing + 2 * i:missing + 2 * i + 2]
                    r = _best_rank(oh + runout)
                    if best_opp is None or r > best_opp:
                        best_opp = r
                        tie = 0
                    elif r == best_opp:
                        tie += 1
                if mine > best_opp:
                    won += 1.0
                elif mine == best_opp:
                    won += 1.0 / (2 + tie)
            done += n
        return won / max(1, done)

    def _river_equity(self, hole_ints, board_ints, n_opp, t0):
        if n_opp != 1:
            return self._equity(hole_ints, board_ints, n_opp, 150, t0)
        deck = [r * 4 + s for r in range(2, 15) for s in range(4)]
        known = set(hole_ints + board_ints)
        deck = [c for c in deck if c not in known]
        mine = _best_rank(hole_ints + board_ints)
        won = 0.0
        total = 0
        # stride-sample to cap at ~400 combos for speed
        combos = list(itertools.combinations(deck, 2))
        step = max(1, len(combos) // 400)
        for i in range(0, len(combos), step):
            if time.perf_counter() - t0 > 6.0:
                break
            r = _best_rank(list(combos[i]) + board_ints)
            total += 1
            if mine > r:
                won += 1.0
            elif mine == r:
                won += 0.5
        return won / max(1, total)
