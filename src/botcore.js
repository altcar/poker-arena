// JS port of botcore.py. Cards are encoded as ints 0..51
// (rank = c >> 2, 0='2'..12='A'; suit = c & 3).

import { Card, RANKS, SUITS } from "./card_utils.js";

export const RANK_TEXT = "23456789TJQKA";
export const FULL = 52;
export const ALL_CARDS = Array.from({ length: FULL }, (_, i) => i);

export function encode(card) {
  return RANK_TEXT.indexOf(card.rank) * 4 + SUITS.indexOf(card.suit);
}

export function decode(c) {
  return new Card(RANK_TEXT[c >> 2], SUITS[c & 3]);
}

export function rank_of(c) {
  return c >> 2;
}

export function suit_of(c) {
  return c & 3;
}

function bitCount(n) {
  let count = 0;
  while (n) {
    count += n & 1;
    n >>>= 1;
  }
  return count;
}

function straightHigh(mask) {
  for (let hi = 12; hi >= 4; hi -= 1) {
    if (((mask >> (hi - 4)) & 0b11111) === 0b11111) return hi;
  }
  if ((mask & 0x100f) === 0x100f) return 3; // wheel: A,2,3,4,5
  return -1;
}

function top5(mask) {
  const out = [];
  for (let r = 12; r >= 0; r -= 1) {
    if ((mask >> r) & 1) {
      out.push(r + 2);
      if (out.length === 5) break;
    }
  }
  while (out.length < 5) out.push(0);
  return out;
}

// Comparable array for the best 5-card hand among 5..7 int cards.
export function eval7(cards) {
  const counts = new Array(13).fill(0);
  const suits = new Array(4).fill(0);
  let rankMask = 0;
  for (const c of cards) {
    const r = c >> 2;
    counts[r] += 1;
    rankMask |= 1 << r;
    suits[c & 3] |= 1 << r;
  }

  let flushSuit = -1;
  for (let s = 0; s < 4; s += 1) {
    if (bitCount(suits[s]) >= 5) {
      flushSuit = s;
      break;
    }
  }
  if (flushSuit >= 0) {
    const sh = straightHigh(suits[flushSuit]);
    if (sh >= 0) return [8, sh + 2];
  }

  const groups = [];
  for (let r = 0; r < 13; r += 1) {
    if (counts[r]) groups.push([counts[r], r]);
  }
  groups.sort((a, b) => (b[0] - a[0]) || (b[1] - a[1]));
  const [topCount, topRank] = groups[0];
  if (topCount === 4) {
    let kicker = -1;
    for (let r = 0; r < 13; r += 1) {
      if (counts[r] && r !== topRank) kicker = Math.max(kicker, r);
    }
    return [7, topRank + 2, kicker + 2];
  }

  const trips = [];
  for (let r = 0; r < 13; r += 1) if (counts[r] >= 3) trips.push(r);
  if (trips.length) {
    const bestTrips = Math.max(...trips);
    const pairs = [];
    for (let r = 0; r < 13; r += 1) {
      if (counts[r] >= 2 && r !== bestTrips) pairs.push(r);
    }
    if (pairs.length) return [6, bestTrips + 2, Math.max(...pairs) + 2];
  }

  if (flushSuit >= 0) return [5, ...top5(suits[flushSuit])];

  const sh = straightHigh(rankMask);
  if (sh >= 0) return [4, sh + 2];

  if (trips.length) {
    const bestTrips = Math.max(...trips);
    const kick = [];
    for (let r = 12; r >= 0; r -= 1) {
      if (counts[r] && r !== bestTrips) kick.push(r);
      if (kick.length === 2) break;
    }
    return [3, bestTrips + 2, kick[0] + 2, kick[1] + 2];
  }

  const pairs = [];
  for (let r = 12; r >= 0; r -= 1) if (counts[r] === 2) pairs.push(r);
  if (pairs.length >= 2) {
    const [hi, lo] = pairs;
    let kick = -1;
    for (let r = 0; r < 13; r += 1) {
      if (counts[r] && r !== hi && r !== lo) kick = Math.max(kick, r);
    }
    return [2, hi + 2, lo + 2, kick + 2];
  }
  if (pairs.length === 1) {
    const p = pairs[0];
    const kick = [];
    for (let r = 12; r >= 0; r -= 1) {
      if (counts[r] && r !== p) kick.push(r);
      if (kick.length === 3) break;
    }
    return [1, p + 2, kick[0] + 2, kick[1] + 2, kick[2] + 2];
  }
  return [0, ...top5(rankMask)];
}

// ------------------------------------------------------------ monte carlo

// Python's random.Random (MT19937) and random.sample, so the Monte-Carlo
// draws match the Python model exactly for a given seed and deck.
import { PythonRandom } from "./pyrandom.js";

export { PythonRandom as Random };

function compareEval(a, b) {
  const n = Math.max(a.length, b.length);
  for (let i = 0; i < n; i += 1) {
    const x = a[i] ?? -1;
    const y = b[i] ?? -1;
    if (x !== y) return x - y;
  }
  return 0;
}

export function equity(hole, board, opponents, rng, iterations = 300, oppFilter = null) {
  const fullBoard = board.slice();
  const dead = new Set([...hole, ...board]);
  const deck = ALL_CARDS.filter((c) => !dead.has(c));
  const missing = 5 - board.length;
  let won = 0;
  let tries = 0;
  while (tries < iterations) {
    const need = missing + 2 * opponents;
    if (need > deck.length) break;
    const draw = rng.sample(deck, need);
    const complete = fullBoard.concat(draw.slice(0, missing));
    const mine = eval7(hole.concat(complete));
    const theirs = [];
    let ok = true;
    let idx = missing;
    for (let i = 0; i < opponents; i += 1) {
      const combo = [draw[idx], draw[idx + 1]];
      idx += 2;
      if (oppFilter && !oppFilter(combo)) {
        ok = false;
        break;
      }
      theirs.push(eval7([...combo, ...complete]));
    }
    if (!ok) {
      tries += 1;
      continue;
    }
    let bestTheirs = theirs[0];
    for (const t of theirs) if (compareEval(t, bestTheirs) > 0) bestTheirs = t;
    const cmp = compareEval(mine, bestTheirs);
    if (cmp > 0) won += 1;
    else if (cmp === 0) {
      const count = theirs.filter((t) => compareEval(t, bestTheirs) === 0).length;
      won += 1 / (1 + count);
    }
    tries += 1;
  }
  return won / Math.max(1, tries);
}

// --------------------------------------------------------------- preflop

export function preflop_class(hole) {
  const r1 = rank_of(hole[0]);
  const r2 = rank_of(hole[1]);
  const s1 = suit_of(hole[0]);
  const s2 = suit_of(hole[1]);
  const hi = Math.max(r1, r2);
  const lo = Math.min(r1, r2);
  if (hi === lo) return RANK_TEXT[hi] + RANK_TEXT[hi];
  return RANK_TEXT[hi] + RANK_TEXT[lo] + (s1 === s2 ? "s" : "o");
}

function chen(hole) {
  const r1 = rank_of(hole[0]);
  const r2 = rank_of(hole[1]);
  const hi = Math.max(r1, r2);
  const lo = Math.min(r1, r2);
  const highVal = { 12: 10, 11: 8, 10: 7, 9: 6, 8: 5 }[hi] ?? (hi + 2) / 2;
  let score;
  if (hi === lo) {
    score = Math.max(5, highVal * 2);
  } else {
    score = highVal;
    const gap = hi - lo - 1;
    if (gap === 1) score -= 1;
    else if (gap === 2) score -= 2;
    else if (gap === 3) score -= 4;
    else if (gap >= 4) score -= 5;
    if (gap <= 1 && hi < 10) score += 1;
  }
  if (suit_of(hole[0]) === suit_of(hole[1])) score += 2;
  return score;
}

function buildPercentiles() {
  const reps = [];
  for (let i = 0; i < 13; i += 1) {
    reps.push([i * 4, i * 4 + 1]); // pair (different suits)
    for (let j = 0; j < i; j += 1) {
      reps.push([i * 4, j * 4]); // suited
      reps.push([i * 4, j * 4 + 1]); // offsuit
    }
  }
  const scored = reps
    .map((h) => ({ score: chen(h), key: preflop_class(h) }))
    .sort((a, b) => b.score - a.score);
  const table = {};
  scored.forEach((row, pos) => {
    table[row.key] = pos / (scored.length - 1);
  });
  return table;
}

export const PERCENTILE = buildPercentiles();

export function preflop_percentile(hole) {
  return PERCENTILE[preflop_class(hole)] ?? 1.0;
}

export function is_suited(hole) {
  return suit_of(hole[0]) === suit_of(hole[1]);
}

export function has_ace(hole) {
  return rank_of(hole[0]) === 12 || rank_of(hole[1]) === 12;
}

// ------------------------------------------------------------- opponent

export class OpponentModel {
  constructor() {
    this.stats = {};
  }

  _row(team) {
    if (!this.stats[team]) {
      this.stats[team] = {
        hands: 0, vpip: 0, pfr: 0, raises: 0,
        calls: 0, folds: 0, showdowns: 0, wins: 0,
        big_bets: 0, fold_to_bet: 0, faced_bet: 0,
      };
    }
    return this.stats[team];
  }

  observe(actions, game_state, me, teams) {
    const bySeat = {};
    for (const a of actions) {
      if (a.kind === "win" || a.kind === "reveal_card") continue;
      if (a.player_id < 0 || a.player_id >= teams.length) continue;
      const seat = a.player_id;
      (bySeat[seat] ||= []).push(a);
    }
    for (const [seatStr, acts] of Object.entries(bySeat)) {
      const seat = Number(seatStr);
      if (seat === me) continue;
      const team = teams[seat];
      const row = this._row(team);
      row.hands += 1;
      let raisedPre = false;
      for (const a of acts) {
        if (a.kind === "raise") {
          row.raises += 1;
          if (a.amount >= game_state.big_blind * 3) row.big_bets += 1;
          raisedPre = true;
        } else if (a.kind === "call") {
          row.calls += 1;
        } else if (a.kind === "fold") {
          row.folds += 1;
        }
      }
      if (raisedPre) row.pfr += 1;
      if (acts.some((a) => a.kind === "call" || a.kind === "raise")) row.vpip += 1;
    }
  }

  rates(team) {
    const row = this.stats[team];
    if (!row || row.hands < 8) return null;
    const n = row.hands;
    return {
      vpip: row.vpip / n,
      pfr: row.pfr / n,
      af: row.raises / Math.max(1, row.calls),
      big: row.big_bets / n,
    };
  }

  style(team) {
    const r = this.rates(team);
    if (r == null) return "unknown";
    if (r.vpip > 0.75 && r.pfr < 0.25) return "station";
    if (r.vpip < 0.45 && r.pfr < 0.2) return "nit";
    if (r.af > 2.2 || r.big > 0.5) return "maniac";
    if (r.vpip > 0.6) return "loose";
    return "reg";
  }
}

// --------------------------------------------------------------- helpers

export function clamp_raise(total, game_state) {
  const [low, high] = game_state.raise_range;
  if (total < low) total = low;
  if (total > high) total = high;
  return Math.floor(total);
}

export function pot_fraction(game_state, fraction, extra = 0) {
  const call = game_state.call_amount;
  const target = game_state.bets[game_state.to_act] + call;
  const size = Math.floor((game_state.pot + extra) * fraction);
  return target + Math.max(size, game_state.big_blind);
}

export function made_category(hole, board) {
  if (board.length < 3) return -1;
  return eval7([...hole, ...board])[0];
}

export function board_texture(board) {
  const ranks = board.map(rank_of);
  const suits = board.map(suit_of);
  const paired = new Set(ranks).size < ranks.length;
  const suited = [...new Set(suits)].some(
    (s) => suits.filter((x) => x === s).length >= 2
  );
  const ordered = [...new Set(ranks)].sort((a, b) => a - b);
  const connected =
    ordered.length > 1 &&
    ordered.some((r, i) => i + 1 < ordered.length && ordered[i + 1] - r <= 2);
  return [paired, suited, connected];
}

export function in_position(game_state, me) {
  if (game_state.num_players !== 2) return true;
  const button = game_state.button;
  if (game_state.street === "preflop") return me !== button;
  return me === button;
}
