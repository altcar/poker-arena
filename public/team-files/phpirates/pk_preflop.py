"""
Preflop policy (BRIEF section 5.1), heads-up, with the opponent model's
exploits (section 6).

decide(model, gs, me, opp) returns (kind, total): kind is "fold", "call"
or "raise", and total the street total for a raise (model.to_action keeps
it legal). ALL_IN asks for every chip.
"""

from pk_cards import class_of
from pk_equity import preflop_equity
from pk_estimate import preflop_classes
from pk_opponent import labels, lam, rate, seen
from pk_ranges import PERCENTILE, top_weights

ALL_IN = 10 ** 9



def preflop_actions(history, player):
    """The kinds of player's moves on the preflop street, in order."""
    return [kind for pid, kind, _, street in history
            if pid == player and street == "preflop"]


def allin_amounts(gs, me, opp):
    """(chips we add, final pot) if we go all in and are called: no more
    than the opponent can match counts."""
    my_bet = gs.bets[me]
    opp_bet = gs.bets[opp]
    my_total = min(my_bet + gs.stacks[me], opp_bet + gs.stacks[opp])
    my_add = max(0, my_total - my_bet)
    opp_add = max(0, my_total - opp_bet)
    return my_add, gs.pot + my_add + opp_add


def allin_rule(model, gs, me, opp, weights, margin):
    """Go (or call) all in when our equity against the range beats the
    price, else fold."""
    eq = preflop_equity(model.hole, weights)
    my_add, final_pot = allin_amounts(gs, me, opp)
    if final_pot > 0 and eq > my_add / final_pot + margin:
        return "raise", ALL_IN  # becomes a call when we cannot raise
    return "fold", 0


def call_allin_chance(stats, p):
    """How often they call an all-in: their record when they have one,
    otherwise guessed from how rarely they fold to an open."""
    guess = 1.0 - rate(stats, "bb_fold") - p["station_guess_drop"]
    guess = min(p["station_guess_max"], max(p["station_guess_min"], guess))
    return rate(stats, "call_allin", guess)


def own_rate(stats, name, default, need=20):
    """Our own rate (hits / chances), or default before 20 chances."""
    pair = stats.get(name)
    if not pair or pair[1] < need:
        return default
    return pair[0] / pair[1]


def decide(model, gs, me, opp):
    """_decide, then: keep our preflop all-in rate under allin_rate_cap
    (raise or call instead), unless the opponent is clearly a folder, so
    other bots' detectors do not take us for a shove dummy."""
    kind, total = _decide(model, gs, me, opp)
    if kind != "raise" or total < ALL_IN:
        return kind, total
    if gs.stacks[opp] == 0 or not gs.can_raise:
        return kind, total  # calling their all-in is not ours
    p = model.params
    stats = model.opp_stats(gs, opp)
    if "folder" in labels(stats):
        return kind, total
    if own_rate(stats, "me_allin", 0.0, p["own_seen"]) < p["allin_rate_cap"]:
        return kind, total
    if gs.call_amount == 0 or gs.bets[me] < gs.big_blind:
        return "raise", round(p["open_size"] * gs.big_blind)
    return "call", 0


def _decide(model, gs, me, opp):
    p = model.params
    stats = model.opp_stats(gs, opp)
    labs = labels(stats)
    exploit = model.rng.random() < lam(stats)
    hole = model.hole
    pct = PERCENTILE[class_of(hole[0], hole[1])]
    bb = gs.big_blind
    to_call = gs.call_amount
    opp_bet = gs.bets[opp]
    my_stack = gs.stacks[me]
    opp_all_in = gs.stacks[opp] == 0
    button = gs.button == me
    acted = len(preflop_actions(model.history, me)) > 0

    if button and not acted:
        return _open(model, gs, me, opp, stats, labs, exploit, pct)

    if not button and not acted:
        if to_call == 0:
            # The button limped.
            limp_pct = p["limp_raise_pct"]
            if ("folder" not in labs
                    and own_rate(stats, "me_vpip", 1.0, p["own_seen"])
                    < p["vpip_floor"]):
                limp_pct = max(limp_pct, p["vpip_floor"])
            if pct <= limp_pct:
                return "raise", round(p["limp_raise_size"] * bb)
            return "call", 0
        their = preflop_classes(model, gs, me, opp, stats)
        if opp_all_in or opp_bet > p["big_raise_bb"] * bb:
            return allin_rule(model, gs, me, opp, their, p["allin_margin"])
        eq = preflop_equity(hole, their)
        if opp_bet <= p["small_open_bb"] * bb:
            if eq >= p["bb_shove_eq"]:
                return "raise", ALL_IN
            if pct <= p["bb_call_pct"] and eq > gs.pot_odds + p[
                    "bb_call_margin"]:
                return "call", 0
            return "fold", 0
        if eq >= p["bb_shove_eq"] + p["big_open_extra"]:
            return "raise", ALL_IN
        return "fold", 0

    # Facing a re-raise after our own move.
    if to_call == 0:
        return "call", 0
    their = preflop_classes(model, gs, me, opp, stats)
    if opp_all_in or p["commit_div"] * to_call >= my_stack:
        return allin_rule(model, gs, me, opp, their, p["allin_margin"])
    kind, total = allin_rule(model, gs, me, opp, their, p["allin_margin"])
    if kind == "raise":
        return kind, total
    eq = preflop_equity(hole, their)
    if eq > gs.pot_odds + p["call_margin"]:
        return "call", 0
    return "fold", 0


def _open(model, gs, me, opp, stats, labs, exploit, pct):
    """The button's first move."""
    p = model.params
    bb = gs.big_blind
    open_to = round(p["open_size"] * bb)
    if exploit and "folder" in labs:
        return "raise", open_to  # they fold: raise every hand
    if exploit:
        calls = call_allin_chance(stats, p)
        evidence = (seen(stats, "call_allin") >= p["station_allin_seen"]
                    or seen(stats, "bb_fold") >= p["min_seen"])
        if evidence and calls >= p["station_call_allin"]:
            # They call too much: all in when ahead of their calling range.
            eq = preflop_equity(model.hole, top_weights(calls))
            my_add, final_pot = allin_amounts(gs, me, opp)
            ev = (1.0 - calls) * gs.pot + calls * (eq * final_pot - my_add)
            if ev > p["station_shove_ev"] * bb:
                return "raise", ALL_IN
            if pct <= p["station_limp_pct"]:
                return "call", 0
            return "fold", 0
        if "pf_maniac" in labs:
            # They re-raise most opens: open only what can call it.
            reraise = rate(stats, "bb_raise") + rate(stats, "bb_allin")
            weights = top_weights(max(0.05, reraise))
            eq = preflop_equity(model.hole, weights)
            my_add, final_pot = allin_amounts(gs, me, opp)
            if eq > my_add / final_pot + p["allin_margin"]:
                return "raise", open_to
            return "fold", 0
    if pct <= p["open_pct"]:
        return "raise", open_to
    return "fold", 0
