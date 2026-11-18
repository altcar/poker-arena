// Test the login form and the 20-hand rated challenge through the UI.
import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = Number(process.env.PORT || (9400 + Math.floor(Math.random() * 400)));
const BASE = process.env.BASE || "https://poker-arena.hheylau.workers.dev";
const profile = join(tmpdir(), `poker-rated-${Date.now()}`);

const edge = spawn(EDGE, [
  "--headless=new", "--disable-gpu", "--no-sandbox",
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
  `${BASE}/#/login`,
], { stdio: "ignore" });
process.on("exit", () => { try { spawn("taskkill", ["/PID", String(edge.pid), "/T", "/F"], { stdio: "ignore" }); } catch { /* ignore */ } });

let target = null;
for (let i = 0; i < 60; i += 1) {
  try {
    const list = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
    target = list.find((item) => item.type === "page" && item.url.includes("poker-arena"));
    if (target) break;
  } catch { /* not ready */ }
  await sleep(500);
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
    ws.send(JSON.stringify({ id, method: "Runtime.evaluate", params: { expression, returnByValue: true, awaitPromise: true } }));
  });
}
async function waitFor(contains, seconds = 30) {
  for (let i = 0; i < seconds * 2; i += 1) {
    const value = await evaluate("document.body.textContent");
    if (typeof value === "string" && value.includes(contains)) return true;
    await sleep(500);
  }
  return false;
}
async function check(name, ok, detail = "") {
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) process.exitCode = 1;
}

await waitFor("Welcome back", 30);
await evaluate(`(() => {
  const inputs = [...document.querySelectorAll("input")];
  inputs.find((i) => i.type === "email").value = "demo@poker-arena.local";
  inputs.find((i) => i.type === "password").value = "arena-demo-2026";
  [...document.querySelectorAll("button")].find((b) => b.textContent.includes("Log in")).click();
})()`);
const loggedIn = await waitFor("PocketRockets", 30);
check("login form", loggedIn, loggedIn ? "" : await evaluate("document.body.textContent"));
console.log("after login:", await evaluate("fetch('/api/auth/me').then(r=>r.json()).then(j=>JSON.stringify(j.user))"), await evaluate("location.hash"));

const bots = await (await fetch(`${BASE}/api/bots`)).json();
const bot = bots.bots.find((b) => b.name === "PocketRockets");
await evaluate(`location.hash = "#/test/${bot.id}"`);
check("rated test page", await waitFor("Run 20-hand rated test", 30));
console.log("auth in page:", await evaluate("fetch('/api/auth/me').then(r => r.json()).then(j => JSON.stringify(j))"));

await evaluate(`[...document.querySelectorAll("button")].find((b) => b.textContent.includes("Run 20-hand rated test")).click()`);
const finished = await waitFor("net ", 240) && await waitFor("Rating ", 30);
check("rated test finished", finished, finished ? "" : await evaluate("document.body.textContent"));
const text = await evaluate("document.body.textContent");
const match = String(text).match(/Rating (\d+) \(([+-]?\d+(?:\.\d+)?)\)/);
check("rating reported", Boolean(match), match ? match[0] : "no rating line");
console.log(match ? `rating now ${match[1]}, change ${match[2]}` : "");

const updated = (await (await fetch(`${BASE}/api/bots`)).json()).bots.find((b) => b.name === "PocketRockets");
check("rating persisted", updated.matches > 0, `matches=${updated.matches} rating=${updated.rating}`);

ws.close();
edge.kill();
