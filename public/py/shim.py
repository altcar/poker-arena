"""Browser-side shims for the organisers' engine classes.

The competition models were written against `card_utils`, `player_action` and
`game_state` from Manchester-AI-Society/poker_bot. Those modules use pydantic,
which is heavy in the browser; these shims implement the same public surface in
plain Python. The models themselves are unchanged.

The JS bridge calls seed(), run() and end_game() with JSON strings.
"""

import importlib
import json
import os
import pickle
import sys
from collections import Counter
from itertools import combinations

RANKS = "23456789TJQKA"
SUITS = "CDHS"
CATEGORIES = [
    "high_card", "pair", "two_pair", "three_of_a_kind", "straight",
    "flush", "full_house", "four_of_a_kind", "straight_flush",
]
DEALER = -1
BOARD_SIZES = {3: "flop", 4: "turn", 5: "river"}


class Card:
    __slots__ = ("rank", "suit")

    def __init__(self, rank, suit):
        if rank not in RANKS or suit not in SUITS:
            raise ValueError(f"bad card {rank!r}{suit!r}")
        self.rank = rank
        self.suit = suit

    @classmethod
    def parse(cls, text):
        if len(text) != 2:
            raise ValueError(f"a card is two characters, not {text!r}")
        return cls(text[0], text[1])

    @property
    def value(self):
        return RANKS.index(self.rank) + 2

    def __str__(self):
        return self.rank + self.suit

    def __repr__(self):
        return f"Card({self.rank!r}, {self.suit!r})"

    def __eq__(self, other):
        return isinstance(other, Card) and self.rank == other.rank and self.suit == other.suit

    def __hash__(self):
        return hash((self.rank, self.suit))


def _straight_high(values):
    if len(set(values)) != 5:
        return None
    if values[0] - values[4] == 4:
        return values[0]
    if values == [14, 5, 4, 3, 2]:
        return 5
    return None


def _rank_five(cards):
    values = sorted((card.value for card in cards), reverse=True)
    flush = len({card.suit for card in cards}) == 1
    straight_high = _straight_high(values)
    groups = sorted(Counter(values).items(), key=lambda g: (g[1], g[0]), reverse=True)
    counts = tuple(count for _, count in groups)
    ranks = tuple(value for value, _ in groups)
    if straight_high and flush:
        return (8, straight_high)
    if counts == (4, 1):
        return (7, *ranks)
    if counts == (3, 2):
        return (6, *ranks)
    if flush:
        return (5, *values)
    if straight_high:
        return (4, straight_high)
    if counts == (3, 1, 1):
        return (3, *ranks)
    if counts == (2, 2, 1):
        return (2, *ranks)
    if counts == (2, 1, 1, 1):
        return (1, *ranks)
    return (0, *values)


def evaluate(cards):
    if len(set(cards)) != len(cards):
        raise ValueError(f"duplicate cards in {[str(c) for c in cards]}")
    return max(_rank_five(five) for five in combinations(cards, 5))


def category(rank):
    return CATEGORIES[rank[0]]


def full_deck():
    return [Card(rank, suit) for rank in RANKS for suit in SUITS]


class PlayerAction:
    __slots__ = ("player_id", "kind", "amount", "card")

    def __init__(self, player_id, kind, amount=0, card=None):
        self.player_id = player_id
        self.kind = kind
        self.amount = amount
        self.card = card

    @classmethod
    def fold(cls, player_id):
        return cls(player_id, "fold")

    @classmethod
    def call(cls, player_id):
        return cls(player_id, "call")

    @classmethod
    def raise_to(cls, player_id, total):
        return cls(player_id, "raise", total)

    @classmethod
    def win(cls, player_id, amount):
        return cls(player_id, "win", amount)

    @classmethod
    def reveal_card(cls, card, player_id=DEALER):
        return cls(player_id, "reveal_card", 0, card)

    def __repr__(self):
        return f"PlayerAction({self.player_id!r}, {self.kind!r}, {self.amount!r})"


class InvalidAction(ValueError):
    pass


class GameState:
    """A mutable copy of the official GameState, used by models that replay
    the hand themselves (bismillahinsurance, humza000, tvt)."""

    def __init__(self, stacks=(500, 500), teams=None):
        self.setup_hand(list(stacks), teams=teams)

    def setup_hand(self, stacks, button=0, small_blind=10, big_blind=20, teams=None):
        stacks = list(stacks)
        count = len(stacks)
        if count < 2:
            raise ValueError("a hand needs at least two players")
        if button >= count:
            raise ValueError(f"button must be a player id 0..{count - 1}, not {button}")
        if small_blind > big_blind:
            raise ValueError(f"small_blind {small_blind} is bigger than big_blind {big_blind}")
        if teams is None:
            teams = [f"seat {p}" for p in range(count)]
        teams = list(teams)
        if len(teams) != count:
            raise ValueError(f"{len(teams)} teams for {count} stacks: give one per seat")
        if len(set(teams)) != count:
            raise ValueError(f"team names must be unique, not {teams}")
        self.teams = teams
        self.button = button
        self.small_blind = small_blind
        self.big_blind = big_blind
        self.stacks = stacks
        self.bets = [0] * count
        self.folded = [False] * count
        self.acted = [False] * count
        self.board = []
        self.street = "preflop"
        self.pot = 0
        self.last_raise = big_blind
        self.to_act = None

        small = button if count == 2 else (button + 1) % count
        big = (small + 1) % count
        self._commit(small, min(small_blind, self.stacks[small]))
        self._commit(big, min(big_blind, self.stacks[big]))
        self._pass_turn(big)

    def set_stack(self, player_id, chips):
        if player_id >= self.num_players:
            raise ValueError(f"no player {player_id}")
        self.stacks[player_id] = chips

    @property
    def num_players(self):
        return len(self.stacks)

    @property
    def players_in_hand(self):
        return [p for p in range(self.num_players) if not self.folded[p]]

    @property
    def active_players(self):
        return [p for p in range(self.num_players) if self.status(p) == "active"]

    @property
    def is_street_closed(self):
        return self.to_act is None

    @property
    def is_hand_over(self):
        return len(self.players_in_hand) == 1 or (
            self.street == "river" and self.is_street_closed
        )

    @property
    def current_bet(self):
        return max(self.bets)

    def status(self, player_id):
        if self.folded[player_id]:
            return "folded"
        return "all_in" if self.stacks[player_id] == 0 else "active"

    def effective_stack(self, player_id):
        others = [self.stacks[p] for p in self.players_in_hand if p != player_id]
        return min(self.stacks[player_id], max(others, default=0))

    def spr(self, player_id):
        return self.effective_stack(player_id) / self.pot

    @property
    def call_amount(self):
        me = self.to_act
        return min(self.current_bet - self.bets[me], self.stacks[me])

    @property
    def max_raise_to(self):
        return self.bets[self.to_act] + self.stacks[self.to_act]

    @property
    def min_raise_to(self):
        return min(self.current_bet + self.last_raise, self.max_raise_to)

    @property
    def can_raise(self):
        others = [p for p in self.active_players if p != self.to_act]
        return (
            not self.acted[self.to_act]
            and self.max_raise_to > self.current_bet
            and len(others) > 0
        )

    @property
    def legal_actions(self):
        if self.is_street_closed:
            return []
        return ["fold", "call"] + (["raise"] if self.can_raise else [])

    @property
    def raise_range(self):
        if not self.can_raise:
            return None
        return (self.min_raise_to, self.max_raise_to)

    @property
    def pot_odds(self):
        if self.call_amount == 0:
            return 0.0
        return self.call_amount / (self.pot + self.call_amount)

    def validate_action(self, action):
        if action.player_id != self.to_act:
            raise InvalidAction(
                f"player {action.player_id} acted out of turn; it is player {self.to_act}'s turn"
            )
        if action.kind in ("fold", "call"):
            return
        if action.kind != "raise":
            raise InvalidAction(f"{action.kind!r} is not a move: fold, call or raise")
        if not self.can_raise:
            raise InvalidAction("you cannot raise now; fold or call")
        low, high = self.min_raise_to, self.max_raise_to
        if not low <= action.amount <= high:
            raise InvalidAction(
                f"raise_to({action.amount}) is outside the allowed range: "
                f"the total must be between {low} and {high}"
            )

    def process_action(self, action):
        if action.player_id == DEALER:
            self.deal_card(action.card)
            return
        if action.kind in ("reveal_card", "win"):
            self._settle(action)
            return
        self.validate_action(action)
        player_id = action.player_id
        if action.kind == "fold":
            self.folded[player_id] = True
        elif action.kind == "call":
            self._commit(player_id, self.call_amount)
        elif action.kind == "raise":
            raise_size = action.amount - self.current_bet
            self._commit(player_id, action.amount - self.bets[player_id])
            if raise_size >= self.last_raise:
                self.last_raise = raise_size
                self.acted = [False] * self.num_players
        else:
            raise InvalidAction(f"{action.kind!r} is not a move")
        self.acted[player_id] = True
        self._pass_turn(after=player_id)

    def deal_card(self, card):
        if self.is_hand_over:
            raise InvalidAction("the hand is over")
        if not self.is_street_closed:
            raise InvalidAction(
                f"betting is still open: it is player {self.to_act}'s turn"
            )
        if card in self.board:
            raise InvalidAction(f"{card} is already on the board")
        self.board.append(card)
        if len(self.board) in BOARD_SIZES:
            self._next_street(BOARD_SIZES[len(self.board)])

    def _settle(self, action):
        if not self.is_hand_over:
            raise InvalidAction(
                f"{action.kind!r} only comes once the hand is over"
            )
        player_id = action.player_id
        if not 0 <= player_id < self.num_players:
            raise InvalidAction(f"no player {player_id}")
        if self.folded[player_id]:
            raise InvalidAction(f"player {player_id} has folded")
        if action.kind == "win":
            self.stacks[player_id] += action.amount

    def _next_street(self, street):
        self.street = street
        self.bets = [0] * self.num_players
        self.acted = [False] * self.num_players
        self.last_raise = self.big_blind
        self._pass_turn(after=self.button)

    def _commit(self, player_id, chips):
        assert chips <= self.stacks[player_id], "bet exceeds stack"
        self.stacks[player_id] -= chips
        self.bets[player_id] += chips
        self.pot += chips

    def _pass_turn(self, after):
        active = self.active_players
        waiting = [
            p for p in active if not self.acted[p] or self.bets[p] < self.current_bet
        ]
        lone_and_matched = (
            len(active) == 1 and self.bets[active[0]] >= self.current_bet
        )
        if len(self.players_in_hand) == 1 or lone_and_matched or not waiting:
            self.to_act = None
            return
        for step in range(1, self.num_players + 1):
            player_id = (after + step) % self.num_players
            if player_id in waiting:
                self.to_act = player_id
                return


class GameStateView:
    """A read-only window onto a GameState, or onto a bridge snapshot."""

    PUBLIC = frozenset({
        "button", "small_blind", "big_blind", "stacks", "bets", "folded",
        "acted", "board", "street", "pot", "last_raise", "to_act", "teams",
        "num_players", "players_in_hand", "active_players",
        "is_street_closed", "is_hand_over", "current_bet",
        "status", "effective_stack", "spr",
        "call_amount", "max_raise_to", "min_raise_to", "can_raise",
        "legal_actions", "raise_range", "pot_odds", "validate_action",
    })

    __slots__ = ("_state",)

    def __init__(self, data):
        if isinstance(data, GameState):
            object.__setattr__(self, "_state", data)
            return
        state = GameState.__new__(GameState)
        state.button = data["button"]
        state.small_blind = data["small_blind"]
        state.big_blind = data["big_blind"]
        state.stacks = list(data["stacks"])
        state.bets = list(data["bets"])
        state.folded = list(data["folded"])
        state.acted = list(data["acted"])
        state.board = [Card.parse(card) for card in data.get("board", [])]
        state.street = data["street"]
        state.pot = data["pot"]
        state.last_raise = data["last_raise"]
        state.to_act = data["to_act"]
        state.teams = list(data["teams"])
        object.__setattr__(self, "_state", state)

    def __getattr__(self, name):
        if name not in GameStateView.PUBLIC:
            raise AttributeError(f"GameStateView has no {name!r}")
        value = getattr(self._state, name)
        return tuple(value) if isinstance(value, list) else value

    def __setattr__(self, name, value):
        raise AttributeError("GameStateView is read-only")

    def __dir__(self):
        return sorted(GameStateView.PUBLIC)


# --------------------------------------------------------------------------
# Loading and running the competition models
# --------------------------------------------------------------------------

SHIM_DIR = "/py"
TEAM_CACHE = {}
TEAM_FOLDERS = set()
ACTIVE_TEAM = None

RESERVED = {
    "card_utils", "engine", "game_interface", "game_state", "player_action",
    "arena", "evals", "submit", "conftest",
}

TEAM_MODULES = {
    "model", "pbbrain", "pbcore", "pbpre", "pb_cards", "pb_equity",
    "pb_opponents", "pb_preflop", "fasteval", "pf_table", "pf_tables",
    "pf_data", "preflop_table", "preflop_tables", "preflop_data",
    "preflop_ranges", "equity_engine", "postflop_policy", "preflop_policy",
    "sidepot_equity", "all_in_equity", "features", "poker_eval",
    "pokey_strategy", "hand_eval", "handeval", "pokerlib", "policy",
    "botcore", "brain", "ranges", "equity", "cards", "decision", "opponents",
    "policy_net", "policy_weights", "strength", "tables", "evaluator",
    "strategy", "tracker", "bot_brain", "bot_decide", "bot_eval", "bot_opp",
    "bot_preflop", "bot_tables", "poker_math", "river_policy",
    "poker_strength", "pk_cards", "pk_equity", "pk_estimate", "pk_eval",
    "pk_opponent", "pk_postflop", "pk_preflop", "pk_ranges", "pk_tables",
    "pk_pretable", "pk_range", "core", "opp", "policy_v1", "blueprint",
    "translation", "hunter_chart", "hunter_eval", "hand_history",
    "bucket_data", "buckets", "arena_sdk", "arena_translate", "combo_bot",
    "core_allin", "core_equity", "core_interface", "main_allin", "main_bot",
    "main_fallback", "main_opponent", "main_postflop", "main_preflop",
    "team_equity_table", "team_opponent", "team_preflop", "team_ranges",
    "preflop_nash", "river_solver", "fast_eval",
}


def _clear_team_modules():
    for name in list(sys.modules):
        if name in TEAM_MODULES:
            sys.modules.pop(name, None)


def _preload(folder):
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".py"):
            continue
        stem = name[:-3]
        if stem in RESERVED or stem.startswith("test_"):
            continue
        try:
            importlib.import_module(stem)
        except Exception:
            pass


def _activate(team):
    global ACTIVE_TEAM
    if ACTIVE_TEAM == team:
        return
    entry = TEAM_CACHE[team]
    sys.path[:] = [p for p in sys.path if p not in TEAM_FOLDERS]
    sys.path.insert(0, SHIM_DIR)
    sys.path.insert(0, entry["folder"])
    _clear_team_modules()
    _preload(entry["folder"])
    try:
        importlib.import_module("model")
    except Exception:
        pass
    ACTIVE_TEAM = team


def load_team(team, folder, pkl_name=""):
    global ACTIVE_TEAM
    if team in TEAM_CACHE:
        return json.dumps({"ok": True, "cached": True, "source": TEAM_CACHE[team]["source"]})
    _clear_team_modules()
    ACTIVE_TEAM = None
    sys.path.insert(0, SHIM_DIR)
    sys.path.insert(0, folder)
    try:
        model_module = importlib.import_module("model")
        _preload(folder)
        source = "model"
        try:
            instance = model_module.Model()
        except Exception:
            if not pkl_name:
                raise
            with open(os.path.join(folder, pkl_name), "rb") as handle:
                instance = pickle.load(handle)
            source = "pickle"
        TEAM_CACHE[team] = {"instance": instance, "folder": folder, "source": source}
        TEAM_FOLDERS.add(folder)
        return json.dumps({"ok": True, "source": source})
    except Exception as error:
        return json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"})
    finally:
        sys.path.remove(folder)


def _action_from_json(data):
    card = Card.parse(data["card"]) if data.get("card") else None
    return PlayerAction(
        int(data["player_id"]), data["kind"], int(data.get("amount") or 0), card
    )


def seed(team, hole_json, player_id):
    _activate(team)
    hole = [Card.parse(card) for card in json.loads(hole_json)]
    TEAM_CACHE[team]["instance"].seed(hole, int(player_id))
    return True


def run(team, state_json, actions_json):
    _activate(team)
    view = GameStateView(json.loads(state_json))
    actions = [_action_from_json(action) for action in json.loads(actions_json)]
    action = TEAM_CACHE[team]["instance"].run(actions, view)
    return json.dumps({
        "player_id": int(action.player_id),
        "kind": action.kind,
        "amount": int(getattr(action, "amount", 0) or 0),
    })


def end_game(team, state_json, actions_json):
    _activate(team)
    instance = TEAM_CACHE[team]["instance"]
    callback = getattr(instance, "end_game", None)
    if callable(callback):
        view = GameStateView(json.loads(state_json))
        actions = [_action_from_json(action) for action in json.loads(actions_json)]
        callback(actions, view)
    return True


def loaded():
    return json.dumps({
        team: {"source": entry["source"], "folder": entry["folder"]}
        for team, entry in TEAM_CACHE.items()
    })
