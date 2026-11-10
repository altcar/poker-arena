import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const base = "all-teams";
const reserved = new Set(["card_utils", "engine", "game_interface", "game_state", "player_action", "arena", "evals", "submit", "tests"]);
const stdlib = new Set("os sys json math random time itertools collections functools dataclasses typing pathlib pickle copy re string abc enum bisect heapq statistics warnings contextlib io csv datetime".split(" "));
const imports = new Map();
const files = [];

function walk(dir) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) { if (name !== ".git" && name !== "__pycache__") walk(full); continue; }
    if (!name.endsWith(".py")) continue;
    files.push(full);
    const text = readFileSync(full, "utf8");
    for (const m of text.matchAll(/^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import|import\s+([A-Za-z_][\w.]*))/gm)) {
      const mod = (m[1] || m[2] || "").split(".")[0];
      if (!mod || mod.startsWith("_")) continue;
      imports.set(mod, (imports.get(mod) || 0) + 1);
    }
  }
}
walk(base);
const third = [...imports.entries()].filter(([m]) => !stdlib.has(m) && !reserved.has(m)).sort((a, b) => b[1] - a[1]);
console.log("files:", files.length);
console.log("third-party / local imports:");
for (const [m, n] of third) console.log(`  ${m}: ${n}`);
console.log("\nnon-py data files:");
for (const name of readdirSync(base)) {
  const dir = join(base, name);
  try { if (!statSync(dir).isDirectory()) continue; } catch { continue; }
  for (const f of readdirSync(dir)) {
    if (/\.(npy|npz|json|txt|csv|pkl|bin|pt|h5)$/i.test(f)) console.log(`  ${name}/${f}`);
  }
}
