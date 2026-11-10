// Generate the list of teams whose model files we actually have.
//   node scripts/make_available.mjs

import { writeFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const folders = readdirSync(join(root, "all-teams"), { withFileTypes: true })
  .filter((d) => d.isDirectory())
  .map((d) => d.name);
const payload = { teams: folders };
writeFileSync(join(root, "data", "available_teams.json"), JSON.stringify(payload, null, 2));
writeFileSync(
  join(root, "src", "available_teams_data.js"),
  `export const availableTeams = ${JSON.stringify(payload)};\n`,
);
console.log(`wrote ${folders.length} available teams: ${folders.join(", ")}`);
