// POST the tournament results to the Worker's /api/matches endpoint.
//   node scripts/post_tournament.mjs [base]

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const base = process.env.BASE || process.argv[2] || "https://poker-arena.hheylau.workers.dev";

const data = JSON.parse(readFileSync(join(root, "data", "tournament_results.json"), "utf8"));
const matches = data.matches || [];
console.log(`posting ${matches.length} matches to ${base}`);

let ok = 0;
let failed = 0;
for (const match of matches) {
  try {
    const response = await fetch(`${base}/api/matches`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        kind: match.kind,
        a: match.a,
        b: match.b,
        deals: match.deals,
        hands: match.hands,
        mean_bb: match.mean_bb,
        ci95_bb: match.ci95_bb,
        verdict: match.verdict,
        faults_a: match.faults_a,
        faults_b: match.faults_b,
      }),
    });
    if (response.ok) ok += 1;
    else {
      failed += 1;
      console.warn(`  ${match.a} vs ${match.b}: HTTP ${response.status}`);
    }
  } catch (error) {
    failed += 1;
    console.warn(`  ${match.a} vs ${match.b}: ${error.message}`);
  }
}
console.log(`done: ${ok} posted, ${failed} failed`);
