// Engine verification: play many 6-player hands and check the invariants.
//   node scripts/verify.mjs

import { fullDeck } from "../src/card_utils.js";
import { playHand } from "../src/engine.js";
import { createBot } from "../src/bots.js";
import { PythonRandom } from "../src/pyrandom.js";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const keys = ["conservative", "workhorse", "adaptive", "mixed", "aggressive"];
const teams = ["A", "B", "C", "D", "E", "A2"];
const HANDS = 100;

let errors = 0;
const actionKinds = new Set(["fold", "call", "raise", "reveal_card", "win"]);

for (let i = 0; i < HANDS; i += 1) {
  const deck = fullDeck();
  new PythonRandom((i * 7919 + 1) >>> 0).shuffle(deck);
  const models = keys.map((key) => createBot(key));
  models.push(createBot("conservative"));
  const result = playHand(models, deck, Array(6).fill(500), i % 6, 10, 20, teams);

  const sum = result.net.reduce((a, b) => a + b, 0);
  if (sum !== 0) {
    console.error(`hand ${i}: chips not conserved (sum ${sum})`);
    errors += 1;
  }
  if (Object.keys(result.faults).length) {
    console.error(`hand ${i}: faults`, result.faults);
    errors += 1;
  }
  for (const action of result.actions) {
    if (!actionKinds.has(action.kind)) {
      console.error(`hand ${i}: bad action kind ${action.kind}`);
      errors += 1;
      break;
    }
  }
  if (result.board.length < 3 && result.actions.some((a) => a.kind === "reveal_card" && a.player_id === -1)) {
    console.error(`hand ${i}: no board`);
    errors += 1;
  }
}

// Compare against the Python dump when it is present.
const pythonFile = join(root, "data", "python_batch.json");
const jsFile = join(root, "data", "js_batch.json");
if (existsSync(pythonFile) && existsSync(jsFile)) {
  const python = JSON.parse(readFileSync(pythonFile, "utf8"));
  const js = JSON.parse(readFileSync(jsFile, "utf8"));
  let same = 0;
  let diff = 0;
  for (let i = 0; i < Math.min(python.hands.length, js.hands.length); i += 1) {
    if (JSON.stringify(python.hands[i]) === JSON.stringify(js.hands[i])) same += 1;
    else diff += 1;
  }
  console.log(`python comparison: ${same} identical, ${diff} different`);
  if (diff > 0) errors += 1;
}

if (errors) {
  console.error(`FAILED (${errors} problems)`);
  process.exit(1);
}
console.log(`OK: ${HANDS} hands, chips conserved, no faults, all actions legal`);
