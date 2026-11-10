"""Auditable, dependency-free preflop range charts for 25-BB Hold'em.

These are hand-designed strategy approximations, NOT solver-derived GTO
solutions. All ranges are fixed 169-class hand abstractions and can be
benchmarked or replaced independently of the postflop decision policy.

Chart syntax: "66+" is 66 through AA; "K9s+" is K9s through KQs;
"QTo" is exactly QTo. s = suited, o = offsuit.
"""

RANKS = "23456789TJQKA"


def hand_class(cards):
    """Canonical preflop class: AA, AKs, AKo, 76s, etc."""
    first, second = cards
    a, b = first.rank, second.rank
    if a == b:
        return a + b
    if RANKS.index(a) < RANKS.index(b):
        a, b = b, a
    return a + b + ("s" if first.suit == second.suit else "o")


def expand_range(spec):
    """Expand simple, explicit poker shorthand into canonical hand classes."""
    result = set()
    for raw in spec.split(","):
        token = raw.strip()
        if not token:
            continue
        plus = token.endswith("+")
        base = token[:-1] if plus else token
        if len(base) == 2 and base[0] == base[1]:
            start = RANKS.index(base[0])
            for rank in (RANKS[start:] if plus else base[:1]):
                result.add(rank + rank)
            continue
        if len(base) != 3 or base[2] not in "so":
            raise ValueError("invalid preflop range entry: " + token)
        high, low, kind = base
        if high not in RANKS or low not in RANKS:
            raise ValueError("invalid rank in: " + token)
        hi, lo = RANKS.index(high), RANKS.index(low)
        if hi <= lo:
            raise ValueError("expected higher card first: " + token)
        for lower in (RANKS[lo:hi] if plus else low):
            result.add(high + lower + kind)
    return frozenset(result)


# The SB/button acts first heads-up. Raise a broad but not universal range,
# retaining limps for some weak holdings and folding the weakest offsuit junk.
HU_OPEN = expand_range(
    "22+, A2s+, K2s+, Q2s+, J3s+, T4s+, 93s+, 83s+, "
    "73s+, 63s+, 53s+, 43s, A2o+, K4o+, Q5o+, J6o+, "
    "T6o+, 96o+, 86o+, 75o+, 65o"
)
HU_FOLD_UNOPENED = expand_range("72o, 82o, 92o, 32o, 42o, 52o, 62o")
HU_ISOLATE_LIMP = expand_range(
    "66+, A8s+, KTs+, QTs+, JTs, ATo+, KJo+, QJo"
)

# Facing a normal 2-3 BB open: reraises are primarily for value, and the
# wider calling range preserves playable pairs, suited hands and broadways.
HU_THREE_BET = expand_range("TT+, AJs+, KQs, AQo+")
HU_DEFEND = expand_range(
    "22+, A2s+, K6s+, Q8s+, J8s+, T8s+, 97s+, 87s, 76s, "
    "65s, A7o+, KTo+, QTo+, JTo"
)
HU_VS_THREE_BET = expand_range("TT+, AQs+, AKo")
HU_FOUR_BET_SHOVE = expand_range("QQ+, AKs, AKo")

# A small effective stack makes some hands more profitable as open shoves.
HU_SHOVE_11BB = expand_range(
    "55+, A5s+, KTs+, QJs, A9o+, KQo"
)
HU_SHOVE_6BB = expand_range(
    "22+, A2s+, K7s+, QTs+, JTs, A5o+, KTo+, QJo"
)

# Multiway uses substantially narrower charts. These are not position-
# specific solved ranges; the postflop system and further PRs will refine.
MULTI_OPEN = expand_range(
    "55+, A5s+, KTs+, QTs+, JTs, T9s, 98s, ATo+, KQo"
)
MULTI_THREE_BET = expand_range("JJ+, AQs+, AKo")
MULTI_DEFEND = expand_range(
    "22+, A8s+, KJs+, QJs, JTs, T9s, AJo+, KQo"
)
