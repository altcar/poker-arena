// Download every competition team's model.py and pickle from its GitHub fork
// of Manchester-AI-Society/poker_bot (the event's official repository).
//
//   node scripts/sync_team_files.mjs
//
// Teams that did not fork the repo are found with `gh search repos <team>`,
// but only accepted when the repository looks like a poker bot and actually
// contains a model.py or a .pkl.
//
// Writes:
//   all-teams/<team>/...          the pickles and the .py files they import
//   data/team_files.csv           one row per team: repo, pkl, model.py, status
//   data/team_repos_resolved.json  team -> verified repo URL
//   data/available_teams.json      teams whose files we have
//   src/available_teams_data.js

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import {
  existsSync, mkdirSync, readFileSync, readdirSync, rmSync, copyFileSync,
  statSync, writeFileSync,
} from "node:fs";
import { dirname, join, basename, extname, relative, isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";

const execFileAsync = promisify(execFile);
const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const teTeamsPath = join(root, "data", "live_teams.json");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const PYTHON = join(root, ".venv", "Scripts", "python.exe");
const REQUIRED = join(here, "required_files.py");
const FORK_PARENT = "Manchester-AI-Society/poker_bot";
const EVENT_START = "2026-11-06";
const EVENT_END = "2026-11-09";

const norm = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
const RESERVED = new Set([
  "card_utils", "engine", "game_interface", "game_state", "player_action",
  "arena", "evals", "submit", "tests",
]);

// pickle name -> team, when the pickle is not named exactly after the team
const PKL_ALIASES = {
  myteam: null, // resolved from the folder
  askchatgpt: "terpsichord",
  zak: "zak",
  skpoker: "sk_poker",
  bismillahinsurance: "bismillahinsurance",
};

function argValue(name, fallback = null) {
  const i = process.argv.indexOf(name);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

async function gh(args, opts = {}) {
  const { stdout } = await execFileAsync("gh", args, {
    maxBuffer: 64 * 1024 * 1024,
    timeout: 120000,
    ...opts,
  });
  return stdout;
}

function walkFiles(folder, predicate, out = [], base = folder) {
  for (const entry of readdirSync(folder)) {
    if (entry === ".git") continue;
    const full = join(folder, entry);
    let stat;
    try { stat = statSync(full); } catch { continue; }
    if (stat.isDirectory()) walkFiles(full, predicate, out, base);
    else if (predicate(full, entry)) out.push({ full, rel: relative(base, full) });
  }
  return out;
}

function parseJson(text) {
  try { return JSON.parse(text); } catch { return null; }
}

async function requiredFiles(folder) {
  try {
    const { stdout } = await execFileAsync(PYTHON, [REQUIRED, folder], {
      timeout: 60000, maxBuffer: 8 * 1024 * 1024,
    });
    return parseJson(stdout) || { error: "unparseable output" };
  } catch (error) {
    return { error: String(error.message || error).slice(0, 300) };
  }
}

async function clone(fullName, dest) {
  rmSync(dest, { recursive: true, force: true });
  mkdirSync(dirname(dest), { recursive: true });
  await execFileAsync(
    "git",
    ["clone", "--depth", "1", "--quiet", "--single-branch", `https://github.com/${fullName}.git`, dest],
    { timeout: 180000, env: { ...process.env, GIT_TERMINAL_PROMPT: "0" } },
  );
}

function choosePickle(files, team) {
  const wanted = norm(team);
  const alias = PKL_ALIASES[wanted];
  const pkls = files.map((f) => f.rel);
  // exact team name, with or without aliases
  for (const p of pkls) {
    const base = norm(basename(p, ".pkl"));
    if (base === wanted || (alias && base === norm(alias))) return p;
  }
  // a pickle next to a model.py
  for (const p of pkls) {
    if (files.some((f) => dirname(f.rel) === dirname(p) && basename(f.rel) === "model.py")) return p;
  }
  return pkls[0] || null;
}

function flattenCopy(sourceFolder, relPaths, dest) {
  mkdirSync(dest, { recursive: true });
  const copied = [];
  for (const rel of relPaths) {
    const src = isAbsolute(rel) ? rel : join(sourceFolder, rel);
    if (!existsSync(src)) continue;
    const target = join(dest, basename(src));
    copyFileSync(src, target);
    copied.push(basename(src));
  }
  return copied;
}

function copyAllLocalSources(sourceFolder, dest, pklRel) {
  // Fallback: copy every non-reserved .py plus the pickle, flattened.
  const py = walkFiles(sourceFolder, (_full, name) => name.endsWith(".py"))
    .map((f) => f.rel)
    .filter((rel) => !RESERVED.has(basename(rel, ".py")));
  const files = [...py];
  if (pklRel) files.push(pklRel);
  return flattenCopy(sourceFolder, files, dest);
}

async function downloadFork(team, fullName, report) {
  const dest = join(root, ".tmp", "forks", team);
  const out = join(root, "all-teams", team);
  const temp = join(root, ".tmp", "all-teams-new", team);
  rmSync(temp, { recursive: true, force: true });
  try {
    await clone(fullName, dest);
  } catch (error) {
    report.push({ team, repo: fullName, status: "clone failed", error: String(error.message || error).slice(0, 200) });
    console.log(`  ${team}: clone failed ${fullName}`);
    return false;
  }

  const allPkl = walkFiles(dest, (_f, name) => name.toLowerCase().endsWith(".pkl"));
  const pklRel = choosePickle(allPkl, team);
  if (!pklRel) {
    // No pickle in the fork: use model.py only.
    const modelRels = walkFiles(dest, (_f, name) => name === "model.py").map((f) => f.rel);
    if (!modelRels.length) {
      report.push({ team, repo: fullName, status: "no model.py or pkl" });
      console.log(`  ${team}: no model.py or pkl in ${fullName}`);
      return false;
    }
    const modelRel = modelRels.sort((a, b) => a.length - b.length)[0];
    const folder = dirname(join(dest, modelRel));
    let needed = await requiredFiles(folder);
    let files;
    if (needed.error) files = copyAllLocalSources(folder, temp);
    else files = flattenCopy(folder, [...(needed.sources || [])], temp);
    if (!files.includes("model.py")) {
      copyFileSync(join(dest, modelRel), join(temp, "model.py"));
      files.push("model.py");
    }
    report.push({ team, repo: fullName, status: "ok (no pkl)", pkl: "", model: "model.py", files });
    console.log(`  ${team}: ${fullName} -> model.py only`);
    return true;
  }

  const pklFolder = dirname(join(dest, pklRel));
  let pklName = basename(pklRel);
  let needed = await requiredFiles(pklFolder);
  let sourceFolder = pklFolder;
  if (needed.error) {
    // try the repository root (the pkl may import modules next to the root model.py)
    needed = await requiredFiles(dest);
    sourceFolder = dest;
  }
  if (!needed.error && needed.pkl) pklName = basename(needed.pkl);
  let files;
  if (needed.error) {
    files = copyAllLocalSources(pklFolder, temp, pklName);
  } else {
    const relPaths = [needed.pkl || pklName, ...(needed.sources || [])];
    files = flattenCopy(sourceFolder, relPaths, temp);
  }
  if (!existsSync(join(temp, pklName))) copyFileSync(join(dest, pklRel), join(temp, pklName));
  if (!files.includes(pklName)) files.push(pklName);

  // If the pkl lives under a subfolder but model.py is at the root, copy the
  // root model.py too (the pickle may name it).
  const rootModel = join(dest, "model.py");
  if (!files.includes("model.py") && existsSync(rootModel)) {
    copyFileSync(rootModel, join(temp, "model.py"));
    files.push("model.py");
  }

  report.push({
    team, repo: fullName, status: "ok", pkl: pklName,
    model: files.includes("model.py") ? "model.py" : "", files,
  });
  console.log(`  ${team}: ${fullName} -> ${files.join(", ")}`);
  return true;
}

async function searchRepo(team) {
  const queries = [
    `${team} poker`,
    `${team} pokerbot`,
    `${team} GUH`,
    `${team} greatunihack`,
  ];
  const seen = new Map();
  for (const query of queries) {
    let items = [];
    try {
      const raw = await gh([
        "search", "repos", query,
        "--created", `${EVENT_START}..${EVENT_END}`,
        "--limit", "10",
        "--json", "fullName,url,description,createdAt",
      ]);
      items = parseJson(raw) || [];
    } catch {
      items = [];
    }
    for (const item of items) if (!seen.has(item.fullName)) seen.set(item.fullName, item);
    if (seen.size) break;
    await sleep(2100);
  }
  return [...seen.values()];
}

async function treeOf(fullName, branch = "") {
  try {
    const slug = branch ? `${fullName}/git/trees/${branch}?recursive=1` : `${fullName}/git/trees/HEAD?recursive=1`;
    const raw = await gh(["api", `repos/${slug}`]);
    return (parseJson(raw) || {}).tree || [];
  } catch {
    return [];
  }
}

async function findSearchRepo(team) {
  const candidates = await searchRepo(team);
  const needle = norm(team);
  for (const candidate of candidates) {
    const hay = norm(`${candidate.fullName} ${candidate.description || ""}`);
    const looksPoker = /poker|holdem|bot|guh|greatunihack/i.test(`${candidate.fullName} ${candidate.description || ""}`);
    if (!(hay.includes(needle) && looksPoker)) continue;
    const tree = await treeOf(candidate.fullName);
    const hasModel = tree.some((t) => t.type === "blob" && t.path.endsWith("model.py"));
    const hasPkl = tree.some((t) => t.type === "blob" && t.path.endsWith(".pkl"));
    if (hasModel || hasPkl) return candidate;
    await sleep(700);
  }
  return null;
}

async function main() {
  const teams = JSON.parse(readFileSync(teTeamsPath, "utf8"));
  const only = argValue("--team");
  const onlySet = only ? new Set(only.split(",").map((s) => s.trim()).filter(Boolean)) : null;
  const targetTeams = onlySet ? teams.filter((t) => onlySet.has(t)) : teams;

  // 1. The forks of the official repository.
  let forks = [];
  try {
    forks = parseJson(await gh([
      "api", `repos/${FORK_PARENT}/forks?per_page=100&sort=newest`,
    ])) || [];
  } catch (error) {
    console.warn(`could not list forks: ${error.message}`);
  }
  const forkByTeam = new Map();
  let cachedForks = [];
  try {
    cachedForks = JSON.parse(readFileSync(join(root, "data", "fork_probe.json"), "utf8"));
  } catch { /* no cache */ }
  const cachedByName = new Map(cachedForks.map((f) => [f.full, f]));

  for (const fork of forks) {
    const [owner, repo] = fork.full_name.split("/");
    let team = null;
    const cached = cachedByName.get(fork.full_name);
    let pkls = cached?.pkl;
    if (!pkls) {
      const tree = await treeOf(fork.full_name);
      pkls = tree
        .filter((entry) => entry.type === "blob" && entry.path.endsWith(".pkl"))
        .map((entry) => entry.path);
      cachedByName.set(fork.full_name, { full: fork.full_name, pkl: pkls });
    }
    for (const p of pkls) {
      const key = norm(basename(p, ".pkl"));
      const alias = PKL_ALIASES[key];
      const resolved = alias || (teams.find((t) => norm(t) === key) || null);
      if (resolved) { team = resolved; break; }
    }
    if (!team) {
      team = teams.find((t) => norm(t).length >= 4 && norm(repo).includes(norm(t))) || null;
    }
    if (!team) {
      team = teams.find((t) => norm(t).length >= 4 && norm(owner).includes(norm(t))) || null;
    }
    if (team && !forkByTeam.has(team)) forkByTeam.set(team, fork.full_name);
  }

  const report = [];
  const resolved = {};
  const available = [];

  for (const team of targetTeams) {
    const fork = forkByTeam.get(team);
    if (fork) {
      const ok = await downloadFork(team, fork, report);
      if (ok) {
        resolved[team] = `https://github.com/${fork}`;
        available.push(team);
      }
      continue;
    }
    // 2. Fallback: search by team name.
    let candidate = null;
    try {
      candidate = await findSearchRepo(team);
    } catch (error) {
      console.warn(`  ${team}: search failed ${error.message}`);
    }
    if (candidate) {
      const ok = await downloadFork(team, candidate.fullName, report);
      if (ok) {
        resolved[team] = candidate.url || `https://github.com/${candidate.fullName}`;
        available.push(team);
        continue;
      }
    }
    // 3. Keep an existing folder if we already have one.
    if (existsSync(join(root, "all-teams", team))) {
      report.push({ team, repo: "", status: "existing", files: readdirSync(join(root, "all-teams", team)) });
      available.push(team);
      console.log(`  ${team}: kept existing files`);
    } else {
      report.push({ team, repo: "", status: "missing" });
      console.log(`  ${team}: MISSING`);
    }
    await sleep(2100);
  }

  // Adopt the freshly built folders.
  for (const team of available) {
    const temp = join(root, ".tmp", "all-teams-new", team);
    const out = join(root, "all-teams", team);
    if (existsSync(temp)) {
      rmSync(out, { recursive: true, force: true });
      mkdirSync(out, { recursive: true });
      for (const file of readdirSync(temp)) copyFileSync(join(temp, file), join(out, file));
    }
  }

  if (process.argv.includes("--no-write")) {
    console.log("\n--no-write: skipping CSV/available writes");
    return;
  }

  // CSV.
  const header = ["team", "status", "repo", "model_py", "pkl", "files"];
  const lines = report.map((r) => [
    r.team, r.status, r.repo || "", r.model || "", r.pkl || "", (r.files || []).join(" "),
  ]);
  const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  writeFileSync(join(root, "data", "team_files.csv"),
    [header.map(esc).join(","), ...lines.map((l) => l.map(esc).join(","))].join("\n"), "utf8");
  writeFileSync(join(root, "data", "team_repos_resolved.json"), JSON.stringify(resolved, null, 2));
  writeFileSync(join(root, "data", "available_teams.json"), JSON.stringify({ teams: available.sort() }, null, 2));
  writeFileSync(join(root, "src", "available_teams_data.js"),
    `export const availableTeams = ${JSON.stringify({ teams: available.sort() })};\n`);

  const ok = report.filter((r) => r.status.startsWith("ok")).length;
  const existing = report.filter((r) => r.status === "existing").length;
  console.log(`\ndone: ${ok} downloaded, ${existing} existing, ${report.length - ok - existing} missing (of ${report.length})`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
