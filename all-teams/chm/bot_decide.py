"""
The expected-value choice between fold, call (check) and raises of several
sizes, given our equity against the opponent's range and how this opponent
answers bets of each size.
"""

from bot_opp import response

# Share of our equity we expect to turn into chips when the hand goes on
# (position matters: in position we see their move before ours).
R_IP = {"preflop": 0.92, "flop": 0.95, "turn": 0.97, "river": 1.0}
R_OOP = {"preflop": 0.84, "flop": 0.88, "turn": 0.93, "river": 1.0}

PRE_OPEN_SIZES = (2.0, 2.5, 3.0, 4.0)  # big blinds, unopened pot
PRE_RAISE_SIZES = (2.2, 3.0)  # times the bet faced
POST_BET_SIZES = (0.33, 0.5, 0.75, 1.0, 1.5)  # pot fractions
POST_RAISE_SIZES = (0.6, 1.0)  # pot fractions on top of the call


def candidates(street, P, c, my_bet, opp_bet, bb, lo, hi):
    """Raise totals to consider, inside [lo, hi]."""
    raw = []
    if street == "preflop":
        if opp_bet <= bb:
            raw = [s * bb for s in PRE_OPEN_SIZES]
        else:
            raw = [s * opp_bet for s in PRE_RAISE_SIZES]
    elif c == 0:
        raw = [my_bet + f * P for f in POST_BET_SIZES]
    else:
        raw = [opp_bet + f * (P + c) for f in POST_RAISE_SIZES]
    out = set()
    for t in raw:
        t = int(round(t))
        t = min(hi, max(lo, t))
        # Leaving a stack smaller than ~a third of the pot behind is
        # pointless: go all-in instead.
        pot_after = P + 2 * (t - my_bet) - c
        if hi - t < 0.35 * pot_after:
            t = hi
        out.add(t)
    out.add(hi)
    return sorted(out)


def choose(P, c, my_bet, opp_bet, my_stack, opp_stack, rr, street, ip, bb,
           eq, stats, key, limp_spot=False, look=None):
    """Best move as (kind, amount, evs). evs maps a move to its EV in
    chips from now on (fold is 0).

    look, when the opponent still acts after our check or limp: pb (how
    often it bets or raises then), size (its usual bet, in chips), beta
    (its bluffiness)."""
    R = (R_IP if ip else R_OOP)[street]
    e = eq.all()
    evs = {}
    if c > 0:
        evs[("fold", 0)] = 0.0
    # Call or check.
    if c == 0 and look is not None and street != "preflop":
        evs[("call", 0)] = _check_ev(P, my_stack, opp_stack, street, R, e,
                                     eq, look)
    elif c == 0:
        evs[("call", 0)] = R * e * P
    elif limp_spot and look is not None:
        evs[("call", 0)] = _limp_ev(P, c, my_bet, my_stack, opp_stack, R, e,
                                    eq, look)
    else:
        excess = max(0, opp_bet - (my_bet + c))  # we call all-in for less
        final = P + c - excess
        closed = c >= my_stack or opp_stack == 0 or street == "river"
        if closed:
            evs[("call", 0)] = e * final - c
        else:
            r = R * (0.92 if limp_spot else 1.0)
            evs[("call", 0)] = r * e * final - c
    if rr is not None and opp_stack > 0:
        lo, hi = rr
        for T in candidates(street, P, c, my_bet, opp_bet, bb, lo, hi):
            a = T - my_bet
            to_call = T - opp_bet
            x = min(to_call, opp_stack)
            pot_after = P + a
            ratio = to_call / max(1.0, pot_after - to_call)
            pf, pc, pr = response(stats, key, ratio)
            me_allin = T >= my_bet + my_stack
            opp_allin = to_call >= opp_stack
            if me_allin or opp_allin:
                pc += pr
                pr = 0.0
            q1 = 1.0 - pf
            e1 = eq.top(q1)
            if pr > 0.003:
                e2 = eq.top(pr)
                band = q1 - pr
                ec = (e1 * q1 - e2 * pr) / band if band > 0.02 else e1
                ec = min(1.0, max(0.0, ec))
            else:
                e2 = e1
                ec = e1
            a_eff = min(a, opp_bet + x - my_bet)
            pot_c = P + a_eff + x
            if me_allin or opp_allin or street == "river":
                ev_c = ec * pot_c - a_eff
            else:
                ev_c = R * ec * pot_c - a_eff
            ev = pf * P + pc * ev_c
            if pr > 0:
                cap = min(my_bet + my_stack, opp_bet + opp_stack)
                final = (P - my_bet - opp_bet) + 2 * cap
                ev_rc = e2 * final - (cap - my_bet)
                ev += pr * max(-a, ev_rc)
            evs[("raise", T)] = ev
    best = max(evs, key=evs.get)
    return best[0], best[1], evs


def _split(e, eq, pb, beta):
    """Equity against the hands the opponent bets (top pb, plus bluffs)
    and against the ones it checks."""
    pb = min(0.97, max(0.03, pb))
    e_bet = (1 - beta) * eq.top(pb) + beta * e
    e_chk = (e - pb * e_bet) / (1 - pb)
    return pb, e_bet, min(1.0, max(0.0, e_chk))


def _check_ev(P, my_stack, opp_stack, street, R, e, eq, look):
    """We check and the opponent acts: it checks behind, or bets and we
    call or fold against its betting range."""
    pb, e_bet, e_chk = _split(e, eq, look["pb"], look["beta"])
    B = max(1, min(look["size"], my_stack, opp_stack))
    final = P + 2 * B
    if street == "river" or B >= my_stack or B >= opp_stack:
        call = e_bet * final - B
    else:
        call = R * e_bet * final - B
    return (1 - pb) * R * e_chk * P + pb * max(0.0, call)


def _limp_ev(P, c, my_bet, my_stack, opp_stack, R, e, eq, look):
    """We limp from the small blind: the big blind checks, or raises to
    look["size"] and we call or fold."""
    pr, e_r, e_nr = _split(e, eq, look["pb"], look["beta"])
    total = my_bet + c  # our street total after the limp
    no_raise = R * e_nr * (P + c) - c
    Z = min(look["size"], total + my_stack - c, total + opp_stack)
    Z = max(Z, total + 1)
    allin = Z >= 0.6 * (total + min(my_stack - c, opp_stack))
    pot_z = (P + c - 2 * total) + 2 * Z
    if allin:
        cap = total + min(my_stack - c, opp_stack)
        pot_z = (P + c - 2 * total) + 2 * cap
        call = e_r * pot_z - (cap - my_bet)
    else:
        call = R * e_r * pot_z - (Z - my_bet)
    return (1 - pr) * no_raise + pr * max(-c, call)
