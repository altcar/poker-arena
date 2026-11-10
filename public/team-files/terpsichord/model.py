import random
import time

from card_utils import evaluate, full_deck
from player_action import DEALER, PlayerAction


def _chen(hole):
    a, b = sorted((hole[0].value, hole[1].value), reverse=True)

    def pts(v):
        return {14: 10, 13: 8, 12: 7, 11: 6}.get(v, v / 2)

    s = pts(a)
    if a == b:
        return max(5.0, s * 2)
    if hole[0].suit == hole[1].suit:
        s += 2
    gap = a - b - 1
    s -= {0: 0, 1: 1, 2: 2, 3: 4}.get(gap, 5)
    if gap <= 1 and a < 12:
        s += 1
    return s


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None
        self.pending = False  # did I raise and not yet see the responses?
        self.budget = 0.45  # seconds of simulation per decision
        # team name -> [raises, calls, folds, raise_opps, folds_to_my_raise]
        self.stats = {}

    # ------------------------------------------------------------------ hooks
    def seed(self, hand, player_id):
        self.hand = list(hand)
        self.player_id = player_id
        self.pending = False

    def end_game(self, actions, game_state):
        try:
            self._observe(actions, game_state)
        except Exception:
            pass

    def run(self, actions, game_state):
        try:
            self._observe(actions, game_state)
            action = self._decide(game_state)
            game_state.validate_action(action)
            return action
        except Exception:
            return self._safe(game_state)

    # --------------------------------------------------------------- learning
    def _observe(self, actions, gs):
        me = self.player_id
        for a in actions:
            if a.kind in ("reveal_card", "win"):
                if a.player_id == DEALER:
                    self.pending = False  # new street
                continue
            if a.player_id == me:
                self.pending = a.kind == "raise"
                continue
            if a.player_id == DEALER or a.kind not in ("fold", "call", "raise"):
                continue
            team = gs.teams[a.player_id]
            st = self.stats.setdefault(team, [0, 0, 0, 0, 0])
            st["raise call fold".split().index(a.kind)] += 1
            if self.pending:
                st[3] += 1
                if a.kind == "fold":
                    st[4] += 1

    def _raise_rate(self, team):
        st = self.stats.get(team)
        if not st:
            return 0.3
        # smoothed with a prior of 4 actions at 30% raises
        return (st[0] + 1.2) / (st[0] + st[1] + st[2] + 4.0)

    def _fold_rate(self, team):
        st = self.stats.get(team)
        if not st:
            return 0.4
        return (st[4] + 1.0) / (st[3] + 2.5)

    # ------------------------------------------------------------- estimation
    def _implied_strength(self, gs, i):
        """0..1: how strong opponent i's hand looks from this street's betting."""
        me = self.player_id
        bb = gs.big_blind
        bet = gs.bets[i]
        if gs.street == "preflop":
            if bet <= bb:
                return 0.0
            s = min(0.9, max(0.0, (bet / bb - 2.0) / 12.0))
        else:
            if bet <= 0 or bet <= gs.bets[me]:
                return 0.0
            frac = bet / max(gs.pot - bet, 1)
            s = min(1.0, 0.3 + 0.5 * frac)
        rr = self._raise_rate(gs.teams[i])
        if rr > 0.35:  # maniac: raises mean little
            s *= 1.0 - 0.8 * min(1.0, (rr - 0.35) / 0.45)
        elif rr < 0.12:  # very passive: a raise means a lot
            s = min(1.0, s * 1.3 + 0.2)
        return s

    def _equity(self, n, strengths, hole, board):
        known = {str(c) for c in hole} | {str(c) for c in board}
        deck = [c for c in full_deck() if str(c) not in known]
        need = 5 - len(board)
        preflop = len(board) == 0
        total = 0.0
        trials = 0
        end = time.perf_counter() + self.budget
        while trials < 2500 and (trials < 60 or time.perf_counter() < end):
            for _ in range(8):
                draw = random.sample(deck, 2 * n + need)
                ok = True
                for k, s in enumerate(strengths):
                    if s <= 0.05 or random.random() < 0.15:
                        continue
                    oh = draw[2 * k:2 * k + 2]
                    if preflop:
                        if _chen(oh) < -1 + s * 11:
                            ok = False
                            break
                    else:
                        cat = evaluate(oh + board)[0]
                        if (s >= 0.5 and cat < 1) or (s >= 0.85 and cat < 2):
                            ok = False
                            break
                if ok:
                    break
            full = board + draw[2 * n:]
            mine = evaluate(hole + full)
            best = None
            ties = 0
            lost = False
            for k in range(n):
                theirs = evaluate(draw[2 * k:2 * k + 2] + full)
                if theirs > mine:
                    lost = True
                    break
                if theirs == mine:
                    ties += 1
            if not lost:
                total += 1.0 / (ties + 1)
            trials += 1
        return total / trials

    # --------------------------------------------------------------- decision
    def _raise(self, gs, target):
        me = self.player_id
        lo, hi = gs.raise_range
        t = int(max(lo, min(hi, target)))
        if t > 0.5 * hi:  # committed: just shove
            t = int(hi)
        return PlayerAction.raise_to(me, t)

    def _safe(self, gs):
        me = self.player_id
        try:
            if gs.call_amount == 0:
                return PlayerAction.call(me)
        except Exception:
            pass
        return PlayerAction.fold(me)

    def _decide(self, gs):
        me = self.player_id
        bb = gs.big_blind
        c = gs.call_amount
        pot = gs.pot
        preflop = gs.street == "preflop"
        board = list(gs.board)
        hole = list(self.hand)
        opps = [i for i in gs.players_in_hand if i != me]
        if not opps:
            return PlayerAction.call(me)
        n = len(opps)
        strengths = [self._implied_strength(gs, i) for i in opps]
        e = self._equity(n, strengths, hole, board)
        can_raise = gs.can_raise
        ftr = self._fold_rate(gs.teams[opps[0]]) if n == 1 else 0.4

        opening = c == 0 or (preflop and gs.current_bet <= bb)

        # Short stack preflop, nobody has raised: push or fold.
        if preflop and opening and can_raise and gs.stacks[me] + gs.bets[me] <= 10 * bb:
            cut = 0.45 if gs.stacks[me] + gs.bets[me] <= 6 * bb else 0.5
            cut -= 0.05 * (n - 1)
            if e >= cut:
                return self._raise(gs, gs.max_raise_to)
            return PlayerAction.call(me) if c == 0 else PlayerAction.fold(me)

        # Value raise threshold.
        if opening:
            th = 0.60 if preflop else 0.64
        else:
            th = 0.74
        th = max(0.42, th - 0.10 * (n - 1))
        if ftr < 0.25:  # calling station: value bet thinner
            th -= 0.04

        if can_raise and e >= th:
            if preflop:
                base = gs.current_bet * 2.8 if gs.current_bet > bb else 3 * bb
                target = base
            else:
                frac = 1.0 if (e > 0.85 or ftr < 0.25) else 0.7
                target = gs.current_bet + frac * (pot + c)
            return self._raise(gs, target)

        if c == 0:
            # Check, or bluff against opponents who fold too much.
            if can_raise and n == 1 and not preflop and ftr > 0.42 and e < 0.5:
                freq = min(0.35, ftr - 0.35)
                if e < 0.28 or gs.street == "river":
                    freq *= 0.5
                if random.random() < freq:
                    return self._raise(gs, 0.66 * pot)
            return PlayerAction.call(me)

        if e >= gs.pot_odds + 0.02:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)
