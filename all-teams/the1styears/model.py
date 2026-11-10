"""
Heads-up no-limit hold'em bot.

Each turn:
1. Replay the hand's actions (what the opponent faced at each action).
2. Build the opponent's range over all 1326 combos; every opponent action
   re-weights it using THAT opponent's observed frequencies.
3. Equity vs that range (Monte Carlo, exact on the river), remembering how
   strong each sampled opponent hand was, so equity vs "the hands that
   would call" is available too.
4. Expected value of fold / check-call / several raise sizes, with fold and
   re-raise probabilities from the opponent's observed frequencies.

Everything learned is kept per opponent team name, in plain dicts.
"""

import random
import time

from player_action import DEALER, PlayerAction

_SUITS = "CDHS"
_STREET = {0: 0, 3: 1, 4: 2, 5: 3}
_MC_SAMPLES = 1500
_PRIOR_N = 4.0

# context: (fold, call, raise, big raise) prior frequencies
_PRIORS = {
    "open": (0.20, 0.25, 0.55, 0.06),  # SB's first action preflop
    "iso": (0.00, 0.70, 0.30, 0.05),   # BB after SB limps (call = check)
    "p1": (0.30, 0.56, 0.14, 0.06),    # preflop facing one raise
    "p2": (0.40, 0.48, 0.12, 0.08),    # preflop facing a re-raise
    "ps": (0.55, 0.45, 0.00, 0.00),    # preflop facing a shove-sized bet
    "fs": (0.30, 0.60, 0.10, 0.02),    # postflop facing a small bet
    "fm": (0.40, 0.52, 0.08, 0.02),    # postflop facing a medium bet
    "fb": (0.50, 0.46, 0.04, 0.02),    # postflop facing a big bet / shove
    "pb": (0.00, 0.60, 0.40, 0.04),    # postflop, nothing to call: bet?
}
_KIND_IDX = {"fold": 1, "call": 2, "raise": 3}


# -- cards and a fast evaluator ------------------------------------------

def _card_int(card):
    return (card.value - 2) * 4 + _SUITS.index(card.suit)


def _straight_top(m):
    for top in range(12, 3, -1):
        need = 0x1F << (top - 4)
        if (m & need) == need:
            return top
    if (m & 0x100F) == 0x100F:  # A-2-3-4-5
        return 3
    return -1


_POP = [bin(m).count("1") for m in range(8192)]
_STRAIGHT = [_straight_top(m) for m in range(8192)]


def _straight_outs(m):
    if _STRAIGHT[m] >= 0:
        return 0
    n = 0
    for r in range(13):
        if not (m >> r) & 1 and _STRAIGHT[m | (1 << r)] >= 0:
            n += 1
    return n


_DRAWOUTS = [_straight_outs(m) for m in range(8192)]
_COMBOS = [(a, b) for a in range(52) for b in range(a + 1, 52)]
_NC = len(_COMBOS)


def _ev(cards):
    """Rank of the best 5-card hand among 5-7 card ints; higher wins."""
    cnt = [0] * 13
    sm = [0, 0, 0, 0]
    for c in cards:
        r = c >> 2
        cnt[r] += 1
        sm[c & 3] |= 1 << r
    for m in sm:
        if _POP[m] >= 5:
            t = _STRAIGHT[m]
            if t >= 0:
                return 0x800000 | (t << 16)
            v = 0x500000
            sh = 16
            for r in range(12, -1, -1):
                if (m >> r) & 1:
                    v |= r << sh
                    sh -= 4
                    if sh < 0:
                        break
            return v
    mask = 0
    quad = -1
    trips = []
    pairs = []
    singles = []
    for r in range(12, -1, -1):
        n = cnt[r]
        if n:
            mask |= 1 << r
            if n == 1:
                singles.append(r)
            elif n == 2:
                pairs.append(r)
            elif n == 3:
                trips.append(r)
            else:
                quad = r
    if quad >= 0:
        for r in range(12, -1, -1):
            if r != quad and cnt[r]:
                return 0x700000 | (quad << 16) | (r << 12)
        return 0x700000 | (quad << 16)
    if trips and (len(trips) > 1 or pairs):
        p = trips[1] if len(trips) > 1 else -1
        if pairs and pairs[0] > p:
            p = pairs[0]
        return 0x600000 | (trips[0] << 16) | (p << 12)
    t = _STRAIGHT[mask]
    if t >= 0:
        return 0x400000 | (t << 16)
    if trips:
        v = 0x300000 | (trips[0] << 16)
        sh = 12
        for r in singles[:2]:
            v |= r << sh
            sh -= 4
        return v
    if len(pairs) > 1:
        k = pairs[2] if len(pairs) > 2 else -1
        if singles and singles[0] > k:
            k = singles[0]
        if k < 0:
            k = 0
        return 0x200000 | (pairs[0] << 16) | (pairs[1] << 12) | (k << 8)
    if pairs:
        v = 0x100000 | (pairs[0] << 16)
        sh = 12
        for r in singles[:3]:
            v |= r << sh
            sh -= 4
        return v
    v = 0
    sh = 16
    for r in singles[:5]:
        v |= r << sh
        sh -= 4
    return v


def _draw_info(hole, board):
    """(flush draw using a hole card, straight-completing ranks)."""
    cards = hole + board
    mask = 0
    for c in cards:
        mask |= 1 << (c >> 2)
    fd = False
    for s in {hole[0] & 3, hole[1] & 3}:
        n = 0
        for c in cards:
            if (c & 3) == s:
                n += 1
        if n == 4:
            fd = True
    return fd, _DRAWOUTS[mask]


def _post_strength(a, b, board):
    """Postflop ordering of a combo: made hand, draws bumped up."""
    v = _ev([a, b] + board)
    if len(board) < 5 and v < 0x200000:
        fd, outs = _draw_info([a, b], board)
        if fd and outs >= 2:
            v = max(v, 0x1C0000)  # like a pair of aces
        elif fd or outs >= 2:
            v = max(v, 0x160000)  # like a pair of eights
    return v


def _is_weak(hole, board):
    """A shown hand that was betting without a pair using a hole card and
    without a real draw: counted as a bluff."""
    v = _ev(hole + board)
    cat = v >> 20
    if cat >= 2:
        return False
    if cat == 1 and ((v >> 16) & 15) in (hole[0] >> 2, hole[1] >> 2):
        return False
    if len(board) < 5:
        fd, outs = _draw_info(hole, board)
        if fd or outs >= 2:
            return False
    return True


def _class_index(a, b):
    ra, rb = a >> 2, b >> 2
    hi, lo = max(ra, rb), min(ra, rb)
    if hi == lo:
        return hi * 13 + hi
    if (a & 3) == (b & 3):
        return hi * 13 + lo
    return lo * 13 + hi


def _preflop_table(trials):
    """Equity of each of the 169 starting hands vs a random hand."""
    rng = random.Random(20240601)
    table = [0.0] * 169
    for i in range(13):
        for j in range(13):
            if i == j:
                c1, c2 = i * 4, i * 4 + 1
            elif i > j:
                c1, c2 = i * 4, j * 4
            else:
                c1, c2 = j * 4, i * 4 + 1
            deck = [c for c in range(52) if c != c1 and c != c2]
            score = 0.0
            for _ in range(trials):
                s = rng.sample(deck, 7)
                mv = _ev([c1, c2] + s[2:])
                ov = _ev(s)
                if mv > ov:
                    score += 1.0
                elif mv == ov:
                    score += 0.5
            table[i * 13 + j] = score / trials
    return table


# -- reading the hand ------------------------------------------------------

def _ctx(street, c_amt, pot, raises, allin=False):
    """Which frequency bucket a decision facing (c_amt, pot) belongs to."""
    if street == 0:
        if raises == 0:
            return "open" if c_amt > 0 else "iso"
        ratio = c_amt / pot if pot > 0 else 0.0
        if allin or ratio >= 0.65:
            return "ps"
        return "p1" if raises == 1 else "p2"
    if c_amt <= 0:
        return "pb"
    ratio = c_amt / pot if pot > 0 else 0.0
    if allin or ratio >= 0.52:
        return "fb"
    if ratio >= 0.36:
        return "fm"
    return "fs"


def _replay(actions, button, sb, bb):
    """Heads-up: one event per betting action:
    (player, street, board size, kind, to call, pot, raises, big raise)."""
    bets = [0, 0]
    bets[button] = sb
    bets[1 - button] = bb
    contrib = list(bets)
    nboard = 0
    street = 0
    raises = 0
    events = []
    for a in actions:
        kind = a.kind
        p = a.player_id
        if kind == "reveal_card":
            if p == DEALER:
                nboard += 1
                if nboard >= 3:
                    street = nboard - 2
                    bets = [0, 0]
                    raises = 0
            continue
        if kind not in _KIND_IDX or p not in (0, 1):
            continue
        cur = max(bets)
        c_amt = cur - bets[p]
        pot = contrib[0] + contrib[1]
        big = False
        if kind == "raise":
            add = a.amount - bets[p]
            facing = a.amount - bets[1 - p]
            after = pot + add
            big = after > 0 and facing / after >= 0.65
        events.append((p, street, nboard, kind, c_amt, pot, raises, big))
        if kind == "call":
            bets[p] += c_amt
            contrib[p] += c_amt
        elif kind == "raise":
            contrib[p] += a.amount - bets[p]
            bets[p] = a.amount
            raises += 1
    return events, raises, street


def _pcts(order, strength, w):
    """Weighted percentile (0 weakest .. 1 strongest) of every combo."""
    tot = 0.0
    for i in order:
        tot += w[i]
    pct = [0.0] * _NC
    if tot <= 0.0:
        return pct
    acc = 0.0
    n = len(order)
    k = 0
    while k < n:
        sv = strength[order[k]]
        j = k
        gw = 0.0
        while j < n and strength[order[j]] == sv:
            gw += w[order[j]]
            j += 1
        mid = (acc + 0.5 * gw) / tot
        for t in range(k, j):
            pct[order[t]] = mid
        acc += gw
        k = j
    return pct


class _Eq:
    """Equity vs the whole range, or vs its strongest q fraction."""

    def __init__(self, samples):
        samples.sort(key=lambda s: -s[0])
        self.p = [s[0] for s in samples]
        cw = [0.0]
        cr = [0.0]
        for _, res, wt in samples:
            cw.append(cw[-1] + wt)
            cr.append(cr[-1] + wt * res)
        self.cw = cw
        self.cr = cr
        self.n = len(samples)

    def top(self, q):
        n = self.n
        if n == 0 or self.cw[n] <= 0:
            return 0.5
        if q >= 0.999:
            return self.cr[n] / self.cw[n]
        thr = 1.0 - q
        lo, hi = 0, n
        while lo < hi:
            mid = (lo + hi) // 2
            if self.p[mid] >= thr:
                lo = mid + 1
            else:
                hi = mid
        m = max(lo, min(n, max(12, n // 25)))
        if self.cw[m] <= 0:
            return self.cr[n] / self.cw[n]
        return self.cr[m] / self.cw[m]


# -- the model ---------------------------------------------------------------

class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None
        self.cards = []
        self.hand_actions = []
        self.cache = {}
        self.opp_stats = {}
        self.rng = None
        self.pf_class = _preflop_table(1000)
        self.pf_combo = [self.pf_class[_class_index(a, b)]
                         for a, b in _COMBOS]
        pf = self.pf_combo
        self.pf_order = sorted(range(_NC), key=lambda i: pf[i])

    def seed(self, hand, player_id):
        self.hand = hand
        self.player_id = player_id
        self.cards = [_card_int(c) for c in hand]
        self.hand_actions = []
        self.cache = {}
        if self.rng is None:
            self.rng = random.Random(987654321)

    def run(self, actions, game_state):
        gs = game_state
        me = self.player_id
        try:
            self.hand_actions.extend(actions)
        except Exception:
            pass
        try:
            if gs.num_players == 2:
                act = self._decide(gs)
            else:
                act = self._simple(gs)
            gs.validate_action(act)
            return act
        except Exception:
            pass
        try:
            act = self._simple(gs)
            gs.validate_action(act)
            return act
        except Exception:
            pass
        if gs.call_amount == 0:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)

    def end_game(self, actions, game_state):
        gs = game_state
        try:
            self.hand_actions.extend(actions)
            if gs.num_players != 2:
                return
            opp = 1 - self.player_id
            st = self.opp_stats.setdefault(gs.teams[opp], {})
            events, _, _ = _replay(self.hand_actions, gs.button,
                                   gs.small_blind, gs.big_blind)
            for p, street, nb, kind, c_amt, pot, raises, big in events:
                if p != opp:
                    continue
                ctx = _ctx(street, c_amt, pot, raises)
                rec = st.get(ctx)
                if rec is None:
                    rec = [0, 0, 0, 0, 0]
                    st[ctx] = rec
                rec[0] += 1
                if kind == "fold" and c_amt <= 0:
                    kind = "call"
                rec[_KIND_IDX[kind]] += 1
                if kind == "raise" and big:
                    rec[4] += 1
            shown = [_card_int(a.card) for a in self.hand_actions
                     if a.kind == "reveal_card" and a.player_id == opp
                     and a.card is not None]
            if len(shown) == 2:
                board = [_card_int(c) for c in gs.board]
                bl = st.get("bl")
                if bl is None:
                    bl = [0, 0]
                    st["bl"] = bl
                for ev in events:
                    if ev[0] == opp and ev[3] == "raise" and ev[1] > 0:
                        bl[0] += 1
                        if _is_weak(shown, board[:ev[2]]):
                            bl[1] += 1
        except Exception:
            pass

    # -- opponent statistics -------------------------------------------------

    def _freqs(self, st, ctx):
        pf, _, pr, pb = _PRIORS[ctx]
        rec = st.get(ctx)
        n, fo, ra, rb = 0, 0, 0, 0
        if rec is not None:
            n, fo, ra, rb = rec[0], rec[1], rec[3], rec[4]
        k = _PRIOR_N
        return ((fo + pf * k) / (n + k), (ra + pr * k) / (n + k),
                (rb + pb * k) / (n + k))

    def _bluff(self, st):
        bl = st.get("bl")
        n, b = (bl[0], bl[1]) if bl is not None else (0, 0)
        return min(0.7, max(0.05, (b + 0.15 * _PRIOR_N) / (n + _PRIOR_N)))

    # -- ranges and equity --------------------------------------------------

    def _order(self, board):
        nb = len(board)
        if nb == 0:
            return self.pf_order, self.pf_combo
        hit = self.cache.get(nb)
        if hit is not None:
            return hit
        bs = set(board)
        s = [-1] * _NC
        for i, (a, b) in enumerate(_COMBOS):
            if a not in bs and b not in bs:
                s[i] = _post_strength(a, b, board)
        order = sorted(range(_NC), key=lambda i: s[i])
        self.cache[nb] = (order, s)
        return order, s

    def _range(self, events, opp, board, mine, st):
        dead = set(mine)
        dead.update(board)
        w = [0.0 if (a in dead or b in dead) else 1.0 for a, b in _COMBOS]
        beta = self._bluff(st)
        for p, street, nb, kind, c_amt, pot, raises, big in events:
            if p != opp or kind == "fold":
                continue
            ctx = _ctx(street, c_amt, pot, raises)
            f, r, rb = self._freqs(st, ctx)
            f = min(max(f, 0.0), 0.95)
            order, strength = self._order(board[:nb])
            pct = _pcts(order, strength, w)
            if kind == "raise":
                cut = 1.0 - max(rb if big else r, 0.03)
                low = beta if ctx in ("open", "iso", "pb") else beta * 0.7
                for i in range(_NC):
                    if w[i] > 0.0 and pct[i] < cut:
                        w[i] *= low
            elif ctx in ("iso", "pb"):
                cut = 1.0 - max(r, 0.03)
                for i in range(_NC):
                    if w[i] > 0.0 and pct[i] >= cut:
                        w[i] *= 0.35
            elif ctx == "open":
                cut = 1.0 - max(r, 0.03)
                for i in range(_NC):
                    if w[i] > 0.0:
                        if pct[i] >= cut:
                            w[i] *= 0.4
                        elif pct[i] < f:
                            w[i] *= 0.15
            else:
                cut = 1.0 - max(r, 0.02)
                for i in range(_NC):
                    if w[i] > 0.0:
                        if pct[i] < f:
                            w[i] *= 0.12
                        elif pct[i] > cut:
                            w[i] *= 0.6
        return w

    def _equity(self, mine, board, live, deadline):
        if not live:
            return []
        rng = self.rng if self.rng is not None else random.Random(1)
        if len(board) == 5:
            mv = _ev(mine + board)
            out = []
            for a, b, wt, p in live:
                ov = _ev([a, b] + board)
                out.append((p, 1.0 if mv > ov else
                            (0.5 if mv == ov else 0.0), wt))
            return out
        cum = []
        t = 0.0
        for x in live:
            t += x[2]
            cum.append(t)
        dead = set(mine)
        dead.update(board)
        deck = [c for c in range(52) if c not in dead]
        nd = len(deck)
        k = 5 - len(board)
        last = len(live) - 1
        rnd = rng.random
        mb = mine + board
        out = []
        for it in range(_MC_SAMPLES):
            if it and (it & 63) == 0 and time.perf_counter() > deadline:
                break
            x = rnd() * t
            lo, hi = 0, last
            while lo < hi:
                mid = (lo + hi) // 2
                if cum[mid] < x:
                    lo = mid + 1
                else:
                    hi = mid
            a, b, _, p = live[lo]
            drawn = []
            while len(drawn) < k:
                c = deck[int(rnd() * nd)]
                if c != a and c != b and c not in drawn:
                    drawn.append(c)
            mv = _ev(mb + drawn)
            ov = _ev([a, b] + board + drawn)
            out.append((p, 1.0 if mv > ov else
                        (0.5 if mv == ov else 0.0), 1.0))
        return out

    # -- decisions -------------------------------------------------------------

    def _targets(self, gs, street, raises_now, pot, c_amt, b_me, b_op,
                 lo, hi):
        bb = gs.big_blind
        cur = gs.current_bet
        raw = []
        if street == 0:
            if raises_now == 0:
                raw.append(int(2.5 * bb) if c_amt > 0 else int(3.5 * bb))
            elif raises_now == 1:
                raw.append(int(3 * cur))
            else:
                raw.append(int(2.2 * cur))
        elif c_amt == 0:
            for frac in (0.33, 0.66, 1.0):
                raw.append(b_me + int(pot * frac))
        else:
            raw.append(int(b_op + 0.8 * (pot + c_amt)))
        raw.append(hi)
        out = []
        for t in raw:
            t = int(max(lo, min(hi, t)))
            if hi - t < 0.4 * (pot + (t - b_me) + (t - b_op)):
                t = hi  # would commit most of the stack: just shove
            if t not in out:
                out.append(t)
        return out

    def _decide(self, gs):
        t0 = time.perf_counter()
        me = self.player_id
        opp = 1 - me
        button = gs.button
        events, raises_now, _ = _replay(self.hand_actions, button,
                                        gs.small_blind, gs.big_blind)
        board = [_card_int(c) for c in gs.board]
        mine = self.cards
        st = self.opp_stats.get(gs.teams[opp], {})

        w = self._range(events, opp, board, mine, st)
        order, strength = self._order(board)
        pct = _pcts(order, strength, w)
        live = [(_COMBOS[i][0], _COMBOS[i][1], w[i], pct[i])
                for i in range(_NC) if w[i] > 1e-9]
        if not live:
            dead = set(mine)
            dead.update(board)
            w = [0.0 if (a in dead or b in dead) else 1.0
                 for a, b in _COMBOS]
            pct = _pcts(order, strength, w)
            live = [(_COMBOS[i][0], _COMBOS[i][1], w[i], pct[i])
                    for i in range(_NC) if w[i] > 0.0]
        eq = _Eq(self._equity(mine, board, live, t0 + 2.0))

        pot = float(gs.pot)
        c_amt = gs.call_amount
        b_me = gs.bets[me]
        b_op = gs.bets[opp]
        prev = (pot - b_me - b_op) / 2.0
        my_in = prev + b_me
        opp_in = prev + b_op
        my_total = my_in + gs.stacks[me]
        opp_total = opp_in + gs.stacks[opp]
        cap = min(my_total, opp_total)
        nb = len(board)
        street = _STREET.get(nb, 1)
        pen = 0.0 if nb == 5 else (0.03 if me == button else 0.06)

        def real(e, allin):
            # Equity that is not all-in is not fully realised.
            return e if allin else e - pen * 4.0 * e * (1.0 - e)

        eq_all = eq.top(1.0)
        if c_amt > 0:
            x = min(opp_in, my_total)
            pass_ev = real(eq_all, x >= cap - 0.5) * 2.0 * x - (x - my_in)
            if street == 0 and raises_now == 0:
                # Limping: the big blind may raise behind us.
                _, r_i, _ = self._freqs(st, "iso")
                cont = max(-(x - my_in),
                           eq.top(max(r_i, 0.04)) * 2.0 * cap
                           - (cap - my_in))
                pass_ev = (1.0 - r_i) * pass_ev + r_i * cont
            if pass_ev > 0.0:
                best_ev, best = pass_ev, None
            else:
                best_ev, best = 0.0, "fold"
        else:
            best_ev, best = real(eq_all, False) * pot, None

        if gs.can_raise:
            lo, hi = gs.raise_range
            for t in self._targets(gs, street, raises_now, pot, c_amt,
                                   b_me, b_op, lo, hi):
                add = t - b_me
                x = min(my_in + add, cap)
                allin = x >= cap - 0.5
                opp_call = x - opp_in
                ctx = _ctx(street, opp_call, pot + add, raises_now + 1,
                           allin)
                f, r, _ = self._freqs(st, ctx)
                f = min(max(f, 0.0), 0.95)
                if allin:
                    r = 0.0
                if f + r > 0.97:
                    r = max(0.0, 0.97 - f)
                eq_c = eq.top(max(1.0 - f, 0.04))
                ev = f * pot + (1.0 - f - r) * (
                    real(eq_c, allin) * 2.0 * x - (x - my_in))
                if r > 0.0:
                    eq_r = eq.top(max(r, 0.04))
                    ev += r * max(-(x - my_in),
                                  eq_r * 2.0 * cap - (cap - my_in))
                if ev - 0.02 * pot > best_ev:
                    best_ev, best = ev, t

        if best == "fold":
            return PlayerAction.fold(me)
        if best is None:
            return PlayerAction.call(me)
        return PlayerAction.raise_to(me, int(best))

    def _simple(self, gs):
        """Multiway tables and emergencies: simple, always legal."""
        me = self.player_id
        board = [_card_int(c) for c in gs.board]
        if board:
            cat = _ev(self.cards + board) >> 20
            strong, ok = cat >= 2, cat >= 1
        else:
            s = self.pf_class[_class_index(self.cards[0], self.cards[1])]
            strong, ok = s >= 0.63, s >= 0.5
        if strong and gs.can_raise:
            lo, hi = gs.raise_range
            return PlayerAction.raise_to(me, int(max(lo, min(hi, gs.pot))))
        if gs.call_amount == 0:
            return PlayerAction.call(me)
        if strong or (ok and gs.pot_odds < 0.3):
            return PlayerAction.call(me)
        return PlayerAction.fold(me)