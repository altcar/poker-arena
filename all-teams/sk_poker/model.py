import random
import time

from card_utils import SUITS, Card
from game_state import GameStateView
from player_action import DEALER, PlayerAction

# Cards are ints 0..51 internally: value index (c >> 2, 0 = deuce), suit (c & 3).
_WHEEL = (1 << 14) | 0b111100  # A, 2, 3, 4, 5 as value bits
MAX_SAMPLES = 800
TIME_BUDGET = 0.15  # seconds of Monte Carlo per decision


def _card_int(card: Card) -> int:
    return (card.value - 2) * 4 + SUITS.index(card.suit)


def _straight_high(mask: int) -> int:
    for hi in range(14, 5, -1):
        m = 0b11111 << (hi - 4)
        if mask & m == m:
            return hi
    return 5 if mask & _WHEEL == _WHEEL else 0


def _rank7(cards: list[int]) -> tuple[int, ...]:
    """Fast best-5 rank of 5 to 7 int cards; same ordering as evaluate()."""
    counts = [0] * 15
    suits = [0, 0, 0, 0]
    mask = 0
    for c in cards:
        v = (c >> 2) + 2
        counts[v] += 1
        suits[c & 3] += 1
        mask |= 1 << v
    flush = None
    for s in range(4):
        if suits[s] >= 5:
            vals = sorted(((c >> 2) + 2 for c in cards if c & 3 == s),
                          reverse=True)
            fmask = 0
            for v in vals:
                fmask |= 1 << v
            hi = _straight_high(fmask)
            if hi:
                return (8, hi)
            flush = (5, *vals[:5])
            break
    quads, trips, pairs, singles = [], [], [], []
    for v in range(14, 1, -1):
        k = counts[v]
        if k == 4:
            quads.append(v)
        elif k == 3:
            trips.append(v)
        elif k == 2:
            pairs.append(v)
        elif k == 1:
            singles.append(v)
    if quads:
        q = quads[0]
        return (7, q, max(v for v in range(2, 15) if counts[v] and v != q))
    if trips and (len(trips) > 1 or pairs):
        return (6, trips[0], max(trips[1:] + pairs))
    if flush:
        return flush
    hi = _straight_high(mask)
    if hi:
        return (4, hi)
    if trips:
        return (3, trips[0], *singles[:2])
    if len(pairs) >= 2:
        return (2, pairs[0], pairs[1], max(pairs[2:] + singles))
    if pairs:
        return (1, pairs[0], *singles[:3])
    return (0, *singles[:5])


def _chen(a: int, b: int) -> float:
    """Chen formula preflop score of a two-card hand."""
    hi, lo = max(a >> 2, b >> 2) + 2, min(a >> 2, b >> 2) + 2
    score = {14: 10, 13: 8, 12: 7, 11: 6}.get(hi, hi / 2)
    if hi == lo:
        return max(5.0, score * 2)
    if a & 3 == b & 3:
        score += 2
    gap = hi - lo - 1
    score -= (0, 1, 2, 4)[gap] if gap < 4 else 5
    if gap <= 1 and hi < 12:
        score += 1
    return score


# All 1326 starting hands, strongest first: "top f of hands" is a prefix.
_COMBOS = sorted(
    ((a, b) for a in range(52) for b in range(a + 1, 52)),
    key=lambda h: (-_chen(*h), -max(h), -min(h)),
)


def _connects(a: int, b: int, board: list[int]) -> bool:
    """Whether a hand has a pair (using a hole card) or a draw on board."""
    va, vb = a >> 2, b >> 2
    board_vals = {c >> 2 for c in board}
    if va == vb or va in board_vals or vb in board_vals:
        return True
    cards = [a, b, *board]
    for s in (a & 3, b & 3):
        if sum(1 for c in cards if c & 3 == s) >= 4:
            return True
    mask = 0
    for c in cards:
        mask |= 1 << ((c >> 2) + 2)
    if mask & (1 << 14):
        mask |= 1 << 1  # ace plays low too
    for lo in range(1, 11):
        window = mask >> lo & 0b11111
        if bin(window).count("1") >= 4 and (
            lo <= va + 2 <= lo + 4 or lo <= vb + 2 <= lo + 4
        ):
            return True
    return False


class Model:
    """
    Your bot: change anything in this file, and add files next to it.

    This object is what gets pickled, so load weights and data in __init__
    and keep them in plain attributes.
    """

    def __init__(self):
        # Load weights, and set up anything you learn across hands (e.g.
        # about other teams, by game_state.teams), here.
        self.hand = []
        self.player_id = None
        self.hole = []
        self.rng = random.Random(2026)
        # team -> counts: actions, raises, bets of ours faced, folds to them
        self.stats = {}
        self._new_hand()

    def _new_hand(self):
        self.board_seen = 0
        self.opp_raises = {}  # player id -> [preflop raises, postflop raises]
        self.awaiting = set()  # opponents yet to answer our last raise

    def seed(self, hand: list[Card], player_id: int):
        """
        Called once before the hand: your two hole cards and your id.

        Store them; run() is not given them again.
        """
        self.hand = hand
        self.player_id = player_id
        self.hole = [_card_int(c) for c in hand]
        self._new_hand()

    def run(
        self, actions: list[PlayerAction], game_state: GameStateView
    ) -> PlayerAction:
        """
        Called on your turn.

        actions: everything done since your last turn this hand, oldest
            first: player moves, your own previous move included, and the
            dealer's reveal_card.
        game_state: the public state of the hand, read-only, e.g.
            game_state.call_amount, game_state.status(self.player_id),
            game_state.min_raise_to.
        """
        try:
            self._observe(actions, game_state)
            return self._decide(game_state)
        except Exception:
            if game_state.call_amount == 0:
                return PlayerAction.call(self.player_id)
            return PlayerAction.fold(self.player_id)

    # Opponent modelling

    def _team_stats(self, team: str) -> dict:
        return self.stats.setdefault(
            team, {"actions": 0, "raises": 0, "faced": 0, "folds": 0}
        )

    def _observe(self, actions, game_state):
        for a in actions:
            pid = a.player_id
            if a.kind == "reveal_card":
                if pid == DEALER:
                    self.board_seen += 1
                continue
            if a.kind not in ("fold", "call", "raise"):
                continue
            if pid == self.player_id:
                if a.kind == "raise":
                    self.awaiting = set(range(game_state.num_players)) - {pid}
                continue
            st = self._team_stats(game_state.teams[pid])
            st["actions"] += 1
            if a.kind == "raise":
                st["raises"] += 1
                self.opp_raises.setdefault(pid, [0, 0])[
                    int(self.board_seen >= 3)
                ] += 1
            if pid in self.awaiting:
                self.awaiting.discard(pid)
                st["faced"] += 1
                if a.kind == "fold":
                    st["folds"] += 1

    def _aggression(self, team: str) -> float:
        st = self._team_stats(team)
        return (st["raises"] + 1) / (st["actions"] + 4)

    def _fold_to_bet(self, team: str) -> float:
        st = self._team_stats(team)
        return (st["folds"] + 1) / (st["faced"] + 3)

    def _range(self, pid: int, game_state) -> tuple[float, bool]:
        """(top fraction of hands, must connect with board) for a player,
        narrowed by their raises this hand and how often they raise."""
        aggr = self._aggression(game_state.teams[pid])
        pre, post = self.opp_raises.get(pid, (0, 0))
        factor = min(1.0, max(0.25, 2.2 * aggr))
        top = factor ** pre * factor ** (0.5 * post)
        return max(0.05, top), post > 0 and aggr < 0.6

    # Equity

    def _sample_hand(self, top, connect, used, board, deck):
        limit = max(1, int(top * len(_COMBOS)))
        rng = self.rng
        for attempt in range(30):
            a, b = _COMBOS[rng.randrange(limit)]
            if a in used or b in used:
                continue
            if (connect and attempt < 20 and not _connects(a, b, board)
                    and rng.random() < 0.75):
                continue
            return [a, b]
        free = [c for c in deck if c not in used]
        return rng.sample(free, 2)

    def _equity(self, hole, board, ranges) -> float:
        """Share of the pot won against the opponents' ranges, by Monte
        Carlo; ties count split."""
        known = set(hole) | set(board)
        deck = [c for c in range(52) if c not in known]
        missing = 5 - len(board)
        rng = self.rng
        deadline = time.perf_counter() + TIME_BUDGET
        won = 0.0
        n = 0
        while n < MAX_SAMPLES:
            if n >= 100 and n % 50 == 0 and time.perf_counter() > deadline:
                break
            used = set(known)
            holes = []
            for top, connect in ranges:
                h = self._sample_hand(top, connect, used, board, deck)
                used.update(h)
                holes.append(h)
            runout = list(board)
            while len(runout) < 5:
                c = deck[rng.randrange(len(deck))]
                if c not in used:
                    used.add(c)
                    runout.append(c)
            mine = _rank7(hole + runout)
            theirs = [_rank7(h + runout) for h in holes]
            best = max(theirs)
            if mine > best:
                won += 1
            elif mine == best:
                won += 1 / (1 + theirs.count(best))
            n += 1
        return won / n

    # Decisions

    def _raise(self, gs, total, commit=True) -> PlayerAction:
        low, high = gs.raise_range
        total = min(high, max(low, int(total)))
        if commit and total >= 0.6 * high:
            total = high  # most of the stack goes in: just shove
        return PlayerAction.raise_to(self.player_id, total)

    def _decide(self, gs) -> PlayerAction:
        me = self.player_id
        opps = [p for p in gs.players_in_hand if p != me]
        if not opps:
            return PlayerAction.call(me)
        board = [_card_int(c) for c in gs.board]
        ranges = [self._range(p, gs) for p in opps]
        equity = self._equity(self.hole, board, ranges)

        n = len(opps)
        preflop = gs.street == "preflop"
        to_call = gs.call_amount
        pot = gs.pot
        if n == 1:
            fold_rate = self._fold_to_bet(gs.teams[opps[0]])
            value_at = 0.6 + 0.12 * fold_rate
        else:
            fair = 1 / (n + 1)
            fold_rate = 1.0
            for p in opps:
                fold_rate *= self._fold_to_bet(gs.teams[p])
            value_at = fair + (1 - fair) * 0.35
        if to_call > 0 and not preflop:
            value_at += 0.08  # raising into a bet needs more

        if gs.can_raise:
            bb = gs.big_blind
            if equity >= value_at:
                if preflop:
                    if gs.current_bet <= bb:
                        total = 3 * bb
                    else:
                        total = (4 if equity > 0.75 else 3) * gs.current_bet
                else:
                    frac = 1.0 if equity > 0.85 else 0.7
                    total = gs.current_bet + frac * (pot + to_call)
                return self._raise(gs, total)
            # Steal or bluff when it is cheap and they fold often enough.
            unopened = preflop and gs.current_bet <= bb
            if n == 1 and (to_call == 0 or unopened):
                bluff = max(0.0, min(0.6, fold_rate - 0.2))
                if equity > 0.4:
                    bluff *= 1.3
                if self.rng.random() < bluff:
                    if preflop:
                        total = 3 * bb
                    else:
                        total = gs.current_bet + 0.6 * (pot + to_call)
                    return self._raise(gs, total, commit=False)

        if to_call == 0:
            return PlayerAction.call(me)
        margin = 0.02 if n == 1 else 0.04
        if equity >= gs.pot_odds + margin:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)


    def end_game(
        self, actions: list[PlayerAction], game_state: GameStateView
    ):
        """
        Called after every hand, whether or not you were still in it.

        actions: everything since your last turn: the last moves, then at a
            showdown each remaining player's hole cards (reveal_card with
            that player's id; the dealer's reveal_card is a board card),
            then one win per player who won chips (amount = their total
            from every pot). A player who wins because everyone else
            folded shows nothing.
        game_state: the final state of the hand, stacks after the pot.

        Learn from the hand here. Per-hand fields need no clearing: seed()
        sets them again at the start of the next hand.
        """
        try:
            self._observe(actions, game_state)
        except Exception:
            pass
