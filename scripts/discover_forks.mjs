import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { writeFileSync } from "node:fs";
const run = promisify(execFile);

async function gh(args, opts = {}) {
  const { stdout } = await run("gh", args, { maxBuffer: 64 * 1024 * 1024, ...opts });
  return stdout;
}

const forks = JSON.parse(await gh([
  "api", "repos/Manchester-AI-Society/poker_bot/forks?per_page=100",
]));
console.log(`forks: ${forks.length}`);

const rows = [];
for (const fork of forks) {
  const full = fork.full_name;
  const branch = fork.default_branch || "main";
  let tree = [];
  try {
    const data = JSON.parse(await gh([
      "api", `repos/${full}/git/trees/${branch}?recursive=1`,
    ]));
    tree = data.tree || [];
  } catch (e) {
    rows.push({ full, branch, error: String(e.message).slice(0, 120) });
    continue;
  }
  const pkl = tree.filter((t) => t.type === "blob" && t.path.endsWith(".pkl")).map((t) => t.path);
  const models = tree.filter((t) => t.type === "blob" && (t.path === "model.py" || t.path.endsWith("/model.py"))).map((t) => t.path);
  const readme = tree.filter((t) => t.type === "blob" && /^readme/i.test(t.path)).map((t) => t.path);
  rows.push({
    full,
    branch,
    createdAt: fork.created_at,
    pushedAt: fork.pushed_at,
    description: fork.description || "",
    pkl,
    models,
    readme,
  });
  console.log(`${full}  pkl=${JSON.stringify(pkl)}  model=${JSON.stringify(models)}`);
}
writeFileSync("data/fork_probe.json", JSON.stringify(rows, null, 2));
