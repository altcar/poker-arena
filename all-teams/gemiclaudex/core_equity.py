"""Equity engine (#10). Contract: docs/API.md -> Equity.

Cards are 2-char strings ("As", "Td"). Internally a card is an int 0..51 = rank_index * 4 + suit_index,
with ranks "23456789TJQKA" and suits "cdhs".

Hand strength encoding (higher is better, equal = tie):
    category << 25 | primary_rank << 21 | secondary_rank << 17 | kicker_bits (13-bit rank mask)
Categories: 0 high card, 1 pair, 2 two pair, 3 trips, 4 straight, 5 flush, 6 full house, 7 quads,
8 straight flush. Kicker masks compare correctly as integers because they hold the same number of bits.

`equity` and `rank7` are pure Python (the tournament sandbox has no numpy). `_rank_matrix` is a numpy
twin for offline tools (EV engine, analysis); numpy is optional and tests check the two agree.
"""
from __future__ import annotations

import random
import time
from bisect import bisect_right
from itertools import combinations

try:  # numpy is optional: only _rank_matrix (offline tools, EV engine) needs it. The tournament has none.
    import numpy as np
except ImportError:  # pragma: no cover - exercised by the no-numpy test
    np = None

RANKS = "23456789TJQKA"
SUITS = "cdhs"

Range = dict[tuple[str, str], float]

_CARD_TO_IDX = {r + s: ri * 4 + si for ri, r in enumerate(RANKS) for si, s in enumerate(SUITS)}
_IDX_TO_CARD = [""] * 52
for _c, _i in _CARD_TO_IDX.items():
    _IDX_TO_CARD[_i] = _c


_CHUNK = 1024  # samples evaluated per numpy batch; the time budget is checked between batches


# ---------------------------------------------------------------- lookup tables (13-bit rank masks)

def _build_tables():
    n = 1 << 13
    highbit = [0] * n          # index of the highest set bit (0 for the empty mask; callers guard)
    top = {k: [0] * n for k in (1, 2, 3, 5)}   # mask keeping only the k highest set bits
    straight = [-1] * n        # rank index of the highest straight's top card, -1 if none
    windows = [(t, sum(1 << r for r in range(t - 4, t + 1))) for t in range(12, 3, -1)]
    windows.append((3, (1 << 12) | 0b1111))   # wheel A-2-3-4-5, top card is the 5
    for m in range(n):
        if m:
            highbit[m] = m.bit_length() - 1
        bits = [b for b in range(12, -1, -1) if m >> b & 1]
        for k in top:
            top[k][m] = sum(1 << b for b in bits[:k])
        for t, w in windows:
            if m & w == w:
                straight[m] = t
                break
    return highbit, top, straight


_HB_L, _TOP_L, _ST_L = _build_tables()
if np is not None:
    _HB = np.array(_HB_L, dtype=np.int64)
    _TOP1, _TOP2, _TOP3, _TOP5 = (np.array(_TOP_L[k], dtype=np.int64) for k in (1, 2, 3, 5))
    _ST = np.array(_ST_L, dtype=np.int64)
    _PW = (1 << np.arange(13, dtype=np.int64))
    _AR13 = np.arange(13)


# ---------------------------------------------------------------- card helpers

def _idx(card: str) -> int:
    try:
        return _CARD_TO_IDX[card]
    except (KeyError, TypeError):
        raise ValueError(f"invalid card {card!r}") from None


def _canon(a: int, b: int) -> tuple[str, str]:
    """Range key for two card indices: higher card first (rank, then suit order cdhs)."""
    hi, lo = (a, b) if a > b else (b, a)
    return _IDX_TO_CARD[hi], _IDX_TO_CARD[lo]


def _hand_class(a: int, b: int) -> str:
    hi, lo = (a, b) if a > b else (b, a)
    rh, rl = RANKS[hi >> 2], RANKS[lo >> 2]
    if rh == rl:
        return rh + rl
    return rh + rl + ("s" if (hi & 3) == (lo & 3) else "o")


# ---------------------------------------------------------------- evaluators

def _rank_matrix(cards):
    """Strength of each row of an (N, k) int array of card indices, 5 <= k <= 7, distinct per row.
    Needs numpy (offline tools only); the bot itself uses the pure-Python _rank_ints."""
    ranks = cards >> 2
    suits = cards & 3
    rbits = np.left_shift(1, ranks).astype(np.int64)

    counts = (ranks[:, :, None] == _AR13).sum(axis=1)                 # (N, 13)
    m1 = (counts == 1).astype(np.int64) @ _PW
    m2 = (counts == 2).astype(np.int64) @ _PW
    m3 = (counts == 3).astype(np.int64) @ _PW
    m4 = (counts == 4).astype(np.int64) @ _PW
    rmask = m1 | m2 | m3 | m4

    fmask = np.zeros(len(cards), dtype=np.int64)
    for s in range(4):
        in_suit = suits == s
        fmask |= np.where(in_suit.sum(axis=1) >= 5, np.where(in_suit, rbits, 0).sum(axis=1), 0)

    sf = _ST[fmask]
    st = _ST[rmask]
    q = _HB[m4]
    t = _HB[m3]
    t_rest = m3 & ~np.left_shift(1, t)
    fh_pair_mask = t_rest | m2
    p1 = _HB[m2]
    p_rest = m2 & ~np.left_shift(1, p1)
    p2 = _HB[p_rest]

    conds = [sf >= 0, m4 > 0, (m3 > 0) & (fh_pair_mask > 0), fmask > 0, st >= 0, m3 > 0, p_rest > 0, m2 > 0]
    vals = [
        (8 << 25) | (np.maximum(sf, 0) << 21),
        (7 << 25) | (q << 21) | _TOP1[rmask & ~np.left_shift(1, q)],
        (6 << 25) | (t << 21) | (_HB[fh_pair_mask] << 17),
        (5 << 25) | _TOP5[fmask],
        (4 << 25) | (np.maximum(st, 0) << 21),
        (3 << 25) | (t << 21) | _TOP2[m1],
        (2 << 25) | (p1 << 21) | (p2 << 17) | _TOP1[rmask & ~np.left_shift(1, p1) & ~np.left_shift(1, p2)],
        (1 << 25) | (p1 << 21) | _TOP3[m1],
    ]
    return np.select(conds, vals, default=_TOP5[m1])


def _rank_ints(cards: list[int]) -> int:
    """Pure-Python twin of _rank_matrix for one hand."""
    counts = [0] * 13
    smask = [0, 0, 0, 0]
    scount = [0, 0, 0, 0]
    for c in cards:
        r, s = c >> 2, c & 3
        counts[r] += 1
        smask[s] |= 1 << r
        scount[s] += 1
    m = [0, 0, 0, 0, 0]
    for r, n in enumerate(counts):
        if n:
            m[n] |= 1 << r
    m1, m2, m3, m4 = m[1], m[2], m[3], m[4]
    rmask = m1 | m2 | m3 | m4
    fmask = 0
    for s in range(4):
        if scount[s] >= 5:
            fmask = smask[s]
    hb, st = _HB_L, _ST_L
    if fmask and st[fmask] >= 0:
        return (8 << 25) | (st[fmask] << 21)
    if m4:
        q = hb[m4]
        return (7 << 25) | (q << 21) | _TOP_L[1][rmask & ~(1 << q)]
    if m3:
        t = hb[m3]
        pair_mask = (m3 & ~(1 << t)) | m2
        if pair_mask:
            return (6 << 25) | (t << 21) | (hb[pair_mask] << 17)
    if fmask:
        return (5 << 25) | _TOP_L[5][fmask]
    if st[rmask] >= 0:
        return (4 << 25) | (st[rmask] << 21)
    if m3:
        return (3 << 25) | (hb[m3] << 21) | _TOP_L[2][m1]
    if m2:
        p1 = hb[m2]
        rest = m2 & ~(1 << p1)
        if rest:
            p2 = hb[rest]
            return (2 << 25) | (p1 << 21) | (p2 << 17) | _TOP_L[1][rmask & ~(1 << p1) & ~(1 << p2)]
        return (1 << 25) | (p1 << 21) | _TOP_L[3][m1]
    return _TOP_L[5][m1]


def rank7(cards: tuple[str, ...]) -> int:
    """Strength of the best 5-card hand within 5-7 cards. Higher is better; equal means a tie."""
    idx = [_idx(c) for c in cards]
    if not 5 <= len(idx) <= 7:
        raise ValueError(f"rank7 needs 5-7 cards, got {len(idx)}")
    if len(set(idx)) != len(idx):
        raise ValueError(f"duplicate cards in {cards!r}")
    return _rank_ints(idx)


# ---------------------------------------------------------------- equity

def _range_lists(opp_range: Range, dead: set[int]) -> tuple[list[tuple[int, int]], list[float]]:
    """Combos and weights of opp_range, minus dead cards and non-positive weights."""
    acc: dict[tuple[int, int], float] = {}
    for (a, b), w in opp_range.items():
        if not w or w <= 0:
            continue
        ia, ib = _idx(a), _idx(b)
        if ia == ib or ia in dead or ib in dead:
            continue
        key = (ia, ib) if ia > ib else (ib, ia)
        acc[key] = acc.get(key, 0.0) + float(w)
    return list(acc.keys()), list(acc.values())


def equity(hole: tuple[str, str], board: tuple[str, ...], opp_range: Range | None = None,
           samples: int = 2000, time_budget_ms: float | None = None, seed: int | None = None) -> float:
    """P(win) + P(tie)/2 against one opponent hand drawn from opp_range (dead cards removed),
    board completed at random. None = any two cards. Stops early at time_budget_ms.

    A range with no live combos left after removing dead cards is treated as any two cards.
    On the river the result is exact (every live opponent combo is enumerated; samples is ignored).
    Pure Python (no numpy), so it runs in the tournament sandbox.
    """
    t0 = time.perf_counter()
    hero = [_idx(c) for c in hole]
    brd = [_idx(c) for c in board]
    if len(hero) != 2:
        raise ValueError(f"hole must have 2 cards, got {len(hero)}")
    if len(brd) not in (0, 3, 4, 5):
        raise ValueError(f"board must have 0, 3, 4 or 5 cards, got {len(brd)}")
    dead = set(hero + brd)
    if len(dead) != len(hero) + len(brd):
        raise ValueError(f"duplicate cards in hole {hole!r} / board {board!r}")

    live = [c for c in range(52) if c not in dead]
    combos: list[tuple[int, int]] | None = None
    weights: list[float] = []
    if opp_range is not None:
        combos, weights = _range_lists(opp_range, dead)
        if not combos:
            combos = None

    need = 5 - len(brd)
    rank = _rank_ints
    if need == 0:  # river: exact
        h = rank(hero + brd)
        if combos is None:
            combos = list(combinations(live, 2))
            weights = [1.0] * len(combos)
        score = total = 0.0
        for (a, b), w in zip(combos, weights):
            o = rank([a, b] + brd)
            score += w if h > o else (w / 2 if h == o else 0.0)
            total += w
        return score / total

    rng = random.Random(seed)
    sample = rng.sample
    base = hero + brd
    total_n = max(1, int(samples))
    done = 0
    score = 0.0
    cum = None
    if combos is not None:
        cum, acc = [], 0.0
        for w in weights:
            acc += w
            cum.append(acc)
    chunk = 250
    while done < total_n:
        n = min(chunk, total_n - done)
        if combos is None:
            for _ in range(n):
                d = sample(live, need + 2)
                ext = d[2:]
                h = rank(base + ext)
                o = rank(d[:2] + brd + ext)
                score += 1.0 if h > o else (0.5 if h == o else 0.0)
        else:
            top = cum[-1]
            for _ in range(n):
                a, b = combos[bisect_right(cum, rng.random() * top)] if len(combos) > 1 else combos[0]
                d = sample(live, need + 2)
                ext = [c for c in d if c != a and c != b][:need]
                h = rank(base + ext)
                o = rank([a, b] + brd + ext)
                score += 1.0 if h > o else (0.5 if h == o else 0.0)
        done += n
        if time_budget_ms is not None and (time.perf_counter() - t0) * 1000.0 >= time_budget_ms:
            break
    return score / done


# ---------------------------------------------------------------- ranges

def _class_combos(cls: str) -> list[tuple[str, str]]:
    """All combos of a hand class: "AKs", "AKo", "AK" (both), "77"."""
    if not isinstance(cls, str) or len(cls) not in (2, 3):
        raise ValueError(f"invalid hand class {cls!r}")
    r1, r2, kind = cls[0], cls[1], cls[2:]
    if r1 not in RANKS or r2 not in RANKS or kind not in ("", "s", "o"):
        raise ValueError(f"invalid hand class {cls!r}")
    if r1 == r2:
        if kind:
            raise ValueError(f"invalid hand class {cls!r}: pairs take no suffix")
        cards = [_idx(r1 + s) for s in SUITS]
        return [_canon(a, b) for i, a in enumerate(cards) for b in cards[i + 1:]]
    out = []
    for s1 in SUITS:
        for s2 in SUITS:
            suited = s1 == s2
            if (kind == "s" and not suited) or (kind == "o" and suited):
                continue
            out.append(_canon(_idx(r1 + s1), _idx(r2 + s2)))
    return out


def range_from_classes(classes: dict[str, float]) -> Range:
    """{"AKs": 1.0, "22": 0.5} -> combos. Later entries overwrite earlier ones for the same combo."""
    rng: Range = {}
    for cls, w in classes.items():
        for combo in _class_combos(cls):
            rng[combo] = float(w)
    return rng


# Loaded once at import, so `arena pack` captures it by value: files on disk aren't available at play time.
_PREFLOP_RANKING_CLASSES: tuple[str, ...] = ('AA', 'KK', 'QQ', 'JJ', 'TT', '99', '88', 'AKs', '77', 'AQs', 'AJs', 'AKo', 'ATs', 'AQo', 'AJo', 'KQs', '66', 'A9s', 'ATo', 'KJs', 'KTs', 'A8s', 'KQo', 'A7s', 'A9o', 'KJo', '55', 'QJs', 'A6s', 'K9s', 'KTo', 'A8o', 'A5s', 'QTs', 'A4s', 'A7o', 'K8s', 'QJo', 'A3s', 'K9o', 'A6o', 'Q9s', 'JTs', 'A5o', 'QTo', 'K7s', '44', 'A2s', 'K6s', 'A4o', 'K8o', 'Q8s', 'J9s', 'K5s', 'A3o', 'Q9o', 'JTo', 'K7o', 'K4s', 'A2o', 'K6o', 'Q7s', 'T9s', 'J8s', 'K3s', '33', 'Q6s', 'Q8o', 'J9o', 'K5o', 'K2s', 'Q5s', 'T8s', 'J7s', 'K4o', 'Q4s', 'T9o', 'Q7o', 'J8o', 'K3o', 'Q6o', 'Q3s', '98s', 'J6s', 'T7s', '22', 'K2o', 'Q2s', 'Q5o', 'T8o', 'J5s', 'J7o', 'T6s', 'Q4o', '97s', 'J4s', 'J3s', '98o', 'Q3o', 'J6o', 'T7o', '87s', 'T5s', '96s', 'J2s', 'Q2o', 'J5o', 'T4s', 'T6o', '97o', '86s', 'J4o', 'T3s', '95s', '76s', 'J3o', 'T2s', '87o', '85s', '96o', 'T5o', 'J2o', '94s', '75s', 'T4o', '93s', '86o', '65s', 'T3o', '95o', '84s', '92s', '76o', 'T2o', '74s', '85o', '54s', '64s', '83s', '94o', '75o', '82s', '73s', '93o', '65o', '53s', '63s', '84o', '92o', '43s', '74o', '72s', '54o', '64o', '52s', '62s', '83o', '82o', '42s', '73o', '53o', '63o', '32s', '43o', '72o', '52o', '62o', '42o', '32o')


def _preflop_ranking() -> tuple[str, ...]:
    return _PREFLOP_RANKING_CLASSES


def top_percent_range(pct: float) -> Range:
    """Strongest pct (0-1) of the 1326 starting combos by preflop equity vs a random hand.

    The boundary class gets a fractional weight so the total weight is exactly pct * 1326.
    """
    pct = min(max(float(pct), 0.0), 1.0)
    target = pct * 1326
    rng: Range = {}
    acc = 0.0
    for cls in _preflop_ranking():
        if acc >= target:
            break
        combos = _class_combos(cls)
        w = min(1.0, (target - acc) / len(combos))
        for combo in combos:
            rng[combo] = w
        acc += w * len(combos)
    return rng
