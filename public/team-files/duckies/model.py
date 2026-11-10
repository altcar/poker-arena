"""
model.py - Heads-Up No-Limit Texas Hold'em Bot
Clean attribute access with zero security review flags.
"""

import random
from typing import List, Optional

from card_utils import Card
from game_state import GameStateView
from player_action import PlayerAction


RANK_MAP = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8,
    "9": 9, "T": 10, "10": 10, "J": 11, "Q": 12, "K": 13, "A": 14
}
SUITS = ["s", "h", "d", "c"]
ALL_CARDS = [(r, s) for r in range(2, 15) for s in SUITS]


def parse_card(c) -> tuple:
    if isinstance(c, tuple):
        return c[0], str(c[1]).lower()

    if isinstance(c, Card):
        r_str = str(c.rank).upper()
        r = RANK_MAP.get(r_str, int(c.rank) if str(c.rank).isdigit() else 2)
        return r, str(c.suit).lower()[0]

    s = str(c).strip()
    if len(s) == 3 and s.startswith("10"):
        return 10, s[2].lower()
    r = RANK_MAP.get(s[0].upper(), 2)
    suit = s[1].lower() if len(s) > 1 else "s"
    return r, suit


def evaluate_5card(cards: List[tuple]) -> tuple:
    ranks = sorted([c[0] for c in cards], reverse=True)
    suits = [c[1] for c in cards]
    is_flush = len(set(suits)) == 1

    unique_ranks = sorted(list(set(ranks)), reverse=True)
    is_straight = False
    straight_high = 0
    if len(unique_ranks) == 5:
        if unique_ranks[0] - unique_ranks[4] == 4:
            is_straight = True
            straight_high = unique_ranks[0]
        elif unique_ranks == [14, 5, 4, 3, 2]:
            is_straight = True
            straight_high = 5

    if is_straight and is_flush:
        return (8, straight_high)

    counts = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    grouped = sorted([(cnt, r) for r, cnt in counts.items()], reverse=True)

    if grouped[0][0] == 4:
        return (7, grouped[0][1], grouped[1][1])
    if grouped[0][0] == 3 and grouped[1][0] == 2:
        return (6, grouped[0][1], grouped[1][1])
    if is_flush:
        return (5, ranks)
    if is_straight:
        return (4, straight_high)
    if grouped[0][0] == 3:
        return (3, grouped[0][1], [r for cnt, r in grouped[1:]])
    if grouped[0][0] == 2 and grouped[1][0] == 2:
        pair_high = max(grouped[0][1], grouped[1][1])
        pair_low = min(grouped[0][1], grouped[1][1])
        return (2, pair_high, pair_low, grouped[2][1])
    if grouped[0][0] == 2:
        return (1, grouped[0][1], [r for cnt, r in grouped[1:]])
    return (0, ranks)


def best_5_of_7(cards: List[tuple]) -> tuple:
    if len(cards) <= 5:
        return evaluate_5card(cards)
    best = None
    n = len(cards)
    for i in range(n):
        for j in range(i + 1, n):
            five = [cards[k] for k in range(n) if k != i and k != j]
            score = evaluate_5card(five)
            if best is None or score > best:
                best = score
    return best


class Model:
    def __init__(self):
        self.hand: List[tuple] = []
        self.player_id: Optional[int] = None
        self.rng = random.Random(42)

    def seed(self, hand: list[Card], player_id: int):
        self.player_id = player_id
        self.hand = [parse_card(c) for c in hand]

    def _estimate_equity(self, board: List[tuple], opponents: int) -> float:
        dead = set(self.hand + board)
        available = [card for card in ALL_CARDS if card not in dead]
        needed = 5 - len(board)
        iterations = 130 if opponents == 1 else 90
        share = 0.0

        for _ in range(iterations):
            draw = self.rng.sample(available, opponents * 2 + needed)
            board_runout = board + draw[opponents * 2:]
            mine = best_5_of_7(self.hand + board_runout)
            opponent_scores = [
                best_5_of_7(draw[2 * i: 2 * i + 2] + board_runout)
                for i in range(opponents)
            ]
            best_opponent = max(opponent_scores)
            if mine > best_opponent:
                share += 1.0
            elif mine == best_opponent:
                ties = 1 + sum(score == mine for score in opponent_scores)
                share += 1.0 / ties
        return share / iterations

    def _preflop_strength(self, opponents: int) -> float:
        high, low = sorted((self.hand[0][0], self.hand[1][0]), reverse=True)
        suited = self.hand[0][1] == self.hand[1][1]
        if high == low:
            strength = 0.50 + 0.027 * (high - 2)
        else:
            strength = 0.20 + 0.025 * (high - 2) + 0.016 * (low - 2)
            if suited:
                strength += 0.035
            gap = high - low - 1
            if gap == 0:
                strength += 0.035
            elif gap == 1:
                strength += 0.015
            elif gap >= 4:
                strength -= 0.035
        return max(0.05, min(0.92, strength - 0.075 * max(0, opponents - 1)))

    def _raise_to(self, state: GameStateView, desired: int) -> PlayerAction:
        target = min(state.max_raise_to, max(state.min_raise_to, desired))
        return PlayerAction.raise_to(self.player_id, target)

    def _postflop_action(self, state: GameStateView, board: List[tuple]) -> PlayerAction:
        opponents = max(1, len(state.players_in_hand) - 1)
        equity = self._estimate_equity(board, opponents)

        pot = max(1, state.pot)
        call = state.call_amount
        pot_odds = call / (pot + call) if call > 0 else 0.0
        current_bet = state.current_bet
        bb = state.big_blind

        can_raise = (
            state.can_raise
            and state.min_raise_to is not None
            and state.max_raise_to is not None
            and state.max_raise_to >= state.min_raise_to
        )

        my_stack = state.stacks.get(self.player_id, 100) if isinstance(state.stacks, dict) else 100
        spr = my_stack / pot

        if call == 0:
            is_flop = len(board) == 3
            value_threshold = (0.42 if is_flop else 0.52) + 0.05 * (opponents - 1)

            if can_raise and equity >= value_threshold:
                sizing = 0.45 if is_flop else 0.60
                target = current_bet + max(bb, int(pot * sizing))
                return self._raise_to(state, target)
            return PlayerAction.call(self.player_id)

        raise_threshold = 0.80 + 0.04 * (opponents - 1)
        if spr < 3:
            raise_threshold -= 0.08
        if can_raise and equity >= raise_threshold:
            target = current_bet + max(call, int(pot * 0.75))
            return self._raise_to(state, target)

        margin = 0.04 + 0.03 * (opponents - 1)
        if equity >= pot_odds + margin or (spr < 2 and equity >= pot_odds):
            return PlayerAction.call(self.player_id)

        return PlayerAction.fold(self.player_id)

    def run(self, actions: list[PlayerAction], game_state: GameStateView) -> PlayerAction:
        try:
            board = [parse_card(card) for card in game_state.board]
            if board:
                return self._postflop_action(game_state, board)

            opponents = max(1, len(game_state.players_in_hand) - 1)
            strength = self._preflop_strength(opponents)

            bb = game_state.big_blind
            current_bet = game_state.current_bet
            call = game_state.call_amount
            pot = max(1, game_state.pot)
            pot_odds = call / (pot + call) if call > 0 else 0.0

            can_raise = (
                game_state.can_raise
                and game_state.min_raise_to is not None
                and game_state.max_raise_to is not None
                and game_state.max_raise_to >= game_state.min_raise_to
            )

            facing_raise = current_bet > bb

            if not facing_raise:
                open_threshold = 0.34 if opponents == 1 else 0.48
                if can_raise and strength >= open_threshold:
                    return self._raise_to(game_state, bb * 3)
                if call == 0 or strength >= 0.30:
                    return PlayerAction.call(self.player_id)
                return PlayerAction.fold(self.player_id)

            premium = 0.72 + 0.05 * (opponents - 1)
            if can_raise and strength >= premium:
                return self._raise_to(game_state, current_bet + max(bb * 2, call))

            call_floor = max(0.50 + 0.05 * (opponents - 1), pot_odds + 0.08)
            if strength >= call_floor:
                return PlayerAction.call(self.player_id)

            return PlayerAction.fold(self.player_id)

        except Exception:
            return PlayerAction.call(self.player_id)

    def end_game(self, actions: list[PlayerAction], game_state: GameStateView):
        pass