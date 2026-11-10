// Finalise data/team_files.csv from the downloaded folders and the verified
// repository map. Run after scripts/sync_team_files.mjs.
//   node scripts/finalize_team_files.mjs

import { readFileSync, writeFileSync, existsSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const teams = JSON.parse(readFileSync(join(root, "data", "live_teams.json"), "utf8"));

let resolved = {};
for (const name of ["team_repos_resolved.json", "team_repos_extra.json"]) {
  try {
    Object.assign(resolved, JSON.parse(readFileSync(join(root, "data", name), "utf8")));
  } catch {
    // optional
  }
}

// Existing rows carry the repository for teams that were already downloaded.
let previous = {};
try {
  const lines = readFileSync(join(root, "data", "team_files.csv"), "utf8").split(/\r?\n/).slice(1).filter(Boolean);
  for (const line of lines) {
    const cells = line.match(/"(?:[^"]|"")*"/g)?.map((cell) => cell.slice(1, -1).replace(/""/g, '"')) || [];
    if (cells[0]) previous[cells[0]] = { repo: cells[2] || "", status: cells[1] || "" };
  }
} catch {
  // no previous CSV
}

const rows = teams.map((team) => {
  const folder = join(root, "all-teams", team);
  let files = [];
  if (existsSync(folder)) {
    files = readdirSync(folder, { withFileTypes: true })
      .filter((entry) => entry.isFile())
      .map((entry) => entry.name)
      .sort();
  }
  const pkls = files.filter((name) => name.toLowerCase().endsWith(".pkl"));
  const model = files.includes("model.py") ? "model.py" : "";
  const pkl = pkls.find((name) => name.toLowerCase().replace(/_/g, "-").startsWith(team.toLowerCase().replace(/_/g, "-"))) || pkls[0] || "";
  const repo = resolved[team] || previous[team]?.repo || "";
  const ok = Boolean(model || pkl);
  return {
    team,
    status: ok ? "ok" : "missing",
    repo,
    model_py: model,
    pkl,
    pkls: pkls.join(" "),
    files: files.join(" "),
  };
});

const esc = (value) => `"${String(value ?? "").replace(/"/g, '""')}"`;
const header = ["team", "status", "repo", "model_py", "pkl", "pkls", "files"];
writeFileSync(
  join(root, "data", "team_files.csv"),
  [header.map(esc).join(","), ...rows.map((row) => header.map((key) => esc(row[key])).join(","))].join("\n"),
  "utf8",
);

const ok = rows.filter((row) => row.status === "ok").length;
const withRepo = rows.filter((row) => row.repo).length;
console.log(`wrote data/team_files.csv: ${ok}/${rows.length} with model files, ${withRepo} with a repo`);
