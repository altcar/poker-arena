"""
Decision engine for Heads-Up No-Limit Texas Hold'em.
Combines preflop tables, postflop evaluator, opponent modeling, and game theory.

Key algorithms:
- Randomized mixed strategy (GTO-approximating): raise with probability = equity
  so we're not 100% predictable on any given hand strength
- Threat discounting: when facing a large bet, reduce our effective equity to
  model the Bayesian update "opponent bet big => they likely have something"
- Street-escalating bet sizing: pot-fraction grows flop->turn->river
- Exploitative overrides for identified dummy archetypes
"""

import random as _rng

from card_utils import Card
from game_state import GameStateView
from player_action import PlayerAction
from preflop_tables import get_preflop_equity, get_preflop_percentile
from evaluator import Evaluator
from tracker import OpponentTracker

# Threat discount: subtract this from effective equity when opp bets large
_THREAT_DISCOUNT = 0.15
_THREAT_THRESHOLD_RATIO = 0.40  # call_amount / pot >= 40% => "large bet signal"

# Street-specific bet fractions when checked to us
_BET_FRACTIONS = {
    "flop":  0.55,
    "turn":  0.75,
    "river": 0.90,
}


class StrategyEngine:
    def __init__(self, evaluator: Evaluator, tracker: OpponentTracker):
        self.evaluator = evaluator
        self.tracker = tracker

    def _safe_raise(self, game_state: GameStateView, player_id: int, target_chips: int) -> PlayerAction:
        """Helper to safely build a raise within legal limits, falling back to call if unable to raise."""
        if not game_state.can_raise or game_state.raise_range is None:
            return PlayerAction.call(player_id)
        low, high = game_state.raise_range
        total = int(min(high, max(low, target_chips)))
        action = PlayerAction.raise_to(player_id, total)
        try:
            game_state.validate_action(action)
            return action
        except Exception:
            return PlayerAction.call(player_id)

    def decide(
        self,
        hand: list[Card],
        player_id: int,
        actions: list[PlayerAction],
        game_state: GameStateView,
    ) -> PlayerAction:
        """Main entry point: selects the optimal action given state and history."""
        opp_id = 1 - player_id if game_state.num_players == 2 else (player_id + 1) % game_state.num_players
        opp_name = game_state.teams[opp_id]
        opp_archetype = self.tracker.get_archetype(opp_name)

        street = game_state.street
        if street == "preflop":
            return self._decide_preflop(hand, player_id, game_state, opp_archetype)
        else:
            return self._decide_postflop(hand, player_id, game_state, opp_archetype)

    def _decide_preflop(
        self,
        hand: list[Card],
        player_id: int,
        game_state: GameStateView,
        opp_archetype: str,
    ) -> PlayerAction:
        is_button = (game_state.button == player_id)
        percentile = get_preflop_percentile(hand)
        equity = get_preflop_equity(hand)
        call_amount = game_state.call_amount
        big_blind = game_state.big_blind

        # --- 1. BUTTON (SMALL BLIND) PREFLOP ---
        if is_button:
            # First action on button (SB completion: call_amount == small_blind == 10)
            if call_amount <= game_state.small_blind:
                if opp_archetype == "FOLD":
                    # FoldBot folds to any raise: raise 100% of hands!
                    return self._safe_raise(game_state, player_id, game_state.min_raise_to)

                if opp_archetype == "SHOVE":
                    # ShoveBot shoves 25 BB: play top 22% hands with 68%+ equity, fold the rest
                    if percentile <= 0.22 or equity >= 0.60:
                        return self._safe_raise(game_state, player_id, game_state.max_raise_to)
                    return PlayerAction.fold(player_id)

                if opp_archetype == "CALL":
                    # Calling station: raise top 70% value hands, limp speculative hands, never fold
                    if percentile <= 0.70:
                        return self._safe_raise(game_state, player_id, min(game_state.max_raise_to, 2 * big_blind + 10))
                    return PlayerAction.call(player_id)

                # Against TIGHT or BALANCED / DEFAULT
                # Use RANDOMIZED raise threshold (reference bot style): raise with prob = equity
                # This creates a natural mixed strategy — top hands raise more often,
                # weaker hands raise less often → not exploitable by pattern-matching
                if percentile <= 0.08:
                    # Premiums (AA/KK/QQ/AK): always raise, size up for value
                    return self._safe_raise(game_state, player_id, min(game_state.max_raise_to, int(2.5 * big_blind)))
                elif percentile <= 0.78:
                    # Good-to-decent hands: raise with prob proportional to equity
                    # equity ranges ~0.52 (marginal) to ~0.78 (strong) in this bucket
                    if equity > _rng.random():
                        return self._safe_raise(game_state, player_id, game_state.min_raise_to)
                    # Else limp to see cheap flop
                    return PlayerAction.call(player_id)
                elif percentile <= 0.92:
                    # Speculative / weak hands: limp cheaply
                    return PlayerAction.call(player_id)
                else:
                    # Fold absolute trash (bottom 8%)
                    return PlayerAction.fold(player_id)

            # Button facing a 3-bet or shove
            else:
                is_all_in = (call_amount >= game_state.stacks[player_id] * 0.7)
                if is_all_in:
                    if opp_archetype == "SHOVE":
                        if percentile <= 0.24 or equity >= 0.58:
                            return PlayerAction.call(player_id)
                        return PlayerAction.fold(player_id)
                    if opp_archetype == "TIGHT":
                        if percentile <= 0.08:
                            return PlayerAction.call(player_id)
                        return PlayerAction.fold(player_id)
                    # Normal all-in
                    if percentile <= 0.16 or equity >= 0.62:
                        return PlayerAction.call(player_id)
                    return PlayerAction.fold(player_id)

                # Small 3-bet
                if percentile <= 0.10:
                    # 4-bet shove with premiums
                    return self._safe_raise(game_state, player_id, game_state.max_raise_to)
                if percentile <= 0.50:
                    return PlayerAction.call(player_id)
                return PlayerAction.fold(player_id)

        # --- 2. BIG BLIND PREFLOP ---
        else:
            # Button limped (call_amount == 0, free check or iso-raise)
            if call_amount == 0:
                if opp_archetype == "FOLD":
                    # Steal immediately!
                    return self._safe_raise(game_state, player_id, game_state.min_raise_to)
                if opp_archetype == "CALL":
                    if percentile <= 0.40:
                        return self._safe_raise(game_state, player_id, min(game_state.max_raise_to, 3 * big_blind))
                    return PlayerAction.call(player_id)
                # Normal limp: iso-raise top hands with randomization
                if percentile <= 0.12:
                    return self._safe_raise(game_state, player_id, game_state.min_raise_to)
                if percentile <= 0.35 and equity > _rng.random():
                    return self._safe_raise(game_state, player_id, game_state.min_raise_to)
                return PlayerAction.call(player_id)

            # Button open min-raised (call_amount <= 30)
            if call_amount <= 1.5 * big_blind:
                if percentile <= 0.12:
                    # 3-bet for value
                    return self._safe_raise(game_state, player_id, min(game_state.max_raise_to, int(3.5 * big_blind)))
                if percentile <= 0.72:
                    # Defend wide with 25-30% pot odds
                    return PlayerAction.call(player_id)
                return PlayerAction.fold(player_id)

            # Button shoved or large raise
            is_shove = (call_amount >= game_state.stacks[player_id] * 0.7)
            if is_shove:
                if opp_archetype == "SHOVE":
                    # Against ShoveBot, calling with top 24% is huge +EV
                    if percentile <= 0.24 or equity >= 0.58:
                        return PlayerAction.call(player_id)
                    return PlayerAction.fold(player_id)
                if opp_archetype == "TIGHT":
                    if percentile <= 0.07:
                        return PlayerAction.call(player_id)
                    return PlayerAction.fold(player_id)
                if percentile <= 0.15 or equity >= 0.63:
                    return PlayerAction.call(player_id)
                return PlayerAction.fold(player_id)

            # Medium raise
            if percentile <= 0.38:
                return PlayerAction.call(player_id)
            return PlayerAction.fold(player_id)

    def _decide_postflop(
        self,
        hand: list[Card],
        player_id: int,
        game_state: GameStateView,
        opp_archetype: str,
    ) -> PlayerAction:
        board = list(game_state.board)
        pot = game_state.pot
        call_amount = game_state.call_amount
        pot_odds = game_state.pot_odds
        street = game_state.street

        # Hand analysis
        made = self.evaluator.analyze_made_hand(hand, board)
        draws = self.evaluator.analyze_draws(hand, board)

        # Monte Carlo rollout
        filter_opp = 0.3 if opp_archetype == "TIGHT" else 0.0
        samples = 80 if street == "flop" else (60 if street == "turn" else 40)
        raw_equity = self.evaluator.monte_carlo_equity(hand, board, num_samples=samples, opp_range_filter=filter_opp)

        # Effective equity with draw potential
        draw_equity = (draws["outs"] * 0.02) if street != "river" else 0.0
        effective_equity = min(0.99, raw_equity + draw_equity)

        # Categorize hand strength
        cat = made["category"]
        is_monster = (cat >= 4 or made["is_set"] or made["strong_two_pair"])
        is_strong = (made["is_overpair"] or made["is_top_pair"] or made["is_two_pair"])
        is_medium = (cat == 1 and not made["is_top_pair"] and made["uses_hole"])
        has_strong_draw = (draws["flush_draw"] or draws["oesd"] or draws["outs"] >= 8)

        # Street-dependent bet fraction (escalates turn/river)
        bet_fraction = _BET_FRACTIONS.get(street, 0.65)

        # --- SCENARIO A: CHECKED TO US (call_amount == 0) ---
        if call_amount == 0:
            if opp_archetype == "FOLD":
                # Folds to any bet: always bet
                return self._safe_raise(game_state, player_id, game_state.min_raise_to)

            if opp_archetype == "CALL":
                # Calling station: stack them with big value bets!
                if is_monster:
                    # Shove or bet huge on turn/river
                    if street in ("turn", "river"):
                        return self._safe_raise(game_state, player_id, game_state.max_raise_to)
                    return self._safe_raise(game_state, player_id, int(pot * 0.85))
                if is_strong or (is_medium and raw_equity >= 0.55):
                    bet = int(pot * 0.70)
                    return self._safe_raise(game_state, player_id, max(game_state.min_raise_to, bet))
                # Semi-bluff draws against calling station for thin value
                if has_strong_draw and street != "river":
                    bet = int(pot * 0.50)
                    return self._safe_raise(game_state, player_id, max(game_state.min_raise_to, bet))
                return PlayerAction.call(player_id)  # check free

            # ---- STANDARD / BALANCED / EQUITYBOT ----
            # RANDOMIZED MIXED STRATEGY (reference bot core algorithm):
            # raise with probability = effective_equity
            # → 80% equity hand raises 80% of the time (not always)
            # → 55% equity hand raises 55% of the time (sometimes bluffs)
            # This prevents opponents from pattern-reading our bet/check frequencies

            if is_monster:
                # Monsters always bet (equity ~0.85+): escalate sizing by street
                bet = int(pot * bet_fraction)
                return self._safe_raise(game_state, player_id, max(game_state.min_raise_to, bet))

            if is_strong:
                # Strong hands: randomized bet (raise with prob = equity)
                if effective_equity > _rng.random():
                    bet = int(pot * bet_fraction)
                    return self._safe_raise(game_state, player_id, max(game_state.min_raise_to, bet))
                return PlayerAction.call(player_id)  # check for deception / pot control

            if has_strong_draw and street != "river":
                # Semi-bluff draws: randomized — bet often but not always
                if effective_equity > _rng.random():
                    bet = int(pot * (bet_fraction * 0.80))
                    return self._safe_raise(game_state, player_id, max(game_state.min_raise_to, bet))
                return PlayerAction.call(player_id)

            if street == "flop" and (draws["overcards"] == 2 or draws["outs"] >= 4) and raw_equity >= 0.42:
                # Thin flop c-bet: randomized at ~50% frequency when equity < 0.59
                if effective_equity > _rng.random() * 0.9:
                    bet = int(pot * 0.40)
                    return self._safe_raise(game_state, player_id, max(game_state.min_raise_to, bet))

            # Default: check
            return PlayerAction.call(player_id)

        # --- SCENARIO B: FACING A BET (call_amount > 0) ---
        else:
            # Exploitative adjustment vs TightBot
            if opp_archetype == "TIGHT":
                # TightBot only bets two pair or better!
                if not is_monster:
                    return PlayerAction.fold(player_id)
                if cat >= 4 or made["is_set"]:
                    return self._safe_raise(game_state, player_id, int(pot * 1.5))
                return PlayerAction.call(player_id)

            # THREAT DISCOUNT: large bet signals opponent has a real hand
            # Apply a Bayesian penalty to our equity estimate
            is_large_bet = (call_amount >= pot * 0.75 or call_amount >= game_state.stacks[player_id] * 0.6)
            is_medium_bet = (call_amount >= pot * _THREAT_THRESHOLD_RATIO)

            threatened_equity = effective_equity
            if is_large_bet:
                threatened_equity = max(0.0, effective_equity - _THREAT_DISCOUNT)
            elif is_medium_bet:
                threatened_equity = max(0.0, effective_equity - _THREAT_DISCOUNT * 0.5)

            # If we hold a monster: re-raise for value regardless of threat!
            if is_monster and threatened_equity >= 0.70:
                raise_target = int(pot * 1.5)
                return self._safe_raise(game_state, player_id, max(game_state.min_raise_to, raise_target))

            # If facing a large bet from sensible opponent (e.g. EquityBot or balanced)
            if is_large_bet and opp_archetype not in ("CALL", "SHOVE"):
                # Large bet represents very strong hand (>0.70 equity).
                # Only call if we have top pair top kicker or better, or strong combo draw.
                if is_strong and threatened_equity >= pot_odds * 1.05:
                    return PlayerAction.call(player_id)
                if has_strong_draw and street != "river" and (threatened_equity + 0.05) >= pot_odds:
                    return PlayerAction.call(player_id)
                return PlayerAction.fold(player_id)

            # Standard pot odds comparison (using threat-adjusted equity)
            if threatened_equity >= pot_odds:
                # Also add randomized aggression: if we're well above pot odds, consider raising
                if threatened_equity >= pot_odds * 1.4 and is_strong and threatened_equity > _rng.random():
                    raise_target = int(pot * bet_fraction)
                    return self._safe_raise(game_state, player_id, max(game_state.min_raise_to, raise_target))
                return PlayerAction.call(player_id)

            # Close call with implied odds for draws
            if has_strong_draw and street != "river" and (threatened_equity + 0.08) >= pot_odds:
                return PlayerAction.call(player_id)

            # Extremely cheap call (<= 3% of stack)
            if call_amount <= game_state.stacks[player_id] * 0.03:
                return PlayerAction.call(player_id)

            return PlayerAction.fold(player_id)
