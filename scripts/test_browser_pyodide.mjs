// Drive headless Edge with the DevTools protocol and wait for the Pyodide test
// page to finish (no virtual-time shortcut).
//   node scripts/test_browser_pyodide.mjs

import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import { rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = 9223;
const URL = process.env.TEST_URL || "http://127.0.0.1:8789/_python_test.html";
const profile = join(tmpdir(), `poker-cdp-${Date.now()}`);

const edge = spawn(EDGE, [
  "--headless=new", "--disable-gpu", "--no-sandbox",
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, URL,
], { stdio: "ignore" });

async function getJson(path) {
  const response = await fetch(`http://127.0.0.1:${PORT}${path}`);
  return response.json();
}

let target = null;
for (let i = 0; i < 50; i += 1) {
  try {
    const list = await getJson("/json");
    target = list.find((item) => item.type === "page" && item.url.includes("_python_test"));
    if (target) break;
  } catch { /* not ready */ }
  await sleep(500);
}
if (!target) {
  console.error("could not find the test page");
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

let last = "";
for (let i = 0; i < 180; i += 1) {
  last = (await evaluate("document.getElementById('result')?.textContent || ''")) || "";
  if (last.includes("RESULT_OK") || last.includes("RESULT_ERR")) break;
  await sleep(1000);
}
console.log(last);
ws.close();
edge.kill();
await sleep(1000);
rmSync(profile, { recursive: true, force: true });
