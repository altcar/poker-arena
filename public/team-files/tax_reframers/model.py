import random
import time

from bucket_data import BUCKET_STATS
from buckets import (
    CONTINUE_DRAWS, DRAWS, MADE, NUM_KEYS, STREETS, TEXTURES, VALUE_DRAWS,
    VALUE_MADE, bucket, bucket_key,
)
from card_utils import Card
from game_state import GameStateView
from hand_eval import card_int
from player_action import DEALER, PlayerAction
from preflop_data import COMBOS, EQUITY, HANDS, NASH_MAX_BB, PREFLOP_CHART

# ---------------------------------------------------------------------------
# Phase 1: Bayesian opponent profiler
# ---------------------------------------------------------------------------
#
# Every stat is a Beta posterior: (hits + mean * strength) /
# (chances + strength). The prior acts as `strength` pseudo-observations at
# `mean`, so a handful of hands cannot push an opponent into a label; real
# data takes over as the counts grow.
#
# name: (hits counter, chances counter, prior mean, prior strength)
STATS = {
    # Preflop, once per hand the player had a preflop decision.
    "vpip": ("vpip", "pf_hands", 0.55, 8.0),
    "pfr": ("pfr", "pf_hands", 0.30, 8.0),
    # Opened with a raise when folded to them in a steal seat.
    "steal": ("steal", "steal_opp", 0.55, 6.0),
    # In a blind, facing a lone steal raise: folded.
    "fold_to_steal": ("fold_steal", "faced_steal", 0.40, 6.0),
    # Facing the first raise, re-raised.
    "three_bet": ("three_bet", "three_bet_opp", 0.12, 6.0),
    # Postflop, per action facing a bet: folded.
    "fold_to_bet": ("fold_bet", "faced_bet", 0.45, 6.0),
    # Postflop, per non-fold action: bet or raised (vs check or call).
    "aggression": ("post_aggr", "post_acts", 0.35, 6.0),
    # Saw the flop, then showed down.
    "wtsd": ("showdown", "saw_flop", 0.35, 6.0),
    # Bet or raised postflop, then showed down: held a value hand (or a
    # value draw) at their last bet. Low = they bluff, high = honest.
    "bet_honesty": ("honest_bet", "sd_aggr", 0.70, 4.0),
}

# Below this many hands an opponent is "Unknown": play the default strategy.
MIN_HANDS = 10

# ---------------------------------------------------------------------------
# Phase 2: preflop
# ---------------------------------------------------------------------------

PUSH_BB = 12  # at or below this effective stack the button shoves or folds
DEEP_BB = 35  # above this, re-raises are sized raises, not all-ins
# Open size (x big blind) scales with the opponent's Fold-to-Steal: from
# OPEN_MAX_X at FTS_LOW or below down to OPEN_MIN_X at FTS_HIGH or above.
OPEN_MIN_X, OPEN_MAX_X = 2.2, 3.0
FTS_LOW, FTS_HIGH = 0.25, 0.65
# Against a known opponent who 3-bets at least this often, an open must
# also be +EV against their 3-bets (else the 85% range bleeds to jams).
TIGHTEN_VS_3BET = 0.30
# The range we assume an opponent puts us on when we jam.
OUR_JAM_RANGE = 0.20
# Share of a called raise's equity we realise postflop (no all-in yet).
REALIZE_IP, REALIZE_OOP = 0.92, 0.80
# How far a label moves an opponent's jam-calling range from "only hands
# with the odds" (0) to "everything they play" (1).
STICKINESS = {"Calling Station": 0.8, "Maniac": 0.8, "Nit": 0.0}
DEFAULT_STICKINESS = 0.2


# ---------------------------------------------------------------------------
# Phase 3: postflop buckets
# ---------------------------------------------------------------------------

# A bucket level needs this many calibration deals before its equity is
# trusted (standard error about 1%); rarer buckets fall back a level.
BUCKET_MIN_DEALS = 2000

# ---------------------------------------------------------------------------
# Phase 4: exploit engine and circuit breaker
# ---------------------------------------------------------------------------

# Seconds into a run() call after which every checkpoint aborts to the
# safe pot-odds action (the engine's limit is 10 s, a timeout is fatal).
DEADLINE_S = 8.5
# Share of postflop equity realised when the hand is not over yet.
REALIZE_POST_IP, REALIZE_POST_OOP = 0.95, 0.85
# Bet sizes we consider, as fractions of the pot.
BET_SIZES = (0.33, 0.66, 1.0)
# Their bet into us after we check, as a fraction of the pot.
THEIR_BET = 0.66
# EVs this close (as a share of the pot) are mixed at random, so the bot
# is harder to read.
MIX_MARGIN = 0.03
# Calling Station guard: never bet or raise unless we are at least this
# far ahead of the range that calls (no bluffs).
STATION_MIN_EQUITY = 0.5

# ---------------------------------------------------------------------------
# Phase 5: fold rates learned per bet size
# ---------------------------------------------------------------------------
#
# Many bots fold by a pot-odds cut-off: they call every small bet and fold
# to every big one. One fold-to-bet number cannot show that, so the
# profiler also counts folds per bet size (the bet as a share of the pot
# before it). Each is a Beta posterior whose prior is what the bot
# assumed before (the size-scaled fold_to_bet), worth FOLD_PRIOR hands:
# with little data the bot plays as it did, the data takes over.
SIZE_EDGES = (0.45, 0.8, 1.3)  # small | medium | large | huge
FOLD_PRIOR = 5.0
# Sizes we have fewer than EXPLORE_MIN answers for are tried now and then
# (EXPLORE_P of our bets), only among sizes the EV says beat checking.
EXPLORE_MIN = 8
EXPLORE_P = 0.2
# Opponents adapt (a bot that sees us bet more starts calling more), so
# each new answer at a size shrinks the older ones there by FOLD_DECAY:
# the counts remember about the last 1 / (1 - FOLD_DECAY) answers.
FOLD_DECAY = 0.97
LEARNED_FOLDS = ("b0", "b1", "b2", "b3")
# Opening hands outside the chart is considered only against a known
# opponent who folds to steals at least this often.
WIDEN_FTS = 0.6
# Not learned, on purpose: folds to our preflop all-ins (tried as a raw
# rate, a ratio and a log-odds shift vs our prediction) and folds to our
# raises of their bets. Each cost 5-12 bb/100 heads-up against the
# baseline bot, which adapts: it tightens when we shove or raise more, so
# what we learned went stale. See gauntlet.py.


def size_bucket(frac: float) -> int:
    """0 small, 1 medium, 2 large, 3 huge: a bet of frac x the pot."""
    for k, edge in enumerate(SIZE_EDGES):
        if frac < edge:
            return k
    return len(SIZE_EDGES)


def learned(hits_chances, prior: float) -> float:
    """A fold rate: the prior worth FOLD_PRIOR observations, plus data."""
    hits, chances = hits_chances
    rate = (hits + prior * FOLD_PRIOR) / (chances + FOLD_PRIOR)
    return min(0.98, max(0.0, rate))


class OutOfTime(Exception):
    """Raised by a checkpoint once run() has used DEADLINE_S."""


def hand_class(hand) -> str:
    """The 169-class label of two Cards: "AA", "AKs", "T9o"."""
    a, b = sorted(hand, key=lambda c: c.value, reverse=True)
    if a.rank == b.rank:
        return a.rank * 2
    return a.rank + b.rank + ("s" if a.suit == b.suit else "o")


def beta_mean(hits, chances, mean, strength):
    """Posterior mean of a Beta prior (mean, strength) after the data."""
    return (hits + mean * strength) / (chances + strength)


def classify(stats):
    """One label from posterior stats: Unknown, Maniac, Calling Station,
    Nit or Regular, checked in that order."""
    if stats["hands"] < MIN_HANDS:
        return "Unknown"
    vpip, pfr = stats["vpip"], stats["pfr"]
    if pfr >= 0.55 or (stats["aggression"] >= 0.55 and vpip >= 0.5):
        return "Maniac"
    if (vpip >= 0.65 and pfr <= 0.25) or (
        vpip >= 0.5 and stats["fold_to_bet"] <= 0.25
    ):
        return "Calling Station"
    if vpip <= 0.35 or (stats["fold_to_steal"] >= 0.7 and vpip <= 0.5):
        return "Nit"
    return "Regular"


class Model:
    """
    Your bot: change anything in this file, and add files next to it.

    This object is what gets pickled, so load weights and data in __init__
    and keep them in plain attributes.
    """

    def __init__(self):
        # Per hand (seed overwrites).
        self.hand = []
        self.player_id = None
        self.hand_log = []  # every PlayerAction seen this hand, in order
        # Across hands: team name -> {counter name: int}. Plain data, so it
        # pickles; never reset by seed or end_game.
        self.profiles = {}
        self.hands_seen = 0
        self.started = 0.0  # perf_counter() when the current run() began
        self.rng = random.Random(20261107)  # mixing; pickles as plain data
        self._build_preflop_tables()
        self._build_bucket_table()

    def _build_bucket_table(self):
        """bucket_eq[bucket_key(...)] = (equity vs any hand, vs continuing
        hands, vs value hands), each column from the finest calibration
        level with BUCKET_MIN_DEALS deals: (street, made, draw, texture),
        then (street, made, draw), then (street, made)."""
        table = [(0.5, 0.5, 0.5)] * NUM_KEYS
        for street in range(3):
            for made in range(len(MADE)):
                for draw in range(len(DRAWS)):
                    for tex in range(len(TEXTURES)):
                        levels = [
                            BUCKET_STATS.get(k) for k in (
                                (street, made, draw, tex),
                                (street, made, draw, -1),
                                (street, made, -1, -1),
                            )
                        ]
                        levels = [s for s in levels if s]
                        if not levels:
                            continue
                        cols = []
                        for col in range(3):
                            eq, n = levels[-1][2 * col], levels[-1][2 * col + 1]
                            for s in levels:
                                if s[2 * col + 1] >= BUCKET_MIN_DEALS:
                                    eq, n = s[2 * col], s[2 * col + 1]
                                    break
                            cols.append(eq / 1000.0)
                        table[bucket_key(street, made, draw, tex)] = tuple(cols)
        self.bucket_eq = table
        # Share of random opponent hands that are value / continuing hands,
        # per street: where eq_value and eq_cont sit on the width scale.
        self.value_share = []
        self.cont_share = []
        for street in range(3):
            rows = [s for k, s in BUCKET_STATS.items()
                    if k[0] == street and k[2] == -1]
            deals = sum(s[1] for s in rows)
            self.value_share.append(sum(s[5] for s in rows) / deals)
            self.cont_share.append(sum(s[3] for s in rows) / deals)

    def postflop_bucket(self, board) -> tuple:
        """(made, draw, texture, (eq_any, eq_cont, eq_value)) for our hand
        on this board: a few microseconds."""
        hole = [card_int(c) for c in self.hand]
        cards = [card_int(c) for c in board]
        made, draw, tex = bucket(hole, cards)
        key = bucket_key(STREETS[len(cards)], made, draw, tex)
        return made, draw, tex, self.bucket_eq[key]

    def _build_preflop_tables(self):
        """Derives the runtime preflop tables from preflop_data, once.

        pf_order lists the classes strongest first (equity vs a random
        hand), pf_top[i] is the share of combos stronger than class i, and
        pf_mass[i][k] is class i's equity summed over the top k% of combos
        (weighted by combos / 1326), so its equity against any slice of a
        range is one subtraction: see _eq_vs.
        """
        n = len(HANDS)
        total = float(sum(COMBOS))
        self.pf_index = {h: i for i, h in enumerate(HANDS)}
        vs_random = [
            sum(COMBOS[j] * EQUITY[i][j] for j in range(n)) for i in range(n)
        ]
        self.pf_order = sorted(range(n), key=lambda i: -vs_random[i])
        self.pf_top = [0.0] * n
        edges = [0.0]  # cumulative share of combos after each class
        acc = 0.0
        for i in self.pf_order:
            self.pf_top[i] = acc / total
            acc += COMBOS[i]
            edges.append(acc / total)
        self.pf_mass = []
        for i in range(n):
            row = EQUITY[i]
            cum = [0.0]
            for j in self.pf_order:
                cum.append(cum[-1] + row[j] / 1000.0 * COMBOS[j] / total)
            grid = []
            seg = 0
            for k in range(101):
                p = k / 100.0
                while seg < n - 1 and edges[seg + 1] < p:
                    seg += 1
                lo, hi = edges[seg], edges[seg + 1]
                t = 0.0 if hi <= lo else min(1.0, (p - lo) / (hi - lo))
                grid.append(cum[seg] + (cum[seg + 1] - cum[seg]) * t)
            self.pf_mass.append(grid)
        chart = [PREFLOP_CHART[h] for h in HANDS]
        self.pf_open = [bool(c[0]) for c in chart]
        self.pf_push_max = [c[1] for c in chart]
        self.pf_call_max = [c[2] for c in chart]
        # Nash shove / call widths per stack depth, as shares of combos.
        self.nash_push_width = [1.0] + [
            sum(COMBOS[i] for i in range(n) if chart[i][1] >= d) / total
            for d in range(1, NASH_MAX_BB + 1)
        ]
        self.nash_call_width = [1.0] + [
            sum(COMBOS[i] for i in range(n) if chart[i][2] >= d) / total
            for d in range(1, NASH_MAX_BB + 1)
        ]

    def seed(self, hand: list[Card], player_id: int):
        """
        Called once before the hand: your two hole cards and your id.

        Store them; run() is not given them again.
        """
        self.hand = list(hand)
        self.player_id = player_id
        self.hand_log = []

    def run(
        self, actions: list[PlayerAction], game_state: GameStateView
    ) -> PlayerAction:
        """Called on your turn. Never raises and never returns an illegal
        move: an error, an illegal result or the DEADLINE_S circuit breaker
        all fall back to _safe_action."""
        self.started = time.perf_counter()
        try:
            self.hand_log.extend(actions)
            action = self._decide(game_state)
            if not isinstance(action, PlayerAction):
                raise TypeError("_decide must return a PlayerAction")
            game_state.validate_action(action)  # InvalidAction if illegal
            return action
        except Exception:
            return self._safe_action(game_state)

    def _tick(self):
        """Circuit-breaker checkpoint: call it in every stage and loop."""
        if time.perf_counter() - self.started > DEADLINE_S:
            raise OutOfTime()

    def _decide(self, game_state: GameStateView) -> PlayerAction:
        self._tick()
        if game_state.street == "preflop":
            return self._preflop(game_state)
        if game_state.num_players != 2:
            return self._postflop_baseline(game_state)
        return self._postflop(game_state)

    # -- Phase 4: postflop exploit engine --------------------------------------

    def _eq_at(self, cols, street: int, width: float) -> float:
        """Our equity against an opponent range made of the strongest
        `width` share of random hands on this board.

        The bucket's three calibrated columns are anchors on that scale:
        eq_value at the share of hands that are value hands, eq_cont at
        the share that continue, eq_any at 1. Linear in between; below the
        value share, a range stronger than value, slightly discounted.
        """
        ea, ec, ev = cols
        vs, cs = self.value_share[street], self.cont_share[street]
        if width >= cs:
            return ec + (ea - ec) * (width - cs) / (1.0 - cs)
        if width >= vs:
            return ev + (ec - ev) * (width - vs) / (cs - vs)
        return ev * (0.75 + 0.25 * max(0.0, width) / vs)

    def _opp_width(self, prof, opp: int, street_now: int) -> float:
        """How much of a random hand range the opponent can still hold,
        from their line this hand before the current street: each bet or
        raise keeps their aggression share, each call of a bet keeps the
        share that does not fold; a preflop raise tightens a little."""
        agg = min(0.95, max(0.05, prof["aggression"]))
        stay = 1.0 - min(0.95, max(0.02, prof["fold_to_bet"]))
        width = 1.0
        street = 0
        board = 0
        bets = {}
        current = 0
        pre_aggressor = None
        for a in self.hand_log:
            if a.player_id == DEALER:
                board += 1
                if board >= 3:
                    street = board - 2
                    bets = {}
                    current = 0
                continue
            if a.kind not in ("fold", "call", "raise"):
                continue
            p = a.player_id
            if street == 0:
                if a.kind == "raise":
                    pre_aggressor = p
                continue
            if street >= street_now:
                break
            facing = current > bets.get(p, 0)
            if p == opp:
                if a.kind == "raise":
                    width *= agg
                elif a.kind == "call" and facing:
                    width *= stay
            if a.kind == "call":
                bets[p] = current
            elif a.kind == "raise":
                bets[p] = current = a.amount
        if pre_aggressor == opp:
            width *= min(1.0, max(0.5, 0.5 + prof["pfr"]))
        return max(0.03, width)

    def _postflop(self, gs: GameStateView) -> PlayerAction:
        me = self.player_id
        opp = 1 - me
        prof = self.opponent_profile(gs.teams[opp])
        made, draw, tex, cols = self.postflop_bucket(gs.board)
        street = STREETS[len(gs.board)]
        self._tick()
        width = self._opp_width(prof, opp, street + 1)
        ip = me == gs.button  # heads-up, the button acts last postflop
        realize = 1.0 if street == 2 else (
            REALIZE_POST_IP if ip else REALIZE_POST_OOP)
        if gs.call_amount > 0:
            return self._facing_bet(gs, prof, cols, street, width, realize)
        return self._bet_or_check(gs, prof, cols, street, width, realize, ip)

    def _fold_share(self, prof, size_frac: float) -> float:
        """How often they fold to a bet of size_frac x pot: what they did
        against bets of that size, from a prior of their fold-to-bet,
        more against bigger bets."""
        f = min(0.95, max(0.02, prof["fold_to_bet"]))
        prior = min(0.95, f * (0.7 + 0.45 * size_frac))
        return learned(prof["fold_sizes"][size_bucket(size_frac)], prior)

    def _bet_or_check(self, gs, prof, cols, street, width, realize, ip):
        """Nothing owed: the best of checking and each bet size.

        EV(bet B) = fold * pot + call * (realize * eq_vs_callers * (pot +
        2B) - B), with their calling range the part of their range that
        does not fold. EV(check) = our share of the pot, plus (out of
        position) their bet into us at their aggression rate.
        """
        me = self.player_id
        opp = 1 - me
        pot = gs.pot
        label = prof["label"]
        agg = min(0.95, max(0.05, prof["aggression"]))
        eq_now = self._eq_at(cols, street, width)
        ev_check = realize * eq_now * pot
        if not ip:
            # They may bet into our check: we then call or fold.
            x = THEIR_BET * pot
            eq_vs_bet = self._eq_at(cols, street, width * agg)
            r = 1.0 if street == 2 else realize
            ev_after = max(0.0, r * eq_vs_bet * (pot + 2 * x) - x)
            ev_check = (1 - agg) * ev_check + agg * ev_after
        if not gs.can_raise:
            return PlayerAction.call(me)

        opp_left = gs.stacks[opp]
        jam = min(gs.max_raise_to, gs.bets[opp] + opp_left)
        sizes = [max(gs.min_raise_to, int(f * pot)) for f in BET_SIZES]
        if jam <= 1.5 * pot:
            sizes.append(jam)
        best_ev, best_size = None, None
        good = []  # sizes that beat checking
        for b in sizes:
            self._tick()
            b = min(b, jam)
            if b < gs.min_raise_to:
                continue
            fold = self._fold_share(prof, b / pot)
            eq_called = self._eq_at(cols, street, width * (1 - fold))
            if label == "Calling Station" and eq_called < STATION_MIN_EQUITY:
                continue  # never bluff a station
            r = 1.0 if (street == 2 or b >= jam) else realize
            ev = fold * pot + (1 - fold) * (r * eq_called * (pot + 2 * b) - b)
            if ev >= ev_check:
                good.append(b)
            if best_ev is None or ev > best_ev:
                best_ev, best_size = ev, b
        if best_ev is None or best_ev < ev_check - MIX_MARGIN * pot:
            return PlayerAction.call(me)  # check
        if best_ev < ev_check + MIX_MARGIN * pot and self.rng.random() < 0.5:
            return PlayerAction.call(me)  # close: mix in checks
        if good and self.rng.random() < EXPLORE_P:
            # Learn how they answer sizes we have little data on.
            seen = {b: prof["fold_sizes"][size_bucket(b / pot)][1]
                    for b in good}
            rare = min(good, key=lambda b: seen[b])
            if seen[rare] < EXPLORE_MIN:
                best_size = rare
        return self._raise_to(gs, best_size)

    def _facing_bet(self, gs, prof, cols, street, width, realize):
        """Owing chips: the best of fold (0), call and raise.

        Their betting range is their current range times their
        aggression, narrower for big bets and halved when they raise our
        bet; our equity against it decides the call. A raise wins the pot
        when they fold (fold-to-bet, less often for maniacs) and plays
        against the part of their betting range that continues.
        """
        me = self.player_id
        opp = 1 - me
        pot = gs.pot
        call = gs.call_amount
        label = prof["label"]
        agg = min(0.95, max(0.05, prof["aggression"]))
        bet_frac = call / max(1, pot - call)
        size_adj = 1.25 if bet_frac <= 0.4 else 1.0 if bet_frac <= 0.8 else 0.8
        share = agg * size_adj
        if gs.bets[me] > 0:
            share *= 0.5  # a raise of our bet
        w_bet = max(0.02, width * min(1.0, share))
        # Honest bettors bet the top of their range; bluffers bet anything
        # in it. Showdowns tell us which (bet_honesty).
        honest = prof["bet_honesty"]
        eq = (honest * self._eq_at(cols, street, w_bet)
              + (1 - honest) * self._eq_at(cols, street, width))

        my_left = gs.stacks[me]
        opp_left = gs.stacks[opp]
        all_in = call >= my_left or opp_left == 0
        r = 1.0 if (all_in or street == 2) else realize
        ev_call = r * eq * (pot + call) - call
        best_ev, best = 0.0, PlayerAction.fold(me)
        if ev_call > best_ev:
            best_ev, best = ev_call, PlayerAction.call(me)

        if gs.can_raise and opp_left > 0:
            jam = min(gs.max_raise_to, gs.bets[opp] + opp_left)
            targets = [int(gs.current_bet + 0.8 * (pot + call)), jam]
            for total in targets:
                self._tick()
                total = min(max(total, gs.min_raise_to), jam)
                if total <= gs.current_bet:
                    continue
                add = total - gs.bets[me]
                opp_add = total - gs.bets[opp]
                fold = min(0.9, max(0.0, prof["fold_to_bet"] * 0.8))
                if label == "Maniac":
                    fold *= 0.5
                eq_called = self._eq_at(cols, street, w_bet * (1 - fold))
                if (label == "Calling Station"
                        and eq_called < STATION_MIN_EQUITY):
                    continue  # never bluff-raise a station
                rr = 1.0 if (street == 2 or total >= jam) else realize
                ev = fold * pot + (1 - fold) * (
                    rr * eq_called * (pot + add + opp_add) - add)
                if ev > best_ev:
                    best_ev, best = ev, self._raise_to(gs, total)
        return best

    def _postflop_baseline(self, gs: GameStateView) -> PlayerAction:
        """Bucket-equity baseline, opponent-blind (Phase 4 replaces it with
        the exploit engine).

        Facing a bet: the bigger the bet relative to the pot, the more its
        range is value hands, so the equity used slides from eq_cont (a
        tiny bet) to eq_value (pot or more, or any raise of our bet).
        Checked to: bet 2/3 pot for value, 1/2 pot as a semi-bluff with a
        real draw before the river.
        """
        me = self.player_id
        made, draw, _, (eq_any, eq_cont, eq_value) = self.postflop_bucket(
            gs.board)
        call = gs.call_amount
        pot = gs.pot
        if call > 0:
            if gs.bets[me] > 0:
                w = 1.0  # they raised our bet
            else:
                w = min(1.0, call / max(1, pot - call))
            eq = eq_cont + (eq_value - eq_cont) * w
            if eq_value >= 0.70 and gs.can_raise:
                return self._raise_to(gs, gs.current_bet + (pot + call))
            if eq >= gs.pot_odds:
                return PlayerAction.call(me)
            return PlayerAction.fold(me)
        if gs.can_raise:
            if eq_cont >= 0.60:
                return self._raise_to(gs, pot * 2 // 3)
            if gs.street != "river" and draw in CONTINUE_DRAWS:
                return self._raise_to(gs, pot // 2)
        return PlayerAction.call(me)  # check

    # -- action helpers --------------------------------------------------------

    def _check_or_fold(self, gs: GameStateView) -> PlayerAction:
        if gs.call_amount == 0:
            return PlayerAction.call(self.player_id)
        return PlayerAction.fold(self.player_id)

    def _raise_to(self, gs: GameStateView, total) -> PlayerAction:
        """A raise to about total, clamped into raise_range; a call when
        raising is not allowed."""
        if gs.raise_range is None:
            return PlayerAction.call(self.player_id)
        low, high = gs.raise_range
        return PlayerAction.raise_to(
            self.player_id, int(min(high, max(low, round(total))))
        )

    # -- preflop ---------------------------------------------------------------

    def _eq_vs(self, i: int, lo: float, hi: float) -> float:
        """Equity of class i against the slice of hands from the top lo to
        the top hi share of combos (0 = strongest, 1 = weakest)."""
        lo = min(max(lo, 0.0), 1.0)
        hi = min(max(hi, 0.0), 1.0)
        if hi - lo < 0.005:  # too thin to average: widen around it
            lo, hi = max(0.0, min(lo, hi) - 0.005), min(1.0, max(lo, hi) + 0.005)
        row = self.pf_mass[i]

        def mass(p):
            x = p * 100.0
            k = int(x)
            if k >= 100:
                return row[100]
            return row[k] + (row[k + 1] - row[k]) * (x - k)

        return (mass(hi) - mass(lo)) / (hi - lo)

    def _calls_jam_width(self, w: float, need: float, label: str) -> float:
        """Share of all combos with which an opponent holding the top w
        calls our jam: the hands in their range with the pot odds against
        OUR_JAM_RANGE, moved towards all of w by how sticky they are."""
        rational = 0.0
        for j in self.pf_order:
            if self.pf_top[j] >= w:
                break
            if self._eq_vs(j, 0.0, OUR_JAM_RANGE) >= need:
                rational += COMBOS[j] / 1326.0
        s = STICKINESS.get(label, DEFAULT_STICKINESS)
        return rational + s * (w - rational)

    def _pf_raises(self) -> list:
        """(player_id, total) for each raise this hand before the flop."""
        out = []
        for a in self.hand_log:
            if a.player_id == DEALER:
                break
            if a.kind == "raise":
                out.append((a.player_id, a.amount))
        return out

    def _preflop(self, gs: GameStateView) -> PlayerAction:
        me = self.player_id
        i = self.pf_index[hand_class(self.hand)]
        if gs.num_players != 2:
            return self._preflop_multiway(gs, i)
        opp = 1 - me
        prof = self.opponent_profile(gs.teams[opp])
        bb = gs.big_blind
        my_total = gs.stacks[me] + gs.bets[me]
        opp_total = gs.stacks[opp] + gs.bets[opp]
        eff_total = min(my_total, opp_total)
        depth = eff_total / bb
        raises = self._pf_raises()

        if not raises:
            if me == gs.button and gs.call_amount > 0:
                return self._open(gs, i, prof, eff_total, depth)
            return self._vs_limp(gs, i, prof, depth)
        if gs.call_amount == 0:
            return PlayerAction.call(me)  # nothing owed: check
        return self._facing_raise(gs, i, prof, len(raises), eff_total, depth)

    def _open(self, gs, i, prof, eff_total, depth) -> PlayerAction:
        """Button, first in: open-raise or fold, never limp."""
        me = self.player_id
        known = prof["hands"] >= MIN_HANDS
        pot = gs.pot
        my_bet = gs.bets[me]
        if depth <= PUSH_BB:
            if not known:
                d = max(1, min(NASH_MAX_BB, round(depth)))
                if self.pf_push_max[i] >= d:
                    return self._raise_to(gs, eff_total)
                return PlayerAction.fold(me)
            d = max(1, min(NASH_MAX_BB, round(depth)))
            # Their calling range: Nash's, widened for sticky players.
            nash_c = self.nash_call_width[d]
            s = STICKINESS.get(prof["label"], DEFAULT_STICKINESS)
            c = nash_c + s * (1.0 - nash_c)
            ev = (1 - c) * pot + c * (
                self._eq_vs(i, 0.0, c) * 2 * eff_total - (eff_total - my_bet)
            )
            if ev > 0:
                return self._raise_to(gs, eff_total)
            return PlayerAction.fold(me)

        fts = prof["fold_to_steal"]
        if not self.pf_open[i] and not (known and fts >= WIDEN_FTS):
            return PlayerAction.fold(me)
        frac = min(1.0, max(0.0, (fts - FTS_LOW) / (FTS_HIGH - FTS_LOW)))
        size = int(round((OPEN_MAX_X - (OPEN_MAX_X - OPEN_MIN_X) * frac)
                         * gs.big_blind))
        t = prof["three_bet"]
        # Outside the chart (they fold a lot), or against a frequent
        # 3-bettor, open only if the open beats folding.
        if not self.pf_open[i] or (known and t >= TIGHTEN_VS_3BET):
            f = min(fts, 1.0 - t)
            called = max(0.0, 1.0 - f - t)
            vs_3bet = max(
                -(size - my_bet),
                self._eq_vs(i, 0.0, t) * 2 * eff_total - (eff_total - my_bet),
            )
            vs_call = (REALIZE_IP * self._eq_vs(i, t, t + called) * 2 * size
                       - (size - my_bet)) if called > 0 else 0.0
            if f * pot + t * vs_3bet + called * vs_call <= 0:
                return PlayerAction.fold(me)
        return self._raise_to(gs, size)

    def _vs_limp(self, gs, i, prof, depth) -> PlayerAction:
        """Big blind after the button limped: raise for value, or check."""
        if not gs.can_raise:
            return PlayerAction.call(self.player_id)
        raised = prof["steal"]  # they would have raised these
        played = max(prof["vpip"], raised + 0.1)
        if self._eq_vs(i, raised, min(1.0, played)) < 0.60:
            return PlayerAction.call(self.player_id)  # check
        if depth <= PUSH_BB:
            return self._raise_to(gs, gs.max_raise_to)
        x = 5 if prof["label"] == "Calling Station" else 4
        return self._raise_to(gs, x * gs.big_blind)

    def _facing_raise(self, gs, i, prof, n_raises, eff_total, depth):
        """Facing an open, 3-bet or more: the best EV of fold, call and
        jam; deep, strong hands re-raise to 3x instead of jamming."""
        me = self.player_id
        opp = 1 - me
        known = prof["hands"] >= MIN_HANDS
        opp_jammed = gs.stacks[opp] == 0
        # The width of the range they raise with here.
        if n_raises == 1:
            if opp_jammed and not known:
                d = max(1, min(NASH_MAX_BB, round(depth)))
                w = self.nash_push_width[d]
            else:
                w = prof["steal"]
        elif n_raises == 2:
            w = prof["three_bet"]
        else:
            w = prof["three_bet"] * 0.4
        w = min(1.0, max(0.02, w))
        eq = self._eq_vs(i, 0.0, w)

        pot = gs.pot
        call = gs.call_amount
        my_bet = gs.bets[me]
        after_call = my_bet + call
        if opp_jammed or call >= gs.stacks[me]:
            won = after_call + min(gs.bets[opp], after_call)
            ev_call = eq * won - call
        else:
            left = eff_total - after_call
            realize = REALIZE_IP if me == gs.button else REALIZE_OOP
            if left < pot + call:  # SPR under 1: as good as all-in
                realize = 1.0
            ev_call = realize * eq * (pot + call) - call

        best, action = 0.0, PlayerAction.fold(me)
        if ev_call > best:
            best, action = ev_call, PlayerAction.call(me)
        if gs.can_raise and not opp_jammed:
            jam = min(gs.max_raise_to, gs.bets[opp] + gs.stacks[opp])
            if jam >= gs.min_raise_to:
                if depth > DEEP_BB and eq >= 0.65:
                    return self._raise_to(gs, 3 * gs.current_bet)
                my_add = jam - my_bet
                opp_add = jam - gs.bets[opp]
                need = opp_add / (pot + my_add + opp_add)
                c = self._calls_jam_width(w, need, prof["label"])
                fold_share = max(0.0, 1.0 - c / w)
                ev_jam = fold_share * pot + (1 - fold_share) * (
                    self._eq_vs(i, 0.0, max(c, 0.005)) * 2 * jam - my_add
                )
                if depth <= DEEP_BB and ev_jam > best:
                    best, action = ev_jam, self._raise_to(gs, jam)
        if gs.call_amount == 0:
            return PlayerAction.call(me)  # never fold a free check
        return action

    def _preflop_multiway(self, gs, i) -> PlayerAction:
        """Three or more players: a plain tight chart (the tournament is
        heads-up; this only has to be legal and sane)."""
        top = self.pf_top[i]
        raised = gs.current_bet > gs.big_blind
        if not raised:
            if top <= 0.15:
                return self._raise_to(gs, 3 * gs.big_blind)
            return self._check_or_fold(gs)
        if top <= 0.03:
            return self._raise_to(gs, gs.max_raise_to)
        if top <= 0.08 or (top <= 0.15 and gs.pot_odds < 0.3):
            return PlayerAction.call(self.player_id)
        return self._check_or_fold(gs)

    def _safe_action(self, gs: GameStateView) -> PlayerAction:
        """The circuit breaker's move: check when free, else call only if
        a table-lookup equity beats the pot odds, else fold. Microseconds,
        and every step is guarded, so it cannot raise."""
        me = self.player_id
        try:
            if gs.call_amount == 0:
                return PlayerAction.call(me)
        except Exception:
            return PlayerAction.fold(me)
        try:
            if gs.street == "preflop":
                i = self.pf_index[hand_class(self.hand)]
                eq = self._eq_vs(i, 0.0, 0.5)
            else:
                eq = self.postflop_bucket(gs.board)[3][1]  # vs continuing
            if eq >= gs.pot_odds:
                return PlayerAction.call(me)
        except Exception:
            pass
        return PlayerAction.fold(me)

    def end_game(
        self, actions: list[PlayerAction], game_state: GameStateView
    ):
        """Called after every hand: completes the hand's log and folds it
        into the opponent profiles. Never raises (a raise is a fault)."""
        try:
            self.hand_log.extend(actions)
            self._update_profiles(self.hand_log, game_state)
            self.hands_seen += 1
        except Exception:
            pass
        finally:
            self.hand_log = []

    # -- profiler ------------------------------------------------------------

    def opponent_profile(self, team: str) -> dict:
        """Posterior stats for one team, plus "hands" and "label". Works
        for a team never seen (all priors, label "Unknown")."""
        counts = self.profiles.get(team, {})
        stats = {"hands": counts.get("hands", 0)}
        for name, (hit, chance, mean, strength) in STATS.items():
            stats[name] = beta_mean(
                counts.get(hit, 0), counts.get(chance, 0), mean, strength
            )
        stats["label"] = classify(stats)
        # Raw (folds, chances) pairs: callers give the prior (`learned`).
        stats["fold_sizes"] = [
            (counts.get(f"fold_b{k}", 0), counts.get(f"faced_b{k}", 0))
            for k in range(len(SIZE_EDGES) + 1)
        ]
        return stats

    def opponent_teams(self, game_state: GameStateView) -> list[str]:
        """Team names of the other players still in the hand."""
        teams = game_state.teams
        return [
            teams[p] for p in game_state.players_in_hand
            if p != self.player_id
        ]

    def _update_profiles(self, log, game_state: GameStateView):
        """Replays one finished hand from its action log and adds what each
        other seat did to its team's counters.

        Preflop flags count once per hand; postflop counters count every
        action. Blinds are not actions, so their bets are posted here. A
        short-stacked blind is treated as posting in full: rare, and it only
        blurs check-vs-call detection.
        """
        n = game_state.num_players
        button = game_state.button
        sb = button if n == 2 else (button + 1) % n
        bb = (sb + 1) % n
        blinds = (sb, bb)
        # Heads-up only the button can steal; at a full table, the cutoff,
        # button and small blind when folded to.
        steal_seats = {button} if n == 2 else {(button - 1) % n, button, sb}

        seen = [{} for _ in range(n)]

        def flag(p, key):
            seen[p][key] = 1

        def bump(p, key):
            seen[p][key] = seen[p].get(key, 0) + 1

        bets = [0] * n
        bets[sb] = game_state.small_blind
        bets[bb] = game_state.big_blind
        current = game_state.big_blind
        pot = game_state.small_blind + game_state.big_blind
        street = 0  # 0 preflop, 1 flop, 2 turn, 3 river
        board_cards = 0
        board = []  # the board cards, in order
        folded = set()
        showed = {}  # player -> hole cards shown at the showdown
        last_aggr_board = {}  # player -> board size at their last bet
        # Preflop context.
        raises = 0
        opener = None
        stealer = None
        steal_clean = False  # the steal raise is still the only action on it
        limped = False
        acted_pf = set()
        responded_open = set()

        for action in log:
            p = action.player_id
            if p == DEALER:
                board_cards += 1
                if action.card is not None:
                    board.append(action.card)
                if board_cards >= 3:  # 3rd, 4th, 5th card open a street
                    street = board_cards - 2
                    bets = [0] * n
                    current = 0
                    if street == 1:
                        for q in range(n):
                            if q not in folded:
                                flag(q, "saw_flop")
                continue
            if not 0 <= p < n:
                continue
            kind = action.kind
            if kind == "reveal_card":
                if action.card is not None:
                    showed.setdefault(p, []).append(action.card)
                continue
            if kind not in ("fold", "call", "raise"):
                continue
            facing = current > bets[p]

            if street == 0:
                first = p not in acted_pf
                acted_pf.add(p)
                if first:
                    flag(p, "pf_hands")
                if first and raises == 0 and not limped and p in steal_seats:
                    flag(p, "steal_opp")
                    if kind == "raise":
                        flag(p, "steal")
                if (
                    steal_clean and raises == 1 and p in blinds
                    and p != stealer and p not in responded_open
                ):
                    flag(p, "faced_steal")
                    if kind == "fold":
                        flag(p, "fold_steal")
                if raises == 1 and p != opener and p not in responded_open:
                    responded_open.add(p)
                    flag(p, "three_bet_opp")
                    if kind == "raise":
                        flag(p, "three_bet")
                if kind == "raise":
                    flag(p, "vpip")
                    flag(p, "pfr")
                elif kind == "call" and facing:
                    flag(p, "vpip")
                # Context for the players after this one.
                if kind == "raise":
                    raises += 1
                    if raises == 1:
                        opener = p
                        is_steal = first and not limped and p in steal_seats
                        stealer = p if is_steal else None
                        steal_clean = is_steal
                    else:
                        steal_clean = False
                elif kind == "call" and facing:
                    if raises == 0:
                        limped = True
                    else:
                        steal_clean = False
            else:
                if facing:
                    bump(p, "faced_bet")
                    if kind == "fold":
                        bump(p, "fold_bet")
                    owed = current - bets[p]
                    if bets[p] == 0:  # a bet (not a raise of theirs),
                        # sized against the pot before it
                        key = f"b{size_bucket(owed / max(1, pot - owed))}"
                        bump(p, "faced_" + key)
                        if kind == "fold":
                            bump(p, "fold_" + key)
                if kind != "fold":
                    bump(p, "post_acts")
                    if kind == "raise":
                        bump(p, "post_aggr")
                        last_aggr_board[p] = len(board)

            if kind == "fold":
                folded.add(p)
            elif kind == "call":
                pot += current - bets[p]
                bets[p] = current
            else:  # raise: amount is the new street total
                pot += action.amount - bets[p]
                bets[p] = action.amount
                current = action.amount

        for p, hole in showed.items():
            if seen[p].get("saw_flop"):
                flag(p, "showdown")
            size = last_aggr_board.get(p, 0)
            if len(hole) == 2 and size >= 3:
                flag(p, "sd_aggr")
                made, draw, _ = bucket([card_int(c) for c in hole],
                                       [card_int(c) for c in board[:size]])
                if made <= VALUE_MADE or draw in VALUE_DRAWS:
                    flag(p, "honest_bet")

        teams = game_state.teams
        for p in range(n):
            if p == self.player_id:
                continue
            counts = self.profiles.setdefault(teams[p], {})
            counts["hands"] = counts.get("hands", 0) + 1
            for key, value in seen[p].items():
                if key.startswith("faced_") and key[6:] in LEARNED_FOLDS:
                    fade = FOLD_DECAY ** value  # recent answers count more
                    for old in (key, "fold_" + key[6:]):
                        counts[old] = counts.get(old, 0) * fade
            for key, value in seen[p].items():
                counts[key] = counts.get(key, 0) + value
