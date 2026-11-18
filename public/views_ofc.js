// Pineapple OFC table: place cards into the three rows, then score the boards.

import { OfcGame, handName, rank3, rank5, royalties } from "./engine/ofc.js";

const SUITS = { H: "♥", S: "♠", D: "♦", C: "♣" };
const ROWS = [
  ["top", "Top", 3],
  ["middle", "Middle", 5],
  ["bottom", "Bottom", 5],
];

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === "class") el.className = value;
      else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
      else el.setAttribute(key, value);
    }
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function cardFace(card, options = {}) {
  if (!card) {
    return h("span", { class: `cardface ofc-card${options.slot ? " slot" : ""}${options.pickable ? " pickable" : ""}` },
      options.slot ? "+" : "");
  }
  const suit = card[1];
  const red = suit === "H" || suit === "D";
  return h("span", {
    class: `cardface ofc-card${red ? " red" : ""}${options.selected ? " selected" : ""}${options.pickable ? " pickable" : ""}`,
  }, h("span", {}, card[0]), h("span", {}, SUITS[suit] || suit));
}

const STREET_NAMES = ["First five", "Street 2", "Street 3", "Street 4", "Street 5"];

function boardView(board, label, interactive, onSlot, state) {
  return h("div", { class: "ofc-board" },
    h("div", { class: "ofc-board-label" }, label),
    ...ROWS.map(([row, name, cap]) => {
      const cards = board[row];
      const ranks = row === "top" ? rank3(cards) : rank5(cards);
      return h("div", { class: `ofc-row ${row}` },
        h("div", { class: "ofc-row-name" }, name),
        h("div", { class: "ofc-row-cards" },
          ...cards.map((card) => cardFace(card)),
          ...Array.from({ length: Math.max(0, cap - cards.length) }, (_, i) =>
            cardFace(null, {
              slot: true,
              pickable: interactive,
              onclick: undefined,
            })
          ).map((el) => {
            if (!interactive) return el;
            el.addEventListener("click", () => onSlot(row));
            return el;
          }),
        ),
        h("div", { class: "ofc-row-rank muted small" }, ranks ? handName(ranks, row) : ""),
      );
    }),
  );
}

export function ofcView() {
  const page = h("div", { class: "page" });
  page.append(
    h("div", { class: "page-head" },
      h("div", {},
        h("h1", {}, "Pineapple OFC"),
        h("p", { class: "muted" }, "No betting · build three rows · bottom ≥ middle ≥ top"),
      ),
      h("div", { class: "actions-inline" },
        h("a", { class: "btn ghost", href: "#/games/ofc" }, "Rules"),
        h("button", { class: "ghost", onclick: () => { location.hash = "#/games"; } }, "All games"),
      ),
    ),
  );

  const root = h("div", {});
  page.append(root);

  let game = new OfcGame({
    humanName: localStorage.getItem("pokerArenaName") || "You",
    botName: "Pineapple",
    seed: Date.now() % 1000000,
  });
  let state = game.startHand();
  let selected = null;

  function draw() {
    state = game.publicState();
    const hand = state.hand;
    root.replaceChildren();
    if (!hand) return;
    const humanTurn = state.waitingFor === "human";

    root.append(
      h("div", { class: "ofc-scoreline" },
        h("span", { class: "badge" }, `You ${state.scores[0] >= 0 ? "+" : ""}${state.scores[0]}`),
        h("span", { class: "badge" }, `${state.names[1]} ${state.scores[1] >= 0 ? "+" : ""}${state.scores[1]}`),
        h("span", { class: "muted small" }, `Hand ${state.handNumber} · ${STREET_NAMES[hand.street]} · button ${state.button === 0 ? "you" : state.names[1]}`),
        hand.discards[0] || hand.discards[1]
          ? h("span", { class: "muted small" }, `discards ${hand.discards[0]} / ${hand.discards[1]}`)
          : null,
      ),
      h("div", { class: "ofc-grid" },
        h("div", {},
          h("h3", {}, state.names[1]),
          boardView(hand.boards[1], "Open board", false, () => {}, state),
        ),
        h("div", {},
          h("h3", {}, "You"),
          boardView(hand.boards[0], "Your board", humanTurn, (row) => place(row), state),
        ),
      ),
    );

    if (hand.phase === "place") {
      const handCards = hand.hands[0];
      root.append(
        h("div", { class: "card" },
          h("div", { class: "ofc-handline" },
            h("strong", {}, humanTurn ? `Place ${handCards.length} card${handCards.length === 1 ? "" : "s"}` : "Waiting…"),
            h("span", { class: "muted small" }, "Pick a card, then click an empty slot. The last card is discarded automatically."),
          ),
          h("div", { class: "ofc-hand" },
            ...handCards.map((card, index) =>
              (() => {
                const el = cardFace(card, { pickable: humanTurn, selected: selected === index });
                if (humanTurn) el.addEventListener("click", () => {
                  selected = selected === index ? null : index;
                  draw();
                });
                return el;
              })()
            ),
          ),
        ),
      );
    }

    if (hand.result) {
      const r = hand.result;
      const you = r.points[0];
      const rows = ROWS.map(([row, name]) =>
        h("tr", {},
          h("td", {}, name),
          h("td", {}, r.names[0][row], r.fouls[0] ? h("span", { class: "badge red" }, " foul") : null),
          h("td", {}, r.names[1][row], r.fouls[1] ? h("span", { class: "badge red" }, " foul") : null),
          h("td", { class: "num" }, r.rows.find((x) => x.row === row).winner === 0 ? "+1" : r.rows.find((x) => x.row === row).winner === 1 ? "−1" : "0"),
        ),
      );
      root.append(
        h("div", { class: "card" },
          h("h2", {}, r.fouls[0] && r.fouls[1] ? "Both boards foul" : r.fouls[0] ? "Your board fouled" : r.fouls[1] ? `${state.names[1]} fouled` : you > 0 ? "You win the hand" : you < 0 ? `${state.names[1]} wins the hand` : "Hand tied"),
          h("p", {}, `Points: ${r.points[0] >= 0 ? "+" : ""}${r.points[0]} · royalties ${r.royalties[0]} vs ${r.royalties[1]}`),
          h("div", { class: "tablewrap" },
            h("table", { class: "tbl" },
              h("thead", {}, h("tr", {}, h("th", {}, "Row"), h("th", {}, "You"), h("th", {}, state.names[1]), h("th", { class: "num" }, "Row"))),
              h("tbody", {}, rows),
            ),
          ),
          h("div", { class: "actions" },
            h("button", { class: "primary", onclick: () => { state = game.startHand(); selected = null; draw(); } }, "Next hand"),
            h("button", { class: "ghost", onclick: reset }, "New match"),
          ),
        ),
      );
    }

    if (hand.log.length) {
      root.append(
        h("details", { class: "card" },
          h("summary", {}, `Placement log (${hand.log.length})`),
          h("div", { class: "log" }, hand.log.slice(-40).map((line) => h("div", {}, line))),
        ),
      );
    }
  }

  function place(row) {
    if (selected == null) return;
    state = game.placeHuman(selected, row);
    selected = null;
    draw();
  }

  function reset() {
    game = new OfcGame({
      humanName: localStorage.getItem("pokerArenaName") || "You",
      botName: "Pineapple",
      seed: Date.now() % 1000000,
    });
    state = game.startHand();
    selected = null;
    draw();
  }

  draw();
  return page;
}
