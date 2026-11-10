"""
A small network that adjusts the decision engine's score of each candidate
move (a residual): score = (chip EV + survival) / pot + net(features). Its
weights are evolved by scripts/evolve.py. With the output layer at zero it
changes nothing, so evolution starts from the engine as it is.

Pure Python: one hidden tanh layer, about 260 weights.
"""

import math
import random
from dataclasses import dataclass, field

STREETS = ("preflop", "flop", "turn", "river")
NUM_FEATURES = 20
HIDDEN = 12


@dataclass
class PolicyNet:
    """Layer sizes and a flat weight list: W1 (hidden x inputs), b1, W2
    (hidden), b2."""

    inputs: int = NUM_FEATURES
    hidden: int = HIDDEN
    weights: list[float] = field(default_factory=list)

    @classmethod
    def zero_output(cls, rng: random.Random | None = None) -> "PolicyNet":
        """Random hidden layer, output layer at zero: no effect yet."""
        rng = rng or random.Random(0)
        net = cls()
        scale = 1.0 / math.sqrt(net.inputs)
        w1 = [rng.gauss(0, scale) for _ in range(net.hidden * net.inputs)]
        net.weights = w1 + [0.0] * net.hidden + [0.0] * net.hidden + [0.0]
        return net

    @staticmethod
    def size(inputs: int = NUM_FEATURES, hidden: int = HIDDEN) -> int:
        return hidden * inputs + hidden + hidden + 1

    def is_zero(self) -> bool:
        """True when the output layer is all zero (the net does nothing)."""
        tail = self.weights[self.hidden * self.inputs + self.hidden:]
        return not any(tail)

    def forward(self, x: list[float]) -> float:
        w, n, h = self.weights, self.inputs, self.hidden
        out_w = n * h + h
        total = w[-1]
        for j in range(h):
            row = j * n
            a = w[n * h + j]
            for i in range(n):
                a += w[row + i] * x[i]
            total += w[out_w + j] * math.tanh(a)
        return total


def _clip(x: float, bound: float) -> float:
    return max(-bound, min(bound, x))


def context_features(state, me: int, trust: float, style_fold: float,
                     style_raise: float) -> list[float]:
    """The decision's features, shared by every candidate (11)."""
    opp = 1 - me
    pot = max(1, state.pot)
    eff = min(state.stacks[me] + state.bets[me],
              state.stacks[opp] + state.bets[opp])
    return [
        *(1.0 if state.street == s else 0.0 for s in STREETS),
        1.0 if state.button == me else 0.0,
        eff / state.big_blind / 25.0,
        _clip(min(state.stacks[me], state.stacks[opp]) / pot / 10.0, 1.0),
        state.pot_odds,
        trust,
        _clip(math.log(max(style_fold, 1e-3)), 3.0),
        _clip(math.log(max(style_raise, 1e-3)), 3.0),
    ]


def option_features(option, pot: int, survival: float) -> list[float]:
    """One candidate's features (9)."""
    kind = option.action.kind
    pot = max(1, pot)
    return [
        1.0 if kind == "fold" else 0.0,
        1.0 if kind == "call" else 0.0,
        1.0 if kind == "raise" else 0.0,
        _clip(option.size, 3.0),
        1.0 if option.all_in else 0.0,
        option.equity,
        option.fold_p,
        _clip(option.ev / pot, 3.0),
        _clip(survival / pot, 3.0),
    ]
