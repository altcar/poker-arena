import bisect

RANK_CHARS = "23456789TJQKA"
SUIT_CHARS = "CDHS"


def card_to_int(card):
    return RANK_CHARS.index(card.rank) * 4 + SUIT_CHARS.index(card.suit)


def make_straight_table():
    table = [-1] * 8192
    wheel = (1 << 12) | 0b1111
    for mask in range(8192):
        best = -1
        for high in range(12, 3, -1):
            need = 0b11111 << (high - 4)
            if mask & need == need:
                best = high
                break
        if best == -1 and mask & wheel == wheel:
            best = 3
        table[mask] = best
    return table


STRAIGHT_HIGH = make_straight_table()
BIT_COUNT = [bin(mask).count("1") for mask in range(8192)]


def top_ranks(mask, how_many):
    ranks = []
    rank = 12
    while rank >= 0 and len(ranks) < how_many:
        if mask >> rank & 1:
            ranks.append(rank)
        rank -= 1
    return ranks


def pack(category, ranks):
    value = category
    for i in range(5):
        value = value * 16 + (ranks[i] if i < len(ranks) else 0)
    return value


def hand_score(cards):
    counts = [0] * 13
    suit_masks = [0, 0, 0, 0]
    for card in cards:
        rank = card >> 2
        counts[rank] += 1
        suit_masks[card & 3] |= 1 << rank
    for mask in suit_masks:
        if BIT_COUNT[mask] >= 5:
            high = STRAIGHT_HIGH[mask]
            if high >= 0:
                return pack(8, [high])
            return pack(5, top_ranks(mask, 5))
    rank_mask = suit_masks[0] | suit_masks[1] | suit_masks[2] | suit_masks[3]
    quads = []
    trips = []
    pairs = []
    singles = []
    for rank in range(12, -1, -1):
        count = counts[rank]
        if count == 4:
            quads.append(rank)
        elif count == 3:
            trips.append(rank)
        elif count == 2:
            pairs.append(rank)
        elif count == 1:
            singles.append(rank)
    if quads:
        kicker = max(rank for rank in range(13) if counts[rank] and rank != quads[0])
        return pack(7, [quads[0], kicker])
    if trips and (len(trips) > 1 or pairs):
        return pack(6, [trips[0], max(trips[1:] + pairs)])
    high = STRAIGHT_HIGH[rank_mask]
    if high >= 0:
        return pack(4, [high])
    if trips:
        return pack(3, [trips[0]] + singles[:2])
    if len(pairs) >= 2:
        return pack(2, [pairs[0], pairs[1], max(pairs[2:] + singles)])
    if pairs:
        return pack(1, [pairs[0]] + singles[:3])
    return pack(0, singles[:5])


def class_names():
    names = []
    for high in range(12, -1, -1):
        for low in range(high, -1, -1):
            if high == low:
                names.append(RANK_CHARS[high] * 2)
            else:
                names.append(RANK_CHARS[high] + RANK_CHARS[low] + "s")
                names.append(RANK_CHARS[high] + RANK_CHARS[low] + "o")
    return names


NAMES = class_names()


def make_class_combos():
    all_combos = []
    for name in NAMES:
        high = RANK_CHARS.index(name[0])
        low = RANK_CHARS.index(name[1])
        combos = []
        for suit_a in range(4):
            for suit_b in range(4):
                a = high * 4 + suit_a
                b = low * 4 + suit_b
                if high == low:
                    if suit_a < suit_b:
                        combos.append((a, b))
                elif name[2] == "s":
                    if suit_a == suit_b:
                        combos.append((a, b))
                elif suit_a != suit_b:
                    combos.append((a, b))
        all_combos.append(combos)
    return all_combos


CLASS_COMBOS = make_class_combos()
COMBO_CLASS = {}
for class_index, class_combos in enumerate(CLASS_COMBOS):
    for a, b in class_combos:
        COMBO_CLASS[(a, b)] = class_index
        COMBO_CLASS[(b, a)] = class_index


def class_of(card_a, card_b):
    return COMBO_CLASS[(card_a, card_b)]


def equity_vs_range(hero, board, weights, samples, rng, keep_top=1.0, drop_top=0.0):
    dead = set(hero) | set(board)
    combos = []
    cumulative = []
    total = 0.0
    for class_index, weight in enumerate(weights):
        if weight <= 0:
            continue
        for a, b in CLASS_COMBOS[class_index]:
            if a in dead or b in dead:
                continue
            total += weight
            combos.append((a, b))
            cumulative.append(total)
    if not combos:
        return 0.5
    picks = []
    for _ in range(samples):
        spot = bisect.bisect_left(cumulative, rng.random() * total)
        picks.append(combos[min(spot, len(combos) - 1)])
    if (keep_top < 0.999 or drop_top > 0.001) and len(board) >= 3:
        rated = [(hand_score(list(pick) + board), pick) for pick in picks]
        rated.sort(reverse=True)
        keep = max(1, int(len(rated) * keep_top))
        rated = rated[:keep]
        drop = min(len(rated) - 1, int(len(rated) * drop_top))
        picks = [pick for _, pick in rated[drop:]]
    deck = [card for card in range(52) if card not in dead]
    need = 5 - len(board)
    won = 0.0
    for a, b in picks:
        extra = []
        if need:
            for card in rng.sample(deck, need + 2):
                if card != a and card != b:
                    extra.append(card)
                    if len(extra) == need:
                        break
        full_board = board + extra
        mine = hand_score(hero + full_board)
        theirs = hand_score([a, b] + full_board)
        if mine > theirs:
            won += 1
        elif mine == theirs:
            won += 0.5
    return won / len(picks)
