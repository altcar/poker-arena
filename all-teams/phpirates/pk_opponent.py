"""
Opponent model (BRIEF section 6): counts of what an opponent did, by team
name, shrunk towards a sensible player's priors, and labels for the big
exploits.

A statistic is [hits, chances]; rate() gives (hits + k * prior) /
(chances + k). Everything is plain dicts and lists, updated in end_game
from the whole hand (its actions replayed) and the cards shown, then every
count is multiplied by the decay factor (decay), so recent hands weigh
more. Only these counters are kept between hands: nothing grows with the
number of hands played, and nothing loops over past hands. labels() is
worked out afresh from the counts at every decision, never fixed.
"""

from pk_cards import class_of
from pk_equity import hand_value
from pk_ranges import PERCENTILE

# The statistics kept. Every number of the opponent model (shrinkage k,
# the priors, bet-size buckets, label evidence, exploit blending) lives in
# model.PARAMS; the model hands them over with configure() before use.
STATS = (
    "btn_fold",
    "btn_limp",
    "btn_raise",
    "btn_allin",
    "bb_fold",
    "bb_call",
    "bb_raise",
    "bb_allin",
    "limp_raise",
    "vs3b_fold",
    "vs3b_call",
    "vs3b_raise",
    "call_allin",
    "bet",
    "fold_small",
    "fold_medium",
    "fold_large",
    "fold_raise",
    "raise_vs_bet",
    "call_vs_bet",
    "bluff",
    "call_weak",
    "pf_noise",
)
K = None  # shrinkage: a statistic counts as K chances at its prior
PRIORS = {}  # statistic -> prior rate
SMALL = None  # bet-size buckets, share of the pot before the bet
MEDIUM = None
MIN_SEEN = None  # chances a label needs before it is used
LAM_N = None  # exploits are trusted n / (n + LAM_N) after n hands
L = {}  # label thresholds and the like, from PARAMS


def configure(params):
    """Takes the opponent model's numbers from the model's PARAMS."""
    global K, SMALL, MEDIUM, MIN_SEEN, LAM_N
    K = params["shrink_k"]
    SMALL = params["size_small"]
    MEDIUM = params["size_medium"]
    MIN_SEEN = params["min_seen"]
    LAM_N = params["lam_n"]
    for name in STATS:
        PRIORS[name] = params["prior_" + name]
    for name in ("label_folder", "label_station_call",
                 "label_station_fold", "label_maniac", "label_maniac_bet",
                 "label_random_seen", "label_random_bluff",
                 "weak_strength", "noise_slack"):
        L[name] = params[name]


def new_stats():
    stats = {name: [0, 0] for name in STATS}
    stats["hands"] = 0
    stats["me_allin"] = [0, 0]  # our own preflop all-ins, per hand
    stats["me_vpip"] = [0, 0]  # our own voluntary preflop chips
    # Sizing tells: their bets by size, shown weak at a showdown.
    for size in ("small", "medium", "large"):
        stats["bluff_" + size] = [0, 0]
    return stats


def decay(stats, factor):
    """Multiplies every stored count by factor (end of each hand), so
    recent hands weigh more: with 0.99 a hand 100 hands ago counts about
    a third. Counts stay bounded (about 1 / (1 - factor) at most), and
    "hands" too, so lam reflects the evidence still in the counts."""
    for name, value in stats.items():
        if name == "hands":
            stats[name] = value * factor
        else:
            value[0] *= factor
            value[1] *= factor


def rate(stats, name, prior=None):
    hits, chances = stats[name]
    if prior is None:
        prior = PRIORS[name]
    return (hits + K * prior) / (chances + K)


def seen(stats, name):
    return stats[name][1]


def lam(stats):
    """How far to trust exploits: n / (n + LAM_N) after n hands."""
    n = stats["hands"]
    return n / (n + LAM_N)


def size_bucket(frac):
    if frac <= SMALL:
        return "fold_small"
    if frac <= MEDIUM:
        return "fold_medium"
    return "fold_large"


def tell_key(amount, pot, to_call):
    """The sizing-tell statistic for a bet or raise to amount (street
    total), made into pot after facing to_call."""
    size = amount / max(1, pot + to_call)
    return size_bucket(size).replace("fold_", "bluff_")


def fold_to_bet(stats, frac):
    return rate(stats, size_bucket(frac))


# -- labels -------------------------------------------------------------------




def labels(stats):
    """The set of labels that apply now, each on strong evidence only: at
    least MIN_SEEN chances in that spot and the shrunk rate past the line.
    "pf_maniac" (re-raises or shoves preflop) and "maniac" (bets or raises
    after the flop) are kept apart, so postflop betting alone never
    changes how we open."""
    out = set()
    folds = [(rate(stats, name), seen(stats, name))
             for name in ("bb_fold", "fold_medium", "fold_large")]
    with_data = [f for f, n in folds if n >= MIN_SEEN]
    if with_data and min(with_data) >= L["label_folder"]:
        out.add("folder")
    if (seen(stats, "call_vs_bet") >= MIN_SEEN
            and rate(stats, "call_vs_bet") >= L["label_station_call"]
            and rate(stats, "fold_medium") <= L["label_station_fold"]
            and rate(stats, "fold_small") <= L["label_station_fold"]):
        out.add("station")
    if (seen(stats, "bb_raise") >= MIN_SEEN
            and rate(stats, "bb_raise") + rate(stats, "bb_allin")
            >= L["label_maniac"]):
        out.add("pf_maniac")
    if (seen(stats, "btn_raise") >= MIN_SEEN
            and rate(stats, "btn_allin") >= L["label_maniac"]):
        out.add("pf_maniac")
    if ((seen(stats, "bet") >= 2 * MIN_SEEN
         and rate(stats, "bet") >= L["label_maniac_bet"])
            or (seen(stats, "raise_vs_bet") >= MIN_SEEN
                and rate(stats, "raise_vs_bet") >= L["label_maniac"])):
        out.add("maniac")
    if (seen(stats, "bluff") >= L["label_random_seen"]
            and rate(stats, "bluff") >= L["label_random_bluff"]):
        out.add("random")
    return out


# -- learning from a hand -------------------------------------------------------


def replay(history, players, button, small_blind, big_blind, start):
    """
    Replays a hand's betting: one record per action, as
    (pid, street, kind, amount, to_call, pot, frac, first, facing_allin,
    their_raises, all_in_after), where pot is before the action, frac the
    bet faced as a share of the pot before that bet, first whether it is
    pid's first move this street, their_raises how many raises pid made
    before on this street.
    """
    out = []
    left = dict(start)
    bets = {p: 0 for p in players}
    pot = 0
    if len(players) == 2:
        small = button
        big = players[1] if players[0] == button else players[0]
    else:
        small = players[(players.index(button) + 1) % len(players)]
        big = players[(players.index(button) + 2) % len(players)]
    for p, chips in ((small, small_blind), (big, big_blind)):
        chips = min(chips, left[p])
        left[p] -= chips
        bets[p] += chips
        pot += chips
    street = "preflop"
    acted = set()
    raises = {p: 0 for p in players}
    for pid, kind, amount, st in history:
        if pid not in bets:
            continue
        if st != street:
            street = st
            bets = {p: 0 for p in players}
            acted = set()
            raises = {p: 0 for p in players}
        current = max(bets.values())
        to_call = max(0, min(current - bets[pid], left[pid]))
        bet_before = pot - to_call
        frac = to_call / bet_before if bet_before > 0 else 1.0
        facing_allin = to_call > 0 and any(
            left[q] == 0 and bets[q] == current for q in players if q != pid)
        first = pid not in acted
        their_raises = raises[pid]
        if kind == "call":
            chips = to_call
        elif kind == "raise":
            chips = max(0, min(amount - bets[pid], left[pid]))
            raises[pid] += 1
        else:
            chips = 0
        left[pid] -= chips
        bets[pid] += chips
        pot_before = pot
        pot += chips
        acted.add(pid)
        out.append((pid, street, kind, amount, to_call, pot_before, frac,
                    first, facing_allin, their_raises, left[pid] == 0))
    return out


def _bump(stats, chosen, options):
    """One chance for each of options; a hit for the one chosen."""
    for name in options:
        stats[name][1] += 1
    if chosen is not None:
        stats[chosen][0] += 1


def river_strength(hole, board):
    """Share of the other possible hands that hole beats on a full board
    (ties count half): 0 weakest .. 1 nuts."""
    dead = set(board)
    dead.add(hole[0])
    dead.add(hole[1])
    mine = hand_value(hole, board)
    won = 0.0
    n = 0
    for a in range(52):
        if a in dead:
            continue
        for b in range(a + 1, 52):
            if b in dead:
                continue
            theirs = hand_value((a, b), board)
            n += 1
            if mine > theirs:
                won += 1.0
            elif mine == theirs:
                won += 0.5
    return won / n if n else 0.5


def contributions(history, players, button, small_blind, big_blind):
    """Chips each player put in this hand (blinds included), assuming
    every call matched the bet in full."""
    total = {p: 0 for p in players}
    bets = {p: 0 for p in players}
    if len(players) == 2:
        bets[button] = small_blind
        other = players[1] if players[0] == button else players[0]
        bets[other] = big_blind
    street = "preflop"
    for pid, kind, amount, st in history:
        if pid not in bets:
            continue
        if st != street:
            for p in players:
                total[p] += bets[p]
            bets = {p: 0 for p in players}
            street = st
        if kind == "raise":
            bets[pid] = amount
        elif kind == "call":
            bets[pid] = max(bets.values())
    for p in players:
        total[p] += bets[p]
    return total


def update(stats, model, gs, opp):
    """Learns from the hand just played (called from end_game)."""
    me = model.player_id
    players = sorted(set([me, opp]))
    # Starting stacks: final stack, minus what was won, plus what was put in.
    put_in = contributions(model.history, players, gs.button,
                           gs.small_blind, gs.big_blind)
    start = {p: gs.stacks[p] - model.won.get(p, 0) + put_in[p]
             for p in players}
    records = replay(model.history, players, gs.button, gs.small_blind,
                     gs.big_blind, start)
    stats["hands"] += 1
    button = gs.button
    for (pid, street, kind, amount, to_call, pot, frac, first, facing_allin,
         their_raises, all_in_after) in records:
        if pid != opp:
            continue
        if facing_allin:
            _bump(stats, "call_allin" if kind == "call" else None,
                  ["call_allin"])
            continue
        if street == "preflop":
            if first and pid == button:
                if kind == "fold":
                    chosen = "btn_fold"
                elif kind == "call":
                    chosen = "btn_limp"
                else:
                    chosen = "btn_allin" if all_in_after else "btn_raise"
                _bump(stats, chosen,
                      ["btn_fold", "btn_limp", "btn_raise", "btn_allin"])
            elif first and to_call == 0:
                _bump(stats, "limp_raise" if kind == "raise" else None,
                      ["limp_raise"])
            elif first:
                if kind == "fold":
                    chosen = "bb_fold"
                elif kind == "call":
                    chosen = "bb_call"
                else:
                    chosen = "bb_allin" if all_in_after else "bb_raise"
                _bump(stats, chosen,
                      ["bb_fold", "bb_call", "bb_raise", "bb_allin"])
            elif to_call > 0:
                chosen = {"fold": "vs3b_fold", "call": "vs3b_call",
                          "raise": "vs3b_raise"}[kind]
                _bump(stats, chosen, ["vs3b_fold", "vs3b_call", "vs3b_raise"])
            continue
        if to_call == 0:
            _bump(stats, "bet" if kind == "raise" else None, ["bet"])
            continue
        bucket = "fold_raise" if their_raises > 0 else size_bucket(frac)
        _bump(stats, bucket if kind == "fold" else None, [bucket])
        _bump(stats, "raise_vs_bet" if kind == "raise" else None,
              ["raise_vs_bet"])
        _bump(stats, "call_vs_bet" if kind == "call" else None,
              ["call_vs_bet"])

    # Our own preflop image: all-in rate and VPIP (what their detectors see).
    mine = [r for r in records if r[0] == me and r[1] == "preflop"]
    if mine and "me_allin" in stats:
        allin = any(r[2] == "raise" and r[10] for r in mine)
        vpip = any(r[2] == "raise" or (r[2] == "call" and r[4] > 0)
                   for r in mine)
        _bump(stats, "me_allin" if allin else None, ["me_allin"])
        _bump(stats, "me_vpip" if vpip else None, ["me_vpip"])

    shown = model.shown.get(opp)
    if shown and len(shown) == 2 and len(model.board) == 5:
        hole = (shown[0], shown[1])
        strength = river_strength(hole, model.board)
        weak = strength < L["weak_strength"]
        pf_raised = False
        for rec in records:
            pid, street, kind = rec[0], rec[1], rec[2]
            if pid != opp:
                continue
            if street == "preflop":
                if kind == "raise":
                    pf_raised = True
                continue
            if kind == "raise":
                _bump(stats, "bluff" if weak else None, ["bluff"])
                key = tell_key(rec[3], rec[5], rec[4])
                if key in stats:
                    _bump(stats, key if weak else None, [key])
            elif kind == "call":
                _bump(stats, "call_weak" if weak else None, ["call_weak"])
        if pf_raised:
            # A raise from outside the range we would expect counts as
            # noise: how often they raise tells us how wide that range is.
            x = preflop_raise_share(stats, opp == button)
            pct = PERCENTILE[class_of(hole[0], hole[1])]
            _bump(stats, "pf_noise" if pct > min(1.0, x + L["noise_slack"])
                  else None,
                  ["pf_noise"])


def preflop_raise_share(stats, opp_is_button):
    """The share of hands the opponent raises (all-ins included) in their
    seat: their raising range is about the top this much."""
    if opp_is_button:
        return rate(stats, "btn_raise") + rate(stats, "btn_allin")
    return rate(stats, "bb_raise") + rate(stats, "bb_allin")
