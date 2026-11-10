// The 6-player table: one human seat plus five bots. The whole game is
// serialisable so a Worker can store it in D1 between requests.

import { Card, fullDeck } from "./card_utils.js";
import { PlayerAction } from "./player_action.js";
import { GameState } from "./game_state.js";
import { HandSession } from "./engine.js";
import { PythonRandom } from "./pyrandom.js";
import { BOTS, botByKey, createBot, TABLE_BOTS } from "./bots.js";

function actionToJSON(action) {
  return action.toJSON();
}

function actionFromJSON(a) {
  if (a.kind === "reveal_card") return PlayerAction.revealCard(Card.parse(a.card), a.player_id);
  if (a.kind === "win") return PlayerAction.win(a.player_id, a.amount);
  if (a.kind === "raise") return PlayerAction.raiseTo(a.player_id, a.amount);
  if (a.kind === "call") return PlayerAction.call(a.player_id);
  return PlayerAction.fold(a.player_id);
}

function serializeModel(model) {
  return {
    hole: [...model.hole],
    player_id: model.player_id,
    opponents: { stats: model.opponents.stats },
    _hand_actions: model._hand_actions.map(actionToJSON),
    _hand_seed: model._hand_seed,
    _opp_raised: model._opp_raised,
    _opp_three_bet: model._opp_three_bet,
    _our_raises: model._our_raises,
    _pre_raises: model._pre_raises,
    _preflop_done: model._preflop_done,
  };
}

function restoreModel(model, data) {
  model.hole = [...data.hole];
  model.player_id = data.player_id;
  model.opponents.stats = data.opponents.stats;
  model._hand_actions = data._hand_actions.map(actionFromJSON);
  model._hand_seed = data._hand_seed;
  model._opp_raised = data._opp_raised;
  model._opp_three_bet = data._opp_three_bet;
  model._our_raises = data._our_raises;
  model._pre_raises = data._pre_raises;
  model._preflop_done = data._preflop_done;
  return model;
}

function serializeHand(hand) {
  return {
    count: hand.count,
    humanId: hand.humanId,
    state: {
      teams: [...hand.state.teams],
      button: hand.state.button,
      small_blind: hand.state.small_blind,
      big_blind: hand.state.big_blind,
      stacks: [...hand.state.stacks],
      bets: [...hand.state.bets],
      folded: [...hand.state.folded],
      acted: [...hand.state.acted],
      board: hand.state.board.map(String),
      street: hand.state.street,
      pot: hand.state.pot,
      last_raise: hand.state.last_raise,
      to_act: hand.state.to_act,
    },
    hands: hand.hands.map((h) => h.map(String)),
    boardCards: hand.boardCards.slice(hand.boardIndex).map(String),
    faults: hand.faults,
    seats: hand.seats.map((s, i) => (s ? { index: i } : null)),
    actions: hand.actions.map(actionToJSON),
    unseen: hand.unseen.map((list) => list.map(actionToJSON)),
    result: hand.result,
    startStacks: [...hand.startStacks],
    button: hand.button,
    smallBlind: hand.smallBlind,
    bigBlind: hand.bigBlind,
  };
}

function restoreHand(data, modelInstances) {
  const session = Object.create(HandSession.prototype);
  session.count = data.count;
  session.humanId = data.humanId;
  const state = new GameState(data.state.stacks);
  state.teams = [...data.state.teams];
  state.button = data.state.button;
  state.small_blind = data.state.small_blind;
  state.big_blind = data.state.big_blind;
  state.stacks = [...data.state.stacks];
  state.bets = [...data.state.bets];
  state.folded = [...data.state.folded];
  state.acted = [...data.state.acted];
  state.board = data.state.board.map((c) => Card.parse(c));
  state.street = data.state.street;
  state.pot = data.state.pot;
  state.last_raise = data.state.last_raise;
  state.to_act = data.state.to_act;
  session.state = state;
  session.hands = data.hands.map((h) => h.map((c) => Card.parse(c)));
  session.boardCards = data.boardCards.map((c) => Card.parse(c));
  session.boardIndex = 0;
  session.faults = { ...data.faults };
  session.seats = data.seats.map((s, i) =>
    s ? { model: modelInstances[i], player_id: i } : null
  );
  session.actions = data.actions.map(actionFromJSON);
  session.unseen = data.unseen.map((list) => list.map(actionFromJSON));
  session.result = data.result;
  session.startStacks = [...data.startStacks];
  session.button = data.button;
  session.smallBlind = data.smallBlind;
  session.bigBlind = data.bigBlind;
  return session;
}

export class Game {
  constructor(options = {}) {
    this.humanName = options.humanName || "You";
    this.botKeys = options.botKeys || TABLE_BOTS;
    this.humanPlaying = options.humanPlaying !== false;
    this.stack = options.stack || 500;
    this.smallBlind = options.smallBlind || 10;
    this.bigBlind = options.bigBlind || 20;
    this.seed = options.seed || 1;
    this.players = [];
    if (this.humanPlaying) {
      this.players.push({ name: this.humanName, key: null, isHuman: true });
    }
    for (const key of this.botKeys) {
      const spec = botByKey(key);
      this.players.push({ name: spec.name, key, isHuman: false });
    }
    this.modelInstances = this.players.map((p) =>
      p.isHuman ? null : createBot(p.key)
    );
    this.stacks = Array(this.players.length).fill(this.stack);
    this.button = 0;
    this.handNumber = 0;
    this.hand = null;
    this.history = [];
    this.awaitingNextHand = true;
    this.humanBusted = false;
  }
  serialize() {
    return {
      version: 1,
      humanName: this.humanName,
      botKeys: this.botKeys,
      humanPlaying: this.humanPlaying,
      stack: this.stack,
      smallBlind: this.smallBlind,
      bigBlind: this.bigBlind,
      seed: this.seed,
      players: this.players,
      stacks: this.stacks,
      button: this.button,
      handNumber: this.handNumber,
      history: this.history,
      awaitingNextHand: this.awaitingNextHand,
      humanBusted: this.humanBusted,
      models: this.modelInstances.map((m) => (m ? serializeModel(m) : null)),
      hand: this.hand ? serializeHand(this.hand) : null,
    };
  }

  static deserialize(data) {
    const game = Object.create(Game.prototype);
    game.humanName = data.humanName;
    game.botKeys = data.botKeys;
    game.humanPlaying = data.humanPlaying !== false;
    game.stack = data.stack;
    game.smallBlind = data.smallBlind;
    game.bigBlind = data.bigBlind;
    game.seed = data.seed;
    game.players = data.players;
    game.stacks = data.stacks;
    game.button = data.button;
    game.handNumber = data.handNumber;
    game.history = data.history || [];
    game.awaitingNextHand = data.awaitingNextHand;
    game.humanBusted = data.humanBusted;
    game.modelInstances = data.players.map((p, i) =>
      p.isHuman ? null : restoreModel(createBot(p.key), data.models[i])
    );
    game.hand = data.hand ? restoreHand(data.hand, game.modelInstances) : null;
    return game;
  }

  get seatCount() {
    return this.players.length;
  }

  get humanId() {
    return this.humanPlaying ? 0 : null;
  }

  startHand() {
    if (!this.awaitingNextHand) throw new Error("a hand is already in progress");
    const deck = fullDeck();
    // Same scheme as the Python arena: a fresh Random per deal, shuffled.
    new PythonRandom((this.seed * 1000003 + this.handNumber) >>> 0).shuffle(deck);
    const teams = this.players.map((p) => p.name);
    this.hand = new HandSession(
      this.modelInstances,
      deck,
      this.stacks,
      this.button,
      this.smallBlind,
      this.bigBlind,
      teams,
      this.humanId
    );
    this.handNumber += 1;
    this.awaitingNextHand = false;
    const status = this.hand.advance();
    return { status, state: this.publicState() };
  }

  act(kind, amount = 0) {
    if (this.awaitingNextHand) throw new Error("start the next hand first");
    if (!this.hand || this.hand.result) throw new Error("the hand is over");
    if (!this.humanPlaying) throw new Error("no human seat");
    let action;
    if (kind === "fold") action = PlayerAction.fold(this.humanId);
    else if (kind === "call") action = PlayerAction.call(this.humanId);
    else if (kind === "raise") action = PlayerAction.raiseTo(this.humanId, amount);
    else throw new Error(`unknown action ${kind}`);
    const status = this.hand.humanAction(action);
    if (status === "over") this._finalizeHand();
    return { status, state: this.publicState() };
  }

  _finalizeHand() {
    const result = this.hand.result;
    this.stacks = [...result.finalStacks];
    // Keep the table full: busted bots rebuy, a busted human ends the run.
    for (let i = 0; i < this.stacks.length; i += 1) {
      if (this.stacks[i] === 0 && !this.players[i].isHuman) {
        this.stacks[i] = this.stack;
      }
    }
    this.humanBusted = this.stacks[this.humanId] === 0;
    this.history.push({
      hand: this.handNumber,
      net: result.net,
      stacks: [...this.stacks],
      board: result.board,
      faults: result.faults,
    });
    this.button = (this.button + 1) % this.seatCount;
    this.awaitingNextHand = true;
  }

  rebuy() {
    this.stacks = Array(this.seatCount).fill(this.stack);
    this.humanBusted = false;
    this.awaitingNextHand = true;
  }

  publicState() {
    const h = this.hand;
    const base = {
      handNumber: this.handNumber,
      button: this.button,
      stacks: [...this.stacks],
      awaitingNextHand: this.awaitingNextHand,
      humanBusted: this.humanBusted,
      seatCount: this.seatCount,
      players: this.players.map((p) => ({ name: p.name, isHuman: p.isHuman })),
    };
    if (!h) return { ...base, waitingFor: "next" };
    const s = h.state;
    const humanTurn = this.humanPlaying && !h.result && s.to_act === this.humanId;
    const youHole = this.humanPlaying && h.hands[0] ? h.hands[0].map(String) : null;
    const seats = this.players.map((p, i) => ({
      seat: i,
      name: p.name,
      isHuman: p.isHuman,
      stack: s.stacks[i],
      bet: s.bets[i],
      folded: s.folded[i],
      acted: s.acted[i],
      status: s.status(i),
      hole:
        this.humanPlaying && i === 0
          ? youHole
          : h.result
            ? h.result.hands[i].map(String)
            : null,
    }));
    return {
      ...base,
      hand: {
        button: s.button,
        street: s.street,
        board: s.board.map(String),
        pot: s.pot,
        teams: [...s.teams],
        stacks: [...s.stacks],
        bets: [...s.bets],
        folded: [...s.folded],
        acted: [...s.acted],
        toAct: s.to_act,
        callAmount: humanTurn ? s.call_amount : null,
        minRaiseTo: humanTurn && s.can_raise ? s.min_raise_to : null,
        maxRaiseTo: humanTurn && s.can_raise ? s.max_raise_to : null,
        canRaise: humanTurn ? s.can_raise : null,
        legalActions: humanTurn ? s.legal_actions : null,
        potOdds: humanTurn ? s.pot_odds : null,
        seats,
        actions: h.actions.map(actionToJSON),
        faults: h.faults,
        result: h.result,
      },
      waitingFor: h.result ? "over" : humanTurn ? "human" : this.humanPlaying ? "ai" : "over",
      history: this.history.slice(-10),
    };
  }
}

export { BOTS, botByKey, createBot, TABLE_BOTS };
