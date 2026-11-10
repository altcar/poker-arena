"""
Postflop information abstraction: hole cards + board -> a discrete bucket.

A bucket is (made, draw, texture), indexes into MADE, DRAWS and TEXTURES:

    made     what the hand is now, counting only what the hole cards add
             (a pair on the board is everyone's pair)
    draw     the best draw that uses a hole card (flop and turn only)
    texture  the board: flush level (0 none, 1 flush draw possible, 2 flush
             possible) x paired

Cards are hand_eval ints (0..51). bucket_data.py (built offline by
build_buckets.py) holds each bucket's average equity; Model looks it up.
Pure Python, no validation: about 10-20 us per call.
"""

from hand_eval import score, straight_high

MADE = (
    "monster",               # straight flush, quads, full house
    "nut_flush",
    "flush",
    "straight",
    "set",                   # pocket pair + one board card
    "trips",                 # one hole card + a board pair
    "two_pair",              # both hole cards pair the board
    "overpair",
    "top_pair_top_kicker",
    "top_pair_good_kicker",  # kicker T or better
    "top_pair_weak_kicker",
    "middle_pair",
    "bottom_pair",           # includes pocket pairs below the 2nd card
    "ace_high",
    "overcards",             # both hole cards above the board
    "trash",
)
DRAWS = (
    "none",
    "gutshot",
    "oesd",                  # open-ended or double gutter: 8 outs
    "flush_draw",
    "nut_flush_draw",
    "combo_draw",            # flush draw + straight draw
)
TEXTURES = (
    "rainbow", "rainbow_paired",
    "two_tone", "two_tone_paired",
    "flushy", "flushy_paired",
)

M = {name: i for i, name in enumerate(MADE)}
D = {name: i for i, name in enumerate(DRAWS)}
STREETS = {3: 0, 4: 1, 5: 2}  # board size -> street index (flop/turn/river)
NUM_KEYS = 3 * len(MADE) * len(DRAWS) * len(TEXTURES)

# Made hands at least this strong (index <= ...) count as betting for value;
# draws in VALUE_DRAWS too. CONTINUE_*: what keeps playing against a bet.
VALUE_MADE = M["top_pair_weak_kicker"]
VALUE_DRAWS = frozenset({D["nut_flush_draw"], D["combo_draw"]})
CONTINUE_MADE = M["bottom_pair"]
CONTINUE_DRAWS = frozenset({
    D["flush_draw"], D["nut_flush_draw"], D["combo_draw"], D["oesd"],
})


def bucket_key(street: int, made: int, draw: int, texture: int) -> int:
    """One int per (street, made, draw, texture), 0..NUM_KEYS-1."""
    return ((street * len(MADE) + made) * len(DRAWS) + draw) * len(
        TEXTURES) + texture


def bucket(hole, board):
    """(made, draw, texture) for two hole cards and 3 to 5 board cards."""
    h1, h2 = hole
    r1, r2 = h1 >> 2, h2 >> 2
    nb = len(board)

    bcount = [0] * 13
    bsuit = [0, 0, 0, 0]
    bmask = 0
    for c in board:
        r = c >> 2
        bcount[r] += 1
        bsuit[c & 3] += 1
        bmask |= 1 << r
    top_count = max(bcount)
    paired = top_count >= 2

    full = score([h1, h2] + list(board))
    cat = full >> 20
    plays_board = False
    if nb == 5:
        board_score = score(board)
        plays_board = full == board_score  # the hole cards add nothing
        board_cat = board_score >> 20 if full > board_score else cat
    elif top_count == 4:
        board_cat = 7
    elif top_count == 3:
        board_cat = 3
    else:
        board_cat = sum(1 for n in bcount if n == 2)  # 0, 1 or 2 pairs

    # Board ranks, distinct, highest first.
    ranks_desc = [r for r in range(12, -1, -1) if bcount[r]]
    made = -1
    if cat > board_cat:
        if cat >= 6:
            made = M["monster"]
        elif cat == 5:
            made = M["nut_flush"] if _nut_flush(h1, h2, board) else M["flush"]
        elif cat == 4:
            made = M["straight"]
        elif cat == 3:
            made = M["set"] if r1 == r2 else M["trips"]
        elif cat == 2 and r1 != r2 and bcount[r1] and bcount[r2]:
            made = M["two_pair"]
        if made < 0:
            made = _pair(r1, r2, bcount, ranks_desc)
    if made < 0 and plays_board:
        made = M["trash"]
    if made < 0 and r1 == r2:
        # A pocket pair on a double-paired board adds no category but is
        # still a pair (e.g. AA on KK55).
        made = _pair(r1, r2, bcount, ranks_desc)
    if made < 0:
        if r1 == 12 or r2 == 12:
            made = M["ace_high"]
        elif min(r1, r2) > ranks_desc[0]:
            made = M["overcards"]
        else:
            made = M["trash"]

    draw = D["none"]
    if nb < 5:
        draw = _draw(h1, h2, board, bsuit, bmask, made)

    max_suit = max(bsuit)
    if max_suit >= 3:
        flush_level = 2
    elif max_suit == 2 and nb < 5:
        flush_level = 1
    else:
        flush_level = 0
    return made, draw, flush_level * 2 + (1 if paired else 0)


def _pair(r1, r2, bcount, ranks_desc) -> int:
    """The pair bucket a hole card makes, or -1 when none does."""
    top = ranks_desc[0]
    second = ranks_desc[1] if len(ranks_desc) > 1 else -1
    if r1 == r2:
        if r1 > top:
            return M["overpair"]
        if r1 > second:
            return M["middle_pair"]
        return M["bottom_pair"]
    matched = [r for r in (r1, r2) if bcount[r]]
    if not matched:
        return -1
    r = max(matched)
    if r == top:
        kicker = r2 if r == r1 else r1
        best = next(k for k in range(12, -1, -1) if not bcount[k])
        if kicker == best:
            return M["top_pair_top_kicker"]
        if kicker >= 8:  # T or better
            return M["top_pair_good_kicker"]
        return M["top_pair_weak_kicker"]
    if r == second:
        return M["middle_pair"]
    return M["bottom_pair"]


def _nut_flush(h1, h2, board) -> bool:
    """True when our flush is the best possible flush in its suit."""
    suits = [0, 0, 0, 0]
    for c in (h1, h2, *board):
        suits[c & 3] += 1
    s = max(range(4), key=lambda x: suits[x])
    on_board = {c >> 2 for c in board if c & 3 == s}
    ours = [c >> 2 for c in (h1, h2) if c & 3 == s]
    nut = next(r for r in range(12, -1, -1) if r not in on_board)
    return bool(ours) and max(ours) == nut


def _draw(h1, h2, board, bsuit, bmask, made) -> int:
    flush_draw = nut_draw = False
    if made > M["flush"]:  # no flush yet
        for s in range(4):
            mine = [c >> 2 for c in (h1, h2) if c & 3 == s]
            if mine and bsuit[s] + len(mine) == 4:
                flush_draw = True
                on_board = {c >> 2 for c in board if c & 3 == s}
                nut = next(r for r in range(12, -1, -1) if r not in on_board)
                if max(mine) == nut:
                    nut_draw = True
    outs = 0
    if made > M["straight"]:
        mask = bmask | (1 << (h1 >> 2)) | (1 << (h2 >> 2))
        for r in range(13):
            bit = 1 << r
            if mask & bit:
                continue
            # The straight must need a hole card: not one the board makes.
            if straight_high(mask | bit) >= 0 and straight_high(bmask | bit) < 0:
                outs += 1
    straight_draw = D["oesd"] if outs >= 2 else D["gutshot"] if outs else 0
    if flush_draw and straight_draw:
        return D["combo_draw"]
    if nut_draw:
        return D["nut_flush_draw"]
    if flush_draw:
        return D["flush_draw"]
    return straight_draw


def name(made: int, draw: int, texture: int) -> str:
    """Readable bucket, e.g. "top_pair_top_kicker+gutshot@two_tone"."""
    out = MADE[made]
    if draw:
        out += "+" + DRAWS[draw]
    return out + "@" + TEXTURES[texture]
