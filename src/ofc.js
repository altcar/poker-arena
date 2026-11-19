// Pineapple OFC: heads-up open-face Chinese poker.
//
// Round 1 deals five cards and you place all five. Rounds 2-5 deal three cards,
// you place two and discard one. The bottom row must be at least as strong as
// the middle, and the middle at least as strong as the top, or the board fouls.

import { makeDeck } from "./draw27.js";

const RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"];
const VALUE = Object.fromEntries(RANKS.map((r, i) => [r, i + 2]));
const CAPS = { top: 3, middle: 5, bottom: 5 };

function shuffle(deck, rng) {
  for (let i = deck.length - 1; i > 0; i -= 1) {
    const j = Math.floor(rng() * (i + 1));
    [deck[i], deck[j]] = [deck[j], deck[i]];
  }
  return deck;
}

function straightHigh(values) {
  const unique = [...new Set(values)].sort((a, b) => b - a);
  if (unique.length !== 5) return null;
  if (unique[0] - unique[4] === 4) return unique[0];
  if (unique[0] === 14 && unique[1] === 5) return 5;
  return null;
}

function compare(a, b) {
  const length = Math.max(a.length, b.length);
  for (let i = 0; i < length; i += 1) {
    const x = a[i] ?? -1;
    const y = b[i] ?? -1;
    if (x !== y) return x - y;
  }
  return 0;
}

/** Three-card top-row rank, higher is better. */
export function rank3(cards) {
  if (!cards || cards.length !== 3) return null;
  const values = cards.map((c) => VALUE[c[0]]).sort((a, b) => b - a);
  if (values[0] === values[2]) return [3, values[0]];
  if (values[0] === values[1]) return [1, values[0], values[2]];
  if (values[1] === values[2]) return [1, values[1], values[0]];
  return [0, ...values];
}

/** Five-card high-hand rank, higher is better. */
export function rank5(cards) {
  if (!cards || cards.length !== 5) return null;
  const values = cards.map((c) => VALUE[c[0]]).sort((a, b) => b - a);
  const flush = cards.every((c) => c[1] === cards[0][1]);
  const high = straightHigh(values);
  const counts = new Map();
  for (const v of values) counts.set(v, (counts.get(v) || 0) + 1);
  const groups = [...counts.entries()]
    .map(([value, count]) => ({ value, count }))
    .sort((a, b) => b.count - a.count || b.value - a.value);
  const shape = groups.map((g) => g.count).join("");
  if (high && flush) return [8, high];
  if (shape === "41") return [7, groups[0].value, groups[1].value];
  if (shape === "32") return [6, groups[0].value, groups[1].value];
  if (flush) return [5, ...values];
  if (high) return [4, high];
  if (shape === "311") return [3, groups[0].value, ...values.filter((v) => v !== groups[0].value)];
  if (shape === "221") {
    const pairs = groups.filter((g) => g.count === 2).map((g) => g.value).sort((a, b) => b - a);
    return [2, ...pairs, groups.find((g) => g.count === 1).value];
  }
  if (shape === "2111") {
    const pair = groups.find((g) => g.count === 2).value;
    return [1, pair, ...values.filter((v) => v !== pair)];
  }
  return [0, ...values];
}

export function handName(rank, row = "middle") {
  if (!rank) return "—";
  if (row === "top") {
    if (rank[0] === 3) return `trips, ${RANKS[rank[1] - 2]}s`;
    if (rank[0] === 1) return `pair of ${RANKS[rank[1] - 2]}s`;
    return `${RANKS[rank[1] - 2]}-high`;
  }
  const names = ["high card", "pair", "two pair", "three of a kind", "straight", "flush", "full house", "four of a kind", "straight flush"];
  if (rank[0] >= 8) return rank[1] === 14 ? "royal flush" : `${RANKS[rank[1] - 2]}-high straight flush`;
  if (rank[0] === 7) return `four ${RANKS[rank[1] - 2]}s`;
  if (rank[0] === 6) return `${RANKS[rank[1] - 2]}s full of ${RANKS[rank[2] - 2]}s`;
  if (rank[0] === 5) return `flush, ${RANKS[rank[1] - 2]}-high`;
  if (rank[0] === 4) return `${RANKS[rank[1] - 2]}-high straight`;
  if (rank[0] === 3) return `three ${RANKS[rank[1] - 2]}s`;
  if (rank[0] === 2) return `${RANKS[rank[1] - 2]}s and ${RANKS[rank[2] - 2]}s`;
  if (rank[0] === 1) return `pair of ${RANKS[rank[1] - 2]}s`;
  return `${RANKS[rank[1] - 2]}-high`;
}

export function royalties(row, rank) {
  if (!rank) return 0;
  if (row === "top") {
    if (rank[0] === 3) return 10;
    if (rank[0] === 1) return rank[1] >= 6 ? rank[1] - 5 : 0;
    return 0;
  }
  if (row === "middle") {
    if (rank[0] === 3) return 2;
    if (rank[0] === 4) return 4;
    if (rank[0] === 5) return 8;
    if (rank[0] === 6) return 12;
    if (rank[0] === 7) return 20;
    if (rank[0] === 8) return rank[1] === 14 ? 50 : 30;
    return 0;
  }
  if (rank[0] === 4) return 2;
  if (rank[0] === 5) return 4;
  if (rank[0] === 6) return 6;
  if (rank[0] === 7) return 10;
  if (rank[0] === 8) return rank[1] === 14 ? 25 : 15;
  return 0;
}

function rowRank(row, cards) {
  if (row === "top") return cards.length === 3 ? rank3(cards) : null;
  return cards.length === 5 ? rank5(cards) : null;
}

function isFoul(board) {
  const top = rowRank("top", board.top);
  const middle = rowRank("middle", board.middle);
  const bottom = rowRank("bottom", board.bottom);
  if (!top || !middle || !bottom) return false;
  return compare(bottom, middle) < 0 || compare(middle, top) < 0;
}

function roughStrength(cards) {
  if (!cards.length) return 0;
  let score = cards.reduce((sum, c) => sum + VALUE[c[0]], 0) / cards.length;
  const counts = {};
  for (const c of cards) counts[c[0]] = (counts[c[0]] || 0) + 1;
  for (const count of Object.values(counts)) if (count > 1) score += (count - 1) * 7;
  return score;
}

function boardScore(board) {
  let score = 0;
  // Raw card strength, weighted towards the rows that need it.
  for (const card of board.top) score += VALUE[card[0]] * 0.18;
  for (const card of board.middle) score += VALUE[card[0]] * 0.55;
  for (const card of board.bottom) score += VALUE[card[0]] * 0.85;

  const top = rowRank("top", board.top);
  const middle = rowRank("middle", board.middle);
  const bottom = rowRank("bottom", board.bottom);
  if (top) score += royalties("top", top) * 1.4 + top[0] * 3;
  if (middle) score += royalties("middle", middle) * 1.2 + middle[0] * 4;
  if (bottom) score += royalties("bottom", bottom) * 1.0 + bottom[0] * 4;

  // Violating the row order is the worst thing a board can do.
  if (top && middle && compare(middle, top) < 0) score -= 45;
  if (middle && bottom && compare(bottom, middle) < 0) score -= 60;
  if (top && !middle && roughStrength(board.top) > roughStrength(board.middle) + 8) score -= 8;
  if (middle && !bottom && roughStrength(board.middle) > roughStrength(board.bottom) + 6) score -= 10;

  // Keep the top row cheap unless it is already a pair of sixes or better.
  if (!top && board.top.length) {
    const pair = board.top.length >= 2 && board.top[0][0] === board.top[1][0];
    if (pair && VALUE[board.top[0][0]] < 6) score -= 6;
    if (board.top.length >= 2 && board.top[0][0] !== board.top[1][0] && board.top.length === 2) score -= 1.5;
  }
  return score;
}

function assignments(cards, board = { top: [], middle: [], bottom: [] }) {
  const out = [];
  const walk = (index, top, middle, bottom) => {
    if (index === cards.length) {
      out.push({ top, middle, bottom });
      return;
    }
    const card = cards[index];
    if (board.top.length + top.length < CAPS.top) walk(index + 1, [...top, card], middle, bottom);
    if (board.middle.length + middle.length < CAPS.middle) walk(index + 1, top, [...middle, card], bottom);
    if (board.bottom.length + bottom.length < CAPS.bottom) walk(index + 1, top, middle, [...bottom, card]);
  };
  walk(0, [], [], []);
  return out;
}

function choosePlacement(board, cards) {
  let best = null;
  let bestScore = -Infinity;
  for (const split of assignments(cards, board)) {
    const trial = {
      top: [...board.top, ...split.top],
      middle: [...board.middle, ...split.middle],
      bottom: [...board.bottom, ...split.bottom],
    };
    const score = boardScore(trial);
    if (score > bestScore) {
      bestScore = score;
      best = split;
    }
  }
  return best || { top: [], middle: [], bottom: [] };
}

function chooseLater(board, cards) {
  let best = null;
  let bestScore = -Infinity;
  for (let discard = 0; discard < cards.length; discard += 1) {
    const remaining = cards.filter((_, i) => i !== discard);
    for (const split of assignments(remaining, board)) {
      const trial = {
        top: [...board.top, ...split.top],
        middle: [...board.middle, ...split.middle],
        bottom: [...board.bottom, ...split.bottom],
      };
      const score = boardScore(trial) - (cards.length === 3 ? 1 : 0);
      if (score > bestScore) {
        bestScore = score;
        best = { ...split, discard: cards[discard] };
      }
    }
  }
  return best;
}

export class OfcGame {
  constructor(options = {}) {
    this.humanName = options.humanName || "You";
    this.botName = options.botName || "Pineapple";
    this.seed = options.seed || (Date.now() % 1000000);
    this.button = 0;
    this.handNumber = 0;
    this.scores = [0, 0];
    this.hand = null;
    this.history = [];
    this.rngState = this.seed >>> 0;
  }

  rng() {
    this.rngState = (this.rngState * 1664525 + 1013904223) >>> 0;
    return this.rngState / 4294967296;
  }

  startHand() {
    if (this.hand && !this.hand.result) throw new Error("the hand is still running");
    this.handNumber += 1;
    this.button = (this.button + 1) % 2;
    const deck = shuffle(makeDeck(), () => this.rng());
    this.hand = {
      deck,
      boards: [
        { top: [], middle: [], bottom: [] },
        { top: [], middle: [], bottom: [] },
      ],
      hands: [deck.splice(0, 5), deck.splice(0, 5)],
      discards: [[], []],
      street: 0,
      phase: "place",
      turn: 1 - this.button,
      result: null,
      log: [],
    };
    this.hand.log.push(`Hand ${this.handNumber}. ${this.names[1 - this.button]} places first.`);
    this._beginStreet(1 - this.button);
    return this.publicState();
  }

  get names() {
    return [this.humanName, this.botName];
  }

  _beginStreet(first) {
    const h = this.hand;
    h.phase = "place";
    h.streetStart = h.street;
    h.turn = first ?? 1 - this.button;
    h.order = [1 - this.button, this.button];
    h.need = h.street === 0 ? 5 : 2;
    if (h.turn === 1) this._botPlaceStreet();
  }

  _advanceTurn() {
    const h = this.hand;
    if (h.phase !== "place") return;
    const humanDone = h.hands[0].length === 0;
    const botDone = h.hands[1].length === 0;
    if (humanDone && botDone) {
      this._finishStreet();
      return;
    }
    // A player places their whole street in one turn, so the human keeps the
    // turn until their hand is empty.
    if (!humanDone) {
      h.turn = 0;
      return;
    }
    h.turn = 1;
    this._botPlaceStreet();
  }

  _botPlaceStreet() {
    const h = this.hand;
    if (h.phase !== "place" || h.hands[1].length === 0) return;
    if (h.street === 0) {
      const split = choosePlacement(h.boards[1], h.hands[1]);
      this._apply(1, split);
    } else {
      const split = chooseLater(h.boards[1], h.hands[1]);
      this._apply(1, { top: split.top, middle: split.middle, bottom: split.bottom, discard: split.discard });
    }
    this._advanceTurn();
  }

  _apply(player, split) {
    const h = this.hand;
    const board = h.boards[player];
    const hand = h.hands[player];
    const place = (row, cards) => {
      for (const card of cards) {
        const index = hand.indexOf(card);
        if (index !== -1 && board[row].length < CAPS[row]) {
          hand.splice(index, 1);
          board[row].push(card);
        }
      }
    };
    place("top", split.top || []);
    place("middle", split.middle || []);
    place("bottom", split.bottom || []);
    if (hand.length && split.discard) {
      const index = hand.indexOf(split.discard);
      if (index !== -1) {
        h.discards[player].push(hand.splice(index, 1)[0]);
        h.lastDiscard = split.discard;
      }
    }
    // Later streets: placing two automatically discards the third.
    if (h.street > 0 && hand.length === 1) {
      h.discards[player].push(hand.pop());
    }
    h.log.push(`${player === 0 ? this.names[0] : this.names[1]} places ${split.top?.length || 0}/${split.middle?.length || 0}/${split.bottom?.length || 0}.`);
  }

  _finishStreet() {
    const h = this.hand;
    h.phase = "between";
    // Safety: any leftover cards are discarded.
    for (let p = 0; p < 2; p += 1) {
      while (h.hands[p].length) h.discards[p].push(h.hands[p].pop());
    }
    if (h.street >= 4) {
      this._score();
      return;
    }
    h.street += 1;
    h.hands = [h.deck.splice(0, 3), h.deck.splice(0, 3)];
    h.log.push(`Street ${h.street + 1}: three cards each.`);
    this._beginStreet(1 - this.button);
  }

  _score() {
    const h = this.hand;
    const rows = ["top", "middle", "bottom"];
    const fouls = [isFoul(h.boards[0]), isFoul(h.boards[1])];
    const royaltiesByPlayer = [0, 0];
    for (let p = 0; p < 2; p += 1) {
      for (const row of rows) {
        royaltiesByPlayer[p] += royalties(row, rowRank(row, h.boards[p][row]));
      }
    }
    const rowWins = [];
    let rowPoints = 0;
    for (const row of rows) {
      const a = rowRank(row, h.boards[0][row]);
      const b = rowRank(row, h.boards[1][row]);
      const cmp = compare(a, b);
      if (cmp > 0) rowPoints += 1;
      else if (cmp < 0) rowPoints -= 1;
      rowWins.push({ row, a, b, winner: cmp > 0 ? 0 : cmp < 0 ? 1 : -1 });
    }
    let points;
    if (fouls[0] && fouls[1]) {
      points = [0, 0];
    } else if (fouls[0] || fouls[1]) {
      const winner = fouls[0] ? 1 : 0;
      const net = 9 + royaltiesByPlayer[winner];
      points = winner === 0 ? [net, -net] : [-net, net];
    } else {
      const net = rowPoints + royaltiesByPlayer[0] - royaltiesByPlayer[1];
      points = [net, -net];
    }
    h.result = {
      points,
      fouls,
      royalties: royaltiesByPlayer,
      rows: rowWins,
      boards: h.boards.map((b) => ({ top: [...b.top], middle: [...b.middle], bottom: [...b.bottom] })),
      names: h.boards.map((b) => ({
        top: handName(rowRank("top", b.top), "top"),
        middle: handName(rowRank("middle", b.middle), "middle"),
        bottom: handName(rowRank("bottom", b.bottom), "bottom"),
      })),
    };
    h.phase = "over";
    this.scores[0] += points[0];
    this.scores[1] += points[1];
    h.log.push(`Hand over. Points ${points[0]} to ${points[1]}.`);
    this.history.push({ hand: this.handNumber, ...h.result });
  }

  placeHuman(cardIndex, row) {
    const h = this.hand;
    if (!h || h.phase !== "place" || h.turn !== 0) return this.publicState();
    if (!CAPS[row] || h.boards[0][row].length >= CAPS[row]) return this.publicState();
    const card = h.hands[0][cardIndex];
    if (!card) return this.publicState();
    h.hands[0].splice(cardIndex, 1);
    h.boards[0][row].push(card);
    h.log.push(`You place ${card} on ${row}.`);
    if (h.hands[0].length === 1 && h.street > 0) {
      const discarded = h.hands[0].pop();
      h.discards[0].push(discarded);
      h.lastDiscard = discarded;
      h.log.push(`You discard one card face down.`);
    }
    this._advanceTurn();
    return this.publicState();
  }

  publicState() {
    const h = this.hand;
    const base = {
      handNumber: this.handNumber,
      button: this.button,
      scores: [...this.scores],
      names: this.names,
      history: this.history.slice(-10),
    };
    if (!h) return { ...base, waitingFor: "next" };
    return {
      ...base,
      hand: {
        street: h.street,
        phase: h.phase,
        turn: h.turn,
        boards: h.boards.map((b) => ({ top: [...b.top], middle: [...b.middle], bottom: [...b.bottom] })),
        hands: [h.hands[0].map(String), h.hands[1].length],
        discards: h.discards.map((d) => d.length),
        need: h.need,
        result: h.result,
        log: [...h.log],
      },
      waitingFor: h.result ? "over" : h.phase === "place" && h.turn === 0 ? "human" : h.phase === "place" ? "bot" : "between",
    };
  }
}
