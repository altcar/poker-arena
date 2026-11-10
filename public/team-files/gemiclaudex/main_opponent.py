"""Opponent model (#13). Contract: docs/API.md -> Strategy modules (opponent.py), OpponentStats.

Learns how the opponent plays from finished hands (`observe_hand`) and turns that into
  * `stats()`: smoothed frequencies (OpponentStats), None until a stat has enough opportunities;
  * `range_estimate(state)`: a weighted range of the opponent's likely holdings right now;
  * `bluff_rate()`: how often their big postflop bets turned out to be bluffs at showdown;
  * `classify(stats)`: a coarse label (station / maniac / nit / tag / unknown).

Ported from v0 (bots/v0/bot.py, PR #16): per-stat shrinkage toward a prior with K = 20 pseudo-observations,
a minimum-sample gate of 12, and the station / maniac / nit / tag labels. Unlike v0 it knows the street of
every action and reads showdown cards, and its range estimate feeds core.equity instead of assuming a
random hand. The priors and label thresholds are heads-up guesses, to be tuned with the harness.
"""
from __future__ import annotations

from dataclasses import dataclass

from core_equity import _CARD_TO_IDX, _IDX_TO_CARD, _hand_class, _preflop_ranking, _rank_ints, Range, rank7
from core_interface import GameState, HandResult, OpponentStats

# Priors: what we assume before we've seen enough. Heads-up values, not v0's 6-max ones.
PRIOR = {
    "vpip": 0.60, "pfr": 0.35, "three_bet": 0.12, "fold_to_cbet": 0.45, "fold_to_bet": 0.45,
    "aggression": 1.0, "wtsd": 0.35, "avg_bet_frac": 0.65, "bluff_rate": 0.30,
}
SHRINK_K = 20.0       # pseudo-observations pulling each rate toward its prior (v0: _SHRINK_K)
MIN_SAMPLES = 12      # opportunities before a stat stops being None (v0: _MIN_OBS)
BIG_BET_FRAC = 0.66   # a postflop bet/raise at least this share of the pot counts as "big"
BLUFF_MIN_SAMPLES = 3
# Arena (#35): we're the big blind for a whole half and never see the hands where the opponent folds its small
# blind, so their observed VPIP as SB is ~1. When that censoring is detected, assume this entry rate instead.
HIDDEN_SB_VPIP_PRIOR = 0.45
CENSOR_MIN_HANDS = 10

_ALL_COMBOS = [(a, b) for a in range(52) for b in range(a)]   # 1326 combos, higher card index first
MAX_AGGRESSION = 10.0  # reported when they bet/raise but never call


def _build_preflop_index():
    order = {c: i for i, c in enumerate(_preflop_ranking())}
    cls_of = [order[_hand_class(a, b)] for a, b in _ALL_COMBOS]
    size = [0.0] * len(order)
    for c in cls_of:
        size[c] += 1
    cum_before, acc = [], 0.0
    for n in size:
        cum_before.append(acc)
        acc += n
    return cls_of, size, cum_before


_CLASS_OF, _CLASS_SIZE, _CLASS_CUM_BEFORE = _build_preflop_index()


def _top_weights(pct: float) -> list[float]:
    """Weight of each of the 1326 combos in core.equity.top_percent_range(pct) (pure Python)."""
    target = min(max(pct, 0.0), 1.0) * 1326
    w_class = [min(1.0, max(0.0, (target - cb) / n)) for cb, n in zip(_CLASS_CUM_BEFORE, _CLASS_SIZE)]
    return [w_class[c] for c in _CLASS_OF]


@dataclass
class _Act:
    """One action, replayed with the pot and the situation it was taken in."""
    street: int
    actor: str            # "me" / "opp"
    kind: str
    raise_to: int | None
    pot_before: int       # chips in the pot before this action
    facing: bool          # the actor faced a bet (had chips to call)
    bet_frac: float | None  # for raises: size relative to the pot (after calling), else None
    raises_before: int    # raises earlier on this street


def _replay(history, i_am_button: bool, sb: int, bb: int) -> list[_Act]:
    committed = {"me": sb if i_am_button else bb, "opp": bb if i_am_button else sb}
    out: list[_Act] = []
    street, raises = 1, 0
    for rec in history:
        if rec.street != street:
            street, raises = rec.street, 0
        me, other = rec.actor, ("opp" if rec.actor == "me" else "me")
        to_call = max(0, committed[other] - committed[me])
        pot = committed["me"] + committed["opp"]
        frac = None
        if rec.kind == "raise" and rec.raise_to is not None:
            frac = (rec.raise_to - committed[other]) / max(1, pot + to_call)
        out.append(_Act(rec.street, me, rec.kind, rec.raise_to, pot, to_call > 0, frac, raises))
        if rec.kind == "call":
            committed[me] = committed[other]
        elif rec.kind == "raise" and rec.raise_to is not None:
            committed[me] = rec.raise_to
            raises += 1
    return out


def _is_weak(hole: tuple[str, str], board: tuple[str, ...]) -> bool:
    """No made hand that uses a hole card: high card, or a pair that is entirely on the board."""
    if len(board) < 3:
        return False
    v = rank7(tuple(hole) + tuple(board))
    cat = v >> 25
    if cat == 0:
        return True
    if cat == 1:
        pair_rank = "23456789TJQKA"[(v >> 21) & 0xF]
        return pair_rank not in (hole[0][0], hole[1][0])
    return False


class _Rate:
    __slots__ = ("hits", "n")

    def __init__(self):
        self.hits = 0.0
        self.n = 0

    def add(self, hit: bool | float):
        self.hits += float(hit)
        self.n += 1

    def value(self, prior: float, k: float = SHRINK_K, min_n: int = MIN_SAMPLES) -> float | None:
        if self.n < min_n:
            return None
        return (self.hits + k * prior) / (self.n + k)


class OpponentModel:
    def __init__(self, min_samples: int = MIN_SAMPLES, shrink_k: float = SHRINK_K) -> None:
        self.min_samples = min_samples
        self.shrink_k = shrink_k
        self.hands = 0
        self.small_blind, self.big_blind = 1, 2   # learned from the first GameState we see
        self._vpip, self._pfr, self._3b = _Rate(), _Rate(), _Rate()
        self._fcb, self._fb, self._wtsd = _Rate(), _Rate(), _Rate()
        self._aggr = 0
        self._calls = 0
        self._bet_fracs: list[float] = []
        self._big_shown = _Rate()
        self._fb_street = {2: _Rate(), 3: _Rate(), 4: _Rate()}   # folds to our bets, by street
        self._checked_to = _Rate()                               # they bet when not facing a bet (postflop)
        self._sb_seen = self._sb_fold = self._bb_seen = 0       # preflop decisions by their seat

    # ------------------------------------------------------------- learning

    def observe_hand(self, result: HandResult) -> None:
        """Update counts from one finished hand. Never raises."""
        try:
            self._observe(result)
        except Exception:
            pass

    def _observe(self, result: HandResult) -> None:
        self.hands += 1
        acts = _replay(result.history, result.was_button, self.small_blind, self.big_blind)
        pre = [a for a in acts if a.street == 1]
        opp_pre = [a for a in pre if a.actor == "opp"]

        if opp_pre:   # they had a preflop decision (not a walk)
            if result.was_button:
                self._bb_seen += 1
            else:
                self._sb_seen += 1
                self._sb_fold += opp_pre[0].kind == "fold"
            self._vpip.add(any(a.kind in ("call", "raise") for a in opp_pre))
            self._pfr.add(any(a.kind == "raise" for a in opp_pre))
            for a in opp_pre:   # first time they face exactly one raise, made by us
                if a.raises_before == 1 and a.facing:
                    self._3b.add(a.kind == "raise")
                    break

        # Continuation bet: we made the last preflop raise and the first flop bet; how did they respond?
        pre_raisers = [a.actor for a in pre if a.kind == "raise"]
        flop = [a for a in acts if a.street == 2]
        if pre_raisers and pre_raisers[-1] == "me":
            first_bet = next((i for i, a in enumerate(flop) if a.kind == "raise"), None)
            if first_bet is not None and flop[first_bet].actor == "me":
                resp = next((a for a in flop[first_bet + 1:] if a.actor == "opp"), None)
                if resp is not None:
                    self._fcb.add(resp.kind == "fold")

        shown = result.opp_hole_cards
        bluffed = None
        for a in acts:
            if a.actor != "opp" or a.street == 1:
                continue
            if a.facing:
                self._fb.add(a.kind == "fold")
                self._fb_street[a.street].add(a.kind == "fold")
            elif a.kind in ("check", "raise"):
                self._checked_to.add(a.kind == "raise")
            if a.kind == "raise":
                self._aggr += 1
                if a.bet_frac is not None:
                    self._bet_fracs.append(a.bet_frac)
                    if shown and a.bet_frac >= BIG_BET_FRAC:
                        n_board = {2: 3, 3: 4, 4: 5}[a.street]
                        weak = _is_weak(shown, tuple(result.board[:n_board]))
                        bluffed = weak if bluffed is None else (bluffed or weak)
            elif a.kind == "call":
                self._calls += 1
        if bluffed is not None:
            self._big_shown.add(bluffed)

        folded_pre = any(a.kind == "fold" for a in pre)
        if not folded_pre and (len(result.board) >= 3 or any(a.street > 1 for a in acts)):
            showdown = shown is not None or (not any(a.kind == "fold" for a in acts) and len(result.board) == 5)
            self._wtsd.add(showdown)

    # ------------------------------------------------------------- outputs

    def stats(self) -> OpponentStats:
        k, m = self.shrink_k, self.min_samples
        n_post = self._aggr + self._calls
        if n_post >= m:
            pa = PRIOR["aggression"]
            pseudo = k / 2   # pseudo-actions split by the prior ratio
            den = self._calls + pseudo / (1 + pa)
            num = self._aggr + pseudo * pa / (1 + pa)
            aggression = min(MAX_AGGRESSION, num / den) if den > 0 else MAX_AGGRESSION
        else:
            aggression = None
        fr = self._bet_fracs
        kf = k / 4   # bet sizes are less noisy than yes/no frequencies: lighter shrinkage
        avg_frac = (sum(fr) + kf * PRIOR["avg_bet_frac"]) / (len(fr) + kf) if len(fr) >= m else None
        vpip = self._vpip.value(PRIOR["vpip"], k, m)
        pfr = self._pfr.value(PRIOR["pfr"], k, m)
        if self.sb_folds_hidden() and self._vpip.n:
            # Only hands they entered are visible: raises / entered hands is still informative, entry rate isn't.
            raise_given_entry = (self._pfr.hits + k * PRIOR["pfr"] / PRIOR["vpip"]) / (self._vpip.hits + k)
            vpip = HIDDEN_SB_VPIP_PRIOR
            pfr = min(vpip, raise_given_entry * vpip)
        return OpponentStats(
            hands=self.hands,
            vpip=vpip,
            pfr=pfr,
            three_bet=self._3b.value(PRIOR["three_bet"], k, m),
            fold_to_cbet=self._fcb.value(PRIOR["fold_to_cbet"], k, m),
            fold_to_bet=self._fb.value(PRIOR["fold_to_bet"], k, m),
            aggression=aggression,
            wtsd=self._wtsd.value(PRIOR["wtsd"], k, m),
            avg_bet_frac=avg_frac,
        )

    def bet_frequency(self) -> float | None:
        """Posterior mean of how often they bet when checked to (Beta(5,5) prior); None below 10 chances."""
        b, n = self._checked_to.hits, self._checked_to.n
        return (b + 5) / (n + 10) if n >= 10 else None

    def sb_folds_hidden(self) -> bool:
        """True when we look censored as in the arena's BB half: many SB hands seen, never an SB fold,
        and no hands with them in the big blind."""
        return self._sb_seen >= CENSOR_MIN_HANDS and self._sb_fold == 0 and self._bb_seen == 0

    def counts(self) -> dict:
        """Raw counts for confidence-gated decisions (#40): (successes, trials)."""
        return {
            "fold_to_bet": (self._fb.hits, self._fb.n),
            "fold_to_bet_by_street": {s: (r.hits, r.n) for s, r in self._fb_street.items()},
            "bet_when_checked_to": (self._checked_to.hits, self._checked_to.n),
        }

    def bluff_rate(self) -> float | None:
        """Share of their big postflop bets (>= 2/3 pot) that were bluffs when shown down; None if too few."""
        return self._big_shown.value(PRIOR["bluff_rate"], k=5.0, min_n=BLUFF_MIN_SAMPLES)

    def range_estimate(self, state: GameState) -> Range | None:
        """The opponent's likely holdings now, from this hand's actions and their stats. None = unknown."""
        try:
            return self._range_estimate(state)
        except Exception:
            return None

    def _range_estimate(self, state: GameState) -> Range | None:
        self.small_blind, self.big_blind = state.small_blind, state.big_blind
        acts = _replay(state.history, state.is_button, state.small_blind, state.big_blind)
        if not any(a.actor == "opp" for a in acts):
            return None
        st = self.stats()

        def get(name):
            v = getattr(st, name)
            return PRIOR[name] if v is None else v

        dead = {_CARD_TO_IDX[c] for c in tuple(state.hole_cards) + tuple(state.board)}
        live = [i for i, (a, b) in enumerate(_ALL_COMBOS) if a not in dead and b not in dead]
        combos = [_ALL_COMBOS[i] for i in live]
        weights = self._preflop_weights(live, [a for a in acts if a.street == 1 and a.actor == "opp"], get)

        for street, n_board in ((2, 3), (3, 4), (4, 5)):
            street_opp = [a for a in acts if a.street == street and a.actor == "opp"]
            if not street_opp or len(state.board) < n_board:
                continue
            board = [_CARD_TO_IDX[c] for c in state.board[:n_board]]
            vals = [_rank_ints([a, b] + board) for a, b in combos]
            for a in street_opp:
                pct = _strength_percentile(vals, weights)
                mult = _postflop_multiplier(a, pct, get, self.bluff_rate(), self.bet_frequency())
                weights = [w * m for w, m in zip(weights, mult)]

        out = {(_IDX_TO_CARD[a], _IDX_TO_CARD[b]): w for (a, b), w in zip(combos, weights) if w > 1e-9}
        return out or None

    def _preflop_weights(self, live: list[int], opp_pre: list[_Act], get) -> list[float]:
        def top(pct: float) -> list[float]:
            tw = _top_weights(pct)
            return [tw[i] for i in live]

        raises = [a for a in opp_pre if a.kind == "raise"]
        if raises:
            pct = get("pfr") if raises[0].raises_before == 0 else get("three_bet")
            pct *= 0.4 ** (len(raises) - 1)          # each further re-raise: much tighter
            return top(max(pct, 0.025))
        # Only calls / checks: they'd have raised their best hands most of the time.
        faced_raise = any(a.facing and a.raises_before >= 1 for a in opp_pre)
        would_raise = top(get("three_bet") if faced_raise else get("pfr"))
        called = any(a.kind == "call" for a in opp_pre)
        base = top(get("vpip")) if called and faced_raise else [1.0] * len(live)
        return [b * (1.0 - 0.65 * r) for b, r in zip(base, would_raise)]


def _strength_percentile(vals: list[int], weights: list[float]) -> list[float]:
    """Weighted percentile (0 = weakest, 1 = strongest) of each combo's hand strength within the range;
    equal strengths share their group's midpoint."""
    n = len(vals)
    total = sum(weights)
    out = [0.0] * n
    if total <= 0:
        return out
    order = sorted(range(n), key=vals.__getitem__)
    cum, i = 0.0, 0
    while i < n:
        j, gw = i, 0.0
        v = vals[order[i]]
        while j < n and vals[order[j]] == v:
            gw += weights[order[j]]
            j += 1
        mid = (cum + gw / 2) / total
        for k in range(i, j):
            out[order[k]] = mid
        cum += gw
        i = j
    return out


def _postflop_multiplier(a: _Act, pct: list[float], get, bluff_rate: float | None,
                         bet_freq: float | None = None) -> list[float]:
    if a.kind == "raise":
        frac = a.bet_frac or 0.5
        keep = 0.5 if frac <= 0.5 else 0.4 if frac <= 1.0 else 0.3
        if a.facing:
            keep *= 0.7                                  # raising over our bet: stronger
        elif bet_freq is not None:
            # Research rule (#40): someone who bets q of the time when checked to can't be betting only their
            # top 30-50%. Pessimistically assume they bet their strongest q fraction.
            keep = max(keep, min(0.95, bet_freq))
        if bluff_rate is not None and frac >= BIG_BET_FRAC:
            b = bluff_rate
        else:
            b = min(0.6, max(0.05, 0.15 + 0.08 * (get("aggression") - 1.0)))
        w = min(1.0, max(0.02, b * keep / ((1 - b) * (1 - keep))))   # so bluffs make up b of their bets
        return [1.0 if p >= 1 - keep else w for p in pct]
    if a.kind == "call" and a.facing:
        mult = min(1.0, max(0.1, 1.0 - 1.5 * get("fold_to_bet")))
        return [mult if p < 0.35 else 1.0 for p in pct]
    if a.kind == "check" and not a.facing:
        return [0.6 if p >= 0.85 else 1.0 for p in pct]   # they'd usually bet their best hands
    return [1.0] * len(pct)


def classify(stats: OpponentStats) -> str:
    """Coarse label: "station", "maniac", "nit", "tag" or "unknown" (not enough data / nothing fits)."""
    v, p, a, f = stats.vpip, stats.pfr, stats.aggression, stats.fold_to_bet
    if v is None:
        return "unknown"
    if (a is not None and a >= 2.5 and v >= 0.5) or (p is not None and p >= 0.5):
        return "maniac"
    if v >= 0.6 and (a is None or a < 0.8) and (f is None or f < 0.3):
        return "station"
    if v < 0.35 or (f is not None and f > 0.6 and v < 0.5):
        return "nit"
    if 0.35 <= v and (p is None or p >= 0.15):
        return "tag"
    return "unknown"
