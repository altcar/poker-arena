"""
Heads-up no-limit hold'em bot.

How it decides, every turn:

1. Replays the hand so far to know what the opponent did in which
   situation (opening, facing a bet, facing an all-in, ...).
2. Builds the opponent's range: a weight for each of the 1326 possible
   hole-card pairs. Every action narrows it, using how often THIS opponent
   (by team name) takes that action in that situation, learned hand by hand
   in end_game. A bot that raises 80% of the time raises with a wide range;
   one that raises 10% of the time has a strong hand.
3. Monte Carlo: our equity against that range, and against the part of it
   that would call a bet (its strongest hands).
4. Expected value of fold / check / call / each bet size, using the
   opponent's fold frequency. The best one is played.

Against a calling station this value-bets big and never bluffs; against a
bot that folds too much it bets nearly every hand; against a maniac it
calls down wide. Everything is plain data, so the model pickles.
"""

import random
import time
from bisect import bisect_right

from fasteval import PAIR, RANK_CHARS, card_to_int, eval_cards, has_draw
from player_action import DEALER, PlayerAction
from preflop_table import PREFLOP_EQUITY

# -- static tables (built on import, not pickled) ---------------------------


def _class_name(a: int, b: int) -> str:
    ra, rb = a >> 2, b >> 2
    hi, lo = max(ra, rb), min(ra, rb)
    if hi == lo:
        return RANK_CHARS[hi] * 2
    suited = "s" if (a & 3) == (b & 3) else "o"
    return RANK_CHARS[hi] + RANK_CHARS[lo] + suited


ALL_COMBOS = [(a, b) for a in range(52) for b in range(a + 1, 52)]
COMBO_PF = [PREFLOP_EQUITY[_class_name(a, b)] for a, b in ALL_COMBOS]
PF_SORTED = sorted(COMBO_PF)

# Showdown calibration: what share of a team's shown bets/raises were weak
# hands (bluffs), and of its calls loose ones. Prior [weak, total].
SD_PRIORS = {
    "pre_aggr": [1.0, 10.0],  # preflop raise with a bottom-40% hand
    "post_aggr": [1.0, 6.0],  # postflop bet/raise with a bottom-half hand
    "post_call": [1.0, 8.0],  # postflop call with a bottom-40% hand
}
# A flush or open-ended draw ranks with a small pair when the opponent's
# range is narrowed (most bots bet or call with draws).
DRAW_KEY = (PAIR << 20) | (3 << 16)

FOLD, CALL, RAISE = 0, 1, 2
KIND_INDEX = {"fold": FOLD, "call": CALL, "raise": RAISE}

# Situations an opponent acts in.
PF_OPEN = "pf_open"  # small blind's first move: fold, limp or raise
PF_VS_LIMP = "pf_vs_limp"  # preflop, nothing owed: check or raise
PF_VS_RAISE = "pf_vs_raise"  # preflop, facing the first raise
PF_VS_3BET = "pf_vs_3bet"  # preflop, facing a re-raise
POST_NOBET = "post_nobet"  # after the flop, nothing owed: check or bet
POST_VS_BET = "post_vs_bet"  # after the flop, facing a bet
POST_VS_RAISE = "post_vs_raise"  # after the flop, facing a raise
VS_ALLIN = "vs_allin"  # facing an all-in: fold or call
VS_ALLIN_AGG = "vs_allin_agg"  # facing an all-in after raising this street

# Prior pseudo-counts [fold, call/check, raise/bet] per situation: what we
# assume about a bot we have not seen yet. Small, so a few hands of
# evidence move them.
PRIORS = {
    PF_OPEN: [1.5, 1.5, 3.0],
    PF_VS_LIMP: [0.0, 3.0, 2.0],
    PF_VS_RAISE: [2.0, 3.0, 1.0],
    PF_VS_3BET: [1.0, 2.5, 1.5],
    POST_NOBET: [0.0, 3.0, 2.0],
    POST_VS_BET: [2.0, 3.0, 1.0],
    POST_VS_RAISE: [1.0, 3.0, 1.0],
    VS_ALLIN: [3.0, 2.0, 0.0],
    VS_ALLIN_AGG: [1.0, 3.0, 0.0],
}
ALLIN_SITUATIONS = (VS_ALLIN, VS_ALLIN_AGG)

# How much of its raw equity a hand keeps when more betting follows: in
# position we realise more of it than out of position.
REALISE = {
    (True, True): 0.92,  # preflop, in position (the button)
    (True, False): 0.85,  # preflop, out of position
    (False, True): 0.95,  # after the flop, in position
    (False, False): 0.88,
}

MC_SAMPLES = 4000
FOLD_SLOPE = 0.6  # extra folds per unit of pot odds above 0.3
REALISE_SCALE = 1.05  # tuned: 0.93 lost ~25 bb/100, 1.05 won ~9
PRIOR_SCALE = 1.0  # how strongly priors resist evidence
SD_ENABLED = False  # showdown bluff learning: no measurable gain yet, off
PF_3BET_SHOVE_ONLY = False  # preflop re-raises only as all-ins
EXTRA_SIZES = False  # use POST_BET_EXTRA bet sizes too
POST_BET_EXTRA = (0.25, 1.5)  # extra bet sizes (pot fractions) after the flop
TIME_BUDGET = 2.0  # seconds per decision; the limit is 10


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None
        self.my_cards = []
        # This hand's actions as plain tuples:
        # (player_id, kind, amount, card as int or -1).
        self.history = []
        # What we learned per opponent team: {team: {situation: counts}}.
        self.stats = {}
        # Showdown calibration per team: {team: {kind: [weak, total]}}.
        self.showdowns = {}
        self.hands_played = 0
        self.rng = random.Random(20261107)

    # -- the engine's calls -------------------------------------------------

    def seed(self, hand, player_id):
        self.hand = hand
        self.player_id = player_id
        self.my_cards = [card_to_int(c) for c in hand]
        self.history = []

    def run(self, actions, game_state):
        start = time.perf_counter()
        action = None
        try:
            self._absorb(actions)
            action = self._decide(game_state, start)
        except Exception:
            action = None
        return self._legalize(action, game_state)

    def end_game(self, actions, game_state):
        try:
            self._absorb(actions)
            self._learn(game_state)
        except Exception:
            pass
        self.hands_played += 1

    # -- bookkeeping ----------------------------------------------------------

    def _absorb(self, actions):
        for a in actions:
            card = card_to_int(a.card) if a.card is not None else -1
            self.history.append((a.player_id, a.kind, a.amount, card))

    def _team_stats(self, team):
        stats = self.stats.get(team)
        if stats is None:
            stats = {sit: [0, 0, 0] for sit in PRIORS}
            self.stats[team] = stats
        return stats

    def _freqs(self, team, sit):
        """[fold, call, raise] frequencies for team in sit."""
        counts = self._team_stats(team)[sit]
        prior = [p * PRIOR_SCALE for p in PRIORS[sit]]
        total = sum(counts) + sum(prior)
        return [(counts[i] + prior[i]) / total for i in range(3)]

    def _learn(self, gs):
        events = self._replay(gs)
        teams = gs.teams
        for street, pid, kind, sit, nboard, allin in events:
            if pid == self.player_id:
                continue
            team = teams[pid] if teams else "?"
            self._team_stats(team)[sit][KIND_INDEX[kind]] += 1
        # Cards shown at a showdown: how strong were the hands behind each
        # bet and call?
        shown = {}
        board = []
        for pid, kind, amount, card in self.history:
            if kind == "reveal_card" and card >= 0:
                if pid == DEALER:
                    board.append(card)
                else:
                    shown.setdefault(pid, []).append(card)
        for pid, cards in shown.items():
            if pid == self.player_id or len(cards) != 2:
                continue
            team = teams[pid] if teams else "?"
            self._calibrate(team, cards, board, events, pid)

    def _calibrate(self, team, cards, board, events, pid):
        sd = self.showdowns.get(team)
        if sd is None:
            sd = {kind: [0, 0] for kind in SD_PRIORS}
            self.showdowns[team] = sd
        a, b = min(cards), max(cards)
        pf_key = PREFLOP_EQUITY[_class_name(a, b)]
        pf_pct = (len(PF_SORTED) - bisect_right(PF_SORTED, pf_key)) / len(PF_SORTED)
        post_pct = {}
        for street, who, kind, sit, nboard, allin in events:
            if who != pid or kind == "fold":
                continue
            if street == 0:
                if kind == "raise":
                    sd["pre_aggr"][1] += 1
                    if pf_pct > 0.6:
                        sd["pre_aggr"][0] += 1
                continue
            if kind == "call" and sit in (POST_NOBET,):
                continue  # a check tells us little
            pct = post_pct.get(nboard)
            if pct is None:
                pct = _absolute_pct([a, b], board[:nboard])
                post_pct[nboard] = pct
            if kind == "raise":
                sd["post_aggr"][1] += 1
                if pct > 0.5:
                    sd["post_aggr"][0] += 1
            else:
                sd["post_call"][1] += 1
                if pct > 0.6:
                    sd["post_call"][0] += 1

    def _shares(self, team):
        """(preflop bluff share, postflop bluff share, loose-call share)
        for a team, from its showdowns."""
        if not SD_ENABLED:
            return (0.10, 0.15, 0.08)
        sd = self.showdowns.get(team, {})

        def share(kind):
            weak, total = sd.get(kind, (0, 0))
            prior = SD_PRIORS[kind]
            return (weak + prior[0]) / (total + prior[1])

        return (min(0.6, max(0.03, share("pre_aggr"))),
                min(0.6, max(0.03, share("post_aggr"))),
                min(0.5, max(0.03, share("post_call"))))

    def _replay(self, gs):
        """This hand's player moves so far, each as (street, player, kind,
        situation, board cards dealt, all-in)."""
        n = gs.num_players
        button = gs.button
        if n == 2:
            sb_seat, bb_seat = button, 1 - button
        else:
            sb_seat, bb_seat = (button + 1) % n, (button + 2) % n
        _, contrib, won = self._walk(n, sb_seat, bb_seat, gs, None)
        start = [gs.stacks[p] + contrib[p] - won[p] for p in range(n)]
        events, _, _ = self._walk(n, sb_seat, bb_seat, gs, start)
        return events

    def _walk(self, n, sb_seat, bb_seat, gs, start):
        stacks = start if start is not None else [10 ** 9] * n
        bb = gs.big_blind
        bets = [0] * n
        bets[sb_seat] = min(gs.small_blind, stacks[sb_seat])
        bets[bb_seat] = min(bb, stacks[bb_seat])
        contrib = bets[:]
        won = [0] * n
        street = 0
        nboard = 0
        raises = 0  # raises (bets) on this street so far
        raisers = set()  # who raised on this street
        events = []
        for pid, kind, amount, card in self.history:
            if kind == "reveal_card":
                if pid == DEALER:
                    nboard += 1
                    if nboard >= 3:
                        street = nboard - 2
                        bets = [0] * n
                        raises = 0
                        raisers = set()
                continue
            if kind == "win":
                if 0 <= pid < n:
                    won[pid] += amount
                continue
            if not 0 <= pid < n or kind not in KIND_INDEX:
                continue
            cur = max(bets)
            owed = cur - bets[pid]
            remaining = stacks[pid] - contrib[pid]
            facing_allin = owed > 0 and any(
                q != pid and bets[q] == cur and stacks[q] - contrib[q] <= 0
                for q in range(n)
            )
            sit = _situation(street, owed, raises, facing_allin,
                             pid in raisers)
            if kind == "call":
                add = max(0, min(owed, remaining))
            elif kind == "raise":
                add = max(0, amount - bets[pid])
            else:
                add = 0
            allin = kind == "raise" and add >= remaining
            bets[pid] += add
            contrib[pid] += add
            if kind == "raise":
                raises += 1
                raisers.add(pid)
            events.append((street, pid, kind, sit, nboard, allin))
        return events, contrib, won

    # -- the opponent's range -----------------------------------------------

    def _opp_range(self, events, team, board):
        """(combos, weights, preflop keys, {board size: postflop keys})."""
        dead = set(self.my_cards) | set(board)
        idx = [
            i for i, (a, b) in enumerate(ALL_COMBOS)
            if a not in dead and b not in dead
        ]
        combos = [ALL_COMBOS[i] for i in idx]
        pf_keys = [COMBO_PF[i] for i in idx]
        weights = [1.0] * len(combos)
        shares = self._shares(team)
        post_keys = {}
        for street, pid, kind, sit, nboard, allin in events:
            if pid == self.player_id or kind == "fold":
                continue
            if street == 0:
                keys = pf_keys
            else:
                keys = post_keys.get(nboard)
                if keys is None:
                    keys = _strength_keys(combos, board[:nboard])
                    post_keys[nboard] = keys
            freqs = self._freqs(team, sit)
            _narrow(weights, keys, freqs, kind, sit, street, shares)
        return combos, weights, pf_keys, post_keys

    # -- deciding -------------------------------------------------------------

    def _decide(self, gs, start):
        board = [card_to_int(c) for c in gs.board]
        if gs.num_players != 2:
            return self._multiway(gs, board, start)
        me = self.player_id
        opp = 1 - me
        teams = gs.teams
        team = teams[opp] if teams else "?"
        street = ("preflop", "flop", "turn", "river").index(gs.street)

        events = self._replay(gs)
        combos, weights, pf_keys, post_keys = self._opp_range(
            events, team, board)
        if street == 0:
            keys = pf_keys
        else:
            keys = post_keys.get(len(board))
            if keys is None:
                keys = _strength_keys(combos, board)
        pct = _percentiles(weights, keys)
        results = self._equity(combos, weights, pct, board, start)
        equity_vs = _EquityCurve(results)
        eq = equity_vs.all()

        pot = gs.pot
        owed = gs.call_amount
        my_rem = gs.stacks[me]
        opp_rem = gs.stacks[opp]
        my_bet = gs.bets[me]
        opp_bet = gs.bets[opp]
        in_position = me == gs.button
        realise = min(1.0, REALISE[(street == 0, in_position)] * REALISE_SCALE)
        last_street = street == 3

        options = []  # (ev, action)
        if owed > 0:
            options.append((0.0, PlayerAction.fold(me)))

        # Check or call.
        if owed == 0:
            r = 1.0 if last_street else realise
            options.append((eq * pot * r, PlayerAction.call(me)))
        else:
            paid = min(owed, my_rem)
            final_pot = pot + paid - (owed - paid)
            closes = paid >= my_rem or opp_rem == 0 or last_street
            r = 1.0 if closes else realise
            if street == 0 and gs.current_bet <= gs.big_blind:
                # A limp: the big blind may raise us off the hand.
                raise_freq = self._freqs(team, PF_VS_LIMP)[RAISE]
                ev = ((1 - raise_freq) * eq * final_pot * r
                      + raise_freq * 0.25 * eq * final_pot - paid)
            else:
                ev = eq * final_pot * r - paid
            options.append((ev, PlayerAction.call(me)))

        # Bets and raises. The opponent then faces one more raise than
        # there has been on this street.
        raises_now = sum(1 for e in events if e[0] == street and e[2] == "raise")
        opp_raised = any(
            e[0] == street and e[1] == opp and e[2] == "raise" for e in events)
        if gs.can_raise and opp_rem > 0:
            low, high = gs.min_raise_to, gs.max_raise_to
            for total in self._sizes(gs, street, pot, owed, my_bet, my_rem):
                total = int(min(high, max(low, round(total))))
                capped = min(total, opp_bet + opp_rem)
                put_in = capped - my_bet
                to_call = capped - opp_bet
                if put_in <= 0 or to_call <= 0:
                    continue
                shove = total >= high or to_call >= opp_rem
                opp_sit = _situation(street, 1, raises_now + 1, shove,
                                     opp_raised)
                fold = self._freqs(team, opp_sit)[FOLD]
                # Bigger bets get more folds from pot-odds players.
                odds = to_call / (pot + put_in + to_call)
                if not shove:
                    fold = fold + FOLD_SLOPE * (odds - 0.3)
                fold = min(0.95, max(0.02, fold))
                eq_called = equity_vs.top(1.0 - fold)
                r = 1.0 if (shove or last_street) else realise
                ev = fold * pot + (1 - fold) * (
                    eq_called * (pot + put_in + to_call) * r - put_in)
                options.append((ev, PlayerAction.raise_to(me, total)))

        options.sort(key=lambda o: -o[0])
        return options[0][1]

    def _sizes(self, gs, street, pot, owed, my_bet, my_rem):
        bb = gs.big_blind
        cur = gs.current_bet
        if street == 0:
            if cur <= bb:
                sizes = [2 * bb, 2.5 * bb, 3 * bb] if owed > 0 else [3 * bb, 4 * bb]
            elif PF_3BET_SHOVE_ONLY:
                # At ~25bb a re-raise commits a third of the stack: shove.
                sizes = []
            else:
                sizes = [2.5 * cur, 3.5 * cur]
        elif owed == 0:
            fracs = (0.33, 0.5, 0.75, 1.0) + (POST_BET_EXTRA if EXTRA_SIZES else ())
            sizes = [my_bet + f * pot for f in fracs]
        else:
            sizes = [2.5 * cur, cur + pot + owed]
        out = []
        top = gs.max_raise_to
        for total in sizes:
            put_in = total - my_bet
            behind = my_rem - put_in
            # Little left behind: just move in.
            if behind < 0.4 * (pot + 2 * put_in):
                total = top
            out.append(total)
        out.append(top)
        return sorted(set(int(round(t)) for t in out))

    def _equity(self, combos, weights, pct, board, start):
        """(percentile, result, weight) samples of our hand against the
        range: result 1 win, 0.5 split, 0 loss."""
        my = self.my_cards
        need = 5 - len(board)
        results = []
        if need == 0:
            mine = eval_cards(my + board)
            for i, (a, b) in enumerate(combos):
                w = weights[i]
                if w <= 0:
                    continue
                theirs = eval_cards([a, b] + board)
                res = 1.0 if mine > theirs else (0.5 if mine == theirs else 0.0)
                results.append((pct[i], res, w))
            return results
        cumulative = []
        total = 0.0
        for w in weights:
            total += w
            cumulative.append(total)
        dead = set(my) | set(board)
        deck = [c for c in range(52) if c not in dead]
        rng = self.rng
        sample = rng.sample
        rand = rng.random
        deadline = start + TIME_BUDGET
        last = len(combos) - 1
        for i in range(MC_SAMPLES):
            if i & 255 == 255 and time.perf_counter() > deadline:
                break
            j = bisect_right(cumulative, rand() * total)
            if j > last:
                j = last
            a, b = combos[j]
            drawn = sample(deck, need + 2)
            run = [c for c in drawn if c != a and c != b][:need]
            full = board + run
            mine = eval_cards(my + full)
            theirs = eval_cards([a, b] + full)
            res = 1.0 if mine > theirs else (0.5 if mine == theirs else 0.0)
            results.append((pct[j], res, 1.0))
        return results

    def _multiway(self, gs, board, start):
        """More than two players (not the tournament format): play by
        equity against random hands."""
        me = self.player_id
        opponents = max(1, len(gs.players_in_hand) - 1)
        dead = set(self.my_cards) | set(board)
        deck = [c for c in range(52) if c not in dead]
        need = 5 - len(board)
        won = 0.0
        count = 0
        deadline = start + TIME_BUDGET
        for i in range(600):
            if time.perf_counter() > deadline:
                break
            drawn = self.rng.sample(deck, need + 2 * opponents)
            full = board + drawn[:need]
            mine = eval_cards(self.my_cards + full)
            best = max(
                eval_cards(drawn[need + 2 * k:need + 2 * k + 2] + full)
                for k in range(opponents)
            )
            won += 1.0 if mine > best else (0.5 if mine == best else 0.0)
            count += 1
        eq = won / max(1, count)
        if eq > 1.5 / (opponents + 1) and eq > 0.5 and gs.can_raise:
            return PlayerAction.raise_to(me, gs.pot)
        if gs.call_amount == 0 or eq > gs.pot_odds:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)

    # -- never fault ----------------------------------------------------------

    def _legalize(self, action, gs):
        """A legal move: the chosen one, clamped, or a safe fallback."""
        me = self.player_id
        try:
            if action is None:
                action = self._fallback(gs)
            if action.kind == "raise":
                if not gs.can_raise:
                    return PlayerAction.call(me)
                low, high = gs.min_raise_to, gs.max_raise_to
                total = int(min(high, max(low, action.amount)))
                return PlayerAction.raise_to(me, total)
            if action.kind == "fold" and gs.call_amount == 0:
                return PlayerAction.call(me)
            if action.kind in ("fold", "call"):
                return action
        except Exception:
            pass
        try:
            if gs.call_amount == 0:
                return PlayerAction.call(me)
        except Exception:
            pass
        return PlayerAction.fold(me)

    def _fallback(self, gs):
        me = self.player_id
        if gs.call_amount == 0:
            return PlayerAction.call(me)
        a, b = sorted(self.my_cards)
        strength = PREFLOP_EQUITY[_class_name(a, b)]
        if strength > 0.6 or gs.pot_odds < 0.25:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)


# -- helpers ---------------------------------------------------------------


def _situation(street, owed, raises, facing_allin, raised_before=False):
    """The situation of a player who owes `owed` after `raises` raises on
    this street (a bet counts as a raise; the blinds do not)."""
    if facing_allin:
        return VS_ALLIN_AGG if raised_before else VS_ALLIN
    if street == 0:
        if owed == 0:
            return PF_VS_LIMP
        if raises == 0:
            return PF_OPEN
        return PF_VS_RAISE if raises == 1 else PF_VS_3BET
    if owed == 0:
        return POST_NOBET
    return POST_VS_BET if raises <= 1 else POST_VS_RAISE


def _strength_keys(combos, board):
    """How strong each combo is on this board: its hand rank, with draws
    lifted to a small pair."""
    keys = []
    small = len(board) < 5
    for a, b in combos:
        s = eval_cards([a, b] + board)
        if small and s < (PAIR << 20) and has_draw((a, b), board):
            s = DRAW_KEY
        keys.append(s)
    return keys


def _percentiles(weights, keys):
    """Each combo's place in the weighted range: 0 the strongest, 1 the
    weakest; equal keys share a place."""
    order = sorted(range(len(keys)), key=lambda i: -keys[i])
    total = sum(weights)
    pct = [1.0] * len(keys)
    if total <= 0:
        return pct
    done = 0.0
    i = 0
    n = len(order)
    while i < n:
        j = i
        group = 0.0
        key = keys[order[i]]
        while j < n and keys[order[j]] == key:
            group += weights[order[j]]
            j += 1
        mid = (done + group / 2) / total
        for k in range(i, j):
            pct[order[k]] = mid
        done += group
        i = j
    return pct


def _band_weight(p, lo, hi, above, below, ramp=0.08):
    """Weight for a combo at percentile p when the action keeps the band
    [lo, hi]: stronger hands get `above`, weaker `below`, with a short
    ramp at each edge."""
    if lo <= p <= hi:
        return 1.0
    if p < lo:
        d = lo - p
        return above if d >= ramp else 1.0 - (1.0 - above) * d / ramp
    d = p - hi
    return below if d >= ramp else 1.0 - (1.0 - below) * d / ramp


def _below_weight(band, share, rest):
    """Weight for hands below a band of width `band` so that they make up
    about `share` of the result; rest is the width below the band."""
    if rest <= 1e-6:
        return 1.0
    return min(1.0, share / (1.0 - share) * band / rest)


def _absolute_pct(cards, board):
    """Where a hand stands on this board among every possible hand: 0 the
    strongest, 1 the weakest."""
    dead = set(board)
    combos = [c for c in ALL_COMBOS if c[0] not in dead and c[1] not in dead]
    keys = _strength_keys(combos, board)
    mine = _strength_keys([tuple(cards)], board)[0]
    stronger = sum(1 for k in keys if k > mine)
    equal = sum(1 for k in keys if k == mine)
    return (stronger + equal / 2) / max(1, len(keys))


def _narrow(weights, keys, freqs, kind, sit, street, shares):
    """Reweights the range in place after the opponent's action. shares:
    (preflop bluffs, postflop bluffs, loose calls) learned at showdowns."""
    pct = _percentiles(weights, keys)
    fold, call, raise_ = freqs
    # Weaker hands than the band make up about this share of what is left
    # (bluffs, loose calls); scaled by the band's width so a narrow band
    # stays narrow.
    bluff_share = shares[0] if street == 0 else shares[1]
    call_share = 0.08 if street == 0 else shares[2]
    if kind == "raise":
        lo, hi, above = 0.0, raise_, 1.0
        below = _below_weight(hi - lo, bluff_share, 1.0 - hi)
    elif sit in (PF_VS_LIMP, POST_NOBET):  # a check
        lo, hi, above, below = raise_, 1.0, 0.35, 1.0
    elif sit in ALLIN_SITUATIONS:
        share = call / max(1e-9, fold + call)
        lo, hi, above = 0.0, share, 1.0
        below = _below_weight(hi, 0.05, 1.0 - hi)
    else:  # a call
        lo, hi, above = raise_, raise_ + call, 0.45
        below = _below_weight(hi - lo, call_share, 1.0 - hi)
    for i, p in enumerate(pct):
        if weights[i] > 0:
            weights[i] *= _band_weight(p, lo, hi, above, below)


class _EquityCurve:
    """Our equity against the whole range, or against its strongest part
    (the hands that would call a bet)."""

    def __init__(self, results):
        results = sorted(results)
        self.pcts = [r[0] for r in results]
        self.cum_w = []
        self.cum_win = []
        w_total = 0.0
        win_total = 0.0
        for p, res, w in results:
            w_total += w
            win_total += w * res
            self.cum_w.append(w_total)
            self.cum_win.append(win_total)

    def all(self):
        if not self.cum_w or self.cum_w[-1] <= 0:
            return 0.5
        return self.cum_win[-1] / self.cum_w[-1]

    def top(self, share):
        """Equity against the strongest `share` of the range."""
        if not self.cum_w or self.cum_w[-1] <= 0:
            return 0.5
        k = bisect_right(self.pcts, share)
        # Enough samples to mean something.
        need = min(len(self.pcts), max(40, k))
        k = max(k, need)
        w = self.cum_w[k - 1]
        if w <= 0:
            return self.all()
        return self.cum_win[k - 1] / w
