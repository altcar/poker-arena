// Test the browser Python runtime in Node (real Pyodide + shim + team files).
//   node scripts/test_pyodide.mjs

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { loadPyodide } from "pyodide";

const root = process.cwd();
const py = await loadPyodide();
await py.loadPackage("numpy");

py.FS.mkdirTree("/py");
py.FS.writeFile("/py/shim.py", readFileSync(join(root, "public", "py", "shim.py"), "utf8"));
py.runPython(`
import sys
sys.path.insert(0, "/py")
import shim
sys.modules["card_utils"] = shim
sys.modules["player_action"] = shim
sys.modules["game_state"] = shim
from shim import load_team, seed, run, end_game
`);

const manifest = JSON.parse(readFileSync(join(root, "public", "team-files", "manifest.json"), "utf8"));
const teams = process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(manifest);

for (const team of teams) {
  const entry = manifest[team];
  if (!entry) { console.log(`${team}: NOT IN MANIFEST`); continue; }
  const folder = `/teams/${team}`;
  py.FS.mkdirTree(folder);
  for (const file of entry.files) {
    const path = join(root, "public", "team-files", team, file);
    if (statSync(path).isFile()) py.FS.writeFile(`${folder}/${file}`, new Uint8Array(readFileSync(path)));
  }
  const loaded = JSON.parse(py.globals.get("load_team")(team, folder, entry.pkl || ""));
  if (!loaded.ok) { console.log(`${team}: LOAD FAIL ${loaded.error}`); continue; }
  try {
    py.globals.get("seed")(team, JSON.stringify(["AS", "KD"]), 0);
    const state = {
      button: 0, small_blind: 10, big_blind: 20,
      stacks: [490, 480], bets: [10, 20],
      folded: [false, false], acted: [false, false],
      board: [], street: "preflop", pot: 30, last_raise: 20,
      to_act: 0, teams: ["hero", team],
    };
    const action = JSON.parse(py.globals.get("run")(team, JSON.stringify(state), "[]"));
    console.log(`${team}: OK (${loaded.source}) -> ${JSON.stringify(action)}`);
  } catch (error) {
    console.log(`${team}: RUN FAIL ${error}`);
  }
}
