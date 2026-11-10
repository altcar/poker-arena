"""
The opponent's range in the current hand (BRIEF section 5.3).

Preflop: a top-x% range from their action and seat, x taken from their
statistics (pk_opponent), mixed with a uniform share for the raises they
make from outside such a range (learned at showdowns).

Postflop, street by street: every combo still in the range is scored by
its made-hand value on that street's board. A bet or raise keeps the
strongest share a (their betting frequency there) and some weaker hands
(bluffs, at the bluff rate seen at showdowns); a call drops most of the
weakest hands they would have folded; a check mildly discounts the very
strongest.
"""

from pk_cards import CLASS_SIZE, COMBO_CLASS, COMBOS
from pk_equity import hand_value
from pk_opponent import (contributions, fold_to_bet, preflop_raise_share,
                         rate, tell_key)
from pk_ranges import RANKING, band_weights, top_weights



def _mix(weights, noise):
    return [(1.0 - noise) * w + noise for w in weights]


def _narrow_classes(weights, keep, rest):
    """Keeps the best `keep` share of the (weighted) class range at full
    weight and multiplies the rest by `rest`."""
    total = sum(weights[k] * _SIZE[k] for k in range(169))
    if total <= 0:
        return weights
    out = list(weights)
    cum = 0.0
    for k in RANKING:
        w = weights[k] * _SIZE[k]
        if w <= 0:
            continue
        if cum / total > keep:
            out[k] = weights[k] * rest
        cum += w
    return out


_SIZE = CLASS_SIZE


def preflop_classes(model, gs, me, opp, stats):
    """169 class weights for the opponent's hand, from their preflop
    actions this hand."""
    p = model.params
    floor = p["range_floor"]
    theirs = [(kind, amount) for pid, kind, amount, street in model.history
              if pid == opp and street == "preflop"]
    mine = [kind for pid, kind, amount, street in model.history
            if pid == me and street == "preflop"]
    if not theirs:
        return [1.0] * 169
    opp_button = gs.button == opp
    x_raise = preflop_raise_share(stats, opp_button)
    noise = min(1.0, rate(stats, "pf_noise") / max(0.05, 1.0 - x_raise))
    first = theirs[0][0]
    all_in_now = gs.stacks[opp] == 0
    if opp_button:
        r = rate(stats, "btn_raise")
        a = rate(stats, "btn_allin")
        limp = rate(stats, "btn_limp")
        if first == "raise":
            if all_in_now and len(theirs) == 1:
                w = top_weights(max(floor, a))
            else:
                w = top_weights(max(floor, r + a))
        else:  # a limp: the hands they neither raise nor fold, a few traps
            w = band_weights(r + a, min(1.0, r + a + limp))
            trap = top_weights(r + a)
            w = [x + p["trap_limp"] * t for x, t in zip(w, trap)]
    elif mine and mine[0] == "call":  # they are the big blind; we limped
        lr = rate(stats, "limp_raise")
        if first == "raise":
            w = top_weights(max(floor, lr))
        else:
            w = band_weights(lr, 1.0)
            trap = top_weights(lr)
            w = [x + p["trap_check"] * t for x, t in zip(w, trap)]
    else:  # they are the big blind facing our raise
        t = rate(stats, "bb_raise")
        a = rate(stats, "bb_allin")
        c = rate(stats, "bb_call")
        if first == "raise":
            if all_in_now and len(theirs) == 1:
                w = top_weights(max(floor, a))
            else:
                w = top_weights(max(floor, t + a))
        else:
            w = band_weights(t + a, min(1.0, t + a + c))
            trap = top_weights(t + a)
            w = [x + p["trap_call"] * tt for x, tt in zip(w, trap)]
    # Later preflop moves: a re-raise is stronger, a call of ours a bit.
    for kind, amount in theirs[1:]:
        if kind == "raise":
            w = _narrow_classes(w, p["reraise_keep"], p["reraise_rest"])
        elif kind == "call":
            w = _narrow_classes(w, p["recall_keep"], p["recall_rest"])
    return _mix(w, noise)


def _percentiles(order, weights):
    """Within-range percentile of each combo index in order (strongest
    first): 0 = the strongest."""
    total = 0.0
    for i in order:
        total += weights[i]
    pct = {}
    if total <= 0:
        for i in order:
            pct[i] = 0.5
        return pct
    cum = 0.0
    for i in order:
        w = weights[i]
        pct[i] = (cum + 0.5 * w) / total
        cum += w
    return pct


def _keep_top(weights, order, pct, keep, rest):
    for i in order:
        if pct[i] > keep:
            weights[i] *= rest


def _bluff_factor(a, beta):
    """Weight for hands outside the top a so that bluffs are a beta share
    of a betting range."""
    a = min(max(a, 0.02), 0.98)
    beta = min(max(beta, 0.0), 0.95)
    return min(1.0, max(0.02, beta * a / ((1.0 - beta) * (1.0 - a))))


def combo_range(model, gs, me, opp, stats):
    """
    The opponent's range now: (weights, order, pct). weights is per combo
    of pk_cards.COMBOS (0 for combos that clash with our cards or the
    board); order lists the live combo indices strongest first on the
    current board, pct their within-range percentiles (postflop only;
    empty preflop).
    """
    classes = preflop_classes(model, gs, me, opp, stats)
    dead = set(model.hole)
    dead.update(model.board)
    weights = [0.0] * len(COMBOS)
    for i, (a, b) in enumerate(COMBOS):
        if a not in dead and b not in dead:
            weights[i] = classes[COMBO_CLASS[i]]
    board = model.board
    if len(board) < 3:
        return weights, [], {}
    beta = rate(stats, "bluff")
    weak_calls = rate(stats, "call_weak")
    order = []
    pct = {}
    bets = {me: 0, opp: 0}
    for street, n in (("flop", 3), ("turn", 4), ("river", 5)):
        if len(board) < n:
            break
        cards = board[:n]
        live = [i for i in range(len(COMBOS)) if weights[i] > 0.0]
        values = {i: hand_value(COMBOS[i], cards) for i in live}
        order = sorted(live, key=values.__getitem__, reverse=True)
        pct = _percentiles(order, weights)
        bets = {me: 0, opp: 0}
        pot = _pot_at_street_start(model, gs, me, opp, street)
        for pid, kind, amount, st in model.history:
            if st != street:
                continue
            if pid not in bets:
                continue
            current = max(bets.values())
            to_call = current - bets[pid]
            if pid == opp:
                if kind == "raise":
                    if to_call == 0:
                        a = rate(stats, "bet")
                    else:
                        a = rate(stats, "raise_vs_bet")
                    # Sizing tell: how often bets of this size were weak.
                    key = tell_key(amount, pot, to_call)
                    beta_size = rate(stats, key, beta) if key in stats else beta
                    _keep_top(weights, order, pct, a,
                              _bluff_factor(a, beta_size))
                elif kind == "call" and to_call > 0:
                    f = fold_to_bet(stats, to_call / max(1, pot - to_call))
                    keep = 1.0 - f
                    rest = _bluff_factor(keep, weak_calls)
                    _keep_top(weights, order, pct, keep, rest)
                elif kind == "call":
                    for i in order:
                        if pct[i] < model.params["check_top"]:
                            weights[i] *= model.params["check_discount"]
                pct = _percentiles(order, weights)
            if kind == "raise":
                pot += amount - bets[pid]
                bets[pid] = amount
            elif kind == "call":
                pot += to_call
                bets[pid] = current
    return weights, order, pct


def _pot_at_street_start(model, gs, me, opp, street):
    """Chips in the pot when this street began (deep-stack replay)."""
    order = ("preflop", "flop", "turn", "river")
    upto = order[:order.index(street)]
    history = [h for h in model.history if h[3] in upto]
    put_in = contributions(history, sorted({me, opp}), gs.button,
                           gs.small_blind, gs.big_blind)
    return max(1, sum(put_in.values()))
