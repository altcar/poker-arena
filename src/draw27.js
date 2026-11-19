// 2-7 Triple Draw, fixed limit, heads-up. Self-contained browser engine.
//
// The ranking is deuce-to-seven lowball: high card is the best category, aces
// are always high, and straights and flushes count against you. The best hand
// is 7-5-4-3-2 with no flush.

const RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"];
const SUITS = ["S", "H", "D", "C"];
const VALUE = Object.fromEntries(RANKS.map((r, i) => [r, i + 2]));

export function makeDeck() {
  const deck = [];
  for (const r of RANKS) for (const s of SUITS) deck.push(r + s);
  return deck;
}

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
  // A-2-3-4-5: the ace plays high, so this is the worst straight in 2-7.
  if (unique[0] === 14 && unique[1] === 5) return 14;
  return null;
}

/** Compare two 2-7 hands. Lower is better. Handles are [category, ...tiebreak]. */
export function evaluate27(cards) {
  if (!cards || cards.length !== 5) return null;
  const values = cards.map((c) => VALUE[c[0]]).sort((a, b) => b - a);
  const flush = cards.every((c) => c[1] === cards[0][1]);
  const counts = new Map();
  for (const v of values) counts.set(v, (counts.get(v) || 0) + 1);
  const groups = [...counts.entries()]
    .map(([value, count]) => ({ value, count }))
    .sort((a, b) => b.count - a.count || a.value - b.value);
  const shape = groups.map((g) => g.count).join("");
  const high = straightHigh(values);
  let category;
  if (high && flush) category = 8;
  else if (shape === "41") category = 7;
  else if (shape === "32") category = 6;
  else if (flush) category = 5;
  else if (high) category = 4;
  else if (shape === "311") category = 3;
  else if (shape === "221") category = 2;
  else if (shape === "2111") category = 1;
  else category = 0;
  if (category === 0 || category === 5) return [category, ...values];
  if (category === 4 || category === 8) return [category, high];
  return [category, ...groups.map((g) => g.value)];
}

export function compare27(a, b) {
  // negative if a is better (lower)
  const length = Math.max(a.length, b.length);
  for (let i = 0; i < length; i += 1) {
    const x = a[i] ?? 0;
    const y = b[i] ?? 0;
    if (x !== y) return x - y;
  }
  return 0;
}

const CATEGORY_NAMES = [
  "high card", "pair", "two pair", "three of a kind", "straight",
  "flush", "full house", "four of a kind", "straight flush",
];

export function describe27(cards) {
  const rank = evaluate27(cards);
  if (!rank) return "five cards";
  const [category, ...tie] = rank;
  if (category === 0) {
    const values = tie.slice(0, 5).sort((a, b) => b - a);
    return `${RANKS[values[0] - 2]}-${RANKS[values[4] - 2]} low`;
  }
  if (category === 4) return `${RANKS[tie[0] - 2]}-high straight`;
  if (category === 5) return "flush";
  if (category === 8) return `${RANKS[tie[0] - 2]}-high straight flush`;
  if (category === 1) return `pair of ${RANKS[tie[0] - 2]}s`;
  if (category === 2) return `two pair, ${RANKS[tie[0] - 2]}s and ${RANKS[tie[1] - 2]}s`;
  if (category === 3) return `three ${RANKS[tie[0] - 2]}s`;
  if (category === 6) return `full house, ${RANKS[tie[0] - 2]}s full`;
  if (category === 7) return `four ${RANKS[tie[0] - 2]}s`;
  return CATEGORY_NAMES[category] || "hand";
}

function lowValue(card) {
  return VALUE[card[0]];
}

function keepScore(cards) {
  if (!cards.length) return -100;
  let score = 0;
  const seen = new Map();
  for (const card of cards) {
    const value = lowValue(card);
    if (value <= 8) score += 1.4;
    else score -= (value - 8) * 0.9;
    if (value === 14) score -= 1.2; // aces are liabilities
    seen.set(value, (seen.get(value) || 0) + 1);
  }
  for (const count of seen.values()) if (count > 1) score -= (count - 1) * 2.2;
  if (cards.length === 5) {
    const rank = evaluate27(cards);
    if (rank) {
      if (rank[0] === 0) score += 8 - (rank[1] - 7) * 0.6;
      else if (rank[0] === 1) score -= 2;
      else score -= 4 + rank[0];
    }
    if (cards.every((c) => c[1] === cards[0][1])) score -= 2.5;
  }
  return score;
}

function strength(cards) {
  const rank = evaluate27(cards);
  if (rank) {
    if (rank[0] === 0) return Math.max(0.35, Math.min(1, (11 - rank[1]) / 4));
    if (rank[0] === 1) return 0.3;
    return 0.18;
  }
  const lows = cards.filter((c) => lowValue(c) <= 8).length;
  return Math.min(0.55, lows * 0.12);
}

export class Draw27Game {
  constructor(options = {}) {
    this.humanName = options.humanName || "You";
    this.botName = options.botName || "Draw Bot";
    this.stack = options.stack || 500;
    this.smallBlind = options.smallBlind || 10;
    this.bigBlind = options.bigBlind || 20;
    this.seed = options.seed || (Date.now() % 1000000);
    this.button = 0;
    this.handNumber = 0;
    this.stacks = [this.stack, this.stack];
    this.names = [this.humanName, this.botName];
    this.hand = null;
    this.log = [];
    this.history = [];
    this.rngState = this.seed >>> 0;
  }

  rng() {
    // Small deterministic LCG so a session can be replayed.
    this.rngState = (this.rngState * 1664525 + 1013904223) >>> 0;
    return this.rngState / 4294967296;
  }

  startHand() {
    if (this.hand && !this.hand.result) throw new Error("the hand is still running");
    this.handNumber += 1;
    this.button = (this.button + 1) % 2;
    const deck = shuffle(makeDeck(), () => this.rng());
    const hands = [deck.slice(0, 5), deck.slice(5, 10)];
    const rest = deck.slice(10);
    this.hand = {
      hands,
      deck: rest,
      discards: [[], []],
      drawn: [0, 0],
      bets: [0, 0],
      folded: [false, false],
      contributed: [0, 0],
      pot: 0,
      street: 0,
      phase: "bet",
      toAct: null,
      acted: [false, false],
      raises: 0,
      currentBet: 0,
      result: null,
      log: [],
    };
    const sb = this.button;
    const bb = 1 - this.button;
    this._commit(sb, Math.min(this.smallBlind, this.stacks[sb]));
    this._commit(bb, Math.min(this.bigBlind, this.stacks[bb]));
    this.hand.currentBet = this.bigBlind;
    this.hand.lastRaise = this.bigBlind;
    this.hand.toAct = sb;
    this._log(`Hand ${this.handNumber}. ${this.names[sb]} posts ${this.smallBlind}, ${this.names[bb]} posts ${this.bigBlind}.`);
    this._advance();
    return this.publicState();
  }

  _commit(player, amount) {
    const paid = Math.max(0, Math.min(amount, this.stacks[player]));
    this.stacks[player] -= paid;
    this.hand.bets[player] += paid;
    this.hand.contributed[player] += paid;
    this.hand.pot += paid;
    return paid;
  }

  _log(line) {
    this.hand.log.push(line);
    if (this.hand.log.length > 120) this.hand.log.shift();
  }

  get betSize() {
    return this.hand.street <= 1 ? this.bigBlind : this.bigBlind * 2;
  }

  _active(player) {
    return !this.hand.folded[player];
  }

  _allIn(player) {
    return this.stacks[player] === 0;
  }

  _canAct(player) {
    return this._active(player) && !this._allIn(player);
  }

  _bettingDone() {
    const h = this.hand;
    const live = [0, 1].filter((p) => this._active(p));
    if (live.length < 2) return true;
    const actors = live.filter((p) => this._canAct(p));
    if (!actors.length) return true;
    const maxBet = Math.max(...live.map((p) => h.bets[p]));
    return actors.every((p) => h.acted[p] && h.bets[p] === maxBet);
  }

  _nextActor(from) {
    const other = 1 - from;
    if (this._canAct(other)) return other;
    if (this._canAct(from)) return from;
    return null;
  }

  _advance() {
    const h = this.hand;
    if (h.result) return;
    if (h.folded[0] || h.folded[1]) {
      const winner = h.folded[0] ? 1 : 0;
      this._finish(winner, "fold");
      return;
    }
    if (this._bettingDone()) {
      if (h.street >= 3) {
        this._showdown();
        return;
      }
      h.phase = "draw";
      h.drawn = [0, 0];
      h.drawDone = [false, false];
      h.drawOrder = [this.button, 1 - this.button];
      h.drawIndex = 0;
      this._beginDraw();
      return;
    }
    if (h.toAct == null || !this._canAct(h.toAct) || h.acted[h.toAct] && h.bets[h.toAct] === Math.max(...[0, 1].map((p) => h.bets[p]))) {
      h.toAct = this._nextActor(h.toAct ?? 0);
    }
    if (h.toAct == null) {
      if (h.street >= 3) this._showdown();
      else {
        h.phase = "draw";
        h.drawDone = [false, false];
        h.drawOrder = [this.button, 1 - this.button];
        h.drawIndex = 0;
        this._beginDraw();
      }
      return;
    }
    if (h.toAct === 1 && h.phase === "bet") this._botBet();
  }

  _beginDraw() {
    const h = this.hand;
    // Skip players who are all-in or folded; they still draw on their own turn,
    // but the loop below only asks the bot. The human always gets a draw prompt.
    if (h.drawOrder[h.drawIndex] === 1) {
      this._botDraw();
    }
    if (h.drawDone[0] && h.drawDone[1]) this._afterDraw();
  }

  _botDraw() {
    const h = this.hand;
    const discard = this._chooseDiscards(h.hands[1]);
    this._draw(1, discard);
    h.drawDone[1] = true;
    h.drawIndex += 1;
    if (discard.length) {
      this._log(`${this.names[1]} draws ${discard.length}.`);
    } else {
      this._log(`${this.names[1]} stands pat.`);
    }
  }

  _chooseDiscards(cards) {
    const rank = evaluate27(cards);
    if (rank && rank[0] === 0 && rank[1] <= 9) return [];
    let best = null;
    let bestScore = -Infinity;
    for (let mask = 0; mask < 32; mask += 1) {
      const keep = [];
      const discard = [];
      for (let i = 0; i < 5; i += 1) {
        if (mask & (1 << i)) keep.push(cards[i]);
        else discard.push(cards[i]);
      }
      if (keep.length < 2) continue;
      // Do not throw away more than four; a five-card redraw is only for junk.
      if (discard.length > 4 && keep.some((c) => lowValue(c) <= 7)) continue;
      const score = keepScore(keep) - discard.length * 0.35;
      if (score > bestScore) {
        bestScore = score;
        best = discard;
      }
    }
    return best || [];
  }

  _draw(player, indices) {
    const h = this.hand;
    const keep = [];
    const discarded = [];
    h.hands[player].forEach((card, i) => {
      if (indices.includes(i)) discarded.push(card);
      else keep.push(card);
    });
    const replacements = [];
    for (let i = 0; i < discarded.length && h.deck.length; i += 1) replacements.push(h.deck.shift());
    h.hands[player] = [...keep, ...replacements];
    h.discards[player] = discarded;
    h.drawn[player] = discarded.length;
  }

  _afterDraw() {
    const h = this.hand;
    h.street += 1;
    h.phase = "bet";
    h.bets = [0, 0];
    h.acted = [false, false];
    h.raises = 0;
    h.currentBet = 0;
    h.lastRaise = this.bigBlind;
    h.toAct = 1 - this.button;
    this._log(`Draw ${h.street}. ${this.names[h.toAct]} acts first.`);
    this._advance();
  }

  _showdown() {
    const h = this.hand;
    const a = evaluate27(h.hands[0]);
    const b = evaluate27(h.hands[1]);
    const cmp = compare27(a, b);
    if (cmp < 0) this._finish(0, "showdown");
    else if (cmp > 0) this._finish(1, "showdown");
    else this._finish(-1, "showdown");
  }

  _finish(winner, reason) {
    const h = this.hand;
    if (h.result) return;
    const pot = h.pot;
    const net = [0, 0];
    if (winner === -1) {
      const half = Math.floor(pot / 2);
      this.stacks[0] += half;
      this.stacks[1] += pot - half;
      net[0] = half - h.contributed[0];
      net[1] = (pot - half) - h.contributed[1];
      h.log.push(`${this.names[0]} and ${this.names[1]} split the pot (${pot}).`);
    } else {
      this.stacks[winner] += pot;
      net[winner] = pot - h.contributed[winner];
      net[1 - winner] = -h.contributed[1 - winner];
      h.log.push(`${this.names[winner]} wins ${pot} (${reason}).`);
    }
    h.result = {
      winner,
      reason,
      pot,
      net,
      hands: [h.hands[0].map(String), h.hands[1].map(String)],
      names: [describe27(h.hands[0]), describe27(h.hands[1])],
    };
    h.phase = "over";
    this.history.push({ hand: this.handNumber, ...h.result });
  }

  _botBet() {
    const h = this.hand;
    if (!this._canAct(1)) {
      h.acted[1] = true;
      h.toAct = 0;
      this._advance();
      return;
    }
    const toCall = Math.max(0, h.currentBet - h.bets[1]);
    const value = strength(h.hands[1]);
    const roll = this.rng();
    const raisesLeft = h.raises < 3;
    const canRaise = raisesLeft && this.stacks[1] > toCall;
    let kind;
    if (toCall === 0) {
      if (canRaise && (value > 0.72 || (value > 0.5 && roll > 0.72))) kind = "raise";
      else kind = "check";
    } else {
      if (value > 0.82 && canRaise && roll > 0.35) kind = "raise";
      else if (value > 0.38 || toCall <= this.bigBlind) kind = "call";
      else kind = "fold";
    }
    this._act(1, kind);
  }

  _act(player, kind) {
    const h = this.hand;
    if (h.result || h.phase !== "bet") return;
    if (h.toAct !== player) return;
    const other = 1 - player;
    const toCall = Math.max(0, h.currentBet - h.bets[player]);
    if (kind === "fold") {
      if (toCall === 0) return; // checking is free; never fold for nothing
      h.folded[player] = true;
      this._log(`${this.names[player]} folds.`);
      this._advance();
      return;
    }
    if (kind === "check") {
      if (toCall > 0) return;
      h.acted[player] = true;
      this._log(`${this.names[player]} checks.`);
      h.toAct = this._nextActor(player);
      this._advance();
      return;
    }
    if (kind === "call") {
      if (toCall === 0) {
        h.acted[player] = true;
        this._log(`${this.names[player]} checks.`);
      } else {
        const paid = this._commit(player, toCall);
        h.acted[player] = true;
        this._log(`${this.names[player]} calls ${paid}${this._allIn(player) ? " (all in)" : ""}.`);
      }
      h.toAct = this._nextActor(player);
      this._advance();
      return;
    }
    if (kind === "raise") {
      if (h.raises >= 4) return;
      const target = h.currentBet + this.betSize;
      const need = target - h.bets[player];
      if (need <= 0) return;
      const paid = this._commit(player, need);
      if (paid < need) {
        // short all-in: not a full raise
        this._log(`${this.names[player]} is all in for ${paid}.`);
      } else {
        h.raises += 1;
        h.currentBet = target;
        h.acted[other] = false;
        this._log(`${this.names[player]} raises to ${target}.`);
      }
      h.acted[player] = true;
      h.toAct = this._nextActor(player);
      this._advance();
    }
  }

  humanAction(kind, amount = 0) {
    if (!this.hand || this.hand.result || this.hand.phase !== "bet" || this.hand.toAct !== 0) return this.publicState();
    this._act(0, kind);
    return this.publicState();
  }

  humanDraw(indices = []) {
    const h = this.hand;
    if (!h || h.result || h.phase !== "draw" || h.drawDone?.[0]) return this.publicState();
    const clean = [...new Set(indices.filter((i) => Number.isInteger(i) && i >= 0 && i < 5))];
    this._draw(0, clean);
    h.drawDone[0] = true;
    h.drawIndex = Math.max(h.drawIndex, 1);
    this._log(clean.length ? `${this.names[0]} draws ${clean.length}.` : `${this.names[0]} stands pat.`);
    if (!h.drawDone[1]) this._botDraw();
    if (h.drawDone[0] && h.drawDone[1]) this._afterDraw();
    return this.publicState();
  }

  botDrawAndContinue() {
    const h = this.hand;
    if (!h || h.result) return this.publicState();
    if (!h.drawDone?.[1]) {
      this._botDraw();
      h.phase = "draw";
    }
    if (h.drawDone[0] && h.drawDone[1]) this._afterDraw();
    return this.publicState();
  }

  get humanTurn() {
    const h = this.hand;
    return Boolean(h && !h.result && h.phase === "bet" && h.toAct === 0 && this._canAct(0));
  }

  get humanDrawTurn() {
    const h = this.hand;
    return Boolean(h && !h.result && h.phase === "draw" && h.drawOrder?.[h.drawIndex] === 0 && !h.drawDone?.[0]);
  }

  publicState() {
    const h = this.hand;
    const base = {
      handNumber: this.handNumber,
      button: this.button,
      stacks: [...this.stacks],
      names: [...this.names],
      smallBlind: this.smallBlind,
      bigBlind: this.bigBlind,
      history: this.history.slice(-10),
    };
    if (!h) return { ...base, waitingFor: "next" };
    const toCall = h.phase === "bet" ? Math.max(0, h.currentBet - h.bets[0]) : 0;
    const result = h.result;
    return {
      ...base,
      hand: {
        street: h.street,
        phase: h.phase,
        pot: h.pot,
        bets: [...h.bets],
        stacks: [...this.stacks],
        folded: [...h.folded],
        drawCounts: [...(h.drawn || [0, 0])],
        toAct: h.toAct,
        toCall,
        betSize: this.betSize,
        canRaise: h.phase === "bet" && h.toAct === 0 && h.raises < 4 && !this._allIn(0),
        canCheck: toCall === 0,
        humanCards: h.hands[0].map(String),
        botCards: result ? h.hands[1].map(String) : h.hands[1].map(() => null),
        humanHand: result ? result.names[0] : describe27(h.hands[0]),
        result,
        log: [...h.log],
      },
      waitingFor: result ? "over" : this.humanTurn ? "human" : this.humanDrawTurn ? "draw" : h.phase === "draw" ? "botdraw" : "ai",
    };
  }
}
