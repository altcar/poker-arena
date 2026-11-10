"""Postflop strategy (#12). Contract: docs/API.md -> Strategy modules (postflop.py).

One decision on the flop, turn or river:
  1. Equity vs the opponent's range (core.equity), the range coming from OpponentModel.range_estimate or,
     when None, a default from who raised preflop.
  2. Facing a bet: raise strong equity, call when equity beats the pot odds plus a margin, else fold.
  3. Checked to: value-bet above a threshold with a size picked by equity and board texture; otherwise
     bet as a bluff / semi-bluff only when a conservative fold estimate clears the actual bet price.
  4. Exploit knobs from OpponentStats (prior defaults when None): vs a calling station bet thinner and
     with bounded sizes and stop bluffing; vs a measured folder bluff selectively; vs a maniac check-call and trap.

Nonterminal calls retain a call cushion and stack-off guard. Terminal calls use contestable-pot
equity; value sizes and bluff confidence follow #40. Thresholds remain empirical starting points.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from core_allin import call_price, effective_raise_to, opponent_start, terminal_call
from core_equity import RANKS, Range, equity, top_percent_range
from core_interface import Action, GameState, OpponentStats

DEFAULT_BUDGET_MS = 60.0
SAMPLES = 2000

# Defaults for unknown stats (None). Hackathon fields lean towards calling, so assume less folding.
DEFAULTS = {"fold_to_bet": 0.40, "fold_to_cbet": 0.45, "aggression": 1.0, "wtsd": 0.40}

VALUE_EQ = {2: 0.62, 3: 0.60, 4: 0.58}      # min equity to bet for value, by street
RAISE_EQ = 0.75                             # facing a bet: raise at or above this
CALL_MARGIN = {2: 0.01, 3: 0.03, 4: 0.03}   # extra equity over pot odds needed to call (reverse implied odds)
DRAW_BONUS = 0.03                           # implied odds for a real draw on flop/turn
STACKOFF_FRAC = 0.5                         # a call this big a share of my stack needs extra equity
STACKOFF_MARGIN = 0.05

# Research rules (#40, deep-research report): exploit only on confident counts.
BLUFF_MARGIN = {2: 0.04, 3: 0.06, 4: 0.08}  # required lower-bound fold rate above break-even, by street
BARREL_MIN_OBS = 15                         # turn/river pure-air barrels need this many same-street samples
BLUFF_SIZES = (0.33, 0.5, 0.67)             # smallest passing size wins: small bluffs need fewer folds
Z05, Z10 = 1.645, 1.2816


def beta_bounds(hits: float, n: int, prior: float = 5.0) -> tuple[float, float]:
    """(mean, sd) of Beta(prior + hits, prior + n - hits); normal approximation is fine for a, b >= 5."""
    a, b = prior + hits, prior + n - hits
    return a / (a + b), math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1)))


def lower_bound(hits: float, n: int, z: float = Z05) -> float:
    m, sd = beta_bounds(hits, n)
    return max(0.0, m - z * sd)


def upper_bound(hits: float, n: int, z: float = Z05) -> float:
    m, sd = beta_bounds(hits, n)
    return min(1.0, m + z * sd)


@dataclass(frozen=True)
class Knobs:
    station: bool
    folder: bool
    maniac: bool
    fold_to_bet: float
    fold_to_cbet: float
    aggression: float
    sticky: bool = False              # almost never folds (q95 fold < 0.12, n >= 20): big bets still get called
    hf_bettor: bool = False           # bets when checked to with q10 > 0.65 (n >= 15)
    auto_bettor: bool = False         # q10 > 0.72 (n >= 25): call at raw pot odds
    counts: dict | None = field(default=None, compare=False)


def knobs(opp: OpponentStats | None, counts: dict | None = None) -> Knobs:
    """Opponent profile. With raw counts (OpponentModel.counts()) the labels are confidence-gated
    (research rules, #40); without them, the smoothed stats with prior defaults are used."""
    def g(name):
        v = getattr(opp, name, None) if opp is not None else None
        return DEFAULTS[name] if v is None else v

    fb, fcb, af, wtsd = g("fold_to_bet"), g("fold_to_cbet"), g("aggression"), g("wtsd")
    if counts:
        f, n = counts.get("fold_to_bet", (0, 0))
        b, nc = counts.get("bet_when_checked_to", (0, 0))
        station = n >= 10 and upper_bound(f, n) < 0.30
        sticky = n >= 20 and upper_bound(f, n) < 0.12
        folder = n >= 10 and lower_bound(f, n) > 0.50
        hf = nc >= 15 and lower_bound(b, nc, Z10) > 0.65
        auto = nc >= 25 and lower_bound(b, nc, Z10) > 0.72
        return Knobs(station=station, folder=folder, maniac=af > 2.5 or auto, fold_to_bet=fb, fold_to_cbet=fcb,
                     aggression=af, sticky=sticky, hf_bettor=hf or auto, auto_bettor=auto, counts=counts)
    return Knobs(
        station=fb < 0.30 or wtsd > 0.55,
        folder=fb > 0.55,
        maniac=af > 2.5,
        fold_to_bet=fb, fold_to_cbet=fcb, aggression=af,
    )


# ------------------------------------------------------------------ board / hand features

def _ranks(cards) -> list[int]:
    return [RANKS.index(c[0]) for c in cards]


def is_wet(board: tuple[str, ...]) -> bool:
    """Flush or straight draws possible: 2+ of a suit on the flop (3+ later), or 3 ranks within 5."""
    suits = [c[1] for c in board]
    if max(suits.count(s) for s in set(suits)) >= (2 if len(board) == 3 else 3):
        return True
    rs = sorted(set(_ranks(board)) | ({-1} if "A" in [c[0] for c in board] else set()))
    return any(rs[i + 2] - rs[i] <= 4 for i in range(len(rs) - 2))


def has_draw(hole: tuple[str, str], board: tuple[str, ...]) -> bool:
    """Flush draw (4 to a suit using a hole card) or open-ended straight draw using a hole card."""
    if len(board) >= 5:
        return False
    cards = tuple(hole) + tuple(board)
    for s in {c[1] for c in hole}:
        if sum(c[1] == s for c in cards) == 4:
            return True
    rs = set(_ranks(cards))
    if 12 in rs:
        rs.add(-1)
    hole_rs = set(_ranks(hole)) | ({-1} if any(c[0] == "A" for c in hole) else set())
    for lo in range(-1, 9):
        run = set(range(lo, lo + 4))
        if run <= rs and run & hole_rs and lo + 4 not in rs and lo - 1 not in rs:
            if lo >= 0 and lo + 4 <= 12:          # open at both ends
                return True
    return False


def default_range(state: GameState) -> Range | None:
    """The opponent's range from preflop action alone, for when no estimate is supplied."""
    pre = [a for a in state.history if a.street == 1]
    opp_raised = any(a.actor == "opp" and a.kind == "raise" for a in pre)
    me_raised = any(a.actor == "me" and a.kind == "raise" for a in pre)
    if opp_raised:
        return top_percent_range(0.35)
    if me_raised:
        return top_percent_range(0.60)
    return None


# ------------------------------------------------------------------ sizing

def _raise_to(state: GameState, frac: float) -> int:
    """raise_to for betting/raising `frac` of the pot (after calling)."""
    call = state.legal.call_amount
    pot_after_call = state.my_committed + state.opp_committed + call
    return int(state.my_committed + call + round(frac * pot_after_call))


def _bet(state: GameState, frac: float) -> Action:
    lg = state.legal
    if lg.min_raise_to is None:
        return Action("check") if lg.can_check else Action("call")
    high = effective_raise_to(state)
    target = _raise_to(state, frac)
    if target - state.my_committed >= 0.8 * (high - state.my_committed):      # don't leave a sliver behind
        target = high
    return Action("raise", max(lg.min_raise_to, min(target, high)))


def _value_size(eq: float, wet: bool, k: Knobs, spr: float) -> float:
    """Research sizing (#40): thin / clear / strong value. Stations: never overbet. Folders: small."""
    if spr <= 1.5 and eq >= 0.70:
        return 10.0                           # shallow: get it all in
    if k.sticky:   # research: 125-150% only once large bets are demonstrably still called
        return 0.50 if eq < 0.70 else 1.00 if eq < 0.82 else 1.50
    if k.station:
        return 0.33 if eq < 0.70 else 0.60 if eq < 0.82 else 0.85
    if k.folder:
        return 0.30 if eq < 0.70 else 0.50 if eq < 0.85 else 0.67
    return 0.33 if eq < 0.70 else 0.67 if eq < 0.82 else 1.0


# ------------------------------------------------------------------ main entry

def postflop_action(state: GameState, opp: OpponentStats | None, opp_range: Range | None,
                    time_budget_ms: float = DEFAULT_BUDGET_MS, opp_counts: dict | None = None) -> Action:
    lg = state.legal
    street = state.street if state.street in (2, 3, 4) else 2
    k = knobs(opp, opp_counts)
    rng_mix = random.Random(f"{state.hand_id}:{street}:{len(state.history)}:{state.hole_cards}")

    if opp_range is None:
        opp_range = default_range(state)
    try:
        eq = equity(state.hole_cards, state.board, opp_range, samples=SAMPLES,
                    time_budget_ms=time_budget_ms, seed=state.hand_id)
    except Exception:
        return Action("check") if lg.can_check else Action("fold")

    pot = state.my_committed + state.opp_committed
    my_behind = state.starting_stack - state.my_committed
    opp_behind = opponent_start(state) - state.opp_committed
    spr = min(my_behind, opp_behind) / max(1, pot)
    wet = is_wet(state.board)
    draw = has_draw(state.hole_cards, state.board)

    if not lg.can_check:
        return _facing_bet(state, eq, k, street, spr, wet, draw, rng_mix)
    return _checked_to(state, eq, k, street, spr, wet, draw, rng_mix)


def _facing_bet(state, eq, k, street, spr, wet, draw, rng_mix) -> Action:
    lg = state.legal
    call = lg.call_amount
    pot = state.my_committed + state.opp_committed
    if terminal_call(state):
        cost, final_pot = call_price(state)
        return Action("call" if eq * final_pot >= cost else "fold")
    pot_odds = call / (pot + call)
    need = pot_odds + CALL_MARGIN[street]
    if draw and street < 4:
        need -= DRAW_BONUS
    if k.maniac:
        need -= 0.03                          # their bets are often air
    elif k.fold_to_bet > 0.55 and k.aggression < 0.8:
        need += 0.04                          # a passive folder that bets has it
    my_behind = state.starting_stack - state.my_committed
    if call >= STACKOFF_FRAC * my_behind and eq < 0.80:
        need += STACKOFF_MARGIN               # v0's stack-off guard, softened
    if k.auto_bettor:
        need = min(need, pot_odds)            # research: call auto-bettors at raw pot odds
    elif k.hf_bettor:
        need = min(need, pot_odds + 0.02)

    raise_eq = RAISE_EQ - (0.05 if k.station else 0.0)
    if eq >= raise_eq and lg.min_raise_to is not None:
        if k.maniac and street < 4 and eq < 0.9 and rng_mix.random() < 0.5:
            return Action("call")             # let the maniac keep firing
        return _bet(state, 0.75 if not k.station else 1.0)
    # Semi-bluff raise with a draw against someone who folds a lot.
    if (draw and street == 2 and k.folder and not k.maniac and lg.min_raise_to is not None
            and eq >= pot_odds - 0.05 and rng_mix.random() < 0.5):
        return _bet(state, 0.75)
    if eq >= need:
        return Action("call")
    return Action("fold")


def _checked_to(state, eq, k, street, spr, wet, draw, rng_mix) -> Action:
    lg = state.legal
    if lg.min_raise_to is None:
        return Action("check")
    value_eq = VALUE_EQ[street] - (0.05 if k.station else 0.0) + (0.03 if k.folder and not k.station else 0.0)
    oop = not state.is_button

    if eq >= value_eq:
        # Trap a maniac: check strong (not nutted) hands out of position and let them bet.
        if (k.maniac or k.hf_bettor) and oop and street < 4 and eq < 0.92 and rng_mix.random() < 0.7:
            return Action("check")
        return _bet(state, _value_size(eq, wet, k, spr))

    # Bluffs: only when even a cautious (5th-percentile) estimate of their fold rate beats break-even
    # (research rule, #40). Pure air uses B/(P+B); draws use the equity-adjusted break-even.
    counts = k.counts or {}
    f_hits, f_n = counts.get("fold_to_bet", (0, 0))
    barrel = street > 2 and any(a.actor == "me" and a.kind == "raise" and a.street == street - 1
                                for a in state.history)
    if barrel:
        sf, sn = counts.get("fold_to_bet_by_street", {}).get(street, (0, 0))
        if sn >= BARREL_MIN_OBS:
            f_hits, f_n = sf, sn
        elif not draw:
            return Action("check")            # no same-street evidence: only barrel strong draws
    fold_lb = lower_bound(f_hits, f_n)
    pot = state.my_committed + state.opp_committed
    eq_called = eq * 0.85                     # they continue with their better hands
    for frac in BLUFF_SIZES:
        action = _bet(state, frac)
        # Confidence must cover the actual legal bet after minimum-raise / stack rounding.
        b = min(action.raise_to, opponent_start(state)) - state.my_committed
        if draw:
            denom = pot + b - eq_called * (pot + 2 * b)
            breakeven = max(0.0, (b - eq_called * (pot + 2 * b)) / denom) if denom > 0 else 0.0
        else:
            breakeven = b / (pot + b)
        if fold_lb > breakeven + BLUFF_MARGIN[street]:
            return action
    return Action("check")
