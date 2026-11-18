// Copy the browser-safe part of the JS engine into public/engine/ so the SPA
// can run a local table (including the Python-backed competition bots).
//
//   node scripts/copy_browser_engine.mjs
//
// Extra browser-only engines (draw27.js, ofc.js) live in public/engine/ and are
// not touched.

import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const from = join(root, "src");
const to = join(root, "public", "engine");

const files = [
  "card_utils.js", "player_action.js", "game_state.js", "engine.js",
  "pyrandom.js", "botcore.js", "policy.js", "bots.js", "game.js",
];

mkdirSync(to, { recursive: true });
for (const file of files) copyFileSync(join(from, file), join(to, file));
console.log(`copied ${files.length} engine files to public/engine/`);
