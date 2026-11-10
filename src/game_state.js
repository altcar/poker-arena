// JS port of game_state.py.
// The engine and bots talk to this state; the interactive game pauses when
// to_act is the human seat.

import { Card } from "./card_utils.js";
import { DEALER, PlayerAction } from "./player_action.js";

export const BOARD_SIZES = { 3: "flop", 4: "turn", 5: "river" };

export class InvalidAction extends Error {}

export class GameState {
  constructor(stacks = [500, 500], teams = null) {
    this.setupHand(stacks, 0, 10, 20, teams);
  }

  setupHand(stacks, button = 0, smallBlind = 10, bigBlind = 20, teams = null) {
    const count = stacks.length;
    if (count < 2) throw new Error("a hand needs at least two players");
    if (button >= count) {
      throw new Error(`button must be a player id 0..${count - 1}, not ${button}`);
    }
    if (smallBlind > bigBlind) {
      throw new Error(`small_blind ${smallBlind} is bigger than big_blind ${bigBlind}`);
    }
    if (teams == null) teams = Array.from({ length: count }, (_, p) => `seat ${p}`);
    if (teams.length !== count) {
      throw new Error(`${teams.length} teams for ${count} stacks: give one per seat`);
    }
    if (new Set(teams).size !== count) {
      throw new Error(`team names must be unique, not ${teams}`);
    }
    this.teams = [...teams];
    this.button = button;
    this.small_blind = smallBlind;
    this.big_blind = bigBlind;
    this.stacks = [...stacks];
    this.bets = Array(count).fill(0);
    this.folded = Array(count).fill(false);
    this.acted = Array(count).fill(false);
    this.board = [];
    this.street = "preflop";
    this.pot = 0;
    this.last_raise = bigBlind;
    this.to_act = null;

    const small = count === 2 ? button : (button + 1) % count;
    const big = (small + 1) % count;
    this._commit(small, Math.min(smallBlind, this.stacks[small]));
    this._commit(big, Math.min(bigBlind, this.stacks[big]));
    this._passTurn(big);
  }

  setStack(playerId, chips) {
    if (playerId >= this.num_players) throw new Error(`no player ${playerId}`);
    this.stacks[playerId] = chips;
  }

  // -- queries: the table -------------------------------------------------

  get num_players() {
    return this.stacks.length;
  }

  get players_in_hand() {
    const out = [];
    for (let p = 0; p < this.num_players; p += 1) {
      if (!this.folded[p]) out.push(p);
    }
    return out;
  }

  get active_players() {
    const out = [];
    for (let p = 0; p < this.num_players; p += 1) {
      if (this.status(p) === "active") out.push(p);
    }
    return out;
  }

  get is_street_closed() {
    return this.to_act === null;
  }

  get is_hand_over() {
    return (
      this.players_in_hand.length === 1 ||
      (this.street === "river" && this.is_street_closed)
    );
  }

  get current_bet() {
    return Math.max(...this.bets);
  }

  // -- queries: any player -----------------------------------------------

  status(playerId) {
    if (this.folded[playerId]) return "folded";
    return this.stacks[playerId] === 0 ? "all_in" : "active";
  }

  effective_stack(playerId) {
    const others = this.players_in_hand
      .filter((p) => p !== playerId)
      .map((p) => this.stacks[p]);
    return Math.min(this.stacks[playerId], others.length ? Math.max(...others) : 0);
  }

  spr(playerId) {
    return this.effective_stack(playerId) / this.pot;
  }

  // -- queries: the player to act ----------------------------------------

  get call_amount() {
    const me = this.to_act;
    return Math.min(this.current_bet - this.bets[me], this.stacks[me]);
  }

  get max_raise_to() {
    return this.bets[this.to_act] + this.stacks[this.to_act];
  }

  get min_raise_to() {
    return Math.min(this.current_bet + this.last_raise, this.max_raise_to);
  }

  get can_raise() {
    const me = this.to_act;
    const others = this.active_players.filter((p) => p !== me);
    return (
      !this.acted[me] &&
      this.max_raise_to > this.current_bet &&
      others.length > 0
    );
  }

  get legal_actions() {
    if (this.is_street_closed) return [];
    return ["fold", "call"].concat(this.can_raise ? ["raise"] : []);
  }

  get raise_range() {
    if (!this.can_raise) return null;
    return [this.min_raise_to, this.max_raise_to];
  }

  get pot_odds() {
    if (this.call_amount === 0) return 0.0;
    return this.call_amount / (this.pot + this.call_amount);
  }

  // -- validation --------------------------------------------------------

  validate_action(action) {
    if (action.player_id !== this.to_act) {
      throw new InvalidAction(
        `player ${action.player_id} acted out of turn; it is player ${this.to_act}'s turn`
      );
    }
    if (action.kind === "fold" || action.kind === "call") return;
    if (action.kind !== "raise") {
      throw new InvalidAction(`${action.kind} is not a move: fold, call or raise`);
    }
    if (!this.can_raise) {
      throw new InvalidAction("you cannot raise now; fold or call");
    }
    const low = this.min_raise_to;
    const high = this.max_raise_to;
    if (!(low <= action.amount && action.amount <= high)) {
      throw new InvalidAction(
        `raise_to(${action.amount}) is outside the allowed range: the total must be between ${low} and ${high}`
      );
    }
  }

  // -- updates -----------------------------------------------------------

  process_action(action) {
    if (action.player_id === DEALER) {
      this.deal_card(action.card);
      return;
    }
    if (action.kind === "reveal_card" || action.kind === "win") {
      this._settle(action);
      return;
    }
    this.validate_action(action);
    const playerId = action.player_id;
    if (action.kind === "fold") {
      this.folded[playerId] = true;
    } else if (action.kind === "call") {
      this._commit(playerId, this.call_amount);
    } else if (action.kind === "raise") {
      const raiseSize = action.amount - this.current_bet;
      this._commit(playerId, action.amount - this.bets[playerId]);
      if (raiseSize >= this.last_raise) {
        this.last_raise = raiseSize;
        this.acted = Array(this.num_players).fill(false);
      }
    } else {
      throw new InvalidAction(`${action.kind} is not a move`);
    }
    this.acted[playerId] = true;
    this._passTurn(playerId);
  }

  deal_card(card) {
    if (this.is_hand_over) throw new InvalidAction("the hand is over");
    if (!this.is_street_closed) {
      throw new InvalidAction(
        `betting is still open: it is player ${this.to_act}'s turn`
      );
    }
    if (this.board.some((c) => c.equals(card))) {
      throw new InvalidAction(`${card} is already on the board`);
    }
    this.board.push(card);
    const street = BOARD_SIZES[this.board.length];
    if (street) this._nextStreet(street);
  }

  _settle(action) {
    if (!this.is_hand_over) {
      throw new InvalidAction(`${action.kind} only comes once the hand is over`);
    }
    const playerId = action.player_id;
    if (!(playerId >= 0 && playerId < this.num_players)) {
      throw new InvalidAction(`no player ${playerId}`);
    }
    if (this.folded[playerId]) {
      throw new InvalidAction(`player ${playerId} has folded`);
    }
    if (action.kind === "win") {
      this.stacks[playerId] += action.amount;
    }
  }

  _nextStreet(street) {
    this.street = street;
    this.bets = Array(this.num_players).fill(0);
    this.acted = Array(this.num_players).fill(false);
    this.last_raise = this.big_blind;
    this._passTurn(this.button);
  }

  _commit(playerId, chips) {
    if (!(chips <= this.stacks[playerId])) {
      throw new Error("bet exceeds stack");
    }
    this.stacks[playerId] -= chips;
    this.bets[playerId] += chips;
    this.pot += chips;
  }

  _passTurn(after) {
    const active = this.active_players;
    const waiting = active.filter(
      (p) => !this.acted[p] || this.bets[p] < this.current_bet
    );
    const loneAndMatched =
      active.length === 1 && this.bets[active[0]] >= this.current_bet;
    if (this.players_in_hand.length === 1 || loneAndMatched || waiting.length === 0) {
      this.to_act = null;
      return;
    }
    for (let step = 1; step <= this.num_players; step += 1) {
      const playerId = (after + step) % this.num_players;
      if (waiting.includes(playerId)) {
        this.to_act = playerId;
        return;
      }
    }
  }
}
