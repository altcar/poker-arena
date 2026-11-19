// Poker Arena front end: a hash-routed single-page app.
//   #/play       the table (you + up to five JS or Python bots)
//   #/teams      the bots and their styles
//   #/matrix     head-to-head matrix
//   #/matches    every match
//   #/team/:name one team's detail
//   #/about      what this is and the API

import { Game } from "./engine/game.js";
import { loadTeams, registerPythonBots, loadUserBot } from "./python_runtime.js";
import {
  auth, ensureAuth, gamesView, gameDetailView, leaderboardView, tournamentsView,
  tournamentView, liveView, forumView, newThreadView, threadView, stackView,
  articleView, newsletterView, loginView, signupView, accountView, uploadView,
  botTestView, rulesView, seasonView,
} from "./platform_client.js";
import { draw27View } from "./views_draw27.js";
import { ofcView } from "./views_ofc.js";

const main = document.getElementById("main");

// ---------------------------------------------------------------- helpers

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === "class") el.className = value;
      else if (key === "onclick") el.addEventListener("click", value);
      else if (key.startsWith("on") && typeof value === "function") {
        el.addEventListener(key.slice(2), value);
      } else el.setAttribute(key, value);
    }
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const SUITS = { H: "♥", S: "♠", D: "♦", C: "♣" };
const SUIT_EMOJI = { H: "♥️", S: "♠️", D: "♦️", C: "♣️" };

// Two-sided 95% Student-t critical values, from the organiser's evals/stats.py.
const T95 = [
  12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
  2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
  2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045,
];

function t95(df) {
  if (df < 1) return Infinity;
  if (df >= 30) return 1.96;
  return T95[df - 1];
}

// The standard deviation behind a 95% interval: ci95 = 100 * t95 * std / sqrt(n).
function stdFromCi(ci95, n) {
  if (n < 2 || ci95 == null || !Number.isFinite(ci95)) return null;
  return (ci95 * Math.sqrt(n)) / (100 * t95(n - 1));
}

function fmtBB(value) {
  if (value == null || !Number.isFinite(value)) return "—";
  const rounded = Number(value.toFixed(1));
  if (rounded === 0) return "0.0";
  return (rounded > 0 ? "+" : "−") + Math.abs(rounded).toFixed(1);
}

function fmtChips(value) {
  if (value == null || !Number.isFinite(value)) return "—";
  return Math.round(value).toLocaleString("en-GB");
}

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

function parseHash() {
  const raw = String(location.hash || "").replace(/^#/, "");
  const at = raw.indexOf("?");
  const pathPart = at === -1 ? raw : raw.slice(0, at);
  const query = {};
  if (at !== -1) {
    for (const [key, value] of new URLSearchParams(raw.slice(at + 1))) query[key] = value;
  }
  return { path: pathPart.split("/").filter(Boolean), query };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function cardFace(card, small = false) {
  if (!card) return h("span", { class: `cardface${small ? " small" : ""}` }, "•");
  const rank = card[0];
  const suit = card[1];
  const symbol = SUITS[suit] || suit;
  const red = suit === "H" || suit === "D";
  return h(
    "span",
    { class: `cardface${red ? " red" : ""}${small ? " small" : ""}` },
    h("span", {}, rank),
    h("span", {}, symbol)
  );
}

function cardBack(small = false) {
  return h("span", { class: `cardface back${small ? " small" : ""}` });
}

// ---------------------------------------------------------------- session

let sessionId = localStorage.getItem("pokerArenaSession") || null;
let gameState = null;
let localGame = null;
let loadingMessage = null;
const knownTeamNames = {};
let playMode = "nlh6";
let lastSetup = null;
let bossMode = false;
let bossRun = null;

const BOSSES = [
  { key: "aggressive", name: "The Bully", stack: 1500, smallBlind: 10, bigBlind: 20 },
  { key: "mixed", name: "The Wall", stack: 2500, smallBlind: 15, bigBlind: 30 },
  { key: "adaptive", name: "The Machine", stack: 4000, smallBlind: 25, bigBlind: 50 },
];

// The five bots that exist in the JavaScript engine. Competition teams are
// Python models; they run in the browser through Pyodide.
const PLAYABLE_KEYS = ["conservative", "workhorse", "adaptive", "mixed", "aggressive"];
const PLAYABLE_NAMES = {
  conservative: "Conservative",
  workhorse: "Workhorse",
  adaptive: "Adaptive",
  mixed: "Mixed",
  aggressive: "Aggressive",
};
const PLAYABLE_SET = new Set(PLAYABLE_KEYS);
const MAX_SELECTED = 6;

function rememberTeams(data) {
  for (const bot of data.bots || []) knownTeamNames[bot.key] = bot.name;
  for (const team of data.allTeams?.teams || []) knownTeamNames[team.team] = team.team;
  for (const row of data.teamsData || []) knownTeamNames[row.team] = row.team;
}

function teamName(key) {
  if (key.startsWith("user:")) {
    const found = (auth.bots || []).find((bot) => `user:${bot.id}` === key);
    if (found) return found.name;
    return knownTeamNames[key] || "your bot";
  }
  return PLAYABLE_NAMES[key] || knownTeamNames[key] || key;
}

function readChosenBots() {
  try {
    const raw = JSON.parse(localStorage.getItem("pokerArenaBots") || "null");
    if (!Array.isArray(raw)) return null;
    return raw.filter((key) => typeof key === "string");
  } catch {
    return null;
  }
}

let selectedBots = new Set(readChosenBots() || PLAYABLE_KEYS);
let humanPlaying = true;

function setLoading(message) {
  loadingMessage = message;
  render();
}

async function startSession(name, botKeys, playing = humanPlaying) {
  humanPlaying = playing !== false;
  bossMode = false;
  bossRun = null;
  playMode = "nlh6";
  let keys = [...new Set((botKeys || []).filter((key) => typeof key === "string"))];
  let available = new Set();
  try {
    const data = await loadTournament();
    rememberTeams(data);
    available = new Set(data.availableTeams || []);
  } catch {
    // The setup page still works with the five JS bots.
  }
  keys = keys.filter((key) => PLAYABLE_SET.has(key) || available.has(key) || key.startsWith("user:"));
  if (!keys.length) keys = [...PLAYABLE_KEYS];
  const userKeys = keys.filter((key) => key.startsWith("user:"));
  const teamKeys = keys.filter((key) => !key.startsWith("user:"));

  const needsPython = teamKeys.some((key) => !PLAYABLE_SET.has(key)) || userKeys.length > 0;
  if (!needsPython) {
    try {
      const data = await api("/api/session", {
        method: "POST",
        body: JSON.stringify({
          humanName: humanPlaying ? (name || "You") : "Spectator",
          botKeys: keys,
          humanPlaying,
        }),
      });
      localGame = null;
      sessionId = data.sessionId;
      gameState = data.state;
      localStorage.setItem("pokerArenaSession", sessionId);
      lastSetup = { mode: "nlh6", keys, name: humanPlaying ? (name || "You") : "Spectator" };
      loadingMessage = null;
      render();
    } catch (error) {
      alert(`Could not start the table: ${error.message}`);
    }
    return;
  }

  // Competition bots: run the submitted Python in the browser.
  try {
    setLoading("Loading the Python runtime (about 10 MB)…");
    await loadTeams(teamKeys, (message) => setLoading(message));
    registerPythonBots(teamKeys, knownTeamNames);
    for (const key of userKeys) {
      const meta = (await api(`/api/bots/${encodeURIComponent(key.slice(5))}`)).bot;
      knownTeamNames[key] = meta.name;
      await loadUserBot(meta, (message) => setLoading(message));
    }
    localGame = new Game({
      humanName: humanPlaying ? (name || "You") : "Spectator",
      botKeys: keys,
      humanPlaying,
      seed: Date.now() % 1000000,
    });
    const { state } = localGame.startHand();
    gameState = state;
    sessionId = null;
    lastSetup = { mode: "nlh6", keys, name: humanPlaying ? (name || "You") : "Spectator" };
    localStorage.removeItem("pokerArenaSession");
    loadingMessage = null;
    render();
  } catch (error) {
    loadingMessage = null;
    render();
    alert(`Could not load the competition models: ${error.message}`);
  }
}

async function resumeSession() {
  if (!sessionId) return false;
  try {
    const data = await api(`/api/session/${sessionId}`);
    gameState = data.state;
    return true;
  } catch {
    sessionId = null;
    localStorage.removeItem("pokerArenaSession");
    return false;
  }
}

function startLocalGame(name, keys, playing = true, options = {}) {
  localGame = new Game({
    humanName: name || "You",
    botKeys: keys,
    humanPlaying: playing,
    seed: Date.now() % 1000000,
    stack: options.stack || 500,
    smallBlind: options.smallBlind || 10,
    bigBlind: options.bigBlind || 20,
    stacks: options.stacks,
    autoRebuy: options.autoRebuy,
    playerNames: options.playerNames,
  });
  const { state } = localGame.startHand();
  gameState = state;
  sessionId = null;
  loadingMessage = null;
  localStorage.removeItem("pokerArenaSession");
  playMode = options.mode || "hu";
  lastSetup = { mode: playMode, keys: [...keys], name: name || "You", options };
  if (location.hash !== "#/play") location.hash = "#/play";
  else render();
}

async function startChallenge(container, { opponent, botId }) {
  container.replaceChildren(h("div", { class: "card" }, h("p", { class: "muted" }, "Loading the opponent…")));
  try {
    if (botId) {
      const meta = (await api(`/api/bots/${encodeURIComponent(botId)}`)).bot;
      await loadUserBot(meta, (message) => {
        container.replaceChildren(h("div", { class: "card" }, h("p", { class: "muted" }, message)));
      });
      startLocalGame("You", [`user:${meta.id}`], true, { mode: "hu", playerNames: ["You", meta.name] });
      return;
    }
    rememberTeams(await loadTournament().catch(() => ({})));
    if (PLAYABLE_SET.has(opponent)) {
      startLocalGame("You", [opponent], true, { mode: "hu" });
      return;
    }
    await loadTeams([opponent], (message) => {
      container.replaceChildren(h("div", { class: "card" }, h("p", { class: "muted" }, message)));
    });
    registerPythonBots([opponent], { ...knownTeamNames, [opponent]: opponent });
    startLocalGame("You", [opponent], true, { mode: "hu", playerNames: ["You", opponent] });
  } catch (error) {
    container.replaceChildren(h("div", { class: "card error" }, `Could not start the challenge: ${error.message}`));
  }
}

function startBossRun(level = 0) {
  const boss = BOSSES[level];
  bossMode = true;
  bossRun = { level, completed: false, over: false };
  startLocalGame("You", [boss.key], true, {
    mode: "boss",
    stack: 1000,
    smallBlind: boss.smallBlind,
    bigBlind: boss.bigBlind,
    stacks: [1000, boss.stack],
    autoRebuy: false,
    playerNames: ["You", boss.name],
  });
  playMode = "boss";
  lastSetup = { mode: "boss", level };
}

function bossNext() {
  if (!bossRun) return;
  const human = localGame.stacks[0];
  const boss = localGame.stacks[1];
  if (human <= 0) {
    startBossRun(0);
    return;
  }
  if (boss <= 0) {
    if (bossRun.level >= BOSSES.length - 1) {
      bossRun.completed = true;
      gameState = localGame.publicState();
      render();
      return;
    }
    startBossRun(bossRun.level + 1);
    return;
  }
  nextHandRaw();
}

function nextHandRaw() {
  if (localGame) {
    const { state } = localGame.startHand();
    gameState = state;
    render();
    return;
  }
  api(`/api/session/${sessionId}/next`, { method: "POST" }).then((data) => {
    gameState = data.state;
    render();
  });
}

async function act(kind, amount = 0) {
  if (localGame) {
    const { state } = localGame.act(kind, amount);
    gameState = state;
    render();
    return;
  }
  const data = await api(`/api/session/${sessionId}/action`, {
    method: "POST",
    body: JSON.stringify({ kind, amount }),
  });
  gameState = data.state;
  render();
}

async function nextHand() {
  if (bossMode) {
    bossNext();
    return;
  }
  if (localGame) {
    const { state } = localGame.startHand();
    gameState = state;
    render();
    return;
  }
  const data = await api(`/api/session/${sessionId}/next`, { method: "POST" });
  gameState = data.state;
  render();
}

async function rebuy() {
  if (localGame) {
    localGame.rebuy();
    gameState = localGame.publicState();
    render();
    return;
  }
  const data = await api(`/api/session/${sessionId}/rebuy`, { method: "POST" });
  gameState = data.state;
  render();
}

function newSession() {
  localGame = null;
  sessionId = null;
  gameState = null;
  loadingMessage = null;
  bossMode = false;
  bossRun = null;
  playMode = "nlh6";
  localStorage.removeItem("pokerArenaSession");
  render();
}

async function startFreshGame() {
  if (bossMode) {
    startBossRun(0);
    return;
  }
  if (lastSetup && lastSetup.mode === "hu" && lastSetup.keys?.length) {
    const options = lastSetup.options || { mode: "hu" };
    startLocalGame("You", lastSetup.keys, true, options);
    return;
  }
  const name = gameState?.players?.[0]?.isHuman ? gameState.players[0].name : "You";
  await startSession(name, [...selectedBots], humanPlaying);
}

// ---------------------------------------------------------------- play view

function seatPositions(seat, count) {
  // Seat 0 (you) sits at the bottom centre. The other seats are spread around
  // the oval, leaving a gap at the bottom so no bot ever covers your cards.
  if (count <= 1) return [50, 50];
  if (seat === 0) return [50, 88];
  if (count === 2) return [50, 12];
  const others = count - 1;
  const gap = Math.min(40, 180 / others);
  const span = 360 - 2 * gap;
  const t = others === 1 ? 0.5 : (seat - 1) / (others - 1);
  const theta = ((90 + gap + t * span) * Math.PI) / 180;
  const rx = 42;
  const ry = 36;
  return [50 + rx * Math.cos(theta), 50 + ry * Math.sin(theta)];
}

function setupView() {
  const chosen = readChosenBots();
  if (chosen && chosen.length) {
    selectedBots = new Set(chosen);
    const names = chosen.map((key) => teamName(key));
    const countEl = h("span", {}, String(names.length + (humanPlaying ? 1 : 0)));
    const humanCheck = h("label", { class: "botcheck" },
      h("input", { type: "checkbox", checked: humanPlaying, onchange: (e) => {
        humanPlaying = e.target.checked;
        countEl.textContent = String(names.length + (humanPlaying ? 1 : 0));
      } }),
      " I'm playing",
    );
    return h("div", { class: "card" },
      h("h2", {}, "Ready to play?"),
      h("p", {}, `You picked ${names.length} bot${names.length === 1 ? "" : "s"}: ${names.join(", ")}.`),
      h("p", {}, ["Do you want to play a ", countEl, "-handed table?"]),
      h("div", { class: "botchecks" }, humanCheck),
      h("div", { class: "actions" },
        h("button", { class: "primary", onclick: () => startSession("You", chosen, humanPlaying) }, "Yes, start the game"),
        h("button", { class: "ghost", onclick: () => { location.hash = "#/teams"; } }, "Change models"),
        h("button", { class: "ghost", onclick: () => startSession("Spectator", chosen, false) }, "No, just watch"),
      ),
      h("p", { class: "muted small" }, "The table stays in this tab. Competition bots load their Python runtime on first use."),
    );
  }

  return h("div", { class: "card" },
    h("h2", {}, "Pick your opponents"),
    h("p", {}, "Choose up to six bots on the Teams page, then press Continue. The five JavaScript bots start instantly; the competition bots load the Python runtime on first use."),
    h("div", { class: "actions" },
      h("button", { class: "primary", onclick: () => { location.hash = "#/teams"; } }, "Choose models on the Teams page"),
      h("button", { class: "ghost", onclick: () => {
        localStorage.setItem("pokerArenaBots", JSON.stringify(PLAYABLE_KEYS));
        selectedBots = new Set(PLAYABLE_KEYS);
        render();
      } }, "Use the classic five"),
    ),
  );
}

function playView() {
  const page = h("div", { class: "page" });
  const boss = bossMode && bossRun ? BOSSES[bossRun.level] : null;
  page.append(h("h1", {}, boss ? "Big Bad Bots" : "Play the bots"));
  page.append(modeBar(bossMode ? "boss" : playMode));

  if (boss) {
    const human = localGame?.stacks?.[0] ?? 0;
    const bossStack = localGame?.stacks?.[1] ?? 0;
    page.append(
      h("div", { class: "card boss-banner" },
        h("div", { class: "boss-title" },
          h("span", { class: "badge gold" }, `Round ${bossRun.level + 1} / 3`),
          h("strong", {}, boss.name),
        ),
        h("div", { class: "boss-stacks" },
          h("span", {}, `You ${fmtChips(human)}`),
          h("span", { class: "muted" }, "vs"),
          h("span", {}, `${boss.name} ${fmtChips(bossStack)}`),
          h("span", { class: "muted small" }, `blinds ${boss.smallBlind}/${boss.bigBlind}`),
        ),
        h("div", { class: "boss-dots" },
          BOSSES.map((_, i) => h("span", {
            class: `boss-dot${i < bossRun.level ? " done" : i === bossRun.level ? " current" : ""}`,
          }, i < bossRun.level ? "✓" : String(i + 1))),
        ),
      ),
    );
  }

  if (loadingMessage) {
    page.append(
      h("div", { class: "card" },
        h("h2", {}, "Getting the table ready"),
        h("p", {}, loadingMessage),
      ),
    );
    return page;
  }

  if (!localGame && (!sessionId || !gameState)) {
    const resume = h("div", { class: "card" }, "Loading your table — ");
    page.append(resume);
    resumeSession().then((ok) => {
      if (ok) render();
      else resume.replaceWith(setupView());
    });
    return page;
  }

  const state = gameState;
  const hand = state.hand;

  // Always available, in every state of the game.
  const seatNames = (state.players || []).map((p) => p.name).join(", ");
  page.append(
    h("div", { class: "controls" },
      h("button", { class: "primary", onclick: startFreshGame }, bossMode ? "Restart the run" : "Start a new game"),
      bossMode
        ? h("button", { class: "ghost", onclick: () => {
            bossMode = false;
            bossRun = null;
            localGame = null;
            sessionId = null;
            gameState = null;
            loadingMessage = null;
            location.hash = "#/play?mode=boss";
          } }, "Leave run")
        : h("button", { class: "ghost", onclick: () => { location.hash = "#/teams"; } }, "Change opponents"),
      h("span", { class: "muted small" }, `${state.players?.length || 0} seats · ${seatNames}`),
    ),
  );

  if (!hand) {
    page.append(
      h("div", { class: "card" },
        h("h2", {}, "No hand yet"),
        h("div", { class: "actions" },
          h("button", { class: "primary", onclick: nextHand }, "Deal the first hand"),
          h("button", { class: "ghost", onclick: newSession }, "New table"),
        ),
      ),
    );
    return page;
  }

  // The table.
  const table = h("div", { class: "table" });
  const center = h("div", { class: "center" },
    h("div", { class: "pot" }, `Pot ${fmtChips(hand.pot)}`),
    h("div", { class: "board" }, hand.board.length ? hand.board.map((c) => cardFace(c)) : [
      cardBack(), cardBack(), cardBack(), cardBack(), cardBack(),
    ]),
    h("div", { class: "muted small" }, `${state.handNumber ? `Hand ${state.handNumber} · ` : ""}${hand.street}`),
  );
  table.append(center);

  hand.seats.forEach((seat) => {
    const [left, top] = seatPositions(seat.seat, hand.seats.length);
    const isTurn = hand.toAct === seat.seat && !hand.result;
    const classes = ["seat", `s${seat.seat}`];
    if (isTurn) classes.push("turn");
    if (seat.isHuman) classes.push("you");
    const hole = seat.hole
      ? seat.hole.map((c) => cardFace(c, true))
      : [cardBack(true), cardBack(true)];
    const status = seat.folded ? "folded" : seat.status === "all_in" ? "all-in" : "";
    table.append(
      h("div", { class: classes.join(" "), style: `left:${left}%;top:${top}%` },
        h("div", { class: "box" },
          h("div", { class: "hole" }, hole),
          h("div", { class: "name" }, seat.name, state.hand.button === seat.seat ? h("span", { class: "dealer" }, "D") : null),
          h("div", { class: "stack" }, `Stack ${fmtChips(seat.stack)}`),
          h("div", { class: "bet" }, seat.bet ? `Bet ${fmtChips(seat.bet)}` : ""),
          h("div", { class: `status ${seat.folded ? "folded" : seat.status === "all_in" ? "all_in" : ""}` }, status),
        ),
      ),
    );
  });
  page.append(table);

  // Result banner.
  if (hand.result) {
    const result = hand.result;
    const you = result.net[0];
    const rows = hand.seats.map((seat, i) =>
      h("tr", {}, h("td", {}, seat.name), h("td", { class: "num" }, fmtChips(result.net[i])),
        h("td", {}, result.hands[i].join(" "))),
    );
    page.append(
      h("div", { class: "card" },
        h("h2", {}, `Hand ${state.handNumber} result`),
        h("p", {}, `You ${you >= 0 ? "won" : "lost"} ${fmtChips(Math.abs(you))}${you >= 0 ? "" : ""} chips.`),
        h("div", { class: "tablewrap" },
          h("table", { class: "tbl" },
            h("thead", {}, h("tr", {}, h("th", {}, "Seat"), h("th", { class: "num" }, "Net"), h("th", {}, "Cards"))),
            h("tbody", {}, rows),
          ),
        ),
        h("div", { class: "actions" },
          bossMode
            ? (bossRun.completed || bossRun.over)
              ? h("button", { class: "primary", onclick: () => startBossRun(0) }, bossRun.completed ? "New run" : "Restart the run")
              : h("button", { class: "primary", onclick: nextHand },
                  localGame.stacks[1] <= 0 ? (bossRun.level >= 2 ? "Finish the run" : `Face ${BOSSES[bossRun.level + 1].name}`) : "Deal next hand")
            : h("button", { class: "primary", onclick: nextHand }, "Deal next hand"),
          bossMode && (bossRun.completed || bossRun.over)
            ? h("a", { class: "btn ghost", href: "#/games/boss" }, "Boss rules")
            : null,
          !bossMode && state.humanBusted
            ? h("button", { class: "danger", onclick: rebuy }, "Rebuy")
            : null,
          h("button", { class: "ghost", onclick: newSession }, "New table"),
        ),
      ),
    );
  } else if (state.waitingFor === "next") {
    page.append(
      h("div", { class: "card" },
        h("div", { class: "actions" },
          h("button", { class: "primary", onclick: nextHand }, "Deal next hand"),
          h("button", { class: "ghost", onclick: newSession }, "New table"),
        ),
      ),
    );
  } else if (state.waitingFor === "human") {
    const call = hand.callAmount;
    const canRaise = hand.canRaise;
    const min = hand.minRaiseTo;
    const max = hand.maxRaiseTo;
    const pot = hand.pot;
    const you = hand.seats[0];
    const raiseInput = h("input", { id: "raiseAmount", type: "number", min, max, value: min });
    const quick = (label, value) =>
      h("button", { onclick: () => { raiseInput.value = value; } }, label);
    const halfPot = Math.min(max, Math.max(min, you.bet + call + Math.floor((pot + call) * 0.5)));
    const fullPot = Math.min(max, Math.max(min, you.bet + call + Math.floor((pot + call) * 1.0)));
    page.append(
      h("div", { class: "card" },
        h("div", { class: "actions" },
          h("button", { class: "danger", onclick: () => act("fold") }, "Fold"),
          h("button", { class: "primary", onclick: () => act("call") }, call === 0 ? "Check" : `Call ${fmtChips(call)}`),
          canRaise
            ? h("div", { class: "group" },
                raiseInput,
                h("button", {
                  class: "primary",
                  onclick: () => {
                    const value = Math.max(min, Math.min(max, Number(raiseInput.value) || min));
                    raiseInput.value = value;
                    act("raise", value);
                  },
                }, "Raise"),
              )
            : null,
        ),
        canRaise
          ? h("div", { class: "actions" },
              h("span", { class: "muted small" }, `Raise to (${fmtChips(min)}–${fmtChips(max)})`),
              quick("Min", min), quick("½ pot", halfPot), quick("Pot", fullPot), quick("All-in", max),
            )
          : null,
        h("p", { class: "muted small" },
          `Your cards: ${you.hole.join(" ")} · pot odds ${hand.potOdds === 0 ? "—" : (hand.potOdds * 100).toFixed(1) + "%"}`),
      ),
    );
  } else {
    page.append(h("div", { class: "card" }, h("p", {}, "Waiting for the bots…")));
  }

  // Action log.
  if (hand.actions.length) {
    const lines = hand.actions.map((a) => {
      if (a.kind === "reveal_card" && a.player_id === -1) return `Dealer reveals ${a.card}`;
      if (a.kind === "reveal_card") return `${hand.seats[a.player_id]?.name || a.player_id} shows ${a.card}`;
      if (a.kind === "win") return `${hand.seats[a.player_id]?.name || a.player_id} wins ${fmtChips(a.amount)}`;
      const name = hand.seats[a.player_id]?.name || `seat ${a.player_id}`;
      if (a.kind === "fold") return `${name} folds`;
      if (a.kind === "call") return `${name} ${a.amount === 0 && hand.pot === 0 ? "checks" : "calls"}`;
      if (a.kind === "raise") return `${name} raises to ${fmtChips(a.amount)}`;
      return `${name} ${a.kind}`;
    });
    page.append(h("div", { class: "log" }, lines.map((line) => h("div", {}, line))));
  }

  return page;
}

// ---------------------------------------------------------------- other play modes

function modeBar(active) {
  return h("div", { class: "mode-bar" },
    h("a", { class: active === "nlh6" ? "active" : "", href: "#/play" }, "NLH 6-max"),
    h("a", { class: active === "hu" ? "active" : "", href: "#/play?mode=hu" }, "Heads-up"),
    h("a", { class: active === "boss" ? "active" : "", href: "#/play?mode=boss" }, "Big Bad Bots"),
    h("a", { class: active === "draw27" ? "active" : "", href: "#/play?mode=draw27" }, "2-7 Triple Draw"),
    h("a", { class: active === "ofc" ? "active" : "", href: "#/play?mode=ofc" }, "Pineapple OFC"),
    h("a", { href: "#/games" }, "All games →"),
  );
}

function headsUpView(query = {}) {
  const page = h("div", { class: "page" });
  page.append(
    h("h1", {}, "Heads-up Hold'em"),
    modeBar("hu"),
    h("p", { class: "muted" }, "One opponent. The button posts the small blind and acts first before the flop, then last after it."),
  );

  if (query.opponent || query.bot) {
    const box = h("div", {});
    page.append(box);
    startChallenge(box, { opponent: query.opponent, botId: query.bot });
    return page;
  }

  const picker = h("div", { class: "card" }, h("p", { class: "muted" }, "Loading opponents…"));
  page.append(picker);
  Promise.all([loadTournament().catch(() => ({})), api("/api/bots").catch(() => ({ bots: [] }))]).then(([data, bots]) => {
    rememberTeams(data || {});
    const available = new Set(data?.availableTeams || []);
    const options = [];
    for (const key of PLAYABLE_KEYS) options.push({ value: key, label: `${PLAYABLE_NAMES[key]} (JavaScript)` });
    const teams = (data?.allTeams?.teams || []).filter((t) => available.has(t.team)).map((t) => t.team).sort();
    for (const team of teams) options.push({ value: team, label: team });
    for (const bot of bots.bots || []) options.push({ value: `bot:${bot.id}`, label: `${bot.name} (uploaded by ${bot.owner || "?"})` });
    const select = h("select", {}, options.map((o) => h("option", { value: o.value }, o.label)));
    picker.replaceChildren(
      h("h2", {}, "Pick one opponent"),
      h("p", { class: "muted small" }, "The five JavaScript bots start instantly. Competition and uploaded bots load their Python in your browser on first use."),
      h("div", { class: "actions" },
        select,
        h("button", { class: "primary", onclick: () => {
          const value = select.value;
          if (value.startsWith("bot:")) startChallenge(picker, { botId: value.slice(4) });
          else startChallenge(picker, { opponent: value });
        } }, "Sit down"),
        h("a", { class: "btn ghost", href: "#/play" }, "Six-max instead"),
      ),
    );
  });
  return page;
}

function bossView() {
  const page = h("div", { class: "page" });
  page.append(
    h("h1", {}, "Big Bad Bots"),
    modeBar("boss"),
    h("div", { class: "card" },
      h("p", {}, "A three-boss gauntlet. You get 1,000 chips each round; the bosses get more. The blinds rise. No rebuys."),
      h("div", { class: "boss-list" },
        BOSSES.map((boss, index) =>
          h("div", { class: "boss-row" },
            h("span", { class: "badge gold" }, `Round ${index + 1}`),
            h("strong", {}, boss.name),
            h("span", { class: "muted small" }, `${boss.stack.toLocaleString("en-GB")} chips · blinds ${boss.smallBlind}/${boss.bigBlind}`),
            h("span", { class: "muted small" }, ["Aggressive", "Mixed", "Adaptive"][index]),
          )
        ),
      ),
      h("div", { class: "actions" },
        h("button", { class: "primary", onclick: () => startBossRun(0) }, "Start the run"),
        h("a", { class: "btn ghost", href: "#/games/boss" }, "Full rules"),
      ),
    ),
    h("div", { class: "card muted" },
      h("p", {}, "Take the boss's whole stack to advance. Lose yours and the run ends and restarts from round 1."),
    ),
  );
  return page;
}

function playRouterView(query = {}) {
  const mode = query.mode || (query.opponent || query.bot ? "hu" : "nlh6");
  if (mode === "draw27") return draw27View();
  if (mode === "ofc") return ofcView();
  if (mode === "boss") return bossMode && localGame ? playView() : bossView();
  if (mode === "hu") {
    if (query.opponent || query.bot) return headsUpView(query);
    if (bossMode) {
      bossMode = false;
      bossRun = null;
    }
    if (localGame && lastSetup?.mode === "hu" && gameState) return playView();
    return headsUpView(query);
  }
  if (bossMode) {
    bossMode = false;
    bossRun = null;
  }
  return playView();
}

// ---------------------------------------------------------------- data views

let tournamentCache = null;

async function loadTournament() {
  if (!tournamentCache) tournamentCache = await api("/api/tournament");
  return tournamentCache;
}

function verdictText(m) {
  if (!m) return "";
  if (m.verdict === "tie") return "tie";
  return m.verdict === "a" ? `${m.a} won` : `${m.b} won`;
}

function matrixTable(matrix, names, caption) {
  const header = h("tr", {}, h("th", {}, "Team"), names.map((n) => h("th", {}, n)));
  const rows = names.map((me) =>
    h("tr", {},
      h("td", {}, h("a", { href: `#/team/${encodeURIComponent(me)}` }, me)),
      names.map((other) => {
        if (other === me) return h("td", { class: "diag" }, "—");
        const cell = matrix[me]?.[other];
        if (!cell) return h("td", { class: "muted" }, "—");
        const value = cell.mean_bb;
        return h("td", { class: `num ${value > 0 ? "pos" : value < 0 ? "neg" : ""}` },
          fmtBB(value), h("br"),
          h("span", { class: "muted small" }, `± ${cell.ci95_bb == null ? "—" : cell.ci95_bb.toFixed(1)}`));
      }),
    ),
  );
  return h("div", { class: "card" },
    caption ? h("p", { class: "muted small" }, caption) : null,
    h("div", { class: "tablewrap" },
      h("table", { class: "tbl matrix" }, h("thead", {}, header), h("tbody", {}, rows)),
    ),
  );
}

function matrixView() {
  const page = h("div", { class: "page" });
  page.append(h("h1", {}, "Head-to-head"));
  loadTournament().then((data) => {
    const ours = data.ourTournament;
    if (ours && ours.teams?.length && ours.matrix) {
      const names = ours.teams.map((t) => t.team);
      page.append(h("h2", {}, "Our round robin"));
      page.append(matrixTable(
        ours.matrix,
        names,
        `All ${names.length} models whose files we have, ${ours.deals} deals each. The row team's bb/100 against the column team.`,
      ));
      const rows = ours.teams.map((t) =>
        h("tr", {},
          h("td", { class: "num" }, String(t.rank)),
          h("td", {}, h("a", { href: `#/team/${encodeURIComponent(t.team)}` }, t.team)),
          h("td", { class: `num ${t.score > 0 ? "pos" : t.score < 0 ? "neg" : ""}` }, fmtBB(t.score)),
          h("td", { class: "num muted" }, t.ci95 == null ? "—" : `± ${t.ci95.toFixed(1)}`),
          h("td", { class: "num" }, `${t.wins}–${t.draws}–${t.losses}`),
          h("td", { class: "num muted" }, String(t.samples ?? "")),
        ),
      );
      page.append(h("h2", {}, "Standings"));
      page.append(
        h("div", { class: "card tablewrap" },
          h("table", { class: "tbl" },
            h("thead", {}, h("tr", {},
              h("th", { class: "num" }, "#"), h("th", {}, "Team"),
              h("th", { class: "num" }, "bb/100"), h("th", { class: "num" }, "95% ±"),
              h("th", { class: "num" }, "W–D–L"), h("th", { class: "num" }, "Deals"),
            )),
            h("tbody", {}, rows),
          ),
        ),
      );
    } else {
      page.append(h("div", { class: "card empty" }, "Our round robin has not been run yet."));
    }

    const available = new Set(data.availableTeams || []);
    const allTeams = (data.allTeams?.teams || []).filter((t) => available.has(t.team));
    const allMatrix = data.allTeams?.matrix || {};
    if (allTeams.length) {
      const names = allTeams.map((t) => t.team).sort();
      page.append(h("details", { class: "card" },
        h("summary", {}, `Official competition matrix (${names.length} teams)`),
        matrixTable(allMatrix, names, "Results from the live competition site, for reference."),
      ));
    }

    const board = data.tournament?.groups?.[0]?.board;
    const teams = board?.teams || [];
    const matrix = board?.matrix || {};
    if (teams.length) {
      const names = teams.map((t) => t.team);
      page.append(h("details", { class: "card" },
        h("summary", {}, "My five-bot round robin"),
        matrixTable(matrix, names, "The five fork bots, from the precomputed five-bot round robin."),
      ));
    }
  }).catch((error) => {
    page.append(h("div", { class: "card error" }, `Could not load the matrix: ${error.message}`));
  });
  return page;
}

function matchesView() {
  const page = h("div", { class: "page" });
  page.append(h("h1", {}, "Matches"));
  loadTournament().then((data) => {
    const matches = (data.matches || []).slice().sort((a, b) => (b.finished_at || 0) - (a.finished_at || 0));
    if (!matches.length) {
      page.append(h("div", { class: "card empty" }, "No matches yet."));
      return;
    }
    const filter = h("input", { type: "search", placeholder: "Filter by team…" });
    const count = h("span", { class: "muted small" }, `${matches.length} matches`);
    const tbody = h("tbody", {});
    const draw = () => {
      const needle = filter.value.trim().toLowerCase();
      const list = needle
        ? matches.filter((m) => m.a.toLowerCase().includes(needle) || m.b.toLowerCase().includes(needle))
        : matches;
      const shown = list.slice(0, 300);
      tbody.replaceChildren(...shown.map((m) =>
        h("tr", {},
          h("td", {}, `${m.a} vs ${m.b}`),
          h("td", {}, verdictText(m)),
          h("td", { class: `num ${m.mean_bb > 0 ? "pos" : m.mean_bb < 0 ? "neg" : ""}` }, fmtBB(m.mean_bb)),
          h("td", { class: "num muted" }, m.ci95_bb == null ? "—" : `± ${m.ci95_bb.toFixed(1)}`),
          h("td", { class: "num muted" }, `${m.hands}`),
          h("td", { class: "num muted" }, `${m.faults_a || 0}/${m.faults_b || 0}`),
          h("td", { class: "muted small nowrap" }, fmtTime(m.finished_at)),
        ),
      ));
      count.textContent = `${list.length} matches${list.length > shown.length ? ` (showing first ${shown.length}; use the filter)` : ""}`;
    };
    filter.addEventListener("input", draw);
    draw();
    page.append(
      h("div", { class: "controls" },
        filter, count,
        h("a", { href: "/data/our-tournament.json", download: "our-tournament.json", class: "small" }, "Download JSON"),
      ),
      h("div", { class: "card" },
        h("div", { class: "tablewrap" },
          h("table", { class: "tbl" },
            h("thead", {}, h("tr", {},
              h("th", {}, "Match"), h("th", {}, "Result"), h("th", { class: "num" }, "bb/100"),
              h("th", { class: "num" }, "95% ±"), h("th", { class: "num" }, "Hands"),
              h("th", { class: "num" }, "Faults"), h("th", {}, "When"),
            )),
            tbody,
          ),
        ),
      ),
    );
  }).catch((error) => {
    page.append(h("div", { class: "card error" }, `Could not load matches: ${error.message}`));
  });
  return page;
}

function leaderboardTable(teams, samplesFor, availableSet = null, repoMap = null) {
  const rows = teams.map((t) => {
    const std = stdFromCi(t.ci95, samplesFor(t));
    const isAvailable = availableSet ? availableSet.has(t.team) : null;
    const repo = repoMap ? repoMap[t.team] : null;
    const teamCell = repo
      ? h("a", { href: repo, target: "_blank", rel: "noopener" }, t.team)
      : h("a", { href: `#/team/${encodeURIComponent(t.team)}` }, t.team);
    return h("tr", {},
      h("td", { class: "num" }, t.rank == null ? "—" : String(t.rank)),
      h("td", {}, teamCell,
        isAvailable === true ? h("span", { class: "badge green" }, " ✓") : isAvailable === false ? h("span", { class: "badge red" }, " ✗") : null),
      h("td", { class: `num ${t.score > 0 ? "pos" : t.score < 0 ? "neg" : ""}` }, fmtBB(t.score)),
      h("td", { class: "num muted" }, t.ci95 == null ? "—" : `± ${t.ci95.toFixed(1)}`),
      h("td", { class: "num muted" }, std == null ? "—" : std.toFixed(1)),
      h("td", { class: "num" }, `${t.wins ?? 0}–${t.draws ?? 0}–${t.losses ?? 0}`),
      h("td", { class: "num muted" }, String(t.faults ?? 0)),
    );
  });
  return h("div", { class: "card tablewrap" },
    h("table", { class: "tbl" },
      h("thead", {}, h("tr", {},
        h("th", { class: "num" }, "#"), h("th", {}, "Team"),
        h("th", { class: "num" }, "bb/100"), h("th", { class: "num" }, "95% ±"),
        h("th", { class: "num" }, "Std"), h("th", { class: "num" }, "W–D–L"),
        h("th", { class: "num" }, "Faults"),
      )),
      h("tbody", {}, rows),
    ),
  );
}

function teamKey(name) {
  return String(name || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function teamsView() {
  const page = h("div", { class: "page" });
  page.append(h("h1", {}, "Teams"));
  loadTournament().then(async (data) => {
    rememberTeams(data);
    const available = new Set(data.availableTeams || []);
    const repoMap = {};
    for (const r of (data.teamsData || [])) {
      if (r.github_repo) repoMap[r.team] = r.github_repo;
    }
    const myBoard = data.tournament?.groups?.[0]?.board;
    const myTeams = myBoard?.teams || [];

    // The floating Continue button belongs to the playable-bot picker.
    const selected = new Set(readChosenBots() || PLAYABLE_KEYS);
    const floatingBtn = h("button", { class: "floating-btn", onclick: () => {
      const chosen = [...selected].slice(0, MAX_SELECTED);
      localStorage.setItem("pokerArenaBots", JSON.stringify(chosen));
      selectedBots = new Set(chosen);
      // Coming from the Teams page starts a fresh table with the new picks.
      localGame = null;
      sessionId = null;
      gameState = null;
      localStorage.removeItem("pokerArenaSession");
      location.hash = "#/play";
    } });
    const toggle = (key, checked, event) => {
      if (checked) {
        if (selected.size >= MAX_SELECTED) {
          if (event?.target) event.target.checked = false;
          return;
        }
        selected.add(key);
      } else {
        selected.delete(key);
      }
      updateFloating();
    };
    const updateFloating = () => {
      floatingBtn.textContent = `Continue (${selected.size} selected)`;
      floatingBtn.disabled = selected.size === 0;
    };
    updateFloating();

    page.append(h("h2", {}, "Playable bots"));
    page.append(h("p", { class: "muted small" },
      "Pick up to six opponents. The five bots below run instantly; the competition bots are the real submitted Python models and load the Python runtime on first use."));
    if (myTeams.length) {
      const playableRows = myTeams.map((t) => {
        const key = teamKey(t.team);
        const checkbox = h("input", { type: "checkbox", checked: selected.has(key), onchange: (e) => toggle(key, e.target.checked, e) });
        return h("tr", {},
          h("td", {}, checkbox),
          h("td", {}, h("a", { href: `#/team/${encodeURIComponent(t.team)}` }, t.team)),
          h("td", {}, t.plan ? `Plan ${t.plan}` : "—"),
          h("td", { class: "num" }, String(t.tier ?? "—")),
          h("td", { class: `num ${t.score > 0 ? "pos" : t.score < 0 ? "neg" : ""}` }, fmtBB(t.score)),
          h("td", { class: "num muted" }, t.ci95 == null ? "—" : `± ${t.ci95.toFixed(1)}`),
        );
      });
      page.append(
        h("div", { class: "card tablewrap" },
          h("table", { class: "tbl" },
            h("thead", {}, h("tr", {},
              h("th", {}, "Pick"), h("th", {}, "Bot"), h("th", {}, "Plan"),
              h("th", { class: "num" }, "Tier"), h("th", { class: "num" }, "bb/100"),
              h("th", { class: "num" }, "95% ±"),
            )),
            h("tbody", {}, playableRows),
          ),
        ),
      );
    }

    const allTeams = data.allTeams?.teams || [];
    page.append(h("h2", {}, `All competition bots (${allTeams.length})`));
    page.append(h("p", { class: "muted small" },
      "Every team from the GreatUniHack 2026 poker bot competition. ✓ = model files downloaded and playable in the browser; ✗ = not found. Each team name links to the GitHub repository where its model was found."));
    if (allTeams.length) {
      const rows = allTeams.map((t) => {
        const std = stdFromCi(t.ci95, t.samples || 0);
        const isAvailable = available.has(t.team);
        const repo = repoMap[t.team];
        const teamCell = repo
          ? h("a", { href: repo, target: "_blank", rel: "noopener" }, t.team)
          : h("a", { href: `#/team/${encodeURIComponent(t.team)}` }, t.team);
        const checkbox = h("input", {
          type: "checkbox",
          checked: selected.has(t.team),
          disabled: !isAvailable,
          onchange: (e) => toggle(t.team, e.target.checked, e),
        });
        return h("tr", {},
          h("td", {}, checkbox),
          h("td", { class: "num" }, t.rank == null ? "—" : String(t.rank)),
          h("td", {}, teamCell,
            isAvailable ? h("span", { class: "badge green" }, " ✓") : h("span", { class: "badge red" }, " ✗")),
          h("td", { class: `num ${t.score > 0 ? "pos" : t.score < 0 ? "neg" : ""}` }, fmtBB(t.score)),
          h("td", { class: "num muted" }, t.ci95 == null ? "—" : `± ${t.ci95.toFixed(1)}`),
          h("td", { class: "num muted" }, std == null ? "—" : std.toFixed(1)),
          h("td", { class: "num" }, `${t.wins ?? 0}–${t.draws ?? 0}–${t.losses ?? 0}`),
          h("td", { class: "num muted" }, String(t.faults ?? 0)),
          h("td", {}, repo ? h("a", { href: repo, target: "_blank", rel: "noopener" }, "repo") : h("span", { class: "muted" }, "—")),
        );
      });
      page.append(
        h("div", { class: "card tablewrap" },
          h("table", { class: "tbl" },
            h("thead", {}, h("tr", {},
              h("th", {}, "Pick"), h("th", { class: "num" }, "#"), h("th", {}, "Team"),
              h("th", { class: "num" }, "bb/100"), h("th", { class: "num" }, "95% ±"),
              h("th", { class: "num" }, "Std"), h("th", { class: "num" }, "W–D–L"),
              h("th", { class: "num" }, "Faults"), h("th", {}, "Model"),
            )),
            h("tbody", {}, rows),
          ),
        ),
      );
    }

    const uploaded = await api("/api/bots").catch(() => ({ bots: [] }));
    if (uploaded.bots?.length) {
      page.append(h("h2", {}, `Uploaded bots (${uploaded.bots.length})`));
      page.append(h("p", { class: "muted small" },
        "Bots uploaded by arena members. They run their Python in your browser, exactly like the competition models."));
      const rows = uploaded.bots.map((bot) => {
        const key = `user:${bot.id}`;
        knownTeamNames[key] = bot.name;
        return h("tr", {},
          h("td", {}, h("input", {
            type: "checkbox",
            checked: selected.has(key),
            onchange: (e) => toggle(key, e.target.checked, e),
          })),
          h("td", {}, bot.name),
          h("td", { class: "muted" }, bot.owner || "—"),
          h("td", {}, h("span", { class: `badge ${bot.division === "elite" ? "gold" : bot.division === "raised" ? "blue" : ""}` }, bot.division)),
          h("td", { class: "num" }, String(bot.rating)),
          h("td", { class: "num" }, `${bot.wins}–${bot.draws}–${bot.losses}`),
          h("td", {}, h("a", { href: `#/test/${bot.id}` }, "Test")),
        );
      });
      page.append(
        h("div", { class: "card tablewrap" },
          h("table", { class: "tbl" },
            h("thead", {}, h("tr", {},
              h("th", {}, "Pick"), h("th", {}, "Bot"), h("th", {}, "Owner"),
              h("th", {}, "Division"), h("th", { class: "num" }, "Rating"),
              h("th", { class: "num" }, "W–D–L"), h("th", {}, ""),
            )),
            h("tbody", {}, rows),
          ),
        ),
      );
    }

    document.body.append(floatingBtn);
    window.addEventListener("hashchange", () => floatingBtn.remove(), { once: true });
  }).catch((error) => {
    page.append(h("div", { class: "card error" }, `Could not load teams: ${error.message}`));
  });
  return page;
}

function teamView(name) {
  const page = h("div", { class: "page" });
  page.append(h("h1", {}, name));
  loadTournament().then((data) => {
    const allTeams = data.allTeams?.teams || [];
    const repoMap = {};
    for (const r of (data.teamsData || [])) {
      if (r.github_repo) repoMap[r.team] = r.github_repo;
    }
    const repo = repoMap[name] || repoMap[teamKey(name)];
    const wantedKey = teamKey(name);
    const team = (data.teams?.teams || []).find((t) => teamKey(t.team) === wantedKey)
      || allTeams.find((t) => teamKey(t.team) === wantedKey);
    if (team) {
      page.append(
        h("div", { class: "card teamcard" },
          h("p", {}, team.tagline || ""),
          h("p", { class: "muted" }, team.description || ""),
          repo ? h("p", {}, ["Model found in ", h("a", { href: repo, target: "_blank", rel: "noopener" }, repo.replace(/^https:\/\/github\.com\//, ""))]) : null,
          team.plan ? h("p", {}, [h("span", { class: "badge" }, `Plan ${team.plan}`), " ", h("span", { class: "badge" }, `Tier ${team.tier}`)]) : null,
        ),
      );
    }
    const board = data.tournament?.groups?.[0]?.board;
    const ourRow = (data.ourTournament?.teams || []).find((t) => t.team === name || teamKey(t.team) === wantedKey);
    const row = ourRow
      || board?.teams?.find((t) => teamKey(t.team) === wantedKey)
      || allTeams.find((t) => teamKey(t.team) === wantedKey);
    if (row) {
      const samples = row.samples || (row.hands ? row.hands / 2 : 0);
      const std = stdFromCi(row.ci95, samples);
      page.append(
        h("div", { class: "card" },
          h("h2", {}, "Standing"),
          h("dl", { class: "kv" },
            h("dt", {}, "Rank"), h("dd", {}, String(row.rank)),
            h("dt", {}, "Score"), h("dd", {}, `${fmtBB(row.score)} bb/100 (± ${row.ci95?.toFixed(1) ?? "—"})`),
            h("dt", {}, "Std dev"), h("dd", {}, std == null ? "—" : std.toFixed(1)),
            h("dt", {}, "Record"), h("dd", {}, `${row.wins}–${row.draws}–${row.losses}`),
            h("dt", {}, "Opponents"), h("dd", {}, String(row.opponents_played)),
          ),
        ),
      );
    }
    const matches = (data.matches || []).filter((m) => teamKey(m.a) === wantedKey || teamKey(m.b) === wantedKey);
    if (matches.length) {
      const rows = matches.map((m) =>
        h("tr", {},
          h("td", {}, `${m.a} vs ${m.b}`),
          h("td", {}, verdictText(m)),
          h("td", { class: "num" }, fmtBB(m.mean_bb)),
          h("td", { class: "muted small" }, fmtTime(m.finished_at)),
        ),
      );
      page.append(
        h("div", { class: "card" },
          h("h2", {}, "Matches"),
          h("div", { class: "tablewrap" },
            h("table", { class: "tbl" },
              h("thead", {}, h("tr", {}, h("th", {}, "Match"), h("th", {}, "Result"), h("th", { class: "num" }, "bb/100"), h("th", {}, "When"))),
              h("tbody", {}, rows),
            ),
          ),
        ),
      );
    }
  }).catch((error) => {
    page.append(h("div", { class: "card error" }, `Could not load the team: ${error.message}`));
  });
  return page;
}

function aboutView() {
  return h("div", { class: "page" },
    h("h1", {}, "About Poker Arena"),
    h("div", { class: "card" },
      h("p", {}, "Poker Arena is a free, no-stakes arena for developer-built poker bots. Upload a Python model, challenge the house bots, enter tournaments and climb the leaderboard. You can also sit down yourself in any of the five game modes."),
      h("h2", {}, "Five formats"),
      h("ul", { class: "prose" },
        h("li", {}, h("a", { href: "#/games/nlh6" }, "No-Limit Hold'em 6-max"), " — the main event and the format behind the 1,891-match round robin."),
        h("li", {}, h("a", { href: "#/games/hu" }, "No-Limit Hold'em heads-up"), " — one opponent, wide ranges, rated challenges."),
        h("li", {}, h("a", { href: "#/games/boss" }, "Big Bad Bots"), " — a three-boss gauntlet with bigger stacks and rising blinds."),
        h("li", {}, h("a", { href: "#/games/draw27" }, "2-7 Triple Draw"), " — fixed-limit lowball with three draws."),
        h("li", {}, h("a", { href: "#/games/ofc" }, "Pineapple OFC"), " — thirteen cards, three rows, fouls and royalties."),
      ),
      h("h2", {}, "Uploading a bot"),
      h("p", {}, "An uploaded bot is a Python file defining class Model with seed, run and end_game. It runs in the browser through Pyodide, against the same engine shim as the 57 competition models. Nothing is executed on the server. Uploaded bots are public so anyone can challenge them."),
      h("p", {}, "House bots keep their original submitted Python. The 57 competition teams were recovered from forks of Manchester-AI-Society/poker_bot; the Teams page links each one to the GitHub repository where its model was found."),
    ),
    h("div", { class: "card" },
      h("h2", {}, "API"),
      h("dl", { class: "kv" },
        h("dt", {}, "POST /api/auth/signup"), h("dd", {}, "Body: { email, handle, password }. Sets an HttpOnly session cookie."),
        h("dt", {}, "POST /api/auth/login"), h("dd", {}, "Body: { email, password }."),
        h("dt", {}, "GET /api/auth/me"), h("dd", {}, "The current user and their bots."),
        h("dt", {}, "POST /api/bots"), h("dd", {}, "Upload a bot. Body: { name, description, mode, source, extras, pickle, pklName }."),
        h("dt", {}, "GET /api/bots/:id/files"), h("dd", {}, "model.py, extra modules and the optional pickle, as JSON."),
        h("dt", {}, "POST /api/bots/:id/result"), h("dd", {}, "Record a rated challenge and update the Elo rating."),
        h("dt", {}, "GET /api/leaderboard?division="), h("dd", {}, "House and uploaded bots, with divisions and ratings."),
        h("dt", {}, "GET /api/tournaments"), h("dd", {}, "Scheduled events, results and open entries."),
        h("dt", {}, "GET /api/forum/threads"), h("dd", {}, "Forum threads; POST to create one."),
        h("dt", {}, "POST /api/newsletter"), h("dd", {}, "Body: { email }."),
        h("dt", {}, "GET /api/live"), h("dd", {}, "The rolling live-match feed."),
        h("dt", {}, "POST /api/session"), h("dd", {}, "Start a server-side table with the five JavaScript bots."),
        h("dt", {}, "GET /api/tournament"), h("dd", {}, "The competition round robin, matrix, matches and team metadata."),
        h("dt", {}, "GET /api/teams.csv"), h("dd", {}, "Teams with the GitHub repositories where their models were found."),
      ),
    ),
    h("div", { class: "card" },
      h("h2", {}, "Verification"),
      h("pre", {}, [
        "npm run verify          # engine invariants\n",
        "npm run verify:python   # real Python engine, 50 hands\n",
        "npm run verify:js       # JavaScript port, 50 hands\n",
        "npm run test:modes      # 2-7 draw and OFC simulations\n",
      ]),
      h("p", { class: "muted small" }, "The two engine dumps match action for action. The platform UI tests drive a real headless browser against the deployed site."),
    ),
  );
}

// ---------------------------------------------------------------- router

const ROUTES = {
  play: playView,
  teams: teamsView,
  matrix: matrixView,
  matches: matchesView,
  about: aboutView,
  games: gamesView,
  leaderboard: leaderboardView,
  tournaments: tournamentsView,
  live: liveView,
  forum: forumView,
  stack: stackView,
  newsletter: newsletterView,
  rules: rulesView,
  login: loginView,
  signup: signupView,
  account: accountView,
  upload: uploadView,
};

let renderSeq = 0;

function updateNavAccount() {
  const el = document.getElementById("nav-account");
  if (!el) return;
  el.textContent = auth.me ? `@${auth.me.handle}` : "Log in";
  el.href = auth.me ? "#/account" : "#/login";
  el.title = auth.me ? "Your account and bots" : "Log in or create an account";
}

async function render() {
  const token = ++renderSeq;
  await ensureAuth();
  if (token !== renderSeq) return;
  updateNavAccount();

  const { path, query } = parseHash();
  const key = path[0] || "";
  let view;
  if (key === "team" && path[1]) {
    view = await teamView(decodeURIComponent(path[1]));
  } else if (key === "games" && path[1]) {
    view = await gameDetailView(decodeURIComponent(path[1]));
  } else if (key === "play") {
    view = playRouterView(query);
  } else if (key === "tournaments" && path[1]) {
    view = await tournamentView(decodeURIComponent(path[1]));
  } else if (key === "forum" && path[1] === "new") {
    view = await newThreadView();
  } else if (key === "forum" && path[1]) {
    view = await threadView(decodeURIComponent(path[1]));
  } else if (key === "stack" && path[1]) {
    view = await articleView(decodeURIComponent(path[1]));
  } else if (key === "season" && path[1]) {
    view = await seasonView(path[1]);
  } else if (key === "test" && path[1]) {
    view = await botTestView(decodeURIComponent(path[1]));
  } else if (key === "leaderboard") {
    view = await leaderboardView(query);
  } else if (key === "forum") {
    view = await forumView(query);
  } else {
    const fn = ROUTES[key] || playView;
    view = await fn(key === "play" ? query : undefined);
  }
  if (token !== renderSeq) return;

  main.replaceChildren(view);
  const activeKey = key === "team" ? "teams" : key;
  for (const link of document.querySelectorAll(".tabs a")) {
    const route = link.getAttribute("data-route");
    const active = route === (activeKey || "play");
    if (active) link.classList.add("active");
    else link.classList.remove("active");
  }
}

window.addEventListener("hashchange", render);
window.addEventListener("arena:refresh", render);
render();
