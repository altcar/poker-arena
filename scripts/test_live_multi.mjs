// Pick three competition models (and none of the JS bots), start a table, and
// wait for the Python-backed hand. Tests switching team modules in one browser.
//   node scripts/test_live_multi.mjs

import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import { rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = 9225;
const BASE = process.env.BASE || "https://poker-arena.hheylau.workers.dev";
const TEAMS = ["relief-grid", "neko_nano", "bismillahinsurance"];
const profile = join(tmpdir(), `poker-multi-${Date.now()}`);

const edge = spawn(EDGE, [
  "--headless=new", "--disable-gpu", "--no-sandbox",
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
  `${BASE}/#/teams`,
], { stdio: "ignore" });

async function getJson(path) {
  const response = await fetch(`http://127.0.0.1:${PORT}${path}`);
  return response.json();
}

let target = null;
for (let i = 0; i < 60; i += 1) {
  try {
    const list = await getJson("/json");
    target = list.find((item) => item.type === "page" && item.url.includes("poker-arena"));
    if (target) break;
  } catch { /* not ready */ }
  await sleep(500);
}
if (!target) {
  console.error("could not find the page");
  edge.kill();
  process.exit(1);
}

const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  ws.addEventListener("open", resolve, { once: true });
  ws.addEventListener("error", reject, { once: true });
});

let nextId = 1;
function evaluate(expression) {
  const id = nextId++;
  return new Promise((resolve) => {
    const handler = (event) => {
      const message = JSON.parse(event.data);
      if (message.id !== id) return;
      ws.removeEventListener("message", handler);
      resolve(message.result?.result?.value);
    };
    ws.addEventListener("message", handler);
    ws.send(JSON.stringify({
      id, method: "Runtime.evaluate",
      params: { expression, returnByValue: true, awaitPromise: true },
    }));
  });
}

async function waitFor(expression, contains, seconds = 60) {
  for (let i = 0; i < seconds; i += 1) {
    const value = await evaluate(expression);
    if (typeof value === "string" && value.includes(contains)) return value;
    await sleep(1000);
  }
  return await evaluate(expression);
}

console.log("1. waiting for Teams…");
await waitFor("document.body.textContent", "All competition bots", 60);

console.log("2. unchecking the JS bots and checking the competition models…");
const checked = await evaluate(`(() => {
  const tables = document.querySelectorAll('table.tbl');
  if (tables.length < 2) return 'NO_TABLES';
  tables[0].querySelectorAll('input[type=checkbox]').forEach((box) => { if (box.checked) box.click(); });
  const wanted = ${JSON.stringify(TEAMS)};
  const result = [];
  for (const team of wanted) {
    const row = [...tables[1].querySelectorAll('tr')].find((r) => r.textContent.includes(team));
    const box = row && row.querySelector('input[type=checkbox]');
    if (!box) { result.push(team + ':NO'); continue; }
    if (!box.checked) box.click();
    result.push(team + ':' + (box.checked ? 'OK' : 'FAIL'));
  }
  return result.join(' ');
})()`);
console.log("   ", checked);

console.log("3. Continue…");
const floating = await evaluate(`(() => {
  const button = document.querySelector('.floating-btn');
  if (!button) return 'NO_BUTTON';
  const text = button.textContent;
  button.click();
  return text;
})()`);
console.log("   ", floating);
await sleep(1000);

console.log("4. Yes, start the game…");
const yes = await waitFor(`(() => {
  const button = [...document.querySelectorAll('button')].find((b) => b.textContent.includes('Yes, start the game'));
  if (button) { button.click(); return 'CLICKED'; }
  return 'WAIT';
})()`, "CLICKED", 30);
console.log("   ", yes);

console.log("5. waiting for the Python-backed table…");
const table = await waitFor("document.body.textContent", "Start a new game", 300);
console.log("--- page text ---");
console.log((table || "").replace(/\s+/g, " ").slice(0, 1600));

ws.close();
edge.kill();
await sleep(1500);
try { rmSync(profile, { recursive: true, force: true }); } catch { /* Edge may still hold it */ }
