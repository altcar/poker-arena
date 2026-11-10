"""
Fast postflop hand strength, draw potential, and Monte Carlo equity evaluator.
"""

import random
from collections import Counter
from card_utils import Card, evaluate, full_deck, RANKS, SUITS


class Evaluator:
    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)
        self.all_cards = full_deck()

    def analyze_made_hand(self, hand: list[Card], board: list[Card]) -> dict:
        """
        Analyzes the made hand: category (0..8), whether it connects with hole cards,
        overpair, top pair, trips/set, two pair, kicker strength.
        """
        if not board:
            return {"category": 0, "rank": (0,), "is_made": False}

        full_hand = hand + board
        rank = evaluate(full_hand)
        cat = rank[0]

        board_ranks = [c.rank for c in board]
        board_vals = [c.value for c in board]
        hand_vals = [c.value for c in hand]
        max_board_val = max(board_vals)

        # Does the hand use our hole cards?
        uses_hole = (
            hand[0].rank == hand[1].rank
            or any(c.rank in board_ranks for c in hand)
            or cat >= 4  # straight, flush, etc.
        )

        is_pocket_pair = (hand[0].rank == hand[1].rank)
        is_overpair = is_pocket_pair and hand[0].value > max_board_val
        is_top_pair = any(c.value == max_board_val for c in hand) and not is_overpair

        # Set vs Trips
        is_set = is_pocket_pair and any(c.value == hand[0].value for c in board)
        is_trips = (cat == 3) and not is_set

        # Two pair analysis
        is_two_pair = (cat == 2)
        # Check if both pairs use hole cards or one pair on board
        board_pair_count = len(board_vals) - len(set(board_vals))
        strong_two_pair = is_two_pair and (board_pair_count == 0 or (is_top_pair and uses_hole))

        return {
            "category": cat,
            "rank": rank,
            "uses_hole": uses_hole,
            "is_overpair": is_overpair,
            "is_top_pair": is_top_pair,
            "is_set": is_set,
            "is_trips": is_trips,
            "is_two_pair": is_two_pair,
            "strong_two_pair": strong_two_pair,
            "max_board_val": max_board_val,
            "hand_max_val": max(hand_vals),
        }

    def analyze_draws(self, hand: list[Card], board: list[Card]) -> dict:
        """
        Detects flush draws, open-ended straight draws (OESD), gutshots, and overcards.
        """
        if len(board) < 3 or len(board) >= 5:
            return {
                "flush_draw": False,
                "oesd": False,
                "gutshot": False,
                "overcards": 0,
                "outs": 0,
            }

        all_cards = hand + board
        board_suits = [c.suit for c in board]
        all_suits = [c.suit for c in all_cards]

        # Flush draw: 4 cards of same suit, at least 1 in our hand
        flush_draw = False
        for s in SUITS:
            if all_suits.count(s) == 4 and any(c.suit == s for c in hand):
                flush_draw = True
                break

        # Straight draw detection
        all_vals = set(c.value for c in all_cards)
        if 14 in all_vals:
            all_vals.add(1)  # Ace low for A-2-3-4-5

        oesd = False
        gutshot = False
        for low in range(1, 11):
            window = set(range(low, low + 5))
            match = len(all_vals & window)
            if match == 4:
                missing = (window - all_vals).pop()
                if missing in (low, low + 4) and 1 < low < 10:
                    oesd = True
                else:
                    gutshot = True

        # Overcards: hole cards higher than highest board card
        max_board = max(c.value for c in board)
        overcards = sum(1 for c in hand if c.value > max_board)

        # Estimate outs
        outs = 0
        if flush_draw:
            outs += 9
        if oesd:
            outs += 8
        elif gutshot:
            outs += 4
        if not flush_draw and not oesd and overcards > 0:
            outs += overcards * 3

        return {
            "flush_draw": flush_draw,
            "oesd": oesd,
            "gutshot": gutshot,
            "overcards": overcards,
            "outs": min(outs, 15),
        }

    def monte_carlo_equity(
        self,
        hand: list[Card],
        board: list[Card],
        num_samples: int = 100,
        opp_range_filter: float = 0.0,
    ) -> float:
        """
        Computes Monte Carlo showdown equity against an opponent range.
        opp_range_filter: if > 0.0, filters out bottom fraction of opponent hands.
        """
        known = hand + board
        unseen = [c for c in self.all_cards if c not in known]
        missing_board = 5 - len(board)

        wins = 0.0
        trials = 0

        for _ in range(num_samples):
            # Sample 2 opp cards + missing board cards
            sample = self.rng.sample(unseen, 2 + missing_board)
            opp_hole = sample[:2]
            runout_board = board + sample[2:]

            # Optional opponent range filtering (e.g. if opp raised preflop)
            if opp_range_filter > 0.0:
                opp_max = max(opp_hole[0].value, opp_hole[1].value)
                is_opp_pair = (opp_hole[0].rank == opp_hole[1].rank)
                is_opp_suited = (opp_hole[0].suit == opp_hole[1].suit)
                # discard bottom unplayable garbage when opp is tight/raiser
                if not is_opp_pair and not is_opp_suited and opp_max < 9:
                    continue

            my_rank = evaluate(hand + runout_board)
            opp_rank = evaluate(opp_hole + runout_board)

            if my_rank > opp_rank:
                wins += 1.0
            elif my_rank == opp_rank:
                wins += 0.5
            trials += 1

            if trials >= num_samples:
                break

        return wins / max(1, trials)
