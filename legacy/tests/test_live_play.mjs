// Drive the deployed site: pick relief-grid on Teams, Continue, start the game,
// and wait for the Python-backed table to appear.
//   node scripts/test_live_play.mjs

import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import { rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = 9224;
const BASE = process.env.BASE || "https://poker-arena.hheylau.workers.dev";
const profile = join(tmpdir(), `poker-live-${Date.now()}`);

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

console.log("2. checking relief-grid…");
const checked = await evaluate(`(() => {
  const row = [...document.querySelectorAll('tr')].find((r) => r.textContent.includes('relief-grid'));
  const box = row && row.querySelector('input[type=checkbox]');
  if (!box) return 'NO_CHECKBOX';
  if (!box.checked) box.click();
  return box.checked ? 'CHECKED' : 'NOT_CHECKED';
})()`);
console.log("   ", checked);

console.log("3. clicking Continue…");
const floating = await evaluate(`(() => {
  const button = document.querySelector('.floating-btn');
  if (!button) return 'NO_BUTTON';
  const text = button.textContent;
  button.click();
  return text;
})()`);
console.log("   ", floating);
await sleep(1000);

console.log("4. confirming on Play…");
const yes = await waitFor(`(() => {
  const button = [...document.querySelectorAll('button')].find((b) => b.textContent.includes('Yes, start the game'));
  if (button) { button.click(); return 'CLICKED'; }
  return 'WAIT';
})()`, "CLICKED", 30);
console.log("   ", yes);

console.log("5. waiting for the table / Python runtime…");
const table = await waitFor("document.body.textContent", "Start a new game", 240);
console.log("--- page text ---");
console.log((table || "").replace(/\s+/g, " ").slice(0, 1400));

ws.close();
edge.kill();
await sleep(1000);
rmSync(profile, { recursive: true, force: true });
