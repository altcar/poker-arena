// Find each competition team's public GitHub repository by searching for
// GreatUniHack / GUH poker repos created on the event day, then matching the
// team name against the repository's name and description.
//   node scripts/find_team_repos.mjs

import { writeFileSync, readFileSync, existsSync } from "node:fs";
import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { promisify } from "util";

const execFileAsync = promisify(execFile);
const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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

async function search(query, page) {
  const { stdout } = await execFileAsync(
    "gh",
    [
      "api",
      `search/repositories?q=${encodeURIComponent(query)}&per_page=100&page=${page}`,
    ],
    { maxBuffer: 16 * 1024 * 1024 },
  );
  return JSON.parse(stdout).items || [];
}

async function main() {
  const teams = parseCsv(readFileSync(join(root, "data", "all-teams.csv"), "utf8"))
    .map((r) => r.team);
  console.log(`teams: ${teams.length}`);

  const queries = [
    "GreatUniHack",
    "greatunihack",
    "GUH",
    "GUH26",
    "poker bot GreatUniHack",
    "holdem GreatUniHack",
  ];
  const repos = new Map();
  for (const query of queries) {
    for (let page = 1; page <= 3; page += 1) {
      try {
        const items = await search(`${query} created:2026-11-07..2026-11-08`, page);
        for (const item of items) {
          repos.set(item.full_name, {
            fullName: item.full_name,
            url: item.html_url,
            description: item.description || "",
            createdAt: item.created_at,
          });
        }
        console.log(`  ${query} page ${page}: ${items.length}`);
        if (items.length < 100) break;
      } catch (error) {
        console.warn(`  ${query} page ${page}: ${error.message}`);
      }
      await sleep(7000);
    }
  }
  console.log(`unique repos: ${repos.size}`);
  writeFileSync(
    join(root, "data", "live_repos.json"),
    JSON.stringify([...repos.values()], null, 2),
  );

  const matches = {};
  for (const team of teams) {
    const needle = normalise(team);
    let best = null;
    for (const repo of repos.values()) {
      const haystack = normalise(`${repo.fullName} ${repo.description}`);
      const nameHaystack = normalise(repo.fullName);
      if (!haystack.includes(needle) && !nameHaystack.includes(needle)) continue;
      const looksPoker = /poker|holdem|bot/.test(`${repo.fullName} ${repo.description}`.toLowerCase());
      // Prefer a poker-looking repo whose name contains the team name.
      const score = (looksPoker ? 100 : 0) + (nameHaystack.includes(needle) ? 50 : 0);
      if (!best || score > best.score) best = { ...repo, score };
    }
    if (best) matches[team] = best;
  }
  writeFileSync(join(root, "data", "team_repos.json"), JSON.stringify(matches, null, 2));
  console.log(`matched: ${Object.keys(matches).length}/${teams.length}`);
  for (const team of teams) {
    const m = matches[team];
    console.log(`  ${team} -> ${m ? m.fullName : "(none)"}`);
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
