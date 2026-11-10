// Clone each competition team's repository, find the files its pickle needs,
// and copy only those into all-teams/<team>/.
//   node scripts/download_team_files.mjs
//
// The competition's submission form (which held the repository links) is
// private, so the repositories are found with GitHub's search: a team-name
// search limited to repos created on the event day, plus the CSV's guess.
// Teams whose repository cannot be found are reported, not invented.

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

function parseCsv(text) {
  const lines = text.split(/\r?\n/);
  const header = splitCsvLine(lines[0]);
  return lines.slice(1).filter(Boolean).map((line) => {
    const cells = splitCsvLine(line);
    const row = {};
    header.forEach((h, i) => (row[h] = cells[i] || ""));
    return row;
  });
}

function splitCsvLine(line) {
  const out = [];
  let cur = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i += 1) {
    const c = line[i];
    if (c === '"') {
      if (inQuotes && line[i + 1] === '"') {
        cur += '"';
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
    } else if (c === "," && !inQuotes) {
      out.push(cur);
      cur = "";
    } else {
      cur += c;
    }
  }
  out.push(cur);
  return out;
}

function normalise(text) {
  return String(text || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function score(repo, team) {
  const hay = normalise(`${repo.fullName} ${repo.description}`);
  const nameHay = normalise(repo.fullName);
  const needle = normalise(team);
  let value = 0;
  const created = repo.createdAt || "";
  if (created >= "2026-11-07" && created < "2026-11-09") value += 100;
  if (/poker|holdem|bot/.test(hay)) value += 50;
  if (nameHay.includes(needle)) value += 30;
  else if (hay.includes(needle)) value += 10;
  return value;
}

async function searchTeam(team) {
  try {
    const { stdout } = await execFileAsync(
      "gh",
      [
        "search", "repos", team,
        "--created", ">=2026-11-06",
        "--limit", "10",
        "--json", "fullName,url,description,createdAt",
      ],
      { maxBuffer: 4 * 1024 * 1024 },
    );
    return JSON.parse(stdout).map((r) => ({ ...r, createdAt: r.createdAt || "" }));
  } catch (error) {
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
  const teams = parseCsv(readFileSync(join(root, "data", "all-teams.csv"), "utf8"));
  const csvRepos = Object.fromEntries(teams.map((r) => [r.team, r.github_repo]));
  let known = {};
  try {
    known = JSON.parse(readFileSync(join(root, "data", "team_repos.json"), "utf8"));
  } catch {
    // no pre-matched repos
  }

  const report = [];
  for (let i = 0; i < teams.length; i += 1) {
    const team = teams[i].team;
    if (existsSync(join(root, "all-teams", team))) {
      report.push({ team, status: "mine" });
      continue;
    }
    const candidates = [];
    if (known[team]?.url) candidates.push({ ...known[team], createdAt: known[team].createdAt || "" });
    if (csvRepos[team] && csvRepos[team].startsWith("https://github.com/")) {
      const parts = csvRepos[team].replace(/\.git$/, "").split("/");
      const fullName = `${parts[3]}/${parts[4]}`;
      candidates.push({
        fullName,
        url: csvRepos[team],
        description: teams[i].description || "",
        createdAt: "",
      });
    }
    const searched = await searchTeam(team);
    candidates.push(...searched);
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
      report.push({ team, status: "not found", best: best?.fullName || null, score: bestScore });
      console.log(`  [${i + 1}/${teams.length}] ${team}: not found`);
      await sleep(2100);
      continue;
    }

    const dest = join(root, ".tmp", "repos", team);
    try {
      await clone(best.url, dest);
    } catch (error) {
      report.push({ team, status: "clone failed", repo: best.fullName, error: String(error.message || error).slice(0, 200) });
      console.log(`  [${i + 1}/${teams.length}] ${team}: clone failed (${best.fullName})`);
      await sleep(2100);
      continue;
    }
    const needed = await requiredFiles(dest);
    if (needed.error) {
      report.push({ team, status: "no pickle", repo: best.fullName, error: needed.error });
      console.log(`  [${i + 1}/${teams.length}] ${team}: ${needed.error} (${best.fullName})`);
      await sleep(2100);
      continue;
    }
    const out = join(root, "all-teams", team);
    mkdirSync(out, { recursive: true });
    const files = [needed.pkl, ...needed.sources];
    for (const file of files) {
      copyFileSync(join(dest, file), join(out, file));
    }
    report.push({ team, status: "ok", repo: best.fullName, files });
    console.log(`  [${i + 1}/${teams.length}] ${team}: ${best.fullName} -> ${files.join(", ")}`);
    await sleep(2100);
  }

  writeFileSync(join(root, "data", "all-teams-report.json"), JSON.stringify(report, null, 2));
  const ok = report.filter((r) => r.status === "ok").length;
  const mine = report.filter((r) => r.status === "mine").length;
  console.log(`done: ${ok} downloaded, ${mine} mine, ${report.length - ok - mine} missing`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
