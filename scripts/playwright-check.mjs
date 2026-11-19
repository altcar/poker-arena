// End-to-end check of the redesigned site with Playwright.
//   node scripts/playwright-check.mjs [base-url]
//
// Uses the installed Edge via Playwright's channel option; no browser download.

import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2] || process.env.BASE || "https://poker-arena.hheylau.workers.dev";
const SHOTS = join(tmpdir(), "arena-shots");
mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch({ channel: "msedge", headless: true });
const results = [];
let failed = 0;

function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failed += 1;
}

function watch(page, errors) {
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
}

async function open(page, hash, text, timeout = 30000) {
  await page.goto(`${BASE}/${hash}`, { waitUntil: "domcontentloaded" });
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForFunction((needle) => document.body.innerText.includes(needle), text, { timeout });
}

const context = await browser.newContext({ viewport: { width: 1360, height: 1000 } });
const page = await context.newPage();
const errors = [];
watch(page, errors);

// ---------------------------------------------------------------- static pages

const pages = [
  ["#/", "Build the bot. Climb the ladder."],
  ["#/pricing", "Free to play. Paid to compete."],
  ["#/prize-pool", "$25,000 on the table"],
  ["#/business", "A competitive layer for the bots people build."],
  ["#/games", "Five formats. One ladder."],
  ["#/games/draw27", "2-7 Triple Draw"],
  ["#/leaderboard", "House bots and uploaded bots"],
  ["#/tournaments", "Scheduled events"],
  ["#/tournaments/season-2-kickoff", "Season 2 Kickoff"],
  ["#/live", "Tables in progress"],
  ["#/forum", "Bot development, tournaments and bugs"],
  ["#/stack", "Notes from the arena"],
  ["#/stack/your-first-bot", "Your first bot"],
  ["#/newsletter", "One email when something actually happens"],
  ["#/rules", "Rules that keep the arena fair"],
  ["#/about", "A competitive arena for bots people build"],
  ["#/teams", "Every competition team"],
  ["#/matrix", "The full round robin"],
  ["#/matches", "round-robin matches"],
  ["#/season/2", "Season 2"],
];

for (const [hash, text] of pages) {
  try {
    await open(page, hash, text);
    check(`page ${hash}`, true);
  } catch (error) {
    check(`page ${hash}`, false, String(error).split("\n")[0]);
  }
}

// ---------------------------------------------------------------- pricing toggle

await open(page, "#/pricing", "Free to play");
await page.getByText("Annual · 2 months free", { exact: false }).filter({ visible: true }).click();
await page.waitForTimeout(400);
const pricingText = await page.locator("body").innerText();
check("annual pricing switches to $190 / $990", pricingText.includes("$190") && pricingText.includes("$990"));
check("prize pool on pricing", pricingText.includes("$25,000"));
await page.screenshot({ path: `${SHOTS}/pricing-annual.png`, fullPage: false });

// ---------------------------------------------------------------- leaderboard tabs

await open(page, "#/leaderboard", "House bots and uploaded bots");
await page.getByText("Elite (6)", { exact: false }).filter({ visible: true }).click();
await page.waitForTimeout(500);
const eliteText = await page.locator("body").innerText();
check("elite division filters the table", eliteText.includes("relief-grid") && !eliteText.includes("chm"));

// ---------------------------------------------------------------- forum thread

await open(page, "#/forum", "Bot development");
await page.getByRole("link", { name: /Welcome to the arena forum/ }).click();
await page.getByText("Start here").first().waitFor({ timeout: 15000 });
check("forum thread opens", true);

// ---------------------------------------------------------------- six-max table

await open(page, "#/play", "Sit at a six-max table");
await page.getByRole("button", { name: "Start the table" }).click();
await page.getByText("Your cards:", { exact: false }).first().waitFor({ timeout: 30000 });
const tableText = await page.locator("body").innerText();
check("six-max table deals a hand", tableText.includes("Pot") && tableText.includes("Your cards"));
const callButton = page.getByRole("button", { name: /^(Check|Call )/ }).first();
if (await callButton.count()) await callButton.click();
await page.waitForTimeout(600);
check("six-max action accepted", !(await page.locator("body").innerText()).includes("Could not"));
await page.screenshot({ path: `${SHOTS}/play-6max.png`, fullPage: false });

// ---------------------------------------------------------------- heads-up

await open(page, "#/play?mode=hu", "Heads-up Hold'em");
await page.getByRole("button", { name: "Sit down" }).click();
await page.getByText("Your cards:", { exact: false }).first().waitFor({ timeout: 30000 });
check("heads-up table starts", (await page.locator("body").innerText()).includes("Your cards"));

// ---------------------------------------------------------------- boss run

await open(page, "#/play?mode=boss", "A three-boss gauntlet");
await page.getByRole("button", { name: "Start the run" }).click();
await page.getByText("The Bully", { exact: false }).first().waitFor({ timeout: 20000 });
check("boss run starts", true);
const bossCall = page.getByRole("button", { name: /^(Check|Call )/ }).first();
if (await bossCall.count()) await bossCall.click();
await page.waitForTimeout(400);

// ---------------------------------------------------------------- draw27

await open(page, "#/play?mode=draw27", "2-7 Triple Draw");
await page.getByText("Predraw", { exact: false }).first().waitFor({ timeout: 15000 });
for (let i = 0; i < 12; i += 1) {
  const text = await page.locator("body").innerText();
  if (/wins the pot|You win the pot|Split pot/.test(text)) break;
  if (text.includes("Stand pat")) {
    await page.getByRole("button", { name: /Stand pat|Draw \d/ }).first().click();
  } else if (text.includes("Check")) {
    await page.getByRole("button", { name: "Check" }).first().click();
  } else if (/Call \d/.test(text)) {
    await page.getByRole("button", { name: /Call \d/ }).first().click();
  } else if (text.includes("Deal next hand")) {
    break;
  }
  await page.waitForTimeout(250);
}
check("draw27 hand progresses", (await page.locator("body").innerText()).includes("Hand log"));

// ---------------------------------------------------------------- OFC

await open(page, "#/play?mode=ofc", "Pineapple OFC");
await page.locator(".ofc-slot").first().waitFor({ timeout: 15000 });
for (let i = 0; i < 5; i += 1) {
  const card = page.locator(".playing-card.clickable").first();
  if (!(await card.count())) break;
  await card.click();
  const slot = page.locator(".ofc-slot.interactive").first();
  if (!(await slot.count())) break;
  await slot.click();
  await page.waitForTimeout(200);
}
const boardCards = await page.locator(".ofc-slot:not(.interactive)").count();
check("OFC accepts placements", boardCards !== 5 || (await page.locator("body").innerText()).includes("Place "));
await page.screenshot({ path: `${SHOTS}/ofc.png`, fullPage: false });

// ---------------------------------------------------------------- uploaded bot (Pyodide)

const bots = await (await fetch(`${BASE}/api/bots`)).json();
const demo = (bots.bots || []).find((bot) => bot.name === "PocketRockets") || (bots.bots || [])[0];
if (demo) {
  await page.goto(`${BASE}/#/play?bot=${demo.id}&mode=hu`, { waitUntil: "domcontentloaded" });
  try {
    await page.getByText("Your cards:", { exact: false }).first().waitFor({ timeout: 120000 });
    check("uploaded bot loads in Pyodide", true);
    const action = page.getByRole("button", { name: /^(Check|Call |Fold)/ }).first();
    if (await action.count()) await action.click();
  } catch {
    check("uploaded bot loads in Pyodide", false, "timeout");
  }
} else {
  check("uploaded bot loads in Pyodide", false, "no uploaded bot");
}

// ---------------------------------------------------------------- login

await open(page, "#/login", "Log in");
await page.getByLabel("Email").fill("demo@poker-arena.local");
await page.getByLabel("Password").fill("arena-demo-2026");
await page.getByRole("button", { name: "Log in", exact: true }).click();
try {
  await page.getByText("@arena", { exact: false }).first().waitFor({ timeout: 20000 });
  check("login reaches the account page", true);
} catch {
  check("login reaches the account page", false);
}

check("no console or page errors", errors.length === 0, errors.slice(0, 4).join(" | "));

// ---------------------------------------------------------------- screenshots

for (const [name, hash] of [["home", "#/"], ["business", "#/business"], ["leaderboard", "#/leaderboard"]]) {
  await page.goto(`${BASE}/${hash}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: false });
}

// ---------------------------------------------------------------- mobile

const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
const thin = await mobile.newPage();
const mobileErrors = [];
watch(thin, mobileErrors);
await thin.goto(`${BASE}/#/`, { waitUntil: "networkidle" });
await thin.waitForTimeout(800);
const width = await thin.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
check("mobile has no horizontal overflow", width.scroll <= width.inner + 2, JSON.stringify(width));
await thin.screenshot({ path: `${SHOTS}/mobile-home.png`, fullPage: false });
await thin.goto(`${BASE}/#/pricing`, { waitUntil: "networkidle" });
await thin.waitForTimeout(600);
await thin.screenshot({ path: `${SHOTS}/mobile-pricing.png`, fullPage: false });
check("mobile has no errors", mobileErrors.length === 0, mobileErrors.slice(0, 3).join(" | "));

await browser.close();
console.log(`\n${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
