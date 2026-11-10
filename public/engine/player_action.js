// JS port of player_action.py.

export const DEALER = -1;

export class PlayerAction {
  constructor(playerId, kind, amount = 0, card = null) {
    if (kind === "reveal_card") {
      if (card == null) throw new Error("reveal_card needs a Card");
    } else {
      if (playerId === DEALER) throw new Error(`the dealer cannot ${kind}`);
      if (card != null) throw new Error(`${kind} takes no card`);
    }
    if (kind === "raise" || kind === "win") {
      if (!(amount > 0)) throw new Error(`${kind} needs chips > 0, not ${amount}`);
    } else if (amount !== 0) {
      throw new Error(`${kind} takes no amount`);
    }
    this.player_id = playerId;
    this.kind = kind;
    this.amount = amount;
    this.card = card;
  }

  static fold(playerId) {
    return new PlayerAction(playerId, "fold");
  }

  static call(playerId) {
    return new PlayerAction(playerId, "call");
  }

  static raiseTo(playerId, total) {
    return new PlayerAction(playerId, "raise", total);
  }

  // Python name, used by the ported policy.
  static raise_to(playerId, total) {
    return PlayerAction.raiseTo(playerId, total);
  }

  static win(playerId, amount) {
    return new PlayerAction(playerId, "win", amount);
  }

  static revealCard(card, playerId = DEALER) {
    return new PlayerAction(playerId, "reveal_card", 0, card);
  }

  toJSON() {
    return {
      player_id: this.player_id,
      kind: this.kind,
      amount: this.amount,
      card: this.card ? String(this.card) : null,
    };
  }
}
