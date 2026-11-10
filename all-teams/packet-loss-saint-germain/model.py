import random

from player_action import PlayerAction

import hand_eval
import pf_tables

SB_FIRST_PRIOR = {"fold": 0.3, "limp": 0.35, "raise": 0.3, "shove": 0.05}


class Model:
    def __init__(self):
        self.names = list(pf_tables.NAMES)
        self.size = len(self.names)
        self.counts = [6 if len(name) == 2 else (4 if name[2] == "s" else 12) for name in self.names]
        self.equity = pf_tables.load_equity()
        total = sum(self.counts)
        versus_random = [
            sum(self.counts[j] * self.equity[i][j] for j in range(self.size)) / total
            for i in range(self.size)
        ]
        self.order = sorted(range(self.size), key=lambda i: -versus_random[i])
        self.start_pct = [0.0] * self.size
        self.end_pct = [0.0] * self.size
        running = 0.0
        for i in self.order:
            self.start_pct[i] = running
            running += self.counts[i] / total
            self.end_pct[i] = running
        self.mid_pct = [(a + b) / 2 for a, b in zip(self.start_pct, self.end_pct)]
        self.rng = random.Random(20261107)
        self.stats = {}
        self.errors = 0
        self.samples = 500
        self.params = {
            "check_realize": 1.05,
            "implied": 0.0,
            "flat_realize": 1.35,
            "call_realize": 1.3,
            "raise_value": 0.78,
            "call_margin": 0.03,
            "value_line": 0.57,
            "value_line_station": 0.53,
            "bluff_scale": 1.5,
            "check_drop": 0.5,
            "size_slope": 0.4,
        }
        self.hand = []
        self.player_id = None
        self.reset_hand()

    def reset_hand(self):
        self.my_cards = []
        self.my_class = 0
        self.history = []
        self.board_cards = []
        self.revealed = {}
        self.start_totals = None
        self.opp = None
        self.opp_name = None
        self.button = None

    def button_is_me(self):
        return self.button is not None and self.button == self.player_id

    def seed(self, hand, player_id):
        self.hand = hand
        self.player_id = player_id
        self.reset_hand()
        self.my_cards = [hand_eval.card_to_int(card) for card in hand]
        self.my_class = hand_eval.class_of(self.my_cards[0], self.my_cards[1])

    def absorb(self, actions):
        for action in actions:
            if action.kind == "reveal_card":
                card = hand_eval.card_to_int(action.card)
                if action.player_id < 0:
                    self.board_cards.append(card)
                else:
                    self.revealed.setdefault(action.player_id, []).append(card)
            elif action.kind in ("fold", "call", "raise"):
                self.history.append((action.player_id, action.kind, action.amount, self.street_name()))

    def street_name(self):
        count = len(self.board_cards)
        if count < 3:
            return "preflop"
        if count == 3:
            return "flop"
        if count == 4:
            return "turn"
        return "river"

    def run(self, actions, game_state):
        me = self.player_id
        try:
            self.absorb(actions)
            if self.start_totals is None:
                self.start_totals = [game_state.stacks[p] + game_state.bets[p] for p in range(game_state.num_players)]
            self.button = game_state.button
            if game_state.num_players == 2:
                self.opp = 1 - me
                self.opp_name = game_state.teams[self.opp]
                action = self.decide(game_state)
            else:
                action = self.simple_decision(game_state)
            return self.make_legal(action, game_state)
        except Exception:
            self.errors += 1
            return PlayerAction.call(me) if game_state.call_amount == 0 else PlayerAction.fold(me)

    def make_legal(self, action, game_state):
        me = self.player_id
        kind, amount = action
        if kind == "raise":
            if not game_state.can_raise:
                return PlayerAction.call(me)
            low, high = game_state.raise_range
            total = int(min(high, max(low, amount)))
            if total <= game_state.current_bet:
                return PlayerAction.call(me)
            return PlayerAction.raise_to(me, total)
        if kind == "fold" and game_state.call_amount == 0:
            return PlayerAction.call(me)
        if kind == "fold":
            return PlayerAction.fold(me)
        return PlayerAction.call(me)

    def end_game(self, actions, game_state):
        try:
            self.absorb(actions)
            if game_state.num_players == 2:
                self.learn(game_state)
        except Exception:
            self.errors += 1

    def opponent_stats(self):
        return self.stats.setdefault(self.opp_name, {"counts": {}, "pcts": {}})

    def add_count(self, context, outcome):
        counts = self.opponent_stats()["counts"].setdefault(context, {})
        counts[outcome] = counts.get(outcome, 0) + 1

    def add_pct(self, context, pct):
        self.opponent_stats()["pcts"].setdefault(context, []).append(pct)

    def freq(self, context, outcome, prior, strength):
        counts = self.opponent_stats()["counts"].get(context, {})
        seen = sum(counts.values())
        return (counts.get(outcome, 0) + prior * strength) / (seen + strength)

    def sb_first_freqs(self):
        counts = self.opponent_stats()["counts"].get("sb_first", {})
        seen = sum(counts.values())
        strength = 3.0
        return {
            key: (counts.get(key, 0) + prior * strength) / (seen + strength)
            for key, prior in SB_FIRST_PRIOR.items()
        }

    def randomness(self, context, low, high):
        expected = (low + high) / 2
        if 0.5 - expected < 0.03:
            return 0.0
        pcts = self.opponent_stats()["pcts"].get(context, [])
        prior_lam = 0.1
        strength = 2.0
        prior_mean = prior_lam * 0.5 + (1 - prior_lam) * expected
        mean = (sum(pcts) + prior_mean * strength) / (len(pcts) + strength)
        return min(1.0, max(0.0, (mean - expected) / (0.5 - expected)))

    def band(self, low, high, lam=0.0):
        low = max(0.0, min(1.0, low))
        high = max(low, min(1.0, high))
        weights = []
        for i in range(self.size):
            start, end = self.start_pct[i], self.end_pct[i]
            inside = max(0.0, min(end, high) - max(start, low)) / (end - start)
            weights.append((1 - lam) * inside + lam * (high - low))
        return weights

    def equity_vs(self, class_index, weights):
        row = self.equity[class_index]
        top = 0.0
        bottom = 0.0
        for j in range(self.size):
            w = self.counts[j] * weights[j]
            if w > 0:
                top += w * row[j]
                bottom += w
        return top / bottom if bottom > 0 else 0.5

    def decide(self, game_state):
        if game_state.street == "preflop":
            return self.preflop(game_state)
        return self.postflop(game_state)

    def preflop(self, game_state):
        me, opp = self.player_id, self.opp
        stack_e = min(self.start_totals[me], self.start_totals[opp])
        my_in = self.start_totals[me] - game_state.stacks[me]
        opp_in = self.start_totals[opp] - game_state.stacks[opp]
        moves = [h for h in self.history if h[3] == "preflop"]
        opp_moves = [h for h in moves if h[0] == opp]
        my_moves = [h for h in moves if h[0] == me]
        facing_all_in = game_state.call_amount > 0 and (
            game_state.stacks[opp] == 0 or game_state.call_amount >= game_state.stacks[me]
        )
        if facing_all_in:
            return self.versus_all_in(game_state, opp_moves, my_moves)
        if game_state.button == me and not my_moves:
            return self.sb_open(game_state, stack_e, my_in, opp_in)
        if game_state.button == me and len(my_moves) == 1 and my_moves[0][1] == "raise" and game_state.call_amount > 0:
            return self.sb_versus_3bet(game_state, stack_e, my_in, opp_in)
        if game_state.button != me and not my_moves and len(opp_moves) == 1:
            if opp_moves[0][1] == "call":
                return self.bb_versus_limp(game_state, stack_e, my_in, opp_in)
            if opp_moves[0][1] == "raise":
                return self.bb_versus_raise(game_state, stack_e, my_in, opp_in)
        return self.generic_preflop(game_state)

    def open_raise_freqs(self):
        fold = self.freq("vs_open_raise", "fold", 0.45, 4.0)
        reraise = self.freq("vs_open_raise", "raise", 0.12, 4.0)
        call = max(0.0, 1.0 - fold - reraise)
        return fold, call, reraise

    def sb_open(self, game_state, stack_e, my_in, opp_in):
        call = self.freq("vs_open_shove", "call", 0.35, 4.0)
        lam = self.randomness("call_open_shove", 0.0, call)
        eq = self.equity_vs(self.my_class, self.band(0.0, call, lam))
        ev_shove = (1 - call) * opp_in + call * (eq * 2 * stack_e - stack_e)
        ev_fold = -my_in
        raise_to = game_state.min_raise_to
        fold, flat, reraise = self.open_raise_freqs()
        reraise_range = self.band(0.0, reraise, self.randomness("bb_3bet", 0.0, reraise))
        flat_range = self.band(reraise, reraise + flat, self.randomness("bb_call_raise", reraise, reraise + flat))
        eq_reraise = self.equity_vs(self.my_class, reraise_range)
        eq_flat = self.equity_vs(self.my_class, flat_range)
        ev_after_reraise = max(-raise_to, eq_reraise * 2 * stack_e - stack_e)
        ev_after_flat = eq_flat * 2 * raise_to * self.params["flat_realize"] - raise_to
        ev_small = fold * opp_in + reraise * ev_after_reraise + flat * ev_after_flat
        best = max(ev_fold, ev_shove, ev_small)
        if best == ev_shove:
            return ("raise", stack_e)
        if best == ev_small:
            return ("raise", raise_to)
        return ("fold", 0)

    def sb_versus_3bet(self, game_state, stack_e, my_in, opp_in):
        fold, flat, reraise = self.open_raise_freqs()
        weights = self.band(0.0, reraise, self.randomness("bb_3bet", 0.0, reraise))
        eq = self.equity_vs(self.my_class, weights)
        call = self.freq("vs_4b_shove", "call", 0.6, 3.0)
        call_weights = self.band(0.0, reraise * call, self.randomness("call_4b_shove", 0.0, reraise * call))
        eq_call = self.equity_vs(self.my_class, call_weights)
        ev_fold = -my_in
        ev_shove = (1 - call) * opp_in + call * (eq_call * 2 * stack_e - stack_e)
        ev_call = -my_in - 1.0
        if game_state.call_amount <= 0.25 * game_state.stacks[self.player_id]:
            pot_after = game_state.pot + game_state.call_amount
            ev_call = eq * pot_after * 0.9 - (my_in + game_state.call_amount)
        best = max(ev_fold, ev_shove, ev_call)
        if best == ev_shove and game_state.can_raise:
            return ("raise", game_state.max_raise_to)
        if best == ev_call:
            return ("call", 0)
        if best == ev_shove:
            return ("call", 0)
        return ("fold", 0)

    def bb_versus_limp(self, game_state, stack_e, my_in, opp_in):
        freqs = self.sb_first_freqs()
        top = freqs["raise"] + freqs["shove"]
        limp = freqs["limp"]
        lam_limp = self.randomness("sb_limp", top, top + limp)
        limp_range = self.band(top, top + limp, lam_limp)
        call = self.freq("vs_iso_shove", "call", 0.4, 4.0)
        lam_call = self.randomness("call_iso_shove", top, top + call * limp)
        call_range = self.band(top, top + call * limp, lam_call)
        eq_call = self.equity_vs(self.my_class, call_range)
        eq_limp = self.equity_vs(self.my_class, limp_range)
        ev_shove = (1 - call) * opp_in + call * (eq_call * 2 * stack_e - stack_e)
        ev_check = eq_limp * game_state.pot * self.params["check_realize"] - my_in
        ev_check += max(0.0, eq_limp - 0.5) * game_state.pot * self.params["implied"]
        if ev_shove > ev_check:
            return ("raise", stack_e)
        return ("call", 0)

    def bb_versus_raise(self, game_state, stack_e, my_in, opp_in):
        freqs = self.sb_first_freqs()
        raise_freq = freqs["raise"]
        lam_raise = self.randomness("sb_raise", 0.0, raise_freq)
        raise_range = self.band(0.0, raise_freq, lam_raise)
        call = self.freq("vs_3b_shove", "call", 0.45, 4.0)
        lam_call = self.randomness("call_3b_shove", 0.0, call * raise_freq)
        call_range = self.band(0.0, call * raise_freq, lam_call)
        eq_raise = self.equity_vs(self.my_class, raise_range)
        eq_call = self.equity_vs(self.my_class, call_range)
        base = self.params["call_realize"]
        realize = base + (1 - base) * min(1.0, opp_in / (0.4 * stack_e))
        ev_fold = -my_in
        ev_call = eq_raise * 2 * opp_in * realize - opp_in
        ev_shove = (1 - call) * opp_in + call * (eq_call * 2 * stack_e - stack_e)
        best = max(ev_fold, ev_call, ev_shove)
        if best == ev_shove:
            return ("raise", stack_e)
        if best == ev_call:
            return ("call", 0)
        return ("fold", 0)

    def versus_all_in(self, game_state, opp_moves, my_moves):
        if opp_moves and opp_moves[0][1] == "raise" and not my_moves and game_state.button == self.opp:
            shove = self.sb_first_freqs()["shove"]
            lam = self.randomness("sb_shove", 0.0, shove)
            weights = self.band(0.0, shove, lam)
        elif game_state.button == self.player_id and len(my_moves) == 1 and my_moves[0][1] == "raise":
            fold, flat, reraise = self.open_raise_freqs()
            weights = self.band(0.0, reraise, self.randomness("bb_3bet", 0.0, reraise))
        else:
            weights = self.band(0.0, 0.2, 0.1)
        eq = self.equity_vs(self.my_class, weights)
        if eq > game_state.pot_odds:
            return ("call", 0)
        return ("fold", 0)

    def generic_preflop(self, game_state):
        eq = self.equity_vs(self.my_class, self.band(0.0, 0.5, 0.2))
        if game_state.call_amount == 0:
            if eq > 0.6:
                return ("raise", game_state.current_bet + game_state.pot)
            return ("call", 0)
        if eq > 0.65:
            return ("raise", game_state.max_raise_to)
        if eq > game_state.pot_odds + 0.05:
            return ("call", 0)
        return ("fold", 0)

    def opponent_preflop_range(self):
        me, opp = self.player_id, self.opp
        moves = [h for h in self.history if h[3] == "preflop"]
        opp_moves = [h for h in moves if h[0] == opp]
        freqs = self.sb_first_freqs()
        top = freqs["raise"] + freqs["shove"]
        if self.start_totals is not None and opp_moves and self.opp is not None and self.button_is_me():
            fold, flat, reraise = self.open_raise_freqs()
            if opp_moves[0][1] == "call":
                return self.band(reraise, reraise + flat, self.randomness("bb_call_raise", reraise, reraise + flat))
            return self.band(0.0, max(0.05, reraise), self.randomness("bb_3bet", 0.0, reraise))
        if self.start_totals is not None and opp_moves:
            first = opp_moves[0]
            if first[1] == "call" and len(opp_moves) == 1:
                limp = freqs["limp"]
                return self.band(top, top + limp, self.randomness("sb_limp", top, top + limp))
            if first[1] == "raise":
                raise_freq = freqs["raise"]
                return self.band(0.0, raise_freq, self.randomness("sb_raise", 0.0, raise_freq))
        return self.band(0.0, 0.7, 0.2)

    def postflop(self, game_state):
        me, opp = self.player_id, self.opp
        street = game_state.street
        weights = self.opponent_preflop_range()
        opp_bets = 0
        for h in self.history:
            if h[0] == opp and h[1] == "raise" and h[3] != "preflop":
                opp_bets += 1
        aggression = self.freq("pf_aggr", "bet", 0.45, 4.0)
        keep = 1.0
        if opp_bets:
            keep = max(0.2, aggression ** opp_bets)
        to_call = game_state.call_amount
        drop = 0.0
        if to_call == 0:
            for h in self.history:
                if h[3] == street and h[0] == opp and h[1] == "call":
                    drop = min(0.6, aggression * self.params["check_drop"])
        board = [hand_eval.card_to_int(card) for card in game_state.board]
        eq = hand_eval.equity_vs_range(self.my_cards, board, weights, self.samples, self.rng, keep, drop)
        pot = game_state.pot
        my_stack = game_state.stacks[me]
        if to_call > 0:
            if eq >= self.params["raise_value"] and game_state.can_raise:
                total = game_state.current_bet + pot + to_call
                if total - game_state.bets[me] >= 0.5 * my_stack:
                    total = game_state.max_raise_to
                return ("raise", total)
            if eq >= game_state.pot_odds + self.params["call_margin"]:
                return ("call", 0)
            return ("fold", 0)
        fold_rate = self.freq("pf_fold", "fold", 0.4, 4.0)
        value_line = self.params["value_line_station"] if fold_rate < 0.3 else self.params["value_line"]
        if eq >= value_line and game_state.can_raise:
            size = min(1.0, max(0.6, 0.6 + self.params["size_slope"] * (1 - fold_rate)))
            bet = int(pot * size)
            if bet >= 0.55 * my_stack:
                return ("raise", game_state.max_raise_to)
            return ("raise", bet)
        bluff_chance = min(0.7, max(0.0, (fold_rate - 0.3) * self.params["bluff_scale"]))
        if street == "river":
            bluff_chance *= 0.8
        if eq < 0.45 and game_state.can_raise and self.rng.random() < bluff_chance:
            bet = int(pot * 0.6)
            if bet >= 0.55 * my_stack:
                return ("raise", game_state.max_raise_to)
            return ("raise", bet)
        return ("call", 0)

    def simple_decision(self, game_state):
        opponents = max(1, len(game_state.players_in_hand) - 1)
        board = [hand_eval.card_to_int(card) for card in game_state.board]
        weights = [1.0] * self.size
        if board:
            eq = hand_eval.equity_vs_range(self.my_cards, board, weights, 300, self.rng)
        else:
            eq = self.equity_vs(self.my_class, weights)
        eq = eq ** opponents
        if eq > 0.6:
            return ("raise", game_state.current_bet + game_state.pot)
        if game_state.call_amount == 0 or eq > game_state.pot_odds + 0.05:
            return ("call", 0)
        return ("fold", 0)

    def learn(self, game_state):
        me = self.player_id
        opp = 1 - me
        self.opp = opp
        self.opp_name = game_state.teams[opp]
        moves = [h for h in self.history if h[3] == "preflop"]
        opp_moves = [h for h in moves if h[0] == opp]
        my_moves = [h for h in moves if h[0] == me]
        shown = self.revealed.get(opp, [])
        opp_pct = None
        if len(shown) == 2:
            opp_pct = self.mid_pct[hand_eval.class_of(shown[0], shown[1])]
        totals = self.start_totals
        if game_state.button == opp and opp_moves:
            first = opp_moves[0]
            if first[1] == "fold":
                outcome = "fold"
            elif first[1] == "call":
                outcome = "limp"
            elif totals is not None and first[2] >= totals[opp]:
                outcome = "shove"
            else:
                outcome = "raise"
            self.add_count("sb_first", outcome)
            if opp_pct is not None and outcome in ("shove", "limp", "raise"):
                self.add_pct("sb_" + outcome, opp_pct)
            if outcome in ("limp", "raise") and my_moves and totals is not None:
                mine = my_moves[0]
                if mine[1] == "raise" and mine[2] >= totals[me]:
                    position = moves.index(mine)
                    replies = [h for h in moves[position + 1:] if h[0] == opp]
                    if replies:
                        context = "vs_iso_shove" if outcome == "limp" else "vs_3b_shove"
                        answer = "fold" if replies[0][1] == "fold" else "call"
                        self.add_count(context, answer)
                        if answer == "call" and opp_pct is not None:
                            self.add_pct("call_" + context[3:], opp_pct)
        elif game_state.button == me and my_moves and totals is not None:
            mine = my_moves[0]
            if mine[1] == "raise" and mine[2] >= totals[me] and opp_moves:
                answer = "fold" if opp_moves[0][1] == "fold" else "call"
                self.add_count("vs_open_shove", answer)
                if answer == "call" and opp_pct is not None:
                    self.add_pct("call_open_shove", opp_pct)
            elif mine[1] == "raise" and opp_moves:
                answer = opp_moves[0][1]
                self.add_count("vs_open_raise", answer)
                if opp_pct is not None and answer == "call":
                    self.add_pct("bb_call_raise", opp_pct)
                if opp_pct is not None and answer == "raise":
                    self.add_pct("bb_3bet", opp_pct)
                if answer == "raise" and len(my_moves) >= 2 and my_moves[1][1] == "raise" and my_moves[1][2] >= totals[me]:
                    position = moves.index(my_moves[1])
                    replies = [h for h in moves[position + 1:] if h[0] == opp]
                    if replies:
                        reply = "fold" if replies[0][1] == "fold" else "call"
                        self.add_count("vs_4b_shove", reply)
                        if reply == "call" and opp_pct is not None:
                            self.add_pct("call_4b_shove", opp_pct)
        for street in ("flop", "turn", "river"):
            street_moves = [h for h in self.history if h[3] == street]
            current_bet = 0
            last_raiser = None
            for h in street_moves:
                if h[0] == opp:
                    if current_bet == 0:
                        self.add_count("pf_aggr", "bet" if h[1] == "raise" else "check")
                    elif last_raiser == me:
                        self.add_count("pf_fold", "fold" if h[1] == "fold" else "continue")
                if h[1] == "raise":
                    current_bet = h[2]
                    last_raiser = h[0]
