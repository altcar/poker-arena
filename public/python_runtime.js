// Browser-side Python runtime for the competition bots.
//
// The submitted models are unchanged Python. Pyodide runs them in the browser;
// this module loads the engine shims, fetches each selected team's files, and
// exposes a bot object with the same seed/run/end_game surface as the JS bots.

import { PlayerAction } from "./engine/player_action.js";
import { registerBotSpec } from "./engine/bots.js";

const PYODIDE_VERSION = "v0.28.3";
const PYODIDE_INDEX = `https://cdn.jsdelivr.net/pyodide/${PYODIDE_VERSION}/full/`;
const PYODIDE_SCRIPT = `${PYODIDE_INDEX}pyodide.js`;

let runtimePromise = null;
let runtime = null;
let manifestPromise = null;
const loadedTeams = new Set();

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.onload = resolve;
    script.onerror = () => reject(new Error(`could not load ${src}`));
    document.head.append(script);
  });
}

async function getManifest() {
  if (!manifestPromise) {
    manifestPromise = fetch("/team-files/manifest.json").then((response) => {
      if (!response.ok) throw new Error(`manifest: HTTP ${response.status}`);
      return response.json();
    });
  }
  return manifestPromise;
}

/**
 * Load Pyodide, numpy and the engine shims. Resolves to a runtime object with
 * seed/run/end_game functions. Call it once; later calls reuse the runtime.
 */
export function ensurePythonRuntime(onProgress = () => {}) {
  if (runtimePromise) return runtimePromise;
  runtimePromise = (async () => {
    onProgress("Loading the Python runtime (about 10 MB)…");
    await loadScript(PYODIDE_SCRIPT);
    // eslint-disable-next-line no-undef
    const pyodide = await loadPyodide({ indexURL: PYODIDE_INDEX });

    onProgress("Loading numpy…");
    await pyodide.loadPackage("numpy");

    onProgress("Loading the engine shims…");
    const shim = await fetch("/py/shim.py").then((response) => {
      if (!response.ok) throw new Error(`shim: HTTP ${response.status}`);
      return response.text();
    });
    pyodide.FS.mkdirTree("/py");
    pyodide.FS.writeFile("/py/shim.py", shim);
    pyodide.runPython(`
import sys
sys.path.insert(0, "/py")
import shim
sys.modules["card_utils"] = shim
sys.modules["player_action"] = shim
sys.modules["game_state"] = shim
from shim import load_team, seed, run, end_game
`);

    runtime = {
      pyodide,
      loadTeam: pyodide.globals.get("load_team"),
      seed: pyodide.globals.get("seed"),
      run: pyodide.globals.get("run"),
      endGame: pyodide.globals.get("end_game"),
    };
    return runtime;
  })();
  return runtimePromise;
}

export async function loadTeams(keys, onProgress = () => {}) {
  const rt = await ensurePythonRuntime(onProgress);
  const manifest = await getManifest();
  const unique = [...new Set(keys)].filter((key) => manifest[key]);
  for (let index = 0; index < unique.length; index += 1) {
    const team = unique[index];
    if (loadedTeams.has(team)) continue;
    const entry = manifest[team];
    onProgress(`Loading ${team} (${index + 1}/${unique.length})…`);
    const folder = `/teams/${team}`;
    rt.pyodide.FS.mkdirTree(folder);
    const contents = await Promise.all(entry.files.map(async (file) => {
      const response = await fetch(`/team-files/${encodeURIComponent(team)}/${encodeURIComponent(file)}`);
      if (!response.ok) throw new Error(`${team}/${file}: HTTP ${response.status}`);
      return [file, new Uint8Array(await response.arrayBuffer())];
    }));
    for (const [file, bytes] of contents) rt.pyodide.FS.writeFile(`${folder}/${file}`, bytes);
    const result = JSON.parse(rt.loadTeam(team, folder, entry.pkl || ""));
    if (!result.ok) throw new Error(`${team}: ${result.error}`);
    loadedTeams.add(team);
  }
  return rt;
}

function stateSnapshot(state) {
  return {
    button: state.button,
    small_blind: state.small_blind,
    big_blind: state.big_blind,
    stacks: [...state.stacks],
    bets: [...state.bets],
    folded: [...state.folded],
    acted: [...state.acted],
    board: state.board.map(String),
    street: state.street,
    pot: state.pot,
    last_raise: state.last_raise,
    to_act: state.to_act,
    teams: [...state.teams],
  };
}

function actionSnapshot(action) {
  return {
    player_id: action.player_id,
    kind: action.kind,
    amount: action.amount || 0,
    card: action.card ? String(action.card) : null,
  };
}

export class PythonBot {
  constructor(team) {
    this.team = team;
    this.key = team;
    this.hole = [];
    this.player_id = 0;
    this.opponents = { stats: {} };
    this._hand_actions = [];
    this._hand_seed = 0;
    this._opp_raised = 0;
    this._opp_three_bet = 0;
    this._our_raises = 0;
    this._pre_raises = 0;
    this._preflop_done = false;
  }

  seed(hole, playerId) {
    this.hole = hole.map(String);
    this.player_id = playerId;
    runtime.seed(this.team, JSON.stringify(this.hole), playerId);
  }

  run(actions, state) {
    const raw = runtime.run(
      this.team,
      JSON.stringify(stateSnapshot(state)),
      JSON.stringify(actions.map(actionSnapshot)),
    );
    const result = JSON.parse(raw);
    return new PlayerAction(result.player_id, result.kind, result.amount);
  }

  end_game(actions, state) {
    runtime.endGame(
      this.team,
      JSON.stringify(stateSnapshot(state)),
      JSON.stringify(actions.map(actionSnapshot)),
    );
  }
}

export function registerPythonBots(keys, names = {}) {
  for (const key of keys) {
    registerBotSpec({
      key,
      name: names[key] || key,
      create: () => new PythonBot(key),
    });
  }
}

export function hasPythonRuntime() {
  return runtime !== null;
}
