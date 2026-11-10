// JS port of engine.py, plus an interactive HandSession that pauses when the
// human seat is to act.

import { Card, evaluate, compareRank, fullDeck } from "./card_utils.js";
import { PlayerAction, DEALER } from "./player_action.js";
import { GameState } from "./game_state.js";

export { fullDeck };

export function describeFault(where, error) {
  let message;
  try {
    message = String(error);
  } catch {
    message = "(message could not be shown)";
  }
  return `${where}: ${error?.name || "Error"}: ${message}`;
}

export function payouts(contributed, folded, hands, board, button) {
  const count = contributed.length;
  const won = Array(count).fill(0);
  const live = [];
  for (let p = 0; p < count; p += 1) if (!folded[p]) live.push(p);
  if (live.length === 1) {
    won[live[0]] = contributed.reduce((a, b) => a + b, 0);
    return won;
  }
  const ranks = {};
  for (const p of live) ranks[p] = evaluate([...hands[p], ...board]);
  const order = [];
  for (let step = 1; step <= count; step += 1) order.push((button + step) % count);
  let previous = 0;
  for (const level of [...new Set(contributed)].sort((a, b) => a - b)) {
    let pot = 0;
    for (const c of contributed) pot += Math.min(c, level) - Math.min(c, previous);
    previous = level;
    const eligible = live.filter((p) => contributed[p] >= level);
    let best = ranks[eligible[0]];
    for (const p of eligible) if (compareRank(ranks[p], best) > 0) best = ranks[p];
    const winners = order.filter((p) => eligible.includes(p) && compareRank(ranks[p], best) === 0);
    const share = Math.floor(pot / winners.length);
    const odd = pot % winners.length;
    winners.forEach((p, i) => {
      won[p] += share + (i < odd ? 1 : 0);
    });
  }
  return won;
}

function ask(seat, pending, state, faults, playerId) {
  if (seat) {
    try {
      let action = seat.model.run(pending, state);
      if (!(action instanceof PlayerAction)) {
        throw new Error("run() must return a PlayerAction");
      }
      action = new PlayerAction(action.player_id, action.kind, action.amount);
      state.validate_action(action);
      return action;
    } catch (error) {
      if (!(playerId in faults)) faults[playerId] = describeFault("run", error);
    }
  }
  if (state.call_amount === 0) return PlayerAction.call(playerId);
  return PlayerAction.fold(playerId);
}

// Plays a whole hand with no human (used for simulations and verification).
export function playHand(models, deck, stacks, button = 0, smallBlind = 10, bigBlind = 20, teams = null) {
  const count = models.length;
  if (stacks.length !== count) throw new Error(`${count} models for ${stacks.length} stacks`);
  if (deck.length < 2 * count + 5) {
    throw new Error(`a deck of ${deck.length} cards is too short for ${count} seats`);
  }
  const deckKeys = deck.map(String);
  if (new Set(deckKeys).size !== deck.length) throw new Error("the deck has a duplicate card");

  const state = new GameState(stacks);
  state.setupHand(stacks, button, smallBlind, bigBlind, teams);

  const hands = [];
  for (let p = 0; p < count; p += 1) hands.push(deck.slice(2 * p, 2 * p + 2));
  const boardCards = deck.slice(2 * count, 2 * count + 5);
  const faults = {};
  const seats = [];
  for (let p = 0; p < count; p += 1) {
    try {
      models[p].seed(hands[p], p);
      seats.push({ model: models[p], player_id: p });
    } catch (error) {
      faults[p] = describeFault("seed", error);
      seats.push(null);
    }
  }

  const actions = [];
  let unseen = Array.from({ length: count }, () => []);
  let boardIndex = 0;
  while (!state.is_hand_over) {
    let action;
    if (state.is_street_closed) {
      action = PlayerAction.revealCard(boardCards[boardIndex]);
      boardIndex += 1;
    } else {
      const p = state.to_act;
      const pending = unseen[p];
      unseen[p] = [];
      action = ask(seats[p], pending, state, faults, p);
    }
    state.process_action(action);
    actions.push(action);
    for (const pending of unseen) pending.push(action);
  }

  const contributed = stacks.map((s, p) => s - state.stacks[p]);
  const won = payouts(contributed, state.folded, hands, state.board, button);
  const order = [];
  for (let step = 1; step <= count; step += 1) order.push((button + step) % count);
  let shown = order.filter((p) => !state.folded[p]);
  if (shown.length === 1) shown = [];
  const endings = [];
  for (const p of shown) for (const card of hands[p]) endings.push(PlayerAction.revealCard(card, p));
  for (const p of order) if (won[p] > 0) endings.push(PlayerAction.win(p, won[p]));
  for (const action of endings) {
    state.process_action(action);
    actions.push(action);
    for (const pending of unseen) pending.push(action);
  }
  for (let p = 0; p < count; p += 1) {
    const pending = unseen[p];
    unseen[p] = [];
    if (!seats[p]) continue;
    try {
      seats[p].model.end_game?.(pending, state);
    } catch (error) {
      if (!(p in faults)) faults[p] = describeFault("end_game", error);
    }
  }

  return {
    net: won.map((w, p) => w - contributed[p]),
    actions,
    hands,
    board: [...state.board],
    faults,
  };
}

// One hand, pausing whenever the human seat is to act.
export class HandSession {
  constructor(models, deck, stacks, button = 0, smallBlind = 10, bigBlind = 20, teams = null, humanId = 0) {
    const count = models.length;
    if (stacks.length !== count) throw new Error(`${count} models for ${stacks.length} stacks`);
    if (deck.length < 2 * count + 5) {
      throw new Error(`a deck of ${deck.length} cards is too short for ${count} seats`);
    }
    const deckKeys = deck.map(String);
    if (new Set(deckKeys).size !== deck.length) throw new Error("the deck has a duplicate card");

    this.count = count;
    this.humanId = humanId;
    this.state = new GameState(stacks);
    this.state.setupHand(stacks, button, smallBlind, bigBlind, teams);
    this.hands = [];
    for (let p = 0; p < count; p += 1) this.hands.push(deck.slice(2 * p, 2 * p + 2));
    this.boardCards = deck.slice(2 * count, 2 * count + 5);
    this.boardIndex = 0;
    this.faults = {};
    this.seats = [];
    for (let p = 0; p < count; p += 1) {
      try {
        models[p].seed(this.hands[p], p);
        this.seats.push({ model: models[p], player_id: p });
      } catch (error) {
        this.faults[p] = describeFault("seed", error);
        this.seats.push(null);
      }
    }
    this.actions = [];
    this.unseen = Array.from({ length: count }, () => []);
    this.result = null;
    this.startStacks = [...stacks];
    this.button = button;
    this.smallBlind = smallBlind;
    this.bigBlind = bigBlind;
  }

  get isOver() {
    return this.state.is_hand_over;
  }

  _apply(action) {
    this.state.process_action(action);
    this.actions.push(action);
    for (const pending of this.unseen) pending.push(action);
  }

  _stepDealer() {
    const action = PlayerAction.revealCard(this.boardCards[this.boardIndex]);
    this.boardIndex += 1;
    this._apply(action);
  }

  _stepPlayer(p) {
    const pending = this.unseen[p];
    this.unseen[p] = [];
    const action = ask(this.seats[p], pending, this.state, this.faults, p);
    this._apply(action);
  }

  // Run dealer and AI actions until the human is to act, or the hand ends.
  advance() {
    if (this.result) return "over";
    while (!this.state.is_hand_over) {
      if (this.state.is_street_closed) {
        this._stepDealer();
        continue;
      }
      const p = this.state.to_act;
      if (p === this.humanId) return "human";
      this._stepPlayer(p);
    }
    this._finish();
    return "over";
  }

  humanAction(action) {
    if (this.result) throw new Error("the hand is over");
    this.state.validate_action(action);
    this._apply(action);
    return this.advance();
  }

  _finish() {
    const state = this.state;
    const contributed = this.startStacks.map((s, p) => s - state.stacks[p]);
    const won = payouts(contributed, state.folded, this.hands, state.board, this.button);
    const order = [];
    for (let step = 1; step <= this.count; step += 1) order.push((this.button + step) % this.count);
    let shown = order.filter((p) => !state.folded[p]);
    if (shown.length === 1) shown = [];
    const endings = [];
    for (const p of shown) for (const card of this.hands[p]) endings.push(PlayerAction.revealCard(card, p));
    for (const p of order) if (won[p] > 0) endings.push(PlayerAction.win(p, won[p]));
    for (const action of endings) {
      state.process_action(action);
      this.actions.push(action);
      for (const pending of this.unseen) pending.push(action);
    }
    for (let p = 0; p < this.count; p += 1) {
      const pending = this.unseen[p];
      this.unseen[p] = [];
      if (!this.seats[p]) continue;
      try {
        this.seats[p].model.end_game?.(pending, state);
      } catch (error) {
        if (!(p in this.faults)) this.faults[p] = describeFault("end_game", error);
      }
    }
    this.result = {
      net: won.map((w, p) => w - contributed[p]),
      actions: [...this.actions],
      hands: this.hands.map((h) => h.map((c) => String(c))),
      board: state.board.map(String),
      faults: { ...this.faults },
      won,
      contributed,
      finalStacks: [...state.stacks],
    };
  }
}
