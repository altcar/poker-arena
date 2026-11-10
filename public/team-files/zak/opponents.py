"""
The opponent model: one generative model per team of how it acts given its
hand, P(outcome | strength bucket, situation), learned from scratch.

Outcomes are fold, check, call and raises bucketed by size. Each cell
(situation, strength bucket) holds Dirichlet counts, shrunk towards the
population (every team seen so far), which is shrunk towards a hand-written
base: weaker hands fold and check more, stronger ones raise more.

The same model answers what a team holds (Bayes over its actions this
hand), how it will respond to our bet, and how much to trust either answer.
"""

from dataclasses import dataclass, field

from cards import COMBOS, NUM_COMBOS, combo_id
from hand_history import ActionRecord, HandHistory
from strength import NUM_BUCKETS, StrengthCache

OUTCOMES = ("fold", "check", "call", "small", "medium", "large", "allin")
FOLD, CHECK, CALL, SMALL, MEDIUM, LARGE, ALLIN = range(len(OUTCOMES))
RAISES = (SMALL, MEDIUM, LARGE, ALLIN)
FACING = ("none", "small", "medium", "large")

# A raise's size_frac (beyond the call, over the pot after calling).
RAISE_EDGES = (0.4, 0.8, 1.5)  # small < 0.4 <= medium < 0.8 <= large <= 1.5
# The bet faced over the pot including it: half pot = 0.33, pot = 0.5.
FACING_EDGES = (0.3, 0.45)  # small <= 0.3 < medium <= 0.45 < large


@dataclass(frozen=True)
class Situation:
    """What an action depends on besides the hand."""

    street: str  # "preflop", "flop", "turn", "river"
    facing: str  # one of FACING
    aggressor: bool  # raised earlier this hand

    @classmethod
    def of(cls, street: str, facing: int, pot: int, aggressor: bool):
        """facing: chips owed; pot: every chip in, the bet faced included."""
        if facing <= 0:
            level = "none"
        else:
            frac = facing / max(1, pot)
            level = ("small" if frac <= FACING_EDGES[0]
                     else "medium" if frac <= FACING_EDGES[1] else "large")
        return cls(street, level, aggressor)

    @classmethod
    def from_record(cls, r: ActionRecord) -> "Situation":
        return cls.of(r.street, r.facing, r.pot_before, r.aggressor)

    def key(self) -> tuple:
        return (self.street, self.facing, self.aggressor)


def outcome_of(r: ActionRecord) -> int:
    if r.kind == "fold":
        return FOLD
    if r.kind == "check":
        return CHECK
    if r.kind == "call":
        return CALL
    frac = r.size_frac
    if r.all_in or frac > RAISE_EDGES[2]:
        return ALLIN
    return SMALL if frac < RAISE_EDGES[0] else MEDIUM if frac < RAISE_EDGES[1] else LARGE


def legal(sit: Situation, can_raise: bool) -> list[int]:
    """The outcomes open to a player (folding when free counts: some bots do)."""
    moves = [FOLD, CHECK] if sit.facing == "none" else [FOLD, CALL]
    return moves + list(RAISES) if can_raise else moves


def group_of(sit: Situation) -> tuple[bool, bool]:
    """A team's style is pooled over these broad groups of situations:
    (preflop, nothing to call)."""
    return (sit.street == "preflop", sit.facing == "none")


def base_probs(sit: Situation, bucket: int) -> list[float]:
    """Hand-written default before any data: stronger hands raise more,
    weaker ones fold (facing a bet) or check more, and a few bluff."""
    s = bucket / (NUM_BUCKETS - 1)  # 0 air .. 1 very strong
    p = [0.0] * len(OUTCOMES)
    raise_p = 0.08 + 0.5 * s * s
    if sit.facing == "none":
        p[FOLD] = 0.01
        p[CHECK] = 1 - raise_p - 0.01
    else:
        pressure = {"small": 0.6, "medium": 0.8, "large": 1.0}[sit.facing]
        p[FOLD] = max(0.02, pressure * (0.8 - 0.9 * s))
        p[CALL] = max(0.05, 1 - raise_p - p[FOLD])
    for o, share in zip(RAISES, (0.3, 0.35, 0.2, 0.15)):
        p[o] = raise_p * share
    total = sum(p)
    return [x / total for x in p]


@dataclass
class ActionModel:
    """Outcome counts per (situation, strength bucket); fractional counts
    come from hands whose cards we never saw."""

    counts: dict[tuple, list[float]] = field(default_factory=dict)
    totals: dict[tuple, float] = field(default_factory=dict)  # per situation
    # Per group (group_of): outcomes seen, and what the population model
    # predicted for those same moves when they were made.
    group_obs: dict[tuple, list[float]] = field(default_factory=dict)
    group_exp: dict[tuple, list[float]] = field(default_factory=dict)

    def add(self, sit: Situation, bucket_probs: list[float], outcome: int) -> None:
        """One observed outcome, spread over strength buckets by bucket_probs."""
        for b, w in enumerate(bucket_probs):
            if w <= 0:
                continue
            cell = self.counts.setdefault(sit.key() + (b,), [0.0] * len(OUTCOMES))
            cell[outcome] += w
        self.totals[sit.key()] = self.totals.get(sit.key(), 0.0) + sum(bucket_probs)

    def cell(self, sit: Situation, bucket: int) -> list[float] | None:
        return self.counts.get(sit.key() + (bucket,))

    def n(self, sit: Situation) -> float:
        return self.totals.get(sit.key(), 0.0)

    def add_group(self, sit: Situation, outcome: int,
                  expected: list[float]) -> None:
        g = group_of(sit)
        self.group_obs.setdefault(g, [0.0] * len(OUTCOMES))[outcome] += 1.0
        exp = self.group_exp.setdefault(g, [0.0] * len(OUTCOMES))
        for o, e in enumerate(expected):
            exp[o] += e

    def marginal(self, sit: Situation) -> list[float] | None:
        """Counts in sit summed over strength buckets."""
        cells = [self.counts.get(sit.key() + (b,)) for b in range(NUM_BUCKETS)]
        cells = [c for c in cells if c]
        if not cells:
            return None
        return [sum(col) for col in zip(*cells)]


def _shrink(counts: list[float] | None, prior: list[float], kappa: float):
    """Dirichlet posterior mean: counts plus kappa pseudo-counts of prior."""
    if not counts:
        return prior
    n = sum(counts)
    return [(c + kappa * q) / (n + kappa) for c, q in zip(counts, prior)]


@dataclass
class Response:
    """How a team is expected to answer a bet, given its range."""

    fold: float
    call: float
    raise_: float
    calling: list[float]  # the range given it calls (weights, unnormalised)
    raising: list[float]  # the range given it raises

    @property
    def continuing(self) -> list[float]:
        """The range given it calls or raises."""
        return [c + r for c, r in zip(self.calling, self.raising)]


@dataclass
class OpponentModel:
    population: ActionModel = field(default_factory=ActionModel)
    teams: dict[str, ActionModel] = field(default_factory=dict)
    kappa_team: float = 5.0  # pseudo-counts per cell of the team's own
    # behaviour in that situation (over all buckets)
    kappa_marginal: float = 10.0  # pseudo-counts of the population in that
    # situation's prior for the team's behaviour over all buckets
    kappa_pop: float = 10.0  # pseudo-counts of the base per cell
    kappa_trust: float = 20.0  # trust = n / (n + kappa_trust) per situation
    kappa_team_trust: float = 30.0  # team_trust = moves / (moves + this)
    kappa_group: float = 5.0  # pseudo-moves behind each style ratio
    tilt_range: tuple[float, float] = (0.05, 20.0)  # clip style ratios

    # -- the generative model -----------------------------------------------

    def population_probs(self, sit: Situation, bucket: int) -> list[float]:
        """The population's P(outcome | bucket, situation): its counts
        shrunk towards the base."""
        return _shrink(self.population.cell(sit, bucket),
                       base_probs(sit, bucket), self.kappa_pop)

    def style(self, team: str, sit: Situation) -> list[float]:
        """Per outcome, how much more often team makes it than the
        population model predicted, over every situation in sit's group
        (1.0 with no data). Carries a team's style, e.g. never folding,
        into situations it has rarely been seen in."""
        model = self.teams.get(team)
        g = group_of(sit)
        obs = model.group_obs.get(g) if model else None
        if not obs:
            return [1.0] * len(OUTCOMES)
        exp = model.group_exp[g]
        n = sum(exp)
        low, high = self.tilt_range
        out = []
        for o_n, e_n in zip(obs, exp):
            pseudo = self.kappa_group * e_n / n
            ratio = (o_n + pseudo) / (e_n + pseudo) if e_n + pseudo > 0 else 1.0
            out.append(min(high, max(low, ratio)))
        return out

    def action_probs(self, team: str, sit: Situation, bucket: int) -> list[float]:
        """
        P(outcome | bucket, situation) for team, before legality. The
        population's cell is tilted by the team's style (style), then the
        team's behaviour in sit over all buckets is shrunk towards that, and
        the cell towards the result. A team whose moves ignore its cards
        then looks the same in every bucket instead of fitting noise.
        """
        pop = self.population_probs(sit, bucket)
        model = self.teams.get(team)
        if model is None:
            return pop
        tilted = [p * r for p, r in zip(pop, self.style(team, sit))]
        total = sum(tilted)
        tilted = [p / total for p in tilted] if total > 0 else pop
        prior = _shrink(model.marginal(sit), tilted, self.kappa_marginal)
        return _shrink(model.cell(sit, bucket), prior, self.kappa_team)

    def trust(self, team: str, sit: Situation) -> float:
        """0..1: how much data backs this team's model in this situation."""
        model = self.teams.get(team)
        n = model.n(sit) if model else 0.0
        return n / (n + self.kappa_trust)

    def team_trust(self, team: str) -> float:
        """0..1: how much data backs this team's model overall (its moves
        seen in any situation)."""
        model = self.teams.get(team)
        n = sum(model.totals.values()) if model else 0.0
        return n / (n + self.kappa_team_trust)

    def likelihood(self, team: str, r: ActionRecord) -> list[float]:
        """P(the move r made | strength bucket), per bucket, among the
        moves that were legal."""
        sit = Situation.from_record(r)
        moves = legal(sit, r.can_raise)
        o = outcome_of(r)
        out = []
        for b in range(NUM_BUCKETS):
            p = self.action_probs(team, sit, b)
            total = sum(p[m] for m in moves)
            out.append(p[o] / total if total > 0 else 1.0)
        return out

    # -- questions ----------------------------------------------------------

    def query_hand_distribution(
        self, team: str, history: HandHistory, player: int,
        strengths: StrengthCache, dead: list[int] | None = None,
    ) -> list[float]:
        """
        Weights over the 1326 combos for what player holds, given every move
        it made this hand: P(combo) times the likelihood of each move with
        the strength that combo had on that street. dead: cards it cannot
        hold (ours and the board's by default).
        """
        if dead is None:
            dead = history.hand + history.board
        dead_set = set(dead)
        weights = [0.0 if a in dead_set or b in dead_set else 1.0
                   for a, b in COMBOS]
        for r in history.actions_of(player):
            buckets = strengths.buckets(history.board[:r.board_len])
            lik = self.likelihood(team, r)
            weights = [w * lik[buckets[i]] if w else 0.0
                       for i, w in enumerate(weights)]
        total = sum(weights)
        if total <= 0:  # moves the model calls impossible: fall back
            return [0.0 if a in dead_set or b in dead_set else 1.0
                    for a, b in COMBOS]
        return [w / total for w in weights]

    def respond(
        self, team: str, sit: Situation, weights: list[float],
        buckets: list[int], can_raise: bool,
    ) -> Response:
        """How team answers a bet that puts it in sit, holding a hand from
        weights (whose strength buckets on this board are buckets)."""
        moves = legal(sit, can_raise)
        per_bucket = []
        for b in range(NUM_BUCKETS):
            p = self.action_probs(team, sit, b)
            total = sum(p[m] for m in moves)
            fold = p[FOLD] / total
            call = p[CALL if sit.facing != "none" else CHECK] / total
            per_bucket.append((fold, call, 1 - fold - call))
        fold = call = 0.0
        calling = [0.0] * NUM_COMBOS
        raising = [0.0] * NUM_COMBOS
        for i, w in enumerate(weights):
            if w:
                f, c, r = per_bucket[buckets[i]]
                fold += w * f
                call += w * c
                calling[i] = w * c
                raising[i] = w * max(0.0, r)
        total = sum(weights) or 1.0
        fold, call = fold / total, call / total
        return Response(fold, call, max(0.0, 1 - fold - call), calling, raising)

    # -- learning -----------------------------------------------------------

    def update(self, history: HandHistory, strengths: StrengthCache) -> None:
        """
        Learn from a finished hand, for every other player who acted. Shown
        cards give exact strength buckets (full counts); otherwise the
        posterior over its combos spreads each count over buckets (one
        online EM step), so hands that never reach showdown, bluffs that
        worked included, still count.
        """
        for player in range(len(history.teams)):
            if player == history.me:
                continue
            records = history.actions_of(player)
            if not records:
                continue
            team = history.teams[player]
            model = self.teams.setdefault(team, ActionModel())
            shown = history.shown.get(player)
            posterior = None
            if not shown or len(shown) != 2:
                posterior = self.query_hand_distribution(
                    team, history, player, strengths)
            for r in records:
                buckets = strengths.buckets(history.board[:r.board_len])
                if posterior is None:
                    probs = [0.0] * NUM_BUCKETS
                    probs[buckets[combo_id(*shown)]] = 1.0
                else:
                    probs = [0.0] * NUM_BUCKETS
                    for i, w in enumerate(posterior):
                        if w:
                            probs[buckets[i]] += w
                sit = Situation.from_record(r)
                outcome = outcome_of(r)
                model.add_group(sit, outcome,
                                self._predicted(sit, probs, r.can_raise))
                model.add(sit, probs, outcome)
                self.population.add(sit, probs, outcome)

    def _predicted(self, sit: Situation, bucket_probs: list[float],
                   can_raise: bool) -> list[float]:
        """The population model's outcome distribution for a move, over
        the legal moves, averaged over the mover's strength buckets."""
        moves = legal(sit, can_raise)
        out = [0.0] * len(OUTCOMES)
        for b, w in enumerate(bucket_probs):
            if w <= 0:
                continue
            p = self.population_probs(sit, b)
            total = sum(p[m] for m in moves)
            for m in moves:
                out[m] += w * p[m] / total
        return out
