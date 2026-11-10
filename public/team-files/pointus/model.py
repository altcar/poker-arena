import random

from card_utils import Card
from game_state import GameStateView
from player_action import PlayerAction

RANKS = "23456789TJQKA"
RANK_VALUE = {ch: i + 2 for i, ch in enumerate(RANKS)}

# Hand types to shove. Format: two ranks + "s" (suited) or "o" (offsuit).
# Pairs are written with "o", for example "AAo".
GOOD_HANDS = [
    # pairs 55 and up
    "AAo", "KKo", "QQo", "JJo", "TTo", "99o", "88o", "77o", "66o", "55o",
    # suited aces
    "AKs", "AQs", "AJs",
    # suited broadway
    "KQs",
    # offsuit aces
    "AKo", "AQo", "AJo",
]

MAX_CALL = 31           # bad hands fold if the call is above this
PAIR_MAX_CALL = 51     # pair-or-better postflop plays on if the call is at most this
# Call limits that scale with the pot (used POT_SCALE_CHANCE of the time).
# The old MAX_CALL / PAIR_MAX_CALL stay as a floor, so these can only raise the limits.
POT_SCALE_CHANCE = 0.85
MAX_CALL_POT_FRAC = 0.4        # bad hands call up to 50% of the pot
PAIR_MAX_CALL_POT_FRAC = 1.0   # pair-or-better calls up to 100% of the pot
# The other 20% of the time, pick a limit from these fixed sets instead.
RANDOM_MAX_CALLS = (21, 31, 41, 61, 81, 101)
RANDOM_PAIR_MAX_CALLS = (31, 51, 81, 101, 151, 201)

RANDOM_RAISE_CHANCE = 0.15
RANDOM_RAISE_SIZES = (111, 101, 91, 67, 51, 51, 51, 31, 21, 31, 31, 31, 16)

# Facing a bet that is at least this fraction of our total chips counts as a shove.
SHOVE_FRACTION = 0.4


def parse_hand(code):
    a, b, kind = code[0], code[1], code[2]
    hi = max(RANK_VALUE[a], RANK_VALUE[b])
    lo = min(RANK_VALUE[a], RANK_VALUE[b])
    return (hi, lo, kind == "s")


GOOD_SET = {parse_hand(code) for code in GOOD_HANDS}

FACE_RANKS = {"T": 10, "J": 11, "Q": 12, "K": 13, "A": 14}


def card_rank(card) -> int:
    """Return a card's rank as a number (2-14)."""
    r = card.rank
    if r in FACE_RANKS:
        return FACE_RANKS[r]
    return int(r)


def wide_call_hand(hole) -> bool:
    """Rough 'beats a random hand ~50%+' range, used only when facing a shove."""
    r1, r2 = card_rank(hole[0]), card_rank(hole[1])
    hi, lo = max(r1, r2), min(r1, r2)
    suited = hole[0].suit == hole[1].suit
    if hi == lo:               # any pair
        return True
    if hi >= 13:               # any ace or king
        return True
    if hi == 12:               # queens
        return suited or lo >= 6
    if hi == 11:               # jacks
        return lo >= 8 or (suited and lo >= 6)
    if hi == 10:               # tens
        return lo >= 8 or (suited and lo >= 7)
    return suited and hi >= 9 and lo >= 7   # 98s, 97s


def has_pair(hole, board) -> bool:
    """True for a pocket pair, or when a hole card matches a board card."""
    hole_ranks = [card_rank(c) for c in hole]
    if len(hole_ranks) == 2 and hole_ranks[0] == hole_ranks[1]:
        return True
    board_ranks = {card_rank(c) for c in board}
    return any(r in board_ranks for r in hole_ranks)


def made_hand(hole, board) -> bool:
    """True if hole cards + board make trips, a straight, a flush, a full house
    or quads, and at least one hole card is part of it."""
    cards = list(hole) + list(board)
    hole_ranks = {card_rank(c) for c in hole}

    counts = {}
    for c in cards:
        r = card_rank(c)
        counts[r] = counts.get(r, 0) + 1

    # Quads or trips that include a hole card
    for r, n in counts.items():
        if n >= 3 and r in hole_ranks:
            return True

    # Full house made of board trips plus a hole-card pair
    has_board_trips = any(n >= 3 for n in counts.values())
    if has_board_trips and any(n >= 2 and r in hole_ranks for r, n in counts.items()):
        return True

    # Flush that includes a hole card
    suits = {}
    for c in cards:
        suits[c.suit] = suits.get(c.suit, 0) + 1
    for s, n in suits.items():
        if n >= 5 and any(c.suit == s for c in hole):
            return True

    # Straight that includes a hole card (ace counts high and low)
    rank_set = set(counts)
    if 14 in rank_set:
        rank_set.add(1)
    hole_set = set(hole_ranks)
    if 14 in hole_set:
        hole_set.add(1)
    for low in range(1, 11):
        window = set(range(low, low + 5))
        if window <= rank_set and window & hole_set:
            return True

    return False


class Model:
    """
    Postflop: all in with trips or better (using a hole card). With a pair and
    a call of 100 or less, raise an extra 20 (sometimes a random 100/50/25/10),
    else call.
    Good preflop hands: shove when a raise is possible, otherwise call.
    Bad preflop hands: when facing a shove, call with the wide range, else fold.
    Otherwise, when the call is under 20, raise an extra 20 (else 10)
    if legal, otherwise call. Call exactly 20, fold above 20.
    """

    def __init__(self):
        self.hand = []
        self.player_id = None
        self.fold_hand = False

    def seed(self, hand: list[Card], player_id: int):
        self.hand = hand
        self.player_id = player_id
        self.fold_hand = False
        try:
            c1, c2 = hand
            r1, r2 = card_rank(c1), card_rank(c2)
            suited = c1.suit == c2.suit
            key = (max(r1, r2), min(r1, r2), suited)
            self.fold_hand = key not in GOOD_SET
        except Exception:
            self.fold_hand = False

    def call_limits(self, game_state):
        """Return (max_call, pair_max_call) for this decision.

        80% of the time both scale with the pot (never below the old constants).
        Otherwise both come from the fixed random sets.
        """
        if random.random() < POT_SCALE_CHANCE:
            pot = game_state.pot
            max_call = max(MAX_CALL, int(MAX_CALL_POT_FRAC * pot))
            pair_max_call = max(PAIR_MAX_CALL, int(PAIR_MAX_CALL_POT_FRAC * pot))
            return max_call, pair_max_call
        return random.choice(RANDOM_MAX_CALLS), random.choice(RANDOM_PAIR_MAX_CALLS)

    def min_raise(self, game_state):
        """Smallest legal raise_to total."""
        try:
            return int(game_state.min_raise_to)
        except Exception:
            pass
        try:
            return int(game_state.current_bet + game_state.last_raise)
        except Exception:
            return int(game_state.current_bet) + 20

    def can_raise_to(self, game_state, total) -> bool:
        me = self.player_id
        if total <= game_state.current_bet:
            return False
        if total < self.min_raise(game_state):
            return False
        if total > game_state.bets[me] + game_state.stacks[me]:
            return False
        others = [p for p in game_state.active_players if p != me]
        return len(others) > 0

    def all_in_total(self, game_state):
        """Return the raise_to amount for an all-in, or None if we cannot raise."""
        me = self.player_id
        if game_state.stacks[me] <= game_state.call_amount:
            return None
        total = int(game_state.bets[me] + game_state.stacks[me])
        if self.can_raise_to(game_state, total):
            return total
        return None

    def pair_play(self, game_state):
        """Pair or better, call at most 100: raise extra 20 (or a random size), else call."""
        me = self.player_id
        base = int(game_state.current_bet)
        sizes = [20]
        if random.random() < RANDOM_RAISE_CHANCE:
            sizes = list(RANDOM_RAISE_SIZES)
            random.shuffle(sizes)
            sizes.append(20)  # fall back to 20 if the random sizes are not legal
        for extra in sizes:
            target = base + extra
            if self.can_raise_to(game_state, target):
                return PlayerAction.raise_to(me, target)
        return PlayerAction.call(me)

    def run(self, actions: list[PlayerAction], game_state: GameStateView) -> PlayerAction:
        try:
            postflop = len(game_state.board) >= 3
            call = game_state.call_amount
            max_call, pair_max_call = self.call_limits(game_state)

            if postflop:
                # Trips, straight, flush, full house or quads with a hole card
                if made_hand(self.hand, game_state.board):
                    total = self.all_in_total(game_state)
                    if total is not None:
                        return PlayerAction.raise_to(self.player_id, total)
                    return PlayerAction.call(self.player_id)
                # Pair or better, cheap enough to continue
                if has_pair(self.hand, game_state.board) and call <= pair_max_call:
                    return self.pair_play(game_state)

            # Preflop, facing a shove: call with the wide range, fold the rest.
            # Hands in GOOD_HANDS skip this and use the shove/call logic below.
            if not postflop and self.fold_hand:
                me = self.player_id
                total_chips = game_state.stacks[me] + game_state.bets[me]
                facing_shove = call > 0 and call >= SHOVE_FRACTION * total_chips
                if facing_shove:
                    if wide_call_hand(self.hand):
                        return PlayerAction.call(me)
                    return PlayerAction.fold(me)

            if self.fold_hand:
                if call > max_call:
                    return PlayerAction.fold(self.player_id)
                if 0 < call < 20:
                    # raise an extra 20, else an extra 10, on top of the call
                    for extra in (20, 10):
                        target = int(game_state.current_bet) + extra
                        if self.can_raise_to(game_state, target):
                            return PlayerAction.raise_to(self.player_id, target)
                return PlayerAction.call(self.player_id)  # checks if call is 0

            total = self.all_in_total(game_state)
            if total is not None:
                return PlayerAction.raise_to(self.player_id, total)
            return PlayerAction.call(self.player_id)
        except Exception:
            return PlayerAction.call(self.player_id)

    def end_game(self, actions: list[PlayerAction], game_state: GameStateView):
        pass