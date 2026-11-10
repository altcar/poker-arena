// Play a batch of fixed 6-player hands with the A-E bots and dump them.
// Mirrors scripts/python_batch.mjs so the two can be diffed.
//   node scripts/js_batch.mjs [hands]

import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { fullDeck } from "../src/card_utils.js";
import { playHand } from "../src/engine.js";
import { createBot } from "../src/bots.js";
import { PythonRandom } from "../src/pyrandom.js";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const HANDS = Number(process.argv[2]) || 20;
const SEED = 1;
const keys = ["conservative", "workhorse", "adaptive", "mixed", "aggressive"];

const hands = [];
for (let h = 0; h < HANDS; h += 1) {
  const deck = fullDeck();
  new PythonRandom((SEED * 1000003 + h) >>> 0).shuffle(deck);
  const models = keys.map((key) => createBot(key));
  models.push(createBot("conservative"));
  const teams = ["A", "B", "C", "D", "E", "A2"];
  const result = playHand(models, deck, Array(6).fill(500), h % 6, 10, 20, teams);
  hands.push({
    hand: h,
    button: h % 6,
    net: result.net,
    hands: result.hands.map((hand) => hand.map(String)),
    board: result.board.map(String),
    faults: result.faults,
    actions: result.actions.map((a) => a.toJSON()),
  });
}

const out = join(root, "data", "js_batch.json");
writeFileSync(out, JSON.stringify({ seed: SEED, hands }));
console.log(`wrote ${out} (${HANDS} hands)`);
