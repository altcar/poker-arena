"""Heads-up poker bot with position-aware preflop play and sampled postflop equity.

Only this Model is submitted. No files, network access, engine internals or
non-serialisable attributes are needed at runtime.
"""

import random
from itertools import combinations

from card_utils import Card, evaluate, full_deck
from all_in_equity import expected_call_payout
from opponent_model import OpponentModel
from postflop_policy import decide_postflop
from sidepot_equity import ContributionLedger
from game_state import GameStateView
from player_action import PlayerAction
from preflop_policy import (
    HU_DEFEND,
    HU_FOLD_UNOPENED,
    HU_FOUR_BET_SHOVE,
    HU_ISOLATE_LIMP,
    HU_OPEN,
    HU_SHOVE_6BB,
    HU_SHOVE_11BB,
    HU_THREE_BET,
    HU_VS_THREE_BET,
    MULTI_DEFEND,
    MULTI_OPEN,
    MULTI_THREE_BET,
    hand_class,
)


class Model:
    def __init__(self):
        # Persistent data survives between hands in a match.
        self.hand = []
        self.player_id = None
        self.opponents = {}  # Legacy preflop counters; do not conflate with A1.
        self.opponent_model = OpponentModel()
        self.contribution_ledger = ContributionLedger()
        self._range_cache = {}
        self._decision_index = 0
        self.random_seed = 20261107

        # Hand-local observations, reset by seed().
        self.board_reveals = 0
        self.preflop_raises_seen = 0
        self.opponent_raised_street = None

    def seed(self, hand: list[Card], player_id: int):
        self.hand = list(hand)
        self.player_id = player_id
        # Keep A1 team-name records across hands, but never carry over the
        # previous hand's public action line, ledger or range cache.
        self.opponent_model.new_hand()
        self.contribution_ledger.reset()
        self._range_cache.clear()
        self._decision_index = 0
        self.board_reveals = 0
        self.preflop_raises_seen = 0
        self.opponent_raised_street = None
        self.random_seed = (self.random_seed * 1664525 + 1013904223) & 0xFFFFFFFF

    def _observe(self, actions, game_state):
        """Read only the action stream since our last turn."""
        for action in actions:
            if action.kind == "reveal_card" and action.player_id == -1:
                self.board_reveals += 1
                continue
            # An action stream spans turns. Count every preflop raise,
            # including our own, before ignoring our actions for stats.
            if action.kind == "raise" and self.board_reveals == 0:
                self.preflop_raises_seen += 1
            if action.player_id == self.player_id or action.player_id < 0:
                continue
            if action.kind not in ("fold", "call", "raise"):
                continue
            name = game_state.teams[action.player_id]
            stats = self.opponents.setdefault(
                name, {"raises": 0, "calls": 0, "folds": 0, "large_raises": 0}
            )
            if action.kind == "raise":
                stats["raises"] += 1
                if action.amount >= 8 * game_state.big_blind:
                    stats["large_raises"] += 1
                self.opponent_raised_street = (
                    "preflop" if self.board_reveals == 0
                    else "flop" if self.board_reveals <= 3
                    else "turn" if self.board_reveals == 4 else "river"
                )
            elif action.kind == "call":
                stats["calls"] += 1
            else:
                stats["folds"] += 1

    def _primary_opponent_stats(self, game_state):
        for player in game_state.players_in_hand:
            if player != self.player_id:
                return self.opponents.get(game_state.teams[player], {})
        return {}

    def _equity(self, board, opponents, samples=120):
        """Monte Carlo showdown equity vs uniform unknown hands; ties are split."""
        unavailable = set(self.hand + board)
        deck = [card for card in full_deck() if card not in unavailable]
        missing_board = 5 - len(board)
        needed = missing_board + 2 * opponents
        if needed > len(deck):
            return 0.0

        rng = random.Random(self.random_seed)
        self.random_seed = (self.random_seed * 1103515245 + 12345) & 0xFFFFFFFF
        earned = 0.0
        for _ in range(samples):
            drawn = rng.sample(deck, needed)
            finished_board = board + drawn[:missing_board]
            hero = evaluate(self.hand + finished_board)
            opponent_cards = drawn[missing_board:]
            ranks = [
                evaluate(opponent_cards[2 * i:2 * i + 2] + finished_board)
                for i in range(opponents)
            ]
            best = max(ranks, default=hero)
            if hero > best:
                earned += 1.0
            elif hero == best:
                earned += 1.0 / (1 + ranks.count(best))
        return earned / samples

    def _raise(self, game_state, total):
        if not game_state.can_raise or game_state.raise_range is None:
            return PlayerAction.call(self.player_id)
        low, high = game_state.raise_range
        total = max(low, min(high, int(total)))
        return PlayerAction.raise_to(self.player_id, total)

    def _check_or_fold(self, game_state):
        if game_state.call_amount == 0:
            return PlayerAction.call(self.player_id)
        return PlayerAction.fold(self.player_id)

    def _preflop(self, game_state):
        """25-BB hand-class ranges, with explicit short-stack fallbacks.

        Ranges are design approximations, not solved equilibrium tables.
        All actions are checked and raise totals are clamped by _raise().
        """
        hand = hand_class(self.hand)
        bb = game_state.big_blind
        cost = game_state.call_amount
        current = game_state.current_bet
        stack = game_state.stacks[self.player_id]
        heads_up = game_state.num_players == 2
        effective_bb = (
            game_state.effective_stack(self.player_id)
            + game_state.bets[self.player_id]
        ) / bb
        stats = self._primary_opponent_stats(game_state)
        opponents = max(1, len(game_state.players_in_hand) - 1)

        # Calls that exhaust our stack and calls versus opponents who are
        # all-in must bypass ordinary open/3-bet sizing caps. In both cases
        # we cannot make further betting decisions: assess the chips we can
        # actually win, including main/side pots, rather than treat a small
        # call as a fraction of our previous stack size.
        remaining_opponents = [
            player for player in game_state.players_in_hand
            if player != self.player_id
        ]
        hero_call_is_all_in = cost > 0 and cost == stack
        opponents_all_in = bool(remaining_opponents) and all(
            game_state.status(player) == "all_in"
            for player in remaining_opponents
        )
        if cost > 0 and remaining_opponents and (
            hero_call_is_all_in or opponents_all_in
        ):
            # Unequal all-in stacks create separate main/side pots.
            # Evaluate *expected chips returned* rather than outright
            # showdown equity. When opponents remain active after our
            # own all-in, freeze their current contributions and assume
            # they all reach showdown: subsequent folds can only improve
            # our share of the already-contested pots.
            samples = 280 if len(remaining_opponents) <= 2 else 160
            payout = expected_call_payout(
                self.hand,
                list(game_state.board),
                game_state.bets,
                game_state.folded,
                game_state.button,
                self.player_id,
                cost,
                samples=samples,
                seed=self.random_seed,
            )
            self.random_seed = (
                self.random_seed * 1103515245 + 12345
            ) & 0xFFFFFFFF
            # Small risk premium absorbs Monte Carlo noise on close calls.
            # This is chips, not a misleading multiway pot-odds fraction.
            margin_chips = max(1.0, 0.025 * cost)
            if payout >= cost + margin_chips:
                return PlayerAction.call(self.player_id)
            return PlayerAction.fold(self.player_id)

        if current <= bb:
            if heads_up:
                if game_state.button == self.player_id:
                    # SB acts first heads-up. A short-stack jam can deny
                    # equity more effectively than a tiny open raise.
                    if effective_bb <= 6 and hand in HU_SHOVE_6BB:
                        return self._raise(game_state, game_state.max_raise_to)
                    if effective_bb <= 11 and hand in HU_SHOVE_11BB:
                        return self._raise(game_state, game_state.max_raise_to)
                    if hand in HU_OPEN or (
                        effective_bb >= 15 and game_state.can_raise
                        and self.opponent_model.supports_blind_steal(
                            game_state.teams[1 - self.player_id]
                        )
                    ):
                        target = round((2.0 if effective_bb <= 12 else 2.3) * bb)
                        return self._raise(game_state, target)
                    if hand in HU_FOLD_UNOPENED:
                        return PlayerAction.fold(self.player_id)
                    return PlayerAction.call(self.player_id)  # complete
                # BB facing a limp: check most hands, isolate for value.
                if cost == 0 and hand in HU_ISOLATE_LIMP:
                    return self._raise(game_state, round(3.2 * bb))
                return PlayerAction.call(self.player_id)

            # Tighten significantly when 3+ players were seated.
            if hand in MULTI_OPEN:
                return self._raise(game_state, 3 * bb)
            if cost == 0 or hand in MULTI_DEFEND:
                return PlayerAction.call(self.player_id)
            return self._check_or_fold(game_state)

        # Distinguish an opening raise from a 3-bet by the actual betting
        # sequence, never by its size: a 6-BB first open is still an open.
        facing_reraise = self.preflop_raises_seen >= 2

        # Premium 4-bets take precedence over the general large-bet guard;
        # otherwise a 10-BB 3-bet would force AA to call instead of jam.
        if (
            heads_up
            and facing_reraise
            and hand in HU_FOUR_BET_SHOVE
            and game_state.can_raise
        ):
            return self._raise(game_state, game_state.max_raise_to)

        # Preserve the v1 showdown-equity guard against very large raises
        # and shoves. Dedicated opponent ranges are planned for the next PR.
        if cost >= max(4 * bb, int(0.30 * (stack + game_state.bets[self.player_id]))):
            equity = self._equity([], opponents, samples=180 if heads_up else 75)
            observed = stats.get("raises", 0) + stats.get("calls", 0)
            frequent_shover = (
                observed >= 5
                and stats.get("large_raises", 0) >= 3
                and stats.get("large_raises", 0) / observed > 0.55
            )
            margin = 0.018 if frequent_shover else 0.075
            if equity >= game_state.pot_odds + margin:
                return PlayerAction.call(self.player_id)
            return PlayerAction.fold(self.player_id)

        # A genuine 3-bet (or later raise) deserves a stronger defend range.
        # Size limits are handled separately, without inferring action count.
        if facing_reraise:
            defend = HU_VS_THREE_BET if heads_up else MULTI_THREE_BET
            if hand in defend and cost <= 7 * bb and cost <= 0.37 * stack:
                return PlayerAction.call(self.player_id)
            return self._check_or_fold(game_state)

        value_three_bet = HU_THREE_BET if heads_up else MULTI_THREE_BET
        if hand in value_three_bet and game_state.can_raise:
            if effective_bb <= 14 and hand in HU_FOUR_BET_SHOVE:
                return self._raise(game_state, game_state.max_raise_to)
            return self._raise(game_state, round(2.8 * current))

        defend = HU_DEFEND if heads_up else MULTI_DEFEND
        if (
            hand in defend
            and cost <= (3.2 if heads_up else 2.5) * bb
            and cost <= 0.26 * stack
        ):
            return PlayerAction.call(self.player_id)
        return self._check_or_fold(game_state)

    def _opponent_ranges(self, game_state):
        """Conservative, seat-ordered A1 combo priors; None means uniform.

        A1 returns a *relative weight for a candidate hand*, not a mapping.
        Build the mapping only for opponents with a meaningful observed
        preflop action and at least eight completed opportunities. Reuse it
        across later streets of this hand; the equity engine removes all
        board and hero blockers and normalizes the residual legal combos.
        """
        seats = tuple(
            seat for seat in game_state.players_in_hand
            if seat != self.player_id
        )
        if not seats or len(seats) > 3:
            return None  # Broad, cheap prior for large tables.

        ranges = []
        for seat in seats:
            team = game_state.teams[seat]
            line = self.opponent_model.hand_line(team).get("preflop", ())
            action = ("raise" if "raise" in line else
                      "call" if "call" in line else None)
            if (
                action is None
                or self.opponent_model.profile(team)["preflop_opportunities"] < 8
            ):
                ranges.append(None)
                continue

            key = (team, action)
            weights = self._range_cache.get(key)
            if weights is None:
                deck = [card for card in full_deck() if card not in self.hand]
                weights = {}
                for hand in combinations(deck, 2):
                    relative = self.opponent_model.preflop_range_weight(
                        team, hand, action=action
                    )
                    # Half uniform plus half of A1's broad prior; do not
                    # overfit a small sample of public action observations.
                    weights[hand] = 0.5 + 0.5 * relative
                self._range_cache[key] = weights
            ranges.append(weights)
        return tuple(ranges) if any(r is not None for r in ranges) else None

    def run(self, actions: list[PlayerAction], game_state: GameStateView) -> PlayerAction:
        # The organiser delivers incremental public events. Feed this batch
        # exactly once to A1; legacy counters are retained for preflop.
        self._observe(actions, game_state)
        self.opponent_model.observe(actions, game_state, self.player_id)
        contributions = self.contribution_ledger.observe(game_state)
        if game_state.street == "preflop":
            return self._preflop(game_state)

        self._decision_index += 1
        self.random_seed = (self.random_seed * 1664525 + 1013904223) & 0xFFFFFFFF
        decision_seed = self.random_seed ^ (self._decision_index * 0x9E3779B9)
        return decide_postflop(
            self.hand, self.player_id, game_state,
            ranges=self._opponent_ranges(game_state),
            seed=decision_seed,
            facing_aggression=self.opponent_raised_street == game_state.street,
            contributions=contributions,
        )

    def end_game(self, actions: list[PlayerAction], game_state: GameStateView):
        self._observe(actions, game_state)
        # Final incremental events can contain both last-street bets and
        # public showdown hole-card reveals; A1 records these by team.
        self.opponent_model.end_hand(actions, game_state, self.player_id)
