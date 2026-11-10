// JS port of policy.py — the shared decision engine for the A-E models.
// One implementation, five tiers. The thresholds below are the actual
// class attributes from the Python source; bots.js sets them per variant.

import { encode, preflop_percentile, board_texture, clamp_raise, equity, eval7, has_ace, rank_of, suit_of, OpponentModel, Random } from "./botcore.js";
import { PlayerAction } from "./player_action.js";

export class PokerBot {
  TIER = 2;

  // Ranges, by percentile (0 is the best hand).
  OPEN = 0.62;
  THREE_BET = 0.12;
  DEFEND = 0.48;
  FOUR_BET = 0.045;
  CALL_FOUR = 0.10;
  JAM_BB = 12.0;

  // Postflop thresholds.
  VALUE = 0.62;
  RAISE = 0.72;
  BLUFF = 0.0;
  MARGIN = 0.02;
  VALUE_SIZE = 0.75;
  ITERATIONS = 220;
  COMMIT = true;
  SHOVE_CLOSE = true;
  USE_MDF = true;
  USE_RANGE_FILTER = true;

  constructor() {
    this.hole = [];
    this.player_id = null;
    this.opponents = new OpponentModel();
    this._hand_actions = [];
    this._hand_seed = 0;
    this._opp_raised = false;
    this._opp_three_bet = false;
    this._our_raises = 0;
    this._pre_raises = 0;
    this._preflop_done = false;
  }

  // ------------------------------------------------------------- lifecycle

  seed(hand, player_id) {
    this.hole = hand.map((c) => encode(c));
    this.player_id = player_id;
    this._hand_actions = [];
    this._opp_raised = false;
    this._opp_three_bet = false;
    this._our_raises = 0;
    this._pre_raises = 0;
    this._preflop_done = false;
    const [a, b] = this.hole;
    this._hand_seed = (a * 2654435761 + b * 40503 + 17) & 0xffffffff;
  }

  run(actions, game_state) {
    try {
      this._remember(actions, game_state);
      return this._decide(game_state);
    } catch {
      try {
        const me = this.player_id;
        if (game_state.call_amount === 0) return PlayerAction.call(me);
        return PlayerAction.fold(me);
      } catch {
        return PlayerAction.call(this.player_id);
      }
    }
  }

  end_game(actions, game_state) {
    try {
      this._remember(actions, game_state);
      this.opponents.observe(
        this._hand_actions,
        game_state,
        this.player_id,
        game_state.teams
      );
    } catch {
      // never let end_game fault
    }
  }

  // ---------------------------------------------------------------- helpers

  _encode(card) {
    return encode(card);
  }

  _remember(actions, game_state) {
    if (actions && actions.length) this._hand_actions.push(...actions);
    const preflop = game_state.street === "preflop" && game_state.board.length === 0;
    for (const action of actions) {
      if (action.kind !== "raise") continue;
      if (action.player_id === this.player_id) {
        this._our_raises += 1;
        if (preflop) this._pre_raises += 1;
      } else if (action.player_id >= 0 && preflop) {
        this._pre_raises += 1;
        this._opp_raised = true;
        if (this._pre_raises >= 2) this._opp_three_bet = true;
      }
    }
    if (!preflop) this._preflop_done = true;
  }

  _rand(salt = 0) {
    // Python ints are arbitrary precision; the sum can exceed 32 bits, so
    // use BigInt for the shift/xor/mask to match the Python arithmetic.
    let x = (this._hand_seed >>> 0) + salt * 1013904223 + this._hand_actions.length * 7919;
    x = Number(BigInt(x) ^ (BigInt(x) >> 13n));
    x = Number((BigInt(x) * 1274126177n) & 0xffffffffn);
    return ((x ^ (x >> 16)) & 0xffff) / 65535.0;
  }

  _opponent_team(game_state) {
    const me = this.player_id;
    for (let seat = 0; seat < game_state.teams.length; seat += 1) {
      if (seat !== me) return game_state.teams[seat];
    }
    return "";
  }

  _style(game_state) {
    if (this.TIER < 3) return "unknown";
    return this.opponents.style(this._opponent_team(game_state));
  }

  _facing_raise_amount(game_state) {
    return game_state.current_bet - game_state.big_blind;
  }

  _opponent_filter(game_state) {
    if (this.TIER < 3 || game_state.num_players > 2) return null;
    if (!this.USE_RANGE_FILTER) return null;
    let limit;
    if (this._opp_three_bet) limit = 0.14;
    else if (this._opp_raised) limit = 0.42;
    else limit = 0.75;
    const style = this._style(game_state);
    if (style === "station") limit = Math.min(0.95, limit + 0.18);
    else if (style === "nit") limit = Math.max(0.08, limit - 0.16);
    else if (style === "maniac") limit = Math.min(0.95, limit + 0.10);
    return (combo) => preflop_percentile(combo) <= limit;
  }

  // -------------------------------------------------------------- decisions

  _decide(game_state) {
    if (game_state.street === "preflop") return this._preflop(game_state);
    return this._postflop(game_state);
  }

  // ---------------------------------------------------------------- preflop

  _preflop(game_state) {
    const me = this.player_id;
    const bb = game_state.big_blind;
    const pct = preflop_percentile(this.hole);
    const stack_bb = game_state.stacks[me] / bb;
    const facing = game_state.current_bet > bb;
    const three_bet = this._opp_three_bet;
    const style = this._style(game_state);

    let open_r = this.OPEN;
    let three_r = this.THREE_BET;
    let defend = this.DEFEND;
    if (style === "nit") {
      open_r = Math.min(0.95, open_r + 0.12);
      three_r = Math.min(0.3, three_r + 0.05);
    } else if (style === "station") {
      open_r = Math.max(0.25, open_r - 0.1);
      three_r = Math.max(0.04, three_r - 0.03);
    } else if (style === "maniac") {
      defend = Math.min(0.85, defend + 0.12);
    }

    if (stack_bb <= this.JAM_BB && game_state.can_raise) {
      const jam_pct = this._jam_threshold(stack_bb);
      if (pct <= jam_pct) {
        return PlayerAction.raise_to(me, game_state.max_raise_to);
      }
      if (!facing) return PlayerAction.call(me);
      return PlayerAction.fold(me);
    }

    if (three_bet) {
      if (pct <= this.FOUR_BET && game_state.can_raise) {
        const total = this._raise_to_bb(game_state, 2.6 * game_state.current_bet);
        return PlayerAction.raise_to(me, total);
      }
      if (pct <= this.CALL_FOUR) return PlayerAction.call(me);
      return PlayerAction.fold(me);
    }

    if (facing) {
      if (pct <= three_r && game_state.can_raise) {
        const target = Math.max(
          3.0 * game_state.current_bet,
          game_state.current_bet + 3.0 * bb
        );
        return PlayerAction.raise_to(me, this._raise_to_bb(game_state, target));
      }
      if (pct <= defend) return PlayerAction.call(me);
      if (
        game_state.num_players === 2 &&
        game_state.call_amount <= bb &&
        pct <= Math.min(0.9, defend + 0.25)
      ) {
        return PlayerAction.call(me);
      }
      return PlayerAction.fold(me);
    }

    if (game_state.call_amount === 0) {
      if (pct <= Math.max(this.THREE_BET, 0.1) && game_state.can_raise) {
        return PlayerAction.raise_to(me, this._raise_to_bb(game_state, 3.0 * bb));
      }
      return PlayerAction.call(me);
    }
    if (pct <= open_r) {
      return PlayerAction.raise_to(me, this._raise_to_bb(game_state, 2.2 * bb));
    }
    if (game_state.num_players === 2) return PlayerAction.call(me);
    return PlayerAction.fold(me);
  }

  _jam_threshold(stack_bb) {
    if (stack_bb <= 5) return 0.72;
    if (stack_bb <= 8) return 0.55;
    if (stack_bb <= 10) return 0.42;
    return 0.3;
  }

  _raise_to_bb(game_state, desired_total) {
    return clamp_raise(Math.floor(desired_total), game_state);
  }

  // --------------------------------------------------------------- postflop

  _postflop(game_state) {
    const me = this.player_id;
    const board = game_state.board.map((c) => this._encode(c));
    const opponents = Math.max(1, game_state.players_in_hand.length - 1);
    const cat = eval7([...this.hole, ...board])[0];
    const style = this._style(game_state);
    let iters = this.ITERATIONS;
    if (this.TIER >= 4) iters = Math.floor(iters * 1.4);
    let eq;
    if (this.TIER <= 1) {
      eq = this._heuristic_equity(cat, board);
    } else {
      eq = equity(
        this.hole,
        board,
        opponents,
        this._rng_for(board),
        iters,
        this._opponent_filter(game_state)
      );
    }

    let value = this.VALUE;
    let raise_at = this.RAISE;
    let margin = this.MARGIN;
    if (style === "station") {
      value -= 0.08;
      raise_at -= 0.04;
      margin -= 0.02;
    } else if (style === "nit") {
      value += 0.03;
      raise_at -= 0.02;
    } else if (style === "maniac") {
      value += 0.02;
      margin += 0.06;
    }

    const call = game_state.call_amount;
    const pot_odds = game_state.pot_odds;
    const can_raise = game_state.can_raise;

    const spr = game_state.spr(me);
    const commit = this.COMMIT && spr < 1.2 && eq > 0.45;

    if (call === 0) {
      if (can_raise && (eq >= value || cat >= 2 || commit)) {
        const frac = this._value_fraction(game_state, eq, board);
        return PlayerAction.raise_to(me, this._size(game_state, frac));
      }
      if (can_raise && this.BLUFF > 0 && this._bluff_spot(game_state, board, eq, style)) {
        return PlayerAction.raise_to(me, this._size(game_state, 0.55));
      }
      return PlayerAction.call(me);
    }

    if (can_raise && (eq >= raise_at || commit) && cat >= 0) {
      if (eq >= raise_at || (this.TIER >= 4 && eq >= 0.6)) {
        const frac = this._value_fraction(game_state, eq, board);
        return PlayerAction.raise_to(me, this._size(game_state, frac));
      }
    }

    let threshold = pot_odds + margin;
    if (this.USE_MDF && this.TIER >= 4 && pot_odds < 0.22) {
      threshold = pot_odds + 0.005;
    }
    if (eq >= threshold) return PlayerAction.call(me);
    if (this.TIER >= 5 && eq >= pot_odds && cat >= 1) return PlayerAction.call(me);
    return PlayerAction.fold(me);
  }

  _rng_for(board) {
    return new Random((this._hand_seed ^ (board.length * 7919)) >>> 0);
  }

  _heuristic_equity(cat, board) {
    if (board.length < 3) return 0.5;
    const base = {
      0: 0.22, 1: 0.55, 2: 0.72, 3: 0.82, 4: 0.86,
      5: 0.9, 6: 0.94, 7: 0.97, 8: 0.99,
    }[Math.max(0, Math.min(8, cat))];
    let value = base;
    if (cat === 0) {
      const high = Math.max(...this.hole.map(rank_of));
      value += (high - 4) * 0.012;
    }
    return Math.min(0.99, value);
  }

  _value_fraction(game_state, eq, board) {
    const [paired, , connected] = board_texture(board);
    if (this.TIER >= 4) {
      if (eq > 0.85 && (game_state.street === "turn" || game_state.street === "river")) {
        return 1.15;
      }
      if (paired || connected) return 0.75;
      return 0.66;
    }
    return this.VALUE_SIZE;
  }

  _size(game_state, fraction) {
    const me = this.player_id;
    const call = game_state.call_amount;
    const pot_after = game_state.pot + call;
    let total = game_state.bets[me] + call + Math.floor(pot_after * fraction);
    total = Math.max(total, game_state.bets[me] + call + game_state.big_blind);
    total = clamp_raise(total, game_state);
    if (
      this.SHOVE_CLOSE &&
      game_state.max_raise_to - total <= game_state.big_blind * 2
    ) {
      total = game_state.max_raise_to;
    }
    return total;
  }

  _bluff_spot(game_state, board, eq, style) {
    if (style === "station") return false;
    if (eq > 0.4) return false;
    let freq = this.BLUFF;
    if (style === "nit") freq = Math.min(0.8, freq * 1.6);
    if (board.length < 4) freq *= 0.6;
    if (this._rand(board.length) > freq) return false;
    if (this.TIER >= 4) {
      if (has_ace(this.hole) || this.hole.some((c) => suit_of(c) === suit_of(this.hole[0]))) {
        return true;
      }
      return this._rand(3 + board.length) < 0.35;
    }
    return true;
  }
}
