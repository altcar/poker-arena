// Poker Arena front end: a hash-routed single-page app.
//   #/play       the 6-player table (you + five bots)
//   #/tournament standings and champion
//   #/teams      the bots and their styles
//   #/matrix     head-to-head matrix
//   #/matches    every match
//   #/team/:name one team's detail
//   #/about      what this is and the API

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

// Only these five bots exist in the JavaScript engine. The competition teams
// are Python pickles, so they can be downloaded and played in the offline
// tournament, but not run in the browser.
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

function readChosenBots() {
  try {
    const raw = JSON.parse(localStorage.getItem("pokerArenaBots") || "null");
    if (!Array.isArray(raw)) return null;
    return raw.filter((key) => PLAYABLE_SET.has(key));
  } catch {
    return null;
  }
}

let selectedBots = new Set(readChosenBots() || PLAYABLE_KEYS);
let humanPlaying = true;

async function startSession(name, botKeys, playing = humanPlaying) {
  const keys = [...new Set((botKeys || []).filter((key) => PLAYABLE_SET.has(key)))];
  if (!keys.length) keys.push(...PLAYABLE_KEYS);
  humanPlaying = playing !== false;
  try {
    const data = await api("/api/session", {
      method: "POST",
      body: JSON.stringify({
        humanName: humanPlaying ? (name || "You") : "Spectator",
        botKeys: keys,
        humanPlaying,
      }),
    });
    sessionId = data.sessionId;
    gameState = data.state;
    localStorage.setItem("pokerArenaSession", sessionId);
    render();
  } catch (error) {
    alert(`Could not start the table: ${error.message}`);
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

async function act(kind, amount = 0) {
  const data = await api(`/api/session/${sessionId}/action`, {
    method: "POST",
    body: JSON.stringify({ kind, amount }),
  });
  gameState = data.state;
  render();
}

async function nextHand() {
  const data = await api(`/api/session/${sessionId}/next`, { method: "POST" });
  gameState = data.state;
  render();
}

async function rebuy() {
  const data = await api(`/api/session/${sessionId}/rebuy`, { method: "POST" });
  gameState = data.state;
  render();
}

function newSession() {
  sessionId = null;
  gameState = null;
  localStorage.removeItem("pokerArenaSession");
  render();
}

async function startFreshGame() {
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
    const names = chosen.map((key) => PLAYABLE_NAMES[key] || key);
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
      h("p", { class: "muted small" }, "Your session is kept in the worker's database, so you can close the tab and come back."),
    );
  }

  return h("div", { class: "card" },
    h("h2", {}, "Pick your opponents"),
    h("p", {}, "First choose up to six bots on the Teams page, then press Continue. The five playable bots are the ones that run in your browser."),
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
  page.append(h("h1", {}, "Play the bots"));

  if (!sessionId || !gameState) {
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
      h("button", { class: "primary", onclick: startFreshGame }, "Start a new game"),
      h("button", { class: "ghost", onclick: () => { location.hash = "#/teams"; } }, "Change opponents"),
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
          h("button", { class: "primary", onclick: nextHand }, "Deal next hand"),
          state.humanBusted
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
  loadTournament().then((data) => {
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
      sessionId = null;
      gameState = null;
      localStorage.removeItem("pokerArenaSession");
      location.hash = "#/play";
    } });
    const updateFloating = () => {
      floatingBtn.textContent = `Continue (${selected.size} selected)`;
      floatingBtn.disabled = selected.size === 0;
    };
    updateFloating();

    page.append(h("h2", {}, "Playable bots"));
    page.append(h("p", { class: "muted small" },
      "Pick up to six opponents for your table. These five bots run in the browser; the competition teams below are Python models that run in the offline tournament."));
    if (myTeams.length) {
      const playableRows = myTeams.map((t) => {
        const key = teamKey(t.team);
        const checkbox = h("input", { type: "checkbox", checked: selected.has(key), onchange: (e) => {
          if (e.target.checked) {
            if (selected.size >= MAX_SELECTED) {
              e.target.checked = false;
              return;
            }
            selected.add(key);
          } else {
            selected.delete(key);
          }
          updateFloating();
        } });
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
      "Every team from the GreatUniHack 2026 poker bot competition. ✓ = model files downloaded, ✗ = not found yet. Each team name links to the GitHub repository where its model was found."));
    if (allTeams.length) {
      const rows = allTeams.map((t) => {
        const std = stdFromCi(t.ci95, t.samples || 0);
        const isAvailable = available.has(t.team);
        const repo = repoMap[t.team];
        const teamCell = repo
          ? h("a", { href: repo, target: "_blank", rel: "noopener" }, t.team)
          : h("a", { href: `#/team/${encodeURIComponent(t.team)}` }, t.team);
        return h("tr", {},
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
              h("th", { class: "num" }, "#"), h("th", {}, "Team"),
              h("th", { class: "num" }, "bb/100"), h("th", { class: "num" }, "95% ±"),
              h("th", { class: "num" }, "Std"), h("th", { class: "num" }, "W–D–L"),
              h("th", { class: "num" }, "Faults"), h("th", {}, "Model"),
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
      h("p", {}, "This is a playable version of the poker bot competition. You sit at a six-handed table with the five bots from the altcar/poker_bot fork, renamed by style: Conservative, Workhorse, Adaptive, Mixed and Aggressive."),
      h("p", {}, "The Teams page lists every competition team. Each name links to the GitHub repository where the team's model.py and pickle were found, and the files are copied into all-teams/. Those competition bots are Python models: they play the offline round robin on the Head-to-head and Matches pages, while the five fork bots are the ones that run live in your browser."),
      h("p", {}, "The engine and the bots are a line-for-line JavaScript port of the Python code in the fork. The port is verified hand-for-hand against the real Python engine: the Monte-Carlo draws use Python's own MT19937 and random.sample, so a given seed and deck produce the same actions."),
      h("p", {}, "The original Python files are in python/ (including the submitted goldman-stanley.pkl). The tournament data is precomputed with the Python engine."),
    ),
    h("div", { class: "card" },
      h("h2", {}, "API"),
      h("dl", { class: "kv" },
        h("dt", {}, "POST /api/session"), h("dd", {}, "Start a table. Body: { humanName, botKeys, humanPlaying, stack, smallBlind, bigBlind, seed }."),
        h("dt", {}, "GET /api/session/:id"), h("dd", {}, "Current state."),
        h("dt", {}, "POST /api/session/:id/action"), h("dd", {}, 'Body: { kind: "fold" | "call" | "raise", amount }.'),
        h("dt", {}, "POST /api/session/:id/next"), h("dd", {}, "Deal the next hand."),
        h("dt", {}, "POST /api/decision"), h("dd", {}, "Ask one bot for its move. Body: { bot, hole, playerId, state, actions }."),
        h("dt", {}, "GET /api/tournament"), h("dd", {}, "Our round robin (standings, matrix, matches), the official competition data, and team metadata."),
        h("dt", {}, "GET /api/teams.csv"), h("dd", {}, "The teams with the GitHub repositories where their models were found."),
      ),
    ),
    h("div", { class: "card" },
      h("h2", {}, "Verification"),
      h("pre", {}, [
        "npm run verify:python   # real Python engine, 50 hands\n",
        "npm run verify:js       # JavaScript port, 50 hands\n",
        "npm run verify          # engine invariants\n",
      ]),
      h("p", { class: "muted small" }, "The two dumps match action for action."),
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
};

async function render() {
  const { path } = parseHash();
  const key = path[0] || "";
  let view;
  if (key === "team" && path[1]) {
    view = teamView(decodeURIComponent(path[1]));
  } else {
    const fn = ROUTES[key] || playView;
    view = fn();
  }
  main.replaceChildren(view);
  for (const link of document.querySelectorAll(".tabs a")) {
    const route = link.getAttribute("data-route");
    const active = route === (key || "play") || (key === "team" && route === "teams");
    if (active) link.classList.add("active");
    else link.classList.remove("active");
  }
}

window.addEventListener("hashchange", render);
render();
