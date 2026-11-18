// 2-7 Triple Draw table. A standalone view rendered by the router.

import { Draw27Game, describe27 } from "./engine/draw27.js";

const SUITS = { H: "♥", S: "♠", D: "♦", C: "♣" };

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

function cardFace(card, selected = false, hidden = false) {
  if (hidden || !card) return h("span", { class: "cardface back draw-card" });
  const suit = card[1];
  const red = suit === "H" || suit === "D";
  return h(
    "span",
    { class: `cardface draw-card${red ? " red" : ""}${selected ? " selected" : ""}` },
    h("span", {}, card[0]),
    h("span", {}, SUITS[suit] || suit)
  );
}

const STREETS = ["Predraw", "After draw 1", "After draw 2", "After draw 3"];
const fmt = (n) => Number(n || 0).toLocaleString("en-GB");

export function draw27View() {
  const page = h("div", { class: "page" });
  page.append(
    h("div", { class: "page-head" },
      h("div", {},
        h("h1", {}, "2-7 Triple Draw"),
        h("p", { class: "muted" }, "Fixed limit · heads-up · lowest hand wins · three draws"),
      ),
      h("div", { class: "actions-inline" },
        h("a", { class: "btn ghost", href: "#/games/draw27" }, "Rules"),
        h("button", { class: "ghost", onclick: () => { location.hash = "#/games"; } }, "All games"),
      ),
    ),
  );

  const board = h("div", { class: "draw-layout" });
  page.append(board);

  let game = new Draw27Game({
    humanName: localStorage.getItem("pokerArenaName") || "You",
    botName: "Deuce",
    stack: 500,
    smallBlind: 10,
    bigBlind: 20,
    seed: Date.now() % 1000000,
  });
  let state = game.startHand();
  let selected = new Set();
  let version = 0;

  function act(kind) {
    state = game.humanAction(kind);
    selected.clear();
    draw();
  }

  function doDraw() {
    state = game.humanDraw([...selected]);
    selected.clear();
    draw();
  }

  function nextHand() {
    if (game.stacks[0] === 0 || game.stacks[1] === 0) {
      game = new Draw27Game({
        humanName: localStorage.getItem("pokerArenaName") || "You",
        botName: "Deuce",
        stack: 500,
        smallBlind: 10,
        bigBlind: 20,
        seed: Date.now() % 1000000,
      });
    }
    state = game.startHand();
    selected.clear();
    draw();
  }

  function reset() {
    game = new Draw27Game({
      humanName: localStorage.getItem("pokerArenaName") || "You",
      botName: "Deuce",
      stack: 500,
      smallBlind: 10,
      bigBlind: 20,
      seed: Date.now() % 1000000,
    });
    state = game.startHand();
    selected.clear();
    draw();
  }

  function seat(player) {
    const hand = state.hand;
    const isHuman = player === 0;
    const cards = isHuman ? hand.humanCards : hand.botCards;
    const result = hand.result;
    const canPick = isHuman && state.waitingFor === "draw";
    const row = cards.map((card, index) => {
      const face = cardFace(card, selected.has(index), !isHuman && !result);
      if (canPick) {
        face.classList.add("pickable");
        face.addEventListener("click", () => {
          if (selected.has(index)) selected.delete(index);
          else selected.add(index);
          draw();
        });
      }
      return face;
    });
    const label = isHuman
      ? describe27(hand.humanCards)
      : result
        ? result.names[1]
        : hand.drawCounts[1]
          ? `${hand.drawCounts[1]} drawn`
          : "waiting";
    return h("div", { class: `draw-seat${isHuman ? " you" : ""}` },
      h("div", { class: "draw-seat-head" },
        h("strong", {}, state.names[player]),
        h("span", { class: "muted small" }, `Stack ${fmt(state.stacks[player])}`),
        hand.bets[player] ? h("span", { class: "badge gold" }, `Bet ${fmt(hand.bets[player])}`) : null,
      ),
      h("div", { class: "draw-hand" }, row),
      h("div", { class: "muted small" }, isHuman ? label : `${label} · ${hand.drawCounts[1]} cards drawn`),
      hand.folded[player] ? h("div", { class: "badge red" }, "folded") : null,
    );
  }

  function draw() {
    version += 1;
    const session = version;
    const hand = state.hand;
    board.replaceChildren();
    if (!hand) return;

    board.append(
      h("div", { class: "draw-table" },
        h("div", { class: "draw-center" },
          h("div", { class: "pot" }, `Pot ${fmt(hand.pot)}`),
          h("div", { class: "muted small" }, `${STREETS[hand.street] || "Showdown"} · bet ${hand.betSize}`),
        ),
        seat(1),
        h("div", { class: "draw-divider" }, "vs"),
        seat(0),
      ),
    );

    const actions = h("div", { class: "actions" });
    if (state.waitingFor === "human") {
      const toCall = hand.toCall;
      actions.append(
        h("button", { class: "danger", onclick: () => act("fold") }, "Fold"),
        h("button", { class: "primary", onclick: () => act("call") }, toCall ? `Call ${fmt(toCall)}` : "Check"),
        hand.canRaise ? h("button", { onclick: () => act("raise") }, `Raise to ${fmt(hand.bets[0] + toCall + hand.betSize)}`) : null,
      );
    } else if (state.waitingFor === "draw") {
      actions.append(
        h("button", { class: "primary", onclick: doDraw }, selected.size ? `Draw ${selected.size}` : "Stand pat"),
        h("button", { class: "ghost", onclick: () => { selected.clear(); draw(); } }, "Clear selection"),
      );
      actions.append(h("span", { class: "muted small" }, "Click cards to discard them. Your opponent sees how many, not which."));
    } else if (state.waitingFor === "over") {
      actions.append(h("button", { class: "primary", onclick: nextHand }, "Deal next hand"));
      actions.append(h("button", { class: "ghost", onclick: reset }, "New match"));
    }
    board.append(actions);

    if (hand.result) {
      const r = hand.result;
      const youWon = r.winner === 0;
      board.append(
        h("div", { class: "card result-card" },
          h("h2", {}, r.winner === -1 ? "Split pot" : youWon ? "You win the pot" : `${state.names[1]} wins the pot`),
          h("p", {},
            `${state.names[0]}: ${r.names[0]} · ${state.names[1]}: ${r.names[1]}`),
          h("p", { class: "muted" }, `Pot ${fmt(r.pot)} · net ${r.net[0] >= 0 ? "+" : ""}${fmt(r.net[0])} for you.`),
        ),
      );
    }

    if (hand.log.length) {
      board.append(
        h("details", { class: "card log-card" },
          h("summary", {}, `Hand log (${hand.log.length})`),
          h("div", { class: "log" }, hand.log.slice(-40).map((line) => h("div", {}, line))),
        ),
      );
    }
    if (session !== version) return;
  }

  draw();
  return page;
}
