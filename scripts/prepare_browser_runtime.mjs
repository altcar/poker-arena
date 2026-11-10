// Copy each team's submission files into public/team-files/ so the browser
// Python runtime can fetch them, and write the manifest the runtime reads.
//
//   node scripts/prepare_browser_runtime.mjs

import {
  copyFileSync, existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const source = join(root, "all-teams");
const target = join(root, "public", "team-files");

// tvt.pkl is 33 MB and tvt's Model() works without it; the browser assets
// should stay well under the per-file limit.
const SKIP_PICKLES = new Set(["tvt.pkl"]);

rmSync(target, { recursive: true, force: true });
mkdirSync(target, { recursive: true });

const rows = readFileSync(join(root, "data", "team_files.csv"), "utf8")
  .split(/\r?\n/).slice(1).filter(Boolean)
  .map((line) => {
    const cells = line.match(/"(?:[^"]|"")*"/g)?.map((cell) => cell.slice(1, -1).replace(/""/g, '"')) || [];
    const header = ["team", "status", "repo", "model_py", "pkl", "pkls", "files"];
    return Object.fromEntries(header.map((key, index) => [key, cells[index] || ""]));
  });

const manifest = {};
let total = 0;
let count = 0;
for (const row of rows) {
  if (row.status !== "ok") continue;
  const folder = join(source, row.team);
  if (!existsSync(folder)) continue;
  const files = row.files.split(/\s+/).filter(Boolean);
  const outDir = join(target, row.team);
  mkdirSync(outDir, { recursive: true });
  const copied = [];
  for (const file of files) {
    if (SKIP_PICKLES.has(file)) continue;
    const from = join(folder, file);
    if (!existsSync(from) || !statSync(from).isFile()) continue;
    copyFileSync(from, join(outDir, file));
    copied.push(file);
    total += statSync(from).size;
    count += 1;
  }
  const pkl = SKIP_PICKLES.has(row.pkl) ? "" : row.pkl;
  manifest[row.team] = {
    files: copied,
    model: copied.includes("model.py") ? "model.py" : "",
    pkl: pkl && copied.includes(pkl) ? pkl : "",
  };
}

writeFileSync(join(target, "manifest.json"), JSON.stringify(manifest, null, 2), "utf8");
console.log(`wrote ${count} files for ${Object.keys(manifest).length} teams (${(total / 1e6).toFixed(1)} MB)`);
