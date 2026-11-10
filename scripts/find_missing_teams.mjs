// Look for the competition teams that did not fork the official repo.
//   node scripts/find_missing_teams.mjs
// Prints the best candidates and writes data/missing_candidates.json.

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFileSync, writeFileSync } from "node:fs";

const run = promisify(execFile);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const norm = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]/g, "");

async function gh(args) {
  try {
    const { stdout } = await run("gh", args, { maxBuffer: 32 * 1024 * 1024, timeout: 60000 });
    return stdout;
  } catch { return ""; }
}

const teams = JSON.parse(readFileSync("data/live_teams.json", "utf8"));
const csv = readFileSync("data/team_files.csv", "utf8").split(/\r?\n/).slice(1).filter(Boolean);
const present = new Set();
for (const line of csv) {
  const m = line.match(/^"([^"]*)","([^"]*)"/);
  if (m && !m[2].startsWith("missing")) present.add(m[1]);
}
const missing = teams.filter((t) => !present.has(t));
console.log(`missing (${missing.length}): ${missing.join(", ")}`);

const queriesFor = (team) => [
  team,
  `${team} poker`,
  `${team} bot`,
  `${team} GUH`,
  `${team} greatunihack`,
];

const out = {};
for (const team of missing) {
  const seen = new Map();
  for (const query of queriesFor(team)) {
    const raw = await gh([
      "search", "repos", query, "--limit", "30",
      "--json", "fullName,url,description,createdAt",
    ]);
    for (const item of JSON.parse(raw || "[]")) if (!seen.has(item.fullName)) seen.set(item.fullName, item);
    await sleep(2200);
  }
  const scored = [];
  for (const candidate of seen.values()) {
    const hay = norm(`${candidate.fullName} ${candidate.description || ""}`);
    const nameHay = norm(candidate.fullName);
    const needle = norm(team);
    let score = 0;
    if (nameHay.includes(needle)) score += 60;
    else if (hay.includes(needle)) score += 30;
    if (/poker|holdem|bot|guh|greatunihack/i.test(`${candidate.fullName} ${candidate.description || ""}`)) score += 25;
    if ((candidate.createdAt || "") >= "2026-11-06" && (candidate.createdAt || "") < "2026-11-09") score += 20;
    if (score < 20) continue;
    // Does it have a model.py or a pickle?
    const treeRaw = await gh(["api", `repos/${candidate.fullName}/git/trees/HEAD?recursive=1`]);
    const tree = JSON.parse(treeRaw || "{}").tree || [];
    const pkls = tree.filter((t) => t.type === "blob" && t.path.endsWith(".pkl")).map((t) => t.path);
    const models = tree.filter((t) => t.type === "blob" && t.path.endsWith("model.py")).map((t) => t.path);
    if (pkls.length) score += 50;
    if (models.length) score += 30;
    scored.push({ ...candidate, score, pkl: pkls, model: models });
    await sleep(700);
  }
  scored.sort((a, b) => b.score - a.score);
  out[team] = scored.slice(0, 5);
  console.log(`\n${team}:`);
  for (const c of out[team]) {
    console.log(`  ${String(c.score).padStart(3)} ${c.fullName}  pkl=${JSON.stringify(c.pkl)} model=${c.model.length}  ${(c.description || "").slice(0, 60)}`);
  }
  if (!out[team].length) console.log("  (no candidates)");
}

writeFileSync("data/missing_candidates.json", JSON.stringify(out, null, 2));
