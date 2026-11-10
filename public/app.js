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
let selectedBots = new Set(["conservative", "workhorse", "adaptive", "mixed", "aggressive"]);
let humanPlaying = true;
const BOT_KEYS = ["conservative", "workhorse", "adaptive", "mixed", "aggressive"];
const BOT_NAMES = {
  conservative: "Conservative",
  workhorse: "Workhorse",
  adaptive: "Adaptive",
  mixed: "Mixed",
  aggressive: "Aggressive",
};

async function startSession(name) {
  const data = await api("/api/session", {
    method: "POST",
    body: JSON.stringify({ humanName: humanPlaying ? (name || "You") : "Spectator", botKeys: [...selectedBots], humanPlaying }),
  });
  sessionId = data.sessionId;
  gameState = data.state;
  localStorage.setItem("pokerArenaSession", sessionId);
  render();
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

// ---------------------------------------------------------------- play view

function seatPositions(seat, count) {
  // Seat 0 (you) at the bottom; the bots spread around the oval.
  if (seat === 0) return [50, 92];
  const remaining = count - 1;
  const t = remaining === 1 ? 0.5 : (seat - 1) / (remaining - 1);
  const angle = ((-90 + t * 180) * Math.PI) / 180;
  return [50 + 42 * Math.cos(angle), 50 - 42 * Math.sin(angle)];
}

function playView() {
  const page = h("div", { class: "page" });
  page.append(h("h1", {}, "Play the bots"));

  if (!sessionId || !gameState) {
    const resume = h("div", { class: "card" }, "Loading your table — ");
    page.append(resume);
    resumeSession().then((ok) => {
      if (ok) {
        render();
      } else {
        const countEl = h("span", {}, "six");
        const humanCheck = h("label", { class: "botcheck" },
          h("input", { type: "checkbox", checked: true, onchange: (e) => {
            humanPlaying = e.target.checked;
            countEl.textContent = String((humanPlaying ? 1 : 0) + selectedBots.size);
          } }),
          " I'm playing",
        );
        const botChecks = BOT_KEYS.map((key) =>
          h("label", { class: "botcheck" },
            h("input", {
              type: "checkbox",
              checked: true,
              onchange: (e) => {
                if (e.target.checked) selectedBots.add(key);
                else selectedBots.delete(key);
                countEl.textContent = String((humanPlaying ? 1 : 0) + selectedBots.size);
              },
            }),
            ` ${BOT_NAMES[key]}`,
          ),
        );
        page.replaceChildren(
          h("div", { class: "card" }, h("h2", {}, "Take a seat"),
            h("p", {}, ["You are at a ", countEl, "-handed table. Choose the bots you want to play against."]),
            h("div", { class: "botchecks" }, humanCheck, botChecks),
            h("p", { class: "muted small" }, "Pick up to six bots. Seven players max. Uncheck \"I'm playing\" to watch the bots play each other."),
            h("div", { class: "actions" },
              h("input", { id: "name", type: "text", value: "You", maxlength: "20" }),
              h("button", { class: "primary", onclick: () => startSession(document.getElementById("name").value) }, "Start playing"),
            ),
            h("p", { class: "muted small" }, "Your session is kept in the worker's database, so you can close the tab and come back."),
          )
        );
      }
    });
    return page;
  }

  const state = gameState;
  const hand = state.hand;

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

function tournamentView() {
  const page = h("div", { class: "page" });
  page.append(h("h1", {}, "Tournament"));
  loadTournament().then((data) => {
    const board = data.tournament?.groups?.[0]?.board;
    const teams = board?.teams || [];
    const champion = data.tournament?.champion;
    page.append(
      h("div", { class: "card" },
        h("h2", {}, "Five-bot round robin"),
        h("p", {}, "Every bot plays every other heads-up, 100 duplicate deals with the seats swapped. Score is mean big blinds won per 100 hands, with the 95% interval."),
        champion ? h("p", {}, [h("span", { class: "badge gold" }, "Champion"), " ", h("strong", {}, champion)]) : null,
      ),
    );
    if (teams.length) {
      const rows = teams.map((t) =>
        h("tr", { class: t.team === "You" ? "you" : "" },
          h("td", { class: "num" }, String(t.rank)),
          h("td", {}, h("a", { href: `#/team/${encodeURIComponent(t.team)}` }, t.team)),
          h("td", { class: `num ${t.score > 0 ? "pos" : t.score < 0 ? "neg" : ""}` }, fmtBB(t.score)),
          h("td", { class: "num muted" }, t.ci95 == null ? "—" : `± ${t.ci95.toFixed(1)}`),
          h("td", { class: "num" }, `${t.wins}–${t.draws}–${t.losses}`),
          h("td", { class: "num muted" }, String(t.opponents_played)),
          h("td", { class: "num muted" }, String(t.faults)),
        ),
      );
      page.append(
        h("div", { class: "card" },
          h("div", { class: "tablewrap" },
            h("table", { class: "tbl" },
              h("thead", {}, h("tr", {},
                h("th", { class: "num" }, "#"), h("th", {}, "Team"),
                h("th", { class: "num" }, "bb/100"), h("th", { class: "num" }, "95% ±"),
                h("th", { class: "num" }, "W–D–L"), h("th", { class: "num" }, "Ops"), h("th", { class: "num" }, "Faults"),
              )),
              h("tbody", {}, rows),
            ),
          ),
        ),
      );
    }
    page.append(h("p", { class: "muted small" }, "The human player's live results are recorded separately on the Play page."));
  }).catch((error) => {
    page.append(h("div", { class: "card error" }, `Could not load the tournament: ${error.message}`));
  });
  return page;
}

function matrixView() {
  const page = h("div", { class: "page" });
  page.append(h("h1", {}, "Head-to-head"));
  loadTournament().then((data) => {
    const available = new Set(data.availableTeams || []);
    const allTeams = (data.allTeams?.teams || []).filter((t) => available.has(t.team));
    const allMatrix = data.allTeams?.matrix || {};
    if (allTeams.length) {
      const names = allTeams.map((t) => t.team).sort();
      const header = h("tr", {}, h("th", {}, "Team"), names.map((n) => h("th", {}, n)));
      const rows = names.map((me) =>
        h("tr", {},
          h("td", {}, h("a", { href: `#/team/${encodeURIComponent(me)}` }, me)),
          names.map((other) => {
            if (other === me) return h("td", { class: "diag" }, "—");
            const cell = allMatrix[me]?.[other];
            if (!cell) return h("td", { class: "muted" }, "—");
            const value = cell.mean_bb;
            return h("td", { class: `num ${value > 0 ? "pos" : value < 0 ? "neg" : ""}` },
              `${fmtBB(value)}`, h("br"), h("span", { class: "muted small" }, `± ${cell.ci95_bb == null ? "—" : cell.ci95_bb.toFixed(1)}`));
          }),
        ),
      );
      page.append(
        h("div", { class: "card" },
          h("p", {}, `Head-to-head for the ${names.length} available models (mean big blinds won per 100 hands by the row team against the column team).`),
          h("div", { class: "tablewrap" },
            h("table", { class: "tbl matrix" }, h("thead", {}, header), h("tbody", {}, rows)),
          ),
        ),
      );
    }
    const board = data.tournament?.groups?.[0]?.board;
    const teams = board?.teams || [];
    const matrix = board?.matrix || {};
    if (teams.length) {
      const names = teams.map((t) => t.team);
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
              `${fmtBB(value)}`, h("br"), h("span", { class: "muted small" }, `± ${cell.ci95_bb == null ? "—" : cell.ci95_bb.toFixed(1)}`));
          }),
        ),
      );
      page.append(
        h("div", { class: "card" },
          h("h2", {}, "My bots"),
          h("p", {}, "Head-to-head for the five fork bots."),
          h("div", { class: "tablewrap" },
            h("table", { class: "tbl matrix" }, h("thead", {}, header), h("tbody", {}, rows)),
          ),
        ),
      );
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
    const matches = data.matches || [];
    if (!matches.length) {
      page.append(h("div", { class: "card empty" }, "No matches yet."));
      return;
    }
    const rows = matches.map((m) =>
      h("tr", {},
        h("td", {}, `${m.a} vs ${m.b}`),
        h("td", {}, verdictText(m)),
        h("td", { class: `num ${m.mean_bb > 0 ? "pos" : m.mean_bb < 0 ? "neg" : ""}` }, fmtBB(m.mean_bb)),
        h("td", { class: "num muted" }, m.ci95_bb == null ? "—" : `± ${m.ci95_bb.toFixed(1)}`),
        h("td", { class: "num muted" }, `${m.hands}`),
        h("td", { class: "num muted" }, `${m.faults_a || 0}/${m.faults_b || 0}`),
        h("td", { class: "muted small nowrap" }, fmtTime(m.finished_at)),
      ),
    );
    page.append(
      h("div", { class: "card" },
        h("div", { class: "tablewrap" },
          h("table", { class: "tbl" },
            h("thead", {}, h("tr", {},
              h("th", {}, "Match"), h("th", {}, "Result"), h("th", { class: "num" }, "bb/100"),
              h("th", { class: "num" }, "95% ±"), h("th", { class: "num" }, "Hands"),
              h("th", { class: "num" }, "Faults"), h("th", {}, "When"),
            )),
            h("tbody", {}, rows),
          ),
        ),
      ),
    );
  }).catch((error) => {
    page.append(h("div", { class: "card error" }, `Could not load matches: ${error.message}`));
  });
  return page;
}

function leaderboardTable(teams, samplesFor, availableSet = null) {
  const rows = teams.map((t) => {
    const std = stdFromCi(t.ci95, samplesFor(t));
    const isAvailable = availableSet ? availableSet.has(t.team) : null;
    return h("tr", {},
      h("td", { class: "num" }, t.rank == null ? "—" : String(t.rank)),
      h("td", {}, h("a", { href: `#/team/${encodeURIComponent(t.team)}` }, t.team),
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

function teamsView() {
  const page = h("div", { class: "page" });
  page.append(h("h1", {}, "Teams"));
  loadTournament().then((data) => {
    const available = new Set(data.availableTeams || []);
    const myBoard = data.tournament?.groups?.[0]?.board;
    const myTeams = myBoard?.teams || [];
    page.append(h("h2", {}, "My bots"));
    page.append(h("p", { class: "muted small" }, "The five A-E models from the poker_bot fork, renamed by style. Score from the precomputed five-bot round robin."));
    if (myTeams.length) page.append(leaderboardTable(myTeams, (t) => (t.hands ? t.hands / 2 : 0), new Set(myTeams.map((t) => t.team))));
    const allTeams = data.allTeams?.teams || [];
    page.append(h("h2", {}, `All competition bots (${allTeams.length})`));
    page.append(h("p", { class: "muted small" }, "Every team from the GreatUniHack 2026 poker bot competition, Group A. Std is the standard deviation of the per-deal bb/100, recovered from the 95% interval. ✓ = model files downloaded, ✗ = not yet."));
    if (allTeams.length) page.append(leaderboardTable(allTeams, (t) => t.samples || 0, available));
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
    const team = (data.teams?.teams || []).find((t) => t.team === name)
      || allTeams.find((t) => t.team === name);
    if (team) {
      page.append(
        h("div", { class: "card teamcard" },
          h("p", {}, team.tagline || ""),
          h("p", { class: "muted" }, team.description || ""),
          team.plan ? h("p", {}, [h("span", { class: "badge" }, `Plan ${team.plan}`), " ", h("span", { class: "badge" }, `Tier ${team.tier}`)]) : null,
        ),
      );
    }
    const board = data.tournament?.groups?.[0]?.board;
    const row = board?.teams?.find((t) => t.team === name)
      || allTeams.find((t) => t.team === name);
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
    const matches = (data.matches || []).filter((m) => m.a === name || m.b === name);
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
      h("p", {}, "The engine and the bots are a line-for-line JavaScript port of the Python code in the fork. The port is verified hand-for-hand against the real Python engine: the Monte-Carlo draws use Python's own MT19937 and random.sample, so a given seed and deck produce the same actions."),
      h("p", {}, "The original Python files are in python/ (including the submitted goldman-stanley.pkl). The tournament data is precomputed with the Python engine."),
    ),
    h("div", { class: "card" },
      h("h2", {}, "API"),
      h("dl", { class: "kv" },
        h("dt", {}, "POST /api/session"), h("dd", {}, "Start a table. Body: { humanName, stack, smallBlind, bigBlind, seed }."),
        h("dt", {}, "GET /api/session/:id"), h("dd", {}, "Current state."),
        h("dt", {}, "POST /api/session/:id/action"), h("dd", {}, 'Body: { kind: "fold" | "call" | "raise", amount }.'),
        h("dt", {}, "POST /api/session/:id/next"), h("dd", {}, "Deal the next hand."),
        h("dt", {}, "POST /api/decision"), h("dd", {}, "Ask one bot for its move. Body: { bot, hole, playerId, state, actions }."),
        h("dt", {}, "GET /api/tournament"), h("dd", {}, "Standings, matrix, matches and team metadata."),
        h("dt", {}, "GET /api/teams.csv"), h("dd", {}, "The teams with their GitHub repositories."),
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
