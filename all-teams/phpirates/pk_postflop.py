"""
Postflop policy (BRIEF section 5.2): equity against the opponent's
estimated range (pk_estimate), the expected value of every candidate move,
then the rules.

EV, in chips from now: fold 0; check eq * pot; call eq * (pot + to_call) -
to_call; a bet or raise adding b chips, which the opponent calls with c
chips: f * pot + (1 - f) * (eq_called * (pot + b + c) - b), where f is
their fold chance for that size (opponent model) and eq_called our equity
against the strongest (1 - f) of their range.
"""

from pk_cards import COMBOS
from pk_equity import mc_profile
from pk_estimate import combo_range
from pk_opponent import fold_to_bet, labels, lam, rate, seen

NB = 10  # strength bands of the opponent's range



def bet_total(gs, me, frac):
    """The street total for a bet (or raise) of frac of the pot, counted
    after calling."""
    to_call = gs.call_amount
    return gs.bets[me] + to_call + frac * (gs.pot + to_call)


def decide(model, gs, me, opp, start):
    p = model.params
    stats = model.opp_stats(gs, opp)
    weights, order, pct = combo_range(model, gs, me, opp, stats)
    groups = [NB - 1] * len(COMBOS)
    for i in order:
        g = int(pct[i] * NB)
        groups[i] = g if g < NB else NB - 1
    deadline = start + p["mc_budget"]
    eq, wins, counts = mc_profile(model.hole, model.board, COMBOS, weights,
                                  groups, NB, model.rng, deadline,
                                  p["mc_samples"])

    def eq_top(q):
        """Our equity against the strongest q share of their range."""
        if q >= 0.999:
            return eq
        edge = max(q, p["eq_top_min"]) * NB
        won = 0.0
        seen_ = 0.0
        for g in range(NB):
            if g >= edge:
                break
            part = min(1.0, edge - g)
            won += part * wins[g]
            seen_ += part * counts[g]
        if seen_ <= 0.0:
            return eq
        return won / seen_

    to_call = gs.call_amount
    pot = gs.pot
    my_bet = gs.bets[me]
    opp_bet = gs.bets[opp]
    opp_stack = gs.stacks[opp]
    labs = labels(stats)

    options = []  # (ev, kind, total, fold chance)
    if to_call == 0:
        options.append((eq * pot, "call", 0, 0.0))
    else:
        options.append((0.0, "fold", 0, 0.0))
        call_ev = eq * (pot + to_call) - to_call
        if opp_stack == 0:
            call_ev -= p["post_allin_margin"] * (pot + to_call)
        options.append((call_ev, "call", 0, 0.0))
    high = None
    if gs.can_raise and opp_stack > 0:
        low, high = gs.raise_range
        totals = set()
        for frac in p["post_sizes"]:
            total = int(max(low, min(high, bet_total(gs, me, frac))))
            if total >= 0.9 * high:
                total = high
            totals.add(total)
        totals.add(high)
        for total in sorted(totals):
            add = total - my_bet
            opp_call = max(0, min(total - opp_bet, opp_stack))
            if to_call == 0:
                f = fold_to_bet(stats, add / max(1, pot))
            else:
                f = rate(stats, "fold_raise")
            if total == high:
                # All in mainly for value: count on folds to it only once
                # measured; many bots call all-ins wide.
                if seen(stats, "call_allin") >= p["allin_seen"]:
                    f = 1.0 - rate(stats, "call_allin")
                else:
                    f = min(f, p["allin_fold_unseen"])
            f = min(max(f, 0.0), p["fold_cap"])
            eqc = eq_top(1.0 - f) - p["post_value_margin"]
            ev = f * pot + (1.0 - f) * (eqc * (pot + add + opp_call) - add)
            options.append((ev, "raise", total, f))

    best = options[0]
    for option in options[1:]:
        if option[0] > best[0]:
            best = option
    kind, total, f = best[1], best[2], best[3]

    # Short stack behind and a good hand: all in.
    if (high is not None and gs.spr(me) <= p["post_spr_allin"]
            and eq >= p["post_spr_eq"]):
        return "raise", high
    if kind == "raise":
        exploit = model.rng.random() < lam(stats)
        no_bluffs = "station" in labs or "random" in labs
        if eq < p["post_bluff_eq"] and (f < p["post_bluff_fold"]
                                        or no_bluffs):
            kind, total = _passive(options)
        elif (exploit and "maniac" in labs and to_call == 0
              and eq < p["post_maniac_check_eq"]):
            kind, total = "call", 0  # check: let them bet into us
    return kind, total


def _passive(options):
    """The best of check, call and fold."""
    best = None
    for option in options:
        if option[1] != "raise" and (best is None or option[0] > best[0]):
            best = option
    return best[1], best[2]
