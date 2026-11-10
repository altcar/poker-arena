// Local API smoke test. Assumes `wrangler dev` is running on :8787.
//   node scripts/test_api.mjs

const base = process.env.BASE || "http://localhost:8787";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitForServer() {
  for (let i = 0; i < 90; i += 1) {
    try {
      const response = await fetch(`${base}/`);
      if (response.ok) return;
    } catch {
      // not up yet
    }
    await sleep(1000);
  }
  throw new Error("server did not start");
}

async function jfetch(path, options = {}) {
  const response = await fetch(`${base}${path}`, {
    headers: { "content-type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status} ${JSON.stringify(data)}`);
  return data;
}

function check(name, condition, detail = "") {
  if (condition) console.log(`  ok   ${name}`);
  else {
    console.error(`  FAIL ${name} ${detail}`);
    process.exitCode = 1;
  }
}

async function main() {
  await waitForServer();
  console.log("server is up");

  const index = await fetch(`${base}/`);
  const html = await index.text();
  check("GET / serves the app", html.includes("Poker Arena") && html.includes("app.js"));

  const tournament = await jfetch("/api/tournament");
  check("GET /api/tournament", tournament.tournament?.groups?.[0]?.board?.teams?.length === 5,
    `teams=${tournament.tournament?.groups?.[0]?.board?.teams?.length}`);
  check("tournament matches", Array.isArray(tournament.matches) && tournament.matches.length === 10,
    `matches=${tournament.matches?.length}`);

  const session = await jfetch("/api/session", {
    method: "POST",
    body: JSON.stringify({ humanName: "Tester", seed: 7 }),
  });
  check("POST /api/session", Boolean(session.sessionId), JSON.stringify(session).slice(0, 120));
  const id = session.sessionId;
  let state = session.state;
  check("session starts at the human or a result", ["human", "over", "next"].includes(state.waitingFor),
    state.waitingFor);
  check("six seats", state.hand?.seats?.length === 6, `seats=${state.hand?.seats?.length}`);

  // Play up to 200 actions, always choosing a legal move, until the hand ends.
  let actions = 0;
  let current = state;
  while (actions < 200) {
    if (current.waitingFor === "over") break;
    if (current.waitingFor === "next") {
      current = (await jfetch(`/api/session/${id}/next`, { method: "POST" })).state;
      continue;
    }
    if (current.waitingFor !== "human") {
      current = (await jfetch(`/api/session/${id}`)).state;
      continue;
    }
    const hand = current.hand;
    let body;
    if (hand.canRaise && actions % 3 === 0) {
      body = { kind: "raise", amount: hand.minRaiseTo };
    } else {
      body = { kind: hand.callAmount === 0 ? "call" : "call" };
    }
    const result = await jfetch(`/api/session/${id}/action`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    current = result.state;
    actions += 1;
    if (result.status === "over") break;
  }
  check("played a hand to the end", current.hand?.result != null || current.waitingFor === "next",
    `waitingFor=${current.waitingFor}`);
  check("chips conserved", current.hand?.result ? current.hand.result.net.reduce((a, b) => a + b, 0) === 0 : true);
  console.log(`  played ${actions} actions`);

  const decision = await jfetch("/api/decision", {
    method: "POST",
    body: JSON.stringify({
      bot: "workhorse",
      hole: ["AS", "KD"],
      playerId: 0,
      state: {
        teams: ["You", "Workhorse"],
        button: 0,
        small_blind: 10,
        big_blind: 20,
        stacks: [500, 500],
        bets: [10, 20],
        folded: [false, false],
        acted: [false, false],
        board: [],
        street: "preflop",
        pot: 30,
        last_raise: 20,
        to_act: 0,
      },
      actions: [],
    }),
  });
  check("POST /api/decision", Boolean(decision.action), JSON.stringify(decision));
  check("decision is legal", ["fold", "call", "raise"].includes(decision.action.kind), decision.action?.kind);

  const teams = await jfetch("/api/teams");
  check("GET /api/teams", teams.teams?.teams?.length >= 6, `teams=${teams.teams?.teams?.length}`);

  const csv = await fetch(`${base}/api/teams.csv`);
  check("GET /api/teams.csv", csv.ok && (await csv.text()).includes("team"), "csv");

  const staticData = await fetch(`${base}/data/tournament.json`);
  check("static data served", staticData.ok);

  console.log(process.exitCode ? "SMOKE TEST FAILED" : "SMOKE TEST PASSED");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
