// Fourth pass: re-check the teams whose repository had no pickle. Some teams
// submitted a single .py file; others had the wrong repository matched.
//   node scripts/finish_no_pickle.mjs

import { writeFileSync, readFileSync, mkdirSync, existsSync, rmSync, copyFileSync } from "node:fs";
import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { promisify } from "util";

const execFileAsync = promisify(execFile);
const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const PYTHON = join(root, ".venv", "Scripts", "python.exe");
const REQUIRED = join(here, "required_files.py");

// Repositories found by hand for teams the search matched incorrectly.
const KNOWN = {
  "manchester_fried_chicken": "https://github.com/golec07/GUH_MFC_Poker_Bot",
};

async function clone(url, dest) {
  rmSync(dest, { recursive: true, force: true });
  await execFileAsync(
    "git",
    ["clone", "--depth", "1", "--quiet", "--single-branch", url, dest],
    { timeout: 120000, env: { ...process.env, GIT_TERMINAL_PROMPT: "0" } },
  );
}

async function requiredFiles(folder) {
  try {
    const { stdout } = await execFileAsync(PYTHON, [REQUIRED, folder], { timeout: 60000 });
    return JSON.parse(stdout);
  } catch (error) {
    return { error: String(error.message || error) };
  }
}

async function main() {
  const reportPath = join(root, "data", "all-teams-report.json");
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  const todo = report.filter((r) => r.status === "no pickle" || r.status === "not found");
  console.log(`fourth pass: ${todo.length} teams`);

  const updated = [...report];
  for (let i = 0; i < todo.length; i += 1) {
    const team = todo[i].team;
    const url = KNOWN[team] || (todo[i].repo ? `https://github.com/${todo[i].repo}` : null);
    if (!url) {
      console.log(`  [${i + 1}/${todo.length}] ${team}: no candidate`);
      continue;
    }
    const dest = join(root, ".tmp", "repos4", team);
    try {
      await clone(url, dest);
    } catch (error) {
      console.log(`  [${i + 1}/${todo.length}] ${team}: clone failed (${url})`);
      continue;
    }
    const needed = await requiredFiles(dest);
    if (needed.error) {
      console.log(`  [${i + 1}/${todo.length}] ${team}: ${needed.error} (${url})`);
      continue;
    }
    const out = join(root, "all-teams", team);
    mkdirSync(out, { recursive: true });
    const files = needed.pkl ? [needed.pkl, ...needed.sources] : needed.sources;
    for (const file of files) {
      copyFileSync(join(dest, file), join(out, file));
    }
    const index = updated.findIndex((r) => r.team === team);
    updated[index] = { team, status: "ok", repo: url, files };
    console.log(`  [${i + 1}/${todo.length}] ${team}: ${url} -> ${files.join(", ")}`);
  }

  writeFileSync(reportPath, JSON.stringify(updated, null, 2));
  const ok = updated.filter((r) => r.status === "ok").length;
  const mine = updated.filter((r) => r.status === "mine").length;
  console.log(`done: ${ok} downloaded, ${mine} mine, ${updated.length - ok - mine} missing`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
