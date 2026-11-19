// Browser smoke test for the platform pages and the new game modes.
//   node scripts/test_platform_ui.mjs [base-url]
//
// Uses headless Edge + the DevTools protocol (no dependencies).

import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = Number(process.env.PORT || (9400 + Math.floor(Math.random() * 400)));
const BASE = process.env.BASE || process.argv[2] || "https://poker-arena.hheylau.workers.dev";
const profile = join(tmpdir(), `poker-platform-${Date.now()}`);

const edge = spawn(EDGE, [
  "--headless=new", "--disable-gpu", "--no-sandbox",
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
  `${BASE}/#/games`,
], { stdio: "ignore" });
process.on("exit", () => { try { spawn("taskkill", ["/PID", String(edge.pid), "/T", "/F"], { stdio: "ignore" }); } catch { /* ignore */ } });

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

async function waitFor(contains, seconds = 30) {
  for (let i = 0; i < seconds * 2; i += 1) {
    const value = await evaluate("document.body.textContent");
    if (typeof value === "string" && value.includes(contains)) return true;
    await sleep(500);
  }
  return false;
}

async function goto(hash, contains, seconds = 30) {
  await evaluate(`location.hash = ${JSON.stringify(hash)}`);
  await sleep(400);
  return waitFor(contains, seconds);
}

async function clickText(text) {
  return evaluate(`(() => {
    const el = [...document.querySelectorAll("button, a")].find((n) => n.textContent.trim().includes(${JSON.stringify(text)}));
    if (!el) return false;
    el.click();
    return true;
  })()`);
}

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? ` — ${detail}` : ""}`);
}

await evaluate(`window.__errs = []; window.addEventListener("error", (e) => window.__errs.push(String(e.message))); window.addEventListener("unhandledrejection", (e) => window.__errs.push(String(e.reason)));`);

check("games page", await waitFor("Pineapple OFC", 30));
check("draw27 rules", await goto("#/games/draw27", "Hand ranking", 20));
check("leaderboard", await goto("#/leaderboard", "relief-grid", 20));
check("live feed", await goto("#/live", "Live bot matches", 20));
check("forum", await goto("#/forum", "Welcome to the arena forum", 20));
check("thread view", await goto("#/forum", "Welcome to the arena forum", 10)
  && await evaluate(`(() => { const a = [...document.querySelectorAll("a")].find((n) => n.textContent.includes("Welcome to the arena forum")); if (!a) return false; a.click(); return true; })()`)
  && await waitFor("Start here", 20));
check("the stack", await goto("#/stack", "How a match is actually run", 20));
check("stack article", await goto("#/stack/your-first-bot", "class Model", 20));
check("newsletter", await goto("#/newsletter", "Subscribe", 20));
check("rules", await goto("#/rules", "Platform rules", 20));
check("login", await goto("#/login", "Log in", 20));
check("tournaments", await goto("#/tournaments", "Season 2 Kickoff", 20));
check("tournament detail", await goto("#/tournaments/season-2-kickoff", "Uploaded entries", 20));
check("season", await goto("#/season/2", "Season 2", 20));
check("teams page", await goto("#/teams", "All competition bots", 30));

check("heads-up picker", await goto("#/play?mode=hu", "Pick one opponent", 30));
check("boss setup", await goto("#/play?mode=boss", "Start the run", 20));
check("boss start", await clickText("Start the run") && await waitFor("The Bully", 20));

// --- draw27: play a few actions
check("draw27 table", await goto("#/play?mode=draw27", "2-7 Triple Draw", 20));
check("draw27 hand", await waitFor("Predraw", 10) || await waitFor("After draw", 10));
for (let i = 0; i < 10; i += 1) {
  const text = await evaluate("document.body.textContent");
  if (text.includes("You win the pot") || text.includes("wins the pot") || text.includes("Split pot")) break;
  if (text.includes("Stand pat")) {
    await clickText("Stand pat");
  } else if (text.includes("Check")) {
    await clickText("Check");
  } else if (text.includes("Call ")) {
    await clickText("Call ");
  } else if (text.includes("Deal next hand")) {
    break;
  }
  await sleep(300);
}
check("draw27 progress", (await evaluate("document.body.textContent")).includes("Hand log") || (await evaluate("document.body.textContent")).includes("Draw"));

// --- OFC: place cards
check("ofc table", await goto("#/play?mode=ofc", "Your board", 20));
for (let i = 0; i < 6; i += 1) {
  await evaluate(`(() => { const c = document.querySelector(".ofc-hand .ofc-card"); if (c) c.click(); })()`);
  await sleep(120);
  await evaluate(`(() => { const s = document.querySelector(".ofc-card.slot.pickable"); if (s) s.click(); })()`);
  await sleep(150);
}
check("ofc placed cards", (await evaluate("document.querySelectorAll('.ofc-board .cardface:not(.slot)').length")) > 3);

// --- uploaded bot challenge (Pyodide)
const bots = await (await fetch(`${BASE}/api/bots`)).json();
const demo = (bots.bots || []).find((b) => b.name === "PocketRockets") || (bots.bots || [])[0];
if (demo) {
  check("uploaded bot challenge", await goto(`#/play?bot=${demo.id}&mode=hu`, "Stack", 120));
  check("uploaded bot table", await waitFor("PocketRockets", 30) && await waitFor("Fold", 20));
  let acted = false;
  for (let i = 0; i < 12 && !acted; i += 1) {
    acted = await clickText("Check") || await clickText("Call ") || await clickText("Fold");
    if (!acted) await sleep(500);
  }
  check("uploaded bot action", acted);
} else {
  check("uploaded bot challenge", false, "no uploaded bot");
}

const errors = await evaluate("JSON.stringify(window.__errs || [])");
console.log("console errors:", errors);
check("no uncaught errors", !errors || errors === "[]", errors);

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
ws.close();
edge.kill();
process.exit(failed.length ? 1 : 0);
