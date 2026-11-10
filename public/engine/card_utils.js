// JS port of card_utils.py (Manchester-AI-Society/poker_bot).
// Cards are value objects; ranks compare by .value (2..14).

export const RANKS = "23456789TJQKA";
export const SUITS = "CDHS";

export const CATEGORIES = [
  "high_card",
  "pair",
  "two_pair",
  "three_of_a_kind",
  "straight",
  "flush",
  "full_house",
  "four_of_a_kind",
  "straight_flush",
];

export class Card {
  constructor(rank, suit) {
    if (!RANKS.includes(rank)) throw new Error(`bad rank ${rank}`);
    if (!SUITS.includes(suit)) throw new Error(`bad suit ${suit}`);
    this.rank = rank;
    this.suit = suit;
  }

  static parse(text) {
    if (typeof text !== "string" || text.length !== 2) {
      throw new Error(`a card is two characters, not ${text}`);
    }
    return new Card(text[0], text[1]);
  }

  get value() {
    return RANKS.indexOf(this.rank) + 2;
  }

  toString() {
    return this.rank + this.suit;
  }

  equals(other) {
    return (
      other instanceof Card &&
      this.rank === other.rank &&
      this.suit === other.suit
    );
  }
}

// Compare two rank tuples lexicographically (higher wins).
export function compareRank(a, b) {
  const n = Math.max(a.length, b.length);
  for (let i = 0; i < n; i += 1) {
    const x = a[i] ?? -1;
    const y = b[i] ?? -1;
    if (x !== y) return x - y;
  }
  return 0;
}

export function maxRank(ranks) {
  let best = ranks[0];
  for (const r of ranks) {
    if (compareRank(r, best) > 0) best = r;
  }
  return best;
}

function combinations5(cards) {
  const out = [];
  const n = cards.length;
  for (let a = 0; a < n - 4; a += 1)
    for (let b = a + 1; b < n - 3; b += 1)
      for (let c = b + 1; c < n - 2; c += 1)
        for (let d = c + 1; d < n - 1; d += 1)
          for (let e = d + 1; e < n; e += 1)
            out.push([cards[a], cards[b], cards[c], cards[d], cards[e]]);
  return out;
}

export function _straightHigh(values) {
  const set = new Set(values);
  if (set.size !== 5) return null;
  const sorted = [...values].sort((a, b) => b - a);
  if (sorted[0] - sorted[4] === 4) return sorted[0];
  if (sorted.join(",") === "14,5,4,3,2") return 5;
  return null;
}

export function _rankFive(cards) {
  const values = cards.map((c) => c.value).sort((a, b) => b - a);
  const flush = cards.every((c) => c.suit === cards[0].suit);
  const straightHigh = _straightHigh(values);

  const counts = new Map();
  for (const v of values) counts.set(v, (counts.get(v) || 0) + 1);
  const groups = [...counts.entries()].sort((a, b) => {
    if (a[1] !== b[1]) return b[1] - a[1];
    return b[0] - a[0];
  });
  const countTuple = groups.map((g) => g[1]);
  const rankTuple = groups.map((g) => g[0]);

  if (straightHigh && flush) return [8, straightHigh];
  if (countTuple.length === 2 && countTuple[0] === 4) return [7, ...rankTuple];
  if (countTuple.length === 2 && countTuple[0] === 3) return [6, ...rankTuple];
  if (flush) return [5, ...values];
  if (straightHigh) return [4, straightHigh];
  if (countTuple[0] === 3) return [3, ...rankTuple];
  if (countTuple[0] === 2 && countTuple[1] === 2) return [2, ...rankTuple];
  if (countTuple[0] === 2) return [1, ...rankTuple];
  return [0, ...values];
}

// The best 5-card hand among 5..7 cards, as a comparable array.
export function evaluate(cards) {
  if (cards.length < 5 || cards.length > 7) {
    throw new Error(`evaluate needs 5 to 7 cards, not ${cards.length}`);
  }
  const seen = new Set(cards.map((c) => String(c)));
  if (seen.size !== cards.length) {
    throw new Error(`duplicate cards in ${cards.map(String).join(" ")}`);
  }
  return maxRank(combinations5(cards).map(_rankFive));
}

export function category(rank) {
  return CATEGORIES[rank[0]];
}

export function fullDeck() {
  const deck = [];
  for (const rank of RANKS) for (const suit of SUITS) deck.push(new Card(rank, suit));
  return deck;
}
