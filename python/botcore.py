"""Shared engine for the A-E bot family.

Pure stdlib (no numpy): the grader only guarantees pydantic.  Cards are
encoded as ints 0..51 (rank = c >> 2, 0='2'..12='A'; suit = c & 3) so the
hot loop never touches pydantic Card objects.

The public surface is:
    encode(card), decode(c), eval7(cards), equity(...), preflop_class(...)
    preflop_percentile(...), OpponentModel, Policy

Nothing here reads engine internals or private state.
"""

import math
import random

RANK_TEXT = "23456789TJQKA"
FULL = 52
ALL_CARDS = tuple(range(FULL))


def encode(card):
    """Card -> 0..51."""
    return RANK_TEXT.index(card.rank) * 4 + "CDHS".index(card.suit)


def rank_of(c):
    return c >> 2


def suit_of(c):
    return c & 3


# ---------------------------------------------------------------- evaluator

def _straight_high(mask):
    """Top rank index of a 5-card straight in a 13-bit rank mask, or -1."""
    for hi in range(12, 3, -1):
        if (mask >> (hi - 4)) & 0b11111 == 0b11111:
            return hi
    # Wheel: A,2,3,4,5 -> bits 12,0,1,2,3.
    if mask & 0x100F == 0x100F:
        return 3  # 5-high
    return -1


def _top5(mask):
    out = []
    for r in range(12, -1, -1):
        if (mask >> r) & 1:
            out.append(r + 2)
            if len(out) == 5:
                break
    while len(out) < 5:
        out.append(0)
    return tuple(out)


def eval7(cards):
    """Comparable tuple for the best 5-card hand among 5..7 int cards."""
    counts = [0] * 13
    suits = [0] * 4
    rank_mask = 0
    for c in cards:
        r = c >> 2
        counts[r] += 1
        rank_mask |= 1 << r
        suits[c & 3] |= 1 << r

    # Straight flush first: nothing beats it.
    flush_suit = -1
    for s in range(4):
        if suits[s].bit_count() >= 5:
            flush_suit = s
            break
    if flush_suit >= 0:
        sh = _straight_high(suits[flush_suit])
        if sh >= 0:
            return (8, sh + 2)

    groups = sorted(
        ((counts[r], r) for r in range(13) if counts[r]),
        key=lambda g: (g[0], g[1]),
        reverse=True,
    )
    top_count, top_rank = groups[0]
    if top_count == 4:
        kicker = max(r for r in range(13) if counts[r] and r != top_rank)
        return (7, top_rank + 2, kicker + 2)

    trips = [r for r in range(13) if counts[r] >= 3]
    if trips:
        best_trips = max(trips)
        pairs = [r for r in range(13) if counts[r] >= 2 and r != best_trips]
        if pairs:
            return (6, best_trips + 2, max(pairs) + 2)

    if flush_suit >= 0:
        return (5,) + _top5(suits[flush_suit])

    sh = _straight_high(rank_mask)
    if sh >= 0:
        return (4, sh + 2)

    if trips:
        best_trips = max(trips)
        kick = sorted((r for r in range(13) if counts[r] and r != best_trips),
                      reverse=True)[:2]
        return (3, best_trips + 2, kick[0] + 2, kick[1] + 2)

    pairs = sorted((r for r in range(13) if counts[r] == 2), reverse=True)
    if len(pairs) >= 2:
        hi, lo = pairs[0], pairs[1]
        kick = max(r for r in range(13) if counts[r] and r not in (hi, lo))
        return (2, hi + 2, lo + 2, kick + 2)
    if pairs:
        p = pairs[0]
        kick = sorted((r for r in range(13) if counts[r] and r != p),
                      reverse=True)[:3]
        return (1, p + 2, kick[0] + 2, kick[1] + 2, kick[2] + 2)
    return (0,) + _top5(rank_mask)


# ------------------------------------------------------------ monte carlo

def _draw(rng, deck, n):
    return rng.sample(deck, n)


def equity(hole, board, opponents, rng, iterations=300, opp_filter=None):
    """Share of the pot won against `opponents` random hands.

    opp_filter(combo) -> bool optionally restricts their hole cards (a
    range estimate).  Ties count as splits.
    """
    board = list(board)
    dead = set(hole) | set(board)
    deck = [c for c in ALL_CARDS if c not in dead]
    missing = 5 - len(board)
    won = 0.0
    tries = 0
    while tries < iterations:
        need = missing + 2 * opponents
        if need > len(deck):
            break
        draw = _draw(rng, deck, need)
        full = board + draw[:missing]
        mine = eval7(hole + full)
        theirs = []
        ok = True
        idx = missing
        for _ in range(opponents):
            combo = (draw[idx], draw[idx + 1])
            idx += 2
            if opp_filter is not None and not opp_filter(combo):
                ok = False
                break
            theirs.append(eval7(list(combo) + full))
        if not ok:
            tries += 1
            continue
        best = max(theirs, default=mine)
        if mine > best:
            won += 1.0
        elif mine == best:
            won += 1.0 / (1 + theirs.count(best))
        tries += 1
    return won / max(1, tries)


# --------------------------------------------------------------- preflop

def preflop_class(hole):
    """'AA', 'AKs', 'AKo' style key for two encoded cards."""
    r1, r2 = rank_of(hole[0]), rank_of(hole[1])
    s1, s2 = suit_of(hole[0]), suit_of(hole[1])
    hi, lo = max(r1, r2), min(r1, r2)
    if hi == lo:
        return RANK_TEXT[hi] * 2
    return RANK_TEXT[hi] + RANK_TEXT[lo] + ("s" if s1 == s2 else "o")


def _chen(hole):
    """Chen formula, used only to order the 169 classes."""
    r1, r2 = rank_of(hole[0]), rank_of(hole[1])
    hi, lo = max(r1, r2), min(r1, r2)
    high_val = {12: 10, 11: 8, 10: 7, 9: 6, 8: 5}.get(hi, (hi + 2) / 2.0)
    if hi == lo:
        score = max(5.0, high_val * 2)
    else:
        score = high_val
        gap = hi - lo - 1
        if gap == 1:
            score -= 1
        elif gap == 2:
            score -= 2
        elif gap == 3:
            score -= 4
        elif gap >= 4:
            score -= 5
        if gap <= 1 and hi < 10:
            score += 1
    if suit_of(hole[0]) == suit_of(hole[1]):
        score += 2
    return score


def _build_percentiles():
    # One representative combo per 169 class, scored and ranked.
    reps = []
    for i in range(13):
        reps.append((i * 4, i * 4 + 1))  # pair (different suits)
        for j in range(i):
            reps.append((i * 4, j * 4))          # suited (same suit)
            reps.append((i * 4, j * 4 + 1))      # offsuit
    scored = sorted((( _chen(h), preflop_class(h)) for h in reps),
                    key=lambda x: -x[0])
    table = {}
    for pos, (_, key) in enumerate(scored):
        table[key] = pos / (len(scored) - 1)
    return table


PERCENTILE = _build_percentiles()


def preflop_percentile(hole):
    return PERCENTILE.get(preflop_class(hole), 1.0)


def is_suited(hole):
    return suit_of(hole[0]) == suit_of(hole[1])


def has_ace(hole):
    return rank_of(hole[0]) == 12 or rank_of(hole[1]) == 12


# ------------------------------------------------------------- opponent

class OpponentModel:
    """Plain-data counters, keyed by team name (seat changes every hand)."""

    def __init__(self):
        self.stats = {}

    def _row(self, team):
        row = self.stats.get(team)
        if row is None:
            row = {
                "hands": 0, "vpip": 0, "pfr": 0, "raises": 0,
                "calls": 0, "folds": 0, "showdowns": 0, "wins": 0,
                "big_bets": 0, "fold_to_bet": 0, "faced_bet": 0,
            }
            self.stats[team] = row
        return row

    def observe(self, actions, game_state, me, teams):
        """Fold one finished hand's action list into per-team counters."""
        by_seat = {}
        for a in actions:
            if a.kind == "win":
                continue
            if a.kind == "reveal_card":
                continue
            if a.player_id < 0 or a.player_id >= len(teams):
                continue
            seat = a.player_id
            by_seat.setdefault(seat, []).append(a)
        for seat, acts in by_seat.items():
            if seat == me:
                continue
            team = teams[seat]
            row = self._row(team)
            row["hands"] += 1
            raised_pre = False
            for a in acts:
                if a.kind == "raise":
                    row["raises"] += 1
                    if a.amount >= game_state.big_blind * 3:
                        row["big_bets"] += 1
                    raised_pre = True
                elif a.kind == "call":
                    row["calls"] += 1
                elif a.kind == "fold":
                    row["folds"] += 1
            if raised_pre:
                row["pfr"] += 1
            if any(a.kind in ("call", "raise") for a in acts):
                row["vpip"] += 1

    def rates(self, team):
        row = self.stats.get(team)
        if not row or row["hands"] < 8:
            return None
        n = float(row["hands"])
        return {
            "vpip": row["vpip"] / n,
            "pfr": row["pfr"] / n,
            "af": row["raises"] / max(1.0, float(row["calls"])),
            "big": row["big_bets"] / n,
        }

    def style(self, team):
        """Coarse archetype used by the exploit tiers."""
        r = self.rates(team)
        if r is None:
            return "unknown"
        if r["vpip"] > 0.75 and r["pfr"] < 0.25:
            return "station"
        if r["vpip"] < 0.45 and r["pfr"] < 0.2:
            return "nit"
        if r["af"] > 2.2 or r["big"] > 0.5:
            return "maniac"
        if r["vpip"] > 0.6:
            return "loose"
        return "reg"


# --------------------------------------------------------------- helpers

def clamp_raise(total, game_state):
    """Clamp a desired street total into the legal raise range."""
    low, high = game_state.raise_range
    if total < low:
        total = low
    if total > high:
        total = high
    return int(total)


def pot_fraction(game_state, fraction, extra=0):
    """A street total that bets `fraction` of the pot after calling."""
    call = game_state.call_amount
    target = game_state.bets[game_state.to_act] + call
    size = int((game_state.pot + extra) * fraction)
    return target + max(size, game_state.big_blind)


def made_category(hole, board):
    """Category index (0..8) of our current best hand."""
    if len(board) < 3:
        return -1
    return eval7(hole + list(board))[0]


def board_texture(board):
    """(paired, suited, connected) flags, cheap and useful for sizing."""
    ranks = [rank_of(c) for c in board]
    suits = [suit_of(c) for c in board]
    paired = len(set(ranks)) < len(ranks)
    suited = any(suits.count(s) >= 2 for s in set(suits))
    ordered = sorted(set(ranks))
    connected = any(
        ordered[i + 1] - ordered[i] <= 2 for i in range(len(ordered) - 1)
    ) if len(ordered) > 1 else False
    return paired, suited, connected


def in_position(game_state, me):
    """Heads-up: the button is out of position preflop, in position after."""
    if game_state.num_players != 2:
        return True
    button = game_state.button
    if game_state.street == "preflop":
        return me != button
    return me == button
