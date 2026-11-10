// Play one fixed 6-player hand with the A-E bots and dump the result.
// Mirrors scripts/python_play.py so the two can be diffed.
//   node scripts/js_play.mjs

import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { fullDeck } from "../src/card_utils.js";
import { playHand } from "../src/engine.js";
import { createBot } from "../src/bots.js";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");

const keys = ["conservative", "workhorse", "adaptive", "mixed", "aggressive"];
const models = keys.map((key) => createBot(key));
models.push(createBot("conservative")); // sixth seat: A again, like the Python script
const teams = ["A", "B", "C", "D", "E", "A2"];
const deck = fullDeck();

const result = playHand(models, deck, Array(6).fill(500), 0, 10, 20, teams);
const payload = {
  net: result.net,
  hands: result.hands.map((h) => h.map(String)),
  board: result.board.map(String),
  faults: result.faults,
  actions: result.actions.map((a) => a.toJSON()),
};
const out = join(root, "data", "js_play.json");
writeFileSync(out, JSON.stringify(payload, null, 2));
console.log(`wrote ${out}`);
