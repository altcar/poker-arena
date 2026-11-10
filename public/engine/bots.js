// The five bots, renamed from the A-E variants in the fork's history
// (commit 14bc9b9). The parameters are the actual class attributes from
// model_a.py .. model_e.py; B is the submitted goldman-stanley model
// (model.py at HEAD).

import { PokerBot } from "./policy.js";
import { board_texture } from "./botcore.js";

function makeModel(spec) {
  const Model = class Model extends PokerBot {
    TIER = spec.tier;
    OPEN = spec.open;
    THREE_BET = spec.threeBet;
    DEFEND = spec.defend;
    FOUR_BET = spec.fourBet;
    CALL_FOUR = spec.callFour;
    VALUE = spec.value;
    RAISE = spec.raise;
    MARGIN = spec.margin;
    BLUFF = spec.bluff;
    VALUE_SIZE = spec.valueSize ?? 0.75;
    ITERATIONS = spec.iterations;
  };
  if (spec.valueFraction) Model.prototype._value_fraction = spec.valueFraction;
  return Model;
}

const aggressiveValueFraction = function (game_state, eq, board) {
  const style = this._style(game_state);
  const [paired, , connected] = board_texture(board);
  if (style === "station") return eq > 0.7 ? 1.0 : 0.8;
  if (style === "nit") return 0.55;
  if (style === "maniac") return eq > 0.8 ? 0.5 : 1.15;
  if (eq > 0.88 && game_state.street === "river") return 1.35;
  if (paired || connected) return 0.8;
  return 0.66;
};

export const BOT_SPECS = [
  {
    key: "conservative",
    name: "Conservative",
    plan: "A",
    tier: 1,
    tagline: "Tight hand-strength heuristic. No Monte-Carlo, no opponent model.",
    description:
      "The safest bot: a deterministic category heuristic with tight preflop ranges and no bluffs. Cheapest on the clock and very hard to fault.",
    open: 0.34,
    threeBet: 0.09,
    defend: 0.32,
    fourBet: 0.03,
    callFour: 0.07,
    value: 0.62,
    raise: 0.78,
    margin: 0.05,
    bluff: 0.0,
    valueSize: 0.75,
    iterations: 0,
  },
  {
    key: "workhorse",
    name: "Workhorse",
    plan: "B",
    tier: 2,
    tagline: "The submitted model: preflop ranges plus Monte-Carlo equity.",
    description:
      "The goldman-stanley submission. A 25 bb opening range, equity against random hands postflop, pot-odds calls and a single value size.",
    open: 0.8,
    threeBet: 0.12,
    defend: 1.0,
    fourBet: 0.045,
    callFour: 1.0,
    value: 0.58,
    raise: 0.58,
    margin: 0.0,
    bluff: 0.0,
    valueSize: 1.25,
    iterations: 200,
  },
  {
    key: "adaptive",
    name: "Adaptive",
    plan: "C",
    tier: 3,
    tagline: "Workhorse plus a live opponent model.",
    description:
      "Per-team counters (VPIP/PFR/aggression/big-bet frequency) drive the equity range filter and switch the strategy archetype.",
    open: 0.58,
    threeBet: 0.13,
    defend: 0.88,
    fourBet: 0.05,
    callFour: 0.11,
    value: 0.63,
    raise: 0.63,
    margin: 0.0,
    bluff: 0.1,
    valueSize: 0.75,
    iterations: 240,
  },
  {
    key: "mixed",
    name: "Mixed",
    plan: "D",
    tier: 4,
    tagline: "Adaptive plus mixed strategies, board texture and MDF.",
    description:
      "Blocker-aware bluffing, texture-driven sizing, small-bet defence and mixed frequencies. Harder to read and harder to exploit.",
    open: 0.62,
    threeBet: 0.15,
    defend: 0.9,
    fourBet: 0.055,
    callFour: 0.13,
    value: 0.62,
    raise: 0.62,
    margin: 0.0,
    bluff: 0.16,
    valueSize: 0.75,
    iterations: 300,
  },
  {
    key: "aggressive",
    name: "Aggressive",
    plan: "E",
    tier: 5,
    tagline: "Maximum aggression: population exploits and overbet shoves.",
    description:
      "Tighter value against stations, wide pressure against nits, traps against maniacs, overbet shoves with the nuts and thin hero calls.",
    open: 0.66,
    threeBet: 0.16,
    defend: 0.92,
    fourBet: 0.06,
    callFour: 0.14,
    value: 0.6,
    raise: 0.61,
    margin: 0.0,
    bluff: 0.22,
    valueSize: 0.75,
    iterations: 320,
    valueFraction: aggressiveValueFraction,
  },
];

export const BOTS = BOT_SPECS.map((spec) => ({
  ...spec,
  Model: makeModel(spec),
}));

// Competition bots are registered at runtime by the browser Python runtime.
const DYNAMIC_BOTS = new Map();

export function registerBotSpec(spec) {
  if (!spec || !spec.key) throw new Error("a bot spec needs a key");
  DYNAMIC_BOTS.set(spec.key, spec);
}

export function botByKey(key) {
  return BOTS.find((b) => b.key === key) || DYNAMIC_BOTS.get(key) || null;
}

export function createBot(key) {
  const spec = botByKey(key);
  if (!spec) throw new Error(`unknown bot ${key}`);
  if (typeof spec.create === "function") return spec.create();
  return new spec.Model();
}

// The five seats around the table, in order.
export const TABLE_BOTS = BOTS.map((b) => b.key);
