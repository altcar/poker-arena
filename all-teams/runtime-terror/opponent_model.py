"""Persistent, public-information-only opponent observations for Poker Bot V2.

This module is deliberately independent of model.py and street-specific policies.
A new OpponentModel is created once per match; new_hand() resets only
hand-local state. observe() takes incremental (not full-history) actions;
end_hand() ingests the final incremental batch and closes that hand.

All state is bounded plain Python data, suitable for pickling in a submission.
No inference from opponents' unrevealed hole cards is ever attempted.
"""

from __future__ import annotations

from copy import deepcopy


STREETS = ("preflop", "flop", "turn", "river")
PRIOR_STRENGTH = 12  # Equivalent pseudo-opportunities; tempers early noise.
PRIOR_RATES = {
    "vpip": 0.50,  # Heads-up-biased, deliberately broad defaults.
    "pfr": 0.30,
    "three_bet": 0.12,
    "fold_to_bet": 0.42,
    "raise_facing_bet": 0.16,
    "bet_unopened": 0.45,
}


def _street_counts():
    return {
        "actions": 0,
        "unopened_opportunities": 0,
        "bets_unopened": 0,
        "limps": 0,  # Preflop calls before a raise that cost chips.
        "checks": 0,
        "facing_bet": 0,
        "fold_to_bet": 0,
        "call_facing_bet": 0,
        "raise_facing_bet": 0,
        "raises": 0,
        "large_raises": 0,
    }


def _blank_record():
    return {
        "hands_seen": 0,
        "preflop_opportunities": 0,
        "vpip_hands": 0,
        "pfr_hands": 0,
        "three_bet_opportunities": 0,
        "three_bets": 0,
        "hu_bb_open_opportunities": 0,
        "hu_bb_open_folds": 0,
        "showdown_hands": 0,
        "shown_hand_classes": {},
        "streets": {street: _street_counts() for street in STREETS},
    }


def _smoothed(successes, opportunities, prior):
    return (successes + PRIOR_STRENGTH * prior) / (opportunities + PRIOR_STRENGTH)


def _hand_class(cards):
    """Return canonical class (e.g. AKs), using only shown or candidate cards."""
    first, second = cards
    if first.rank == second.rank:
        return first.rank + second.rank
    if first.value < second.value:
        first, second = second, first
    return first.rank + second.rank + ("s" if first.suit == second.suit else "o")


def _strength(cards):
    """Cheap ordering of hypothetical preflop holdings, NOT true equity."""
    a, b = cards
    high, low = sorted((a.value, b.value), reverse=True)
    if high == low:
        return 0.53 + 0.47 * ((high - 2) / 12)
    suited = a.suit == b.suit
    gap = high - low - 1
    value = (0.09 + 0.040 * (high - 2) + 0.020 * (low - 2)
             + (0.10 if suited else 0.0)
             + (0.05 if gap == 0 else 0.0)
             + (0.05 if high == 14 else 0.0))
    return min(1.0, max(0.03, value))


class OpponentModel:
    """Observes public action streams and summarizes behaviour by team name.

    Public API:
      new_hand()                                      # call from Model.seed
      observe(incremental_actions, game_state, hero_id, batch_id=None)
      end_hand(incremental_actions, game_state, hero_id, batch_id=None)
      profile(team)                                  # smoothed, copy-safe dict
      hand_line(team)                                # this hand's actions
      preflop_range_weight(team, cards, action=None) # relative sampling weight

    batch_id is an *optional* idempotency key for callers with stable batch
    identifiers. The organiser supplies only incremental actions and no IDs,
    so callers must NOT replay an old batch without a matching key: repeated
    identical call/check actions can be legitimate distinct actions.
    """

    def __init__(self):
        self._records = {}
        self._hand = None
        self._last_completed = None
        self._closed = False

    def new_hand(self):
        """Reset only hand-local tracking; completed opponent history survives."""
        if self._hand is not None:
            self._finalize()
        self._hand = None
        self._last_completed = None
        self._closed = False

    def _start_hand(self, game_state, hero_id, actions):
        if self._hand is not None:
            if self._hand["hero_id"] != hero_id:
                raise ValueError("new_hand() must be called when seat changes")
            return
        names = tuple(game_state.teams)
        if not 0 <= hero_id < len(names):
            raise ValueError("hero_id out of bounds")
        button = game_state.button
        small = button if len(names) == 2 else (button + 1) % len(names)
        big = (small + 1) % len(names)
        # Blind posts are min(blind, starting stack). They are NOT emitted
        # in the action stream, so never assume nominal amounts when the
        # blind could have been posted all-in for less. On the first observe
        # call, `actions` is the whole stream before our first decision. If a
        # blind has already acted, they could not have started all-in and
        # hence posted the full blind. Otherwise, when still on preflop the
        # public view contains their untouched actual initial street bet.
        first_street_actors = set()
        board_count = 0
        for action in actions:
            if action.kind == "reveal_card" and action.player_id == -1:
                board_count += 1
                if board_count == 3:
                    break
            elif action.kind in ("fold", "call", "raise"):
                first_street_actors.add(action.player_id)

        invested = [0] * len(names)
        for seat, nominal in ((small, game_state.small_blind),
                              (big, game_state.big_blind)):
            if seat in first_street_actors:
                invested[seat] = nominal
            elif game_state.street == "preflop":
                invested[seat] = min(nominal, game_state.bets[seat])
            else:
                # Cannot recover an unacted, potentially short blind from
                # a postflop snapshot. Unknown cost is not a free check.
                invested[seat] = None
        # An unknown contribution is still bounded by the corresponding
        # nominal blind. These ceilings let later raises prove that an
        # otherwise ambiguous call was voluntary without inventing chips.
        bet_ceilings = [0] * len(names)
        bet_ceilings[small] = (invested[small] if invested[small] is not None
                               else game_state.small_blind)
        bet_ceilings[big] = (invested[big] if invested[big] is not None
                             else game_state.big_blind)
        initial_bet = (max(invested) if all(bet is not None for bet in invested)
                       else None)
        for i, name in enumerate(names):
            if i != hero_id:
                self._records.setdefault(name, _blank_record())
        self._hand = {
            "teams": names,
            "hero_id": hero_id,
            "street": "preflop",
            "board_reveals": 0,
            "bets": invested,
            "bet_ceilings": bet_ceilings,
            "current_bet": initial_bet,
            "preflop_raises": 0,
            "preflop_acted": set(),
            # An unclassifiable preflop call must not count as non-VPIP.
            # A later confirmed voluntary action can classify the hand.
            "preflop_ambiguous": set(),
            "vpip": set(),
            "pfr": set(),
            "shown": {},
            "lines": {name: {street: [] for street in STREETS}
                      for i, name in enumerate(names) if i != hero_id},
            "seen_batches": set(),
        }

    def observe(self, actions, game_state, hero_id, batch_id=None):
        """Consume exactly one incremental action batch (no secret data)."""
        if self._closed:
            raise ValueError("new_hand() required after end_hand()")
        self._start_hand(game_state, hero_id, actions)
        hand = self._hand
        if batch_id is not None:
            if batch_id in hand["seen_batches"]:
                return
            hand["seen_batches"].add(batch_id)
        for action in actions:
            pid = action.player_id
            if action.kind == "reveal_card":
                if pid == -1:
                    hand["board_reveals"] += 1
                    count = hand["board_reveals"]
                    next_street = {3: "flop", 4: "turn", 5: "river"}.get(count)
                    if next_street is not None:
                        hand["street"] = next_street
                        hand["bets"] = [0] * len(hand["teams"])
                        hand["bet_ceilings"] = [0] * len(hand["teams"])
                        hand["current_bet"] = 0
                elif 0 <= pid < len(hand["teams"]) and pid != hero_id:
                    name = hand["teams"][pid]
                    cards = hand["shown"].setdefault(name, [])
                    if len(cards) < 2:  # No unbounded history of individual cards.
                        cards.append(action.card)
                continue
            if action.kind == "win" or pid < 0 or pid >= len(hand["teams"]):
                continue
            if action.kind not in ("fold", "call", "raise"):
                continue

            street = hand["street"]
            old_bet = hand["bets"][pid]
            current_bet = hand["current_bet"]
            cost = (max(0, current_bet - old_bet)
                    if current_bet is not None and old_bet is not None else None)
            if cost is None and current_bet is not None:
                # A raise above the maximum possible prior contribution
                # proves a subsequent call is voluntary, even without
                # knowing the exact size of an earlier short blind/call.
                minimum_cost = current_bet - hand["bet_ceilings"][pid]
                if minimum_cost > 0:
                    cost = minimum_cost
            # A preflop SB completion faces the forced BB, not aggression.
            # An unknown blind cost must not be mistaken for a free check.
            unraised = street == "preflop" and hand["preflop_raises"] == 0
            facing = (cost is not None and cost > 0 and not unraised)
            name = hand["teams"][pid]
            if pid != hero_id:
                record = self._records[name]
                counts = record["streets"][street]
                counts["actions"] += 1
                hand["lines"][name][street].append(action.kind)
                if street == "preflop":
                    # Only BB facing the SB's first normal-sized open.
                    # Do not mix folds to 3-bets, shoves or multiway opens.
                    if (len(hand["teams"]) == 2 and pid != game_state.button
                            and hand["preflop_raises"] == 1 and facing
                            and old_bet == game_state.big_blind
                            and 2 * game_state.big_blind <= current_bet <= 3 * game_state.big_blind):
                        key = "hu_bb_open_opportunities"
                        record[key] = record.get(key, 0) + 1
                        if action.kind == "fold":
                            key = "hu_bb_open_folds"
                            record[key] = record.get(key, 0) + 1
                    hand["preflop_acted"].add(name)
                    if action.kind == "raise":
                        # A definite raise is VPIP/PFR regardless of any
                        # earlier ambiguous preflop call.
                        hand["vpip"].add(name)
                        hand["pfr"].add(name)
                        hand["preflop_ambiguous"].discard(name)
                    elif action.kind == "call" and cost is None:
                        # Don't treat unknown check-vs-call as a no-VPIP
                        # hand. Exclude it from both VPIP and PFR rate
                        # denominators unless later evidence resolves it.
                        if name not in hand["vpip"]:
                            hand["preflop_ambiguous"].add(name)
                    elif action.kind == "call" and cost > 0:
                        hand["vpip"].add(name)
                        hand["preflop_ambiguous"].discard(name)
                    if hand["preflop_raises"] == 1:
                        record["three_bet_opportunities"] += 1
                        if action.kind == "raise":
                            record["three_bets"] += 1
                if facing:
                    counts["facing_bet"] += 1
                    if action.kind == "fold":
                        counts["fold_to_bet"] += 1
                    elif action.kind == "call":
                        counts["call_facing_bet"] += 1
                    else:
                        counts["raise_facing_bet"] += 1
                elif unraised or cost == 0:
                    # Include SB/button open/limp decisions even though the
                    # small blind still owes the forced BB difference. They
                    # are unopened pots until the first *voluntary* raise.
                    counts["unopened_opportunities"] += 1
                    if action.kind == "raise":
                        counts["bets_unopened"] += 1
                    elif action.kind == "call" and cost == 0:
                        counts["checks"] += 1
                    elif action.kind == "call" and cost is not None and cost > 0:
                        counts["limps"] += 1
                if action.kind == "raise":
                    counts["raises"] += 1
                    if action.amount >= 8 * game_state.big_blind:
                        counts["large_raises"] += 1

            # Reconstruct *historical* call/raise amounts from chronological
            # actions, never from the final GameStateView.bets snapshot.
            if action.kind == "raise":
                hand["bets"][pid] = action.amount
                hand["bet_ceilings"][pid] = action.amount
                hand["current_bet"] = action.amount
                if street == "preflop":
                    hand["preflop_raises"] += 1  # hero raises count too
            elif action.kind == "call":
                hand["bets"][pid] = (max(old_bet, hand["current_bet"])
                                     if old_bet is not None and hand["current_bet"] is not None
                                     else None)
                # A call is no larger than the current betting target.
                # Prior to a voluntary preflop raise it is <= the BB.
                ceiling = (hand["current_bet"] if hand["current_bet"] is not None
                           else game_state.big_blind)
                hand["bet_ceilings"][pid] = max(hand["bet_ceilings"][pid], ceiling)
            # Fold does not change current_bet.

    def end_hand(self, actions, game_state, hero_id, batch_id=None):
        """Ingest the final unseen events and commit one hand's stats."""
        if self._closed:
            return  # Defensive idempotence for repeated end_game delivery.
        self.observe(actions, game_state, hero_id, batch_id=batch_id)
        self._finalize()
        self._closed = True

    def _finalize(self):
        hand = self._hand
        if hand is None:
            return
        for name in hand["lines"]:
            record = self._records[name]
            record["hands_seen"] += 1
            if (name in hand["preflop_acted"]
                    and name not in hand["preflop_ambiguous"]):
                record["preflop_opportunities"] += 1
                record["vpip_hands"] += name in hand["vpip"]
                record["pfr_hands"] += name in hand["pfr"]
            shown = hand["shown"].get(name, ())
            if len(shown) == 2:
                hand_class = _hand_class(shown)
                classes = record["shown_hand_classes"]
                classes[hand_class] = classes.get(hand_class, 0) + 1
                record["showdown_hands"] += 1
        self._last_completed = {name: deepcopy(line) for name, line in hand["lines"].items()}
        self._hand = None

    def hand_line(self, team):
        """A copy of one team's current hand actions grouped by street."""
        if self._hand is None:
            data = (self._last_completed or {}).get(team, {})
        else:
            data = self._hand["lines"].get(team, {})
        return deepcopy(data)

    def profile(self, team):
        """Snapshot with raw denominators AND smoothed rates (never NaN)."""
        record = deepcopy(self._records.get(team, _blank_record()))
        pre = record["preflop_opportunities"]
        record["rates"] = {
            "vpip": _smoothed(record["vpip_hands"], pre, PRIOR_RATES["vpip"]),
            "pfr": _smoothed(record["pfr_hands"], pre, PRIOR_RATES["pfr"]),
            "three_bet": _smoothed(
                record["three_bets"], record["three_bet_opportunities"],
                PRIOR_RATES["three_bet"]),
        }
        for street in STREETS:
            counts = record["streets"][street]
            counts["rates"] = {
                "fold_to_bet": _smoothed(counts["fold_to_bet"], counts["facing_bet"],
                                         PRIOR_RATES["fold_to_bet"]),
                "raise_facing_bet": _smoothed(
                    counts["raise_facing_bet"], counts["facing_bet"],
                    PRIOR_RATES["raise_facing_bet"]),
                "bet_unopened": _smoothed(
                    counts["bets_unopened"], counts["unopened_opportunities"],
                    PRIOR_RATES["bet_unopened"]),
            }
        return record

    def supports_blind_steal(self, team):
        """Require a 95% Wilson lower fold-rate bound above 70%.

        Exact-context public observations only. Missing fields in older
        pickles mean no evidence, never automatic exploitation.
        """
        record = self._records.get(team, {})
        n = record.get("hu_bb_open_opportunities", 0)
        if n < 20:
            return False
        p = record.get("hu_bb_open_folds", 0) / n
        z2 = 1.96 ** 2
        lower = (p + z2 / (2 * n)
                 - 1.96 * (p * (1 - p) / n + z2 / (4 * n * n)) ** 0.5
                 ) / (1 + z2 / n)
        return lower > 0.70

    def preflop_range_weight(self, team, cards, action=None):
        """Positive *relative* combo weight, not a probability or solved range.

        action is 'raise', 'call', or None. If absent, the current hand's
        observed preflop line is used. Unknown action -> neutral weight 1.
        The prior alone favours strong holdings when somebody raises. With
        evidence, low VPIP tightens and high VPIP loosens this prior; the
        effect is bounded to avoid speculative exploitation.
        """
        if action is None:
            line = self.hand_line(team).get("preflop", ())
            action = "raise" if "raise" in line else "call" if "call" in line else None
        if action not in ("raise", "call"):
            return 1.0
        quality = _strength(cards)
        # These are intentionally broad priors; normalize across available
        # non-blocked combos before sampling in the separate equity engine.
        prior = (0.16 + 2.2 * quality**3 if action == "raise"
                 else 0.55 + 0.90 * quality)
        record = self._records.get(team)
        if record is None or record["preflop_opportunities"] == 0:
            return prior
        n = record["preflop_opportunities"]
        confidence = min(1.0, n / 40.0)
        vpip = _smoothed(record["vpip_hands"], n, PRIOR_RATES["vpip"])
        # A tight player weights strong combos more; loose opponent flattens.
        tilt = min(0.7, max(-0.7, (0.50 - vpip) * 1.6 * confidence))
        multiplier = 1.0 + tilt * (2.0 * quality - 1.0)
        return max(0.05, prior * multiplier)
