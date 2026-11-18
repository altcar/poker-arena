import { Draw27Game, evaluate27, compare27, describe27 } from "../public/engine/draw27.js";
import { OfcGame } from "../public/engine/ofc.js";

// --- 2-7 ranking
const best = evaluate27(["7S", "5H", "4D", "3C", "2S"]);
const wheel = evaluate27(["AS", "2H", "3D", "4C", "5S"]);
const six = evaluate27(["6S", "5H", "4D", "3C", "2S"]);
const pair = evaluate27(["7S", "7H", "4D", "3C", "2S"]);
console.log("best", describe27(["7S","5H","4D","3C","2S"]), JSON.stringify(best));
console.log("wheel", describe27(["AS","2H","3D","4C","5S"]), JSON.stringify(wheel));
console.log("six low?", compare27(six, wheel) < 0, "pair worse than 9 low?", compare27(pair, evaluate27(["9S","8H","4D","3C","2S"])) > 0);

// --- play 12 draw hands with a simple human policy
let faults = 0;
for (let h = 1; h <= 12; h += 1) {
  const game = new Draw27Game({ seed: 1000 + h, stack: 500 });
  game.startHand();
  let guard = 0;
  while (!game.hand.result && guard++ < 200) {
    const s = game.publicState();
    if (s.waitingFor === "human") {
      const hand = s.hand;
      game.humanAction(hand.toCall > 0 && Math.random() < 0.25 ? "fold" : hand.canCheck ? "check" : "call");
    } else if (s.waitingFor === "draw") {
      const cards = s.hand.humanCards;
      const discards = cards.map((c, i) => ("23456789".includes(c[0]) ? -1 : i)).filter((i) => i >= 0);
      game.humanDraw(discards.slice(0, 4));
    } else if (s.waitingFor === "botdraw") {
      game.botDrawAndContinue();
    } else {
      faults += 1;
      break;
    }
  }
  if (!game.hand.result) { console.log("draw hand did not finish", h); faults += 1; continue; }
  const total = game.stacks[0] + game.stacks[1];
  if (total !== 1000) { console.log("chips not conserved", h, total); faults += 1; }
  if (h <= 3) console.log("draw hand", h, "result", game.hand.result.names, game.hand.result.net);
}
console.log("draw faults", faults);

// --- play 8 OFC hands with a simple legal placement policy
let ofcFaults = 0;
for (let h = 1; h <= 8; h += 1) {
  const game = new OfcGame({ seed: 2000 + h });
  game.startHand();
  let guard = 0;
  while (!game.hand.result && guard++ < 400) {
    const s = game.publicState();
    if (s.waitingFor !== "human") { ofcFaults += 1; break; }
    const board = s.hand.boards[0];
    const counts = { top: board.top.length, middle: board.middle.length, bottom: board.bottom.length };
    const row = counts.top < 3 ? "top" : counts.middle < 5 ? "middle" : "bottom";
    game.placeHuman(0, row);
  }
  const result = game.hand.result;
  if (!result) { console.log("ofc hand did not finish", h); ofcFaults += 1; continue; }
  const totalTop = result.boards[0].top.length;
  const totalMid = result.boards[0].middle.length;
  const totalBot = result.boards[0].bottom.length;
  if (totalTop !== 3 || totalMid !== 5 || totalBot !== 5) { console.log("bad board counts", h, totalTop, totalMid, totalBot); ofcFaults += 1; }
  if (result.points[0] + result.points[1] !== 0) { console.log("points not zero sum", h, result.points); ofcFaults += 1; }
  if (h <= 3) console.log("ofc hand", h, "points", result.points, "fouls", result.fouls, "rows", result.names[0]);
}
console.log("ofc faults", ofcFaults);
