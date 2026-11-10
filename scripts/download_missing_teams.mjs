// Second pass: for the teams the first pass could not download, look at the
// matched repository owner's other repositories and at broader searches.
//   node scripts/download_missing_teams.mjs

import { writeFileSync, readFileSync, mkdirSync, existsSync, rmSync, copyFileSync } from "node:fs";
import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { promisify } from "util";

const execFileAsync = promisify(execFile);
const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const PYTHON = join(root, ".venv", "Scripts", "python.exe");
const REQUIRED = join(here, "required_files.py");

function normalise(text) {
  return String(text || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function score(repo, team) {
  const hay = normalise(`${repo.full_name || repo.fullName} ${repo.description || ""}`);
  const nameHay = normalise(repo.full_name || repo.fullName);
  const needle = normalise(team);
  let value = 0;
  const created = repo.created_at || repo.createdAt || "";
  if (created >= "2026-11-07" && created < "2026-11-09") value += 100;
  if (/poker|holdem|bot/.test(hay)) value += 50;
  if (nameHay.includes(needle)) value += 30;
  else if (hay.includes(needle)) value += 10;
  return value;
}

async function ghApi(path) {
  const { stdout } = await execFileAsync("gh", ["api", path], { maxBuffer: 8 * 1024 * 1024 });
  return JSON.parse(stdout);
}

async function search(query) {
  try {
    const { stdout } = await execFileAsync(
      "gh",
      ["search", "repos", query, "--created", ">=2026-11-06", "--limit", "20",
        "--json", "fullName,url,description,createdAt"],
      { maxBuffer: 4 * 1024 * 1024 },
    );
    return JSON.parse(stdout).map((r) => ({ ...r, createdAt: r.createdAt || "" }));
  } catch {
    return [];
  }
}

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
  const todo = report.filter((r) => r.status !== "ok" && r.status !== "mine");
  console.log(`second pass: ${todo.length} teams`);

  const updated = [...report];
  for (let i = 0; i < todo.length; i += 1) {
    const row = todo[i];
    const team = row.team;
    const candidates = [];
    if (row.repo) {
      const owner = row.repo.split("/")[0];
      try {
        const repos = await ghApi(`users/${owner}/repos?per_page=100`);
        for (const repo of repos) {
          candidates.push({
            fullName: repo.full_name,
            url: repo.html_url,
            description: repo.description || "",
            createdAt: repo.created_at || "",
          });
        }
      } catch {
        // ignore
      }
    }
    for (const query of [`${team} poker`, `${team} bot`, `${team} holdem`, team]) {
      candidates.push(...(await search(query)));
      await sleep(1500);
    }
    let best = null;
    let bestScore = 0;
    for (const candidate of candidates) {
      const value = score(candidate, team);
      if (value > bestScore) {
        best = candidate;
        bestScore = value;
      }
    }
    if (!best || bestScore < 60) {
      console.log(`  [${i + 1}/${todo.length}] ${team}: still not found`);
      await sleep(1500);
      continue;
    }
    const dest = join(root, ".tmp", "repos2", team);
    try {
      await clone(best.url, dest);
    } catch (error) {
      console.log(`  [${i + 1}/${todo.length}] ${team}: clone failed (${best.fullName})`);
      await sleep(1500);
      continue;
    }
    const needed = await requiredFiles(dest);
    if (needed.error) {
      console.log(`  [${i + 1}/${todo.length}] ${team}: ${needed.error} (${best.fullName})`);
      await sleep(1500);
      continue;
    }
    const out = join(root, "all-teams", team);
    mkdirSync(out, { recursive: true });
    for (const file of [needed.pkl, ...needed.sources]) {
      copyFileSync(join(dest, file), join(out, file));
    }
    const index = updated.findIndex((r) => r.team === team);
    updated[index] = { team, status: "ok", repo: best.fullName, files: [needed.pkl, ...needed.sources] };
    console.log(`  [${i + 1}/${todo.length}] ${team}: ${best.fullName} -> ${needed.pkl}, ${needed.sources.join(", ")}`);
    await sleep(1500);
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
