// The Poker Arena worker: a static site plus the game API.
//
//   POST /api/session              start a 6-player table (you + five bots)
//   GET  /api/session/:id          current state
//   POST /api/session/:id/action   { kind: "fold" | "call" | "raise", amount }
//   POST /api/session/:id/next     deal the next hand
//   POST /api/session/:id/rebuy    reset every stack
//   POST /api/decision             ask one bot for its move (verification)
//   GET  /api/tournament           standings + matrix + matches
//   GET  /api/teams                team metadata
//   GET  /api/teams.csv            teams with their GitHub repos
//   POST /api/matches              record a match result

import { Game } from "./game.js";
import { BOTS, botByKey, createBot } from "./bots.js";
import { GameState } from "./game_state.js";
import { Card } from "./card_utils.js";
import { PlayerAction } from "./player_action.js";
import { tournament, matches as staticMatches, teams as staticTeams } from "./tournament_data.js";
import { teamsData } from "./teams_data.js";
import { allTeams } from "./all_teams_data.js";

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "access-control-allow-origin": "*",
      "cache-control": "no-store",
    },
  });

function actionFromJSON(a) {
  if (a.kind === "reveal_card") return PlayerAction.revealCard(Card.parse(a.card), a.player_id);
  if (a.kind === "win") return PlayerAction.win(a.player_id, a.amount);
  if (a.kind === "raise") return PlayerAction.raiseTo(a.player_id, a.amount);
  if (a.kind === "call") return PlayerAction.call(a.player_id);
  return PlayerAction.fold(a.player_id);
}

function reviveState(state) {
  const gs = new GameState(state.stacks);
  gs.teams = [...state.teams];
  gs.button = state.button;
  gs.small_blind = state.small_blind;
  gs.big_blind = state.big_blind;
  gs.stacks = [...state.stacks];
  gs.bets = [...state.bets];
  gs.folded = [...state.folded];
  gs.acted = [...state.acted];
  gs.board = state.board.map((c) => Card.parse(c));
  gs.street = state.street;
  gs.pot = state.pot;
  gs.last_raise = state.last_raise;
  gs.to_act = state.to_act;
  return gs;
}

async function loadGame(env, id) {
  const row = await env.DB.prepare("SELECT state FROM sessions WHERE id = ?").bind(id).first();
  if (!row) throw new Error("session not found");
  return Game.deserialize(JSON.parse(row.state));
}

async function saveGame(env, id, game) {
  await env.DB.prepare(
    `INSERT INTO sessions (id, state, updated_at) VALUES (?, ?, ?)
     ON CONFLICT(id) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at`
  )
    .bind(id, JSON.stringify(game.serialize()), Date.now())
    .run();
}

async function ensureTeamsSeeded(env) {
  const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM teams").first();
  if (row && row.n > 0) return;
  for (const team of teamsData) {
    await env.DB.prepare(
      `INSERT OR IGNORE INTO teams (team, plan, tier, github_repo, github_url, site_url)
       VALUES (?, ?, ?, ?, ?, ?)`
    )
      .bind(team.team, team.plan || "", team.tier || "", team.github_repo || "", team.search_url || "", team.site_url || "")
      .run();
  }
}

async function handleApi(request, env, url) {
  const path = url.pathname;
  const method = request.method;

  if (method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: {
        "access-control-allow-origin": "*",
        "access-control-allow-methods": "GET, POST, OPTIONS",
        "access-control-allow-headers": "content-type",
      },
    });
  }

  if (method === "POST" && path === "/api/session") {
    const body = await request.json().catch(() => ({}));
    const game = new Game({
      humanName: typeof body.humanName === "string" ? body.humanName : "You",
      botKeys: Array.isArray(body.botKeys) && body.botKeys.length ? body.botKeys : undefined,
      stack: Number(body.stack) || 500,
      smallBlind: Number(body.smallBlind) || 10,
      bigBlind: Number(body.bigBlind) || 20,
      seed: Number(body.seed) || (Date.now() % 1000000),
    });
    const { state } = game.startHand();
    const id = crypto.randomUUID();
    await saveGame(env, id, game);
    return json({ sessionId: id, state });
  }

  const sessionMatch = path.match(/^\/api\/session\/([^/]+)(\/(action|next|rebuy))?$/);
  if (sessionMatch) {
    const id = decodeURIComponent(sessionMatch[1]);
    const action = sessionMatch[3];
    const game = await loadGame(env, id);
    if (method === "GET" && !action) {
      return json({ sessionId: id, state: game.publicState() });
    }
    if (method === "POST" && action === "action") {
      const body = await request.json();
      const { status, state } = game.act(body.kind, Number(body.amount) || 0);
      await saveGame(env, id, game);
      return json({ sessionId: id, status, state });
    }
    if (method === "POST" && action === "next") {
      const { status, state } = game.startHand();
      await saveGame(env, id, game);
      return json({ sessionId: id, status, state });
    }
    if (method === "POST" && action === "rebuy") {
      game.rebuy();
      await saveGame(env, id, game);
      return json({ sessionId: id, state: game.publicState() });
    }
  }

  if (method === "POST" && path === "/api/decision") {
    const body = await request.json();
    const bot = createBot(body.bot);
    const hole = (body.hole || []).map((c) => Card.parse(c));
    bot.seed(hole, Number(body.playerId) || 0);
    const gs = reviveState(body.state);
    const actions = (body.actions || []).map(actionFromJSON);
    const action = bot.run(actions, gs);
    return json({ bot: body.bot, action: action.toJSON() });
  }

  if (method === "GET" && path === "/api/tournament") {
    const recorded = await env.DB.prepare(
      "SELECT * FROM matches ORDER BY finished_at DESC"
    ).all();
    return json({
      tournament,
      allTeams,
      matches: [...recorded.results, ...staticMatches],
      teams: staticTeams,
      bots: BOTS.map((b) => ({
        key: b.key,
        name: b.name,
        plan: b.plan,
        tier: b.tier,
        tagline: b.tagline,
        description: b.description,
      })),
    });
  }

  if (method === "GET" && path === "/api/teams") {
    return json({ teams: staticTeams, bots: BOTS, allTeams });
  }

  if (method === "GET" && path === "/api/teams.csv") {
    await ensureTeamsSeeded(env);
    const rows = await env.DB.prepare("SELECT * FROM teams ORDER BY team").all();
    const header = "team,plan,tier,github_repo,github_url,site_url";
    const lines = rows.results.map((r) =>
      [r.team, r.plan || "", r.tier || "", r.github_repo || "", r.github_url || "", r.site_url || ""]
        .map((v) => `"${String(v).replace(/"/g, '""')}"`)
        .join(",")
    );
    return new Response([header, ...lines].join("\n"), {
      headers: {
        "content-type": "text/csv; charset=utf-8",
        "content-disposition": "attachment; filename=\"teams.csv\"",
        "access-control-allow-origin": "*",
      },
    });
  }

  if (method === "POST" && path === "/api/matches") {
    const body = await request.json();
    const id = crypto.randomUUID();
    await env.DB.prepare(
      `INSERT INTO matches (id, kind, a, b, deals, hands, mean_bb, ci95_bb, verdict, faults_a, faults_b, finished_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
      .bind(
        id,
        body.kind || "group",
        body.a,
        body.b,
        Number(body.deals) || 0,
        Number(body.hands) || 0,
        body.mean_bb ?? null,
        body.ci95_bb ?? null,
        body.verdict || "tie",
        Number(body.faults_a) || 0,
        Number(body.faults_b) || 0,
        Date.now()
      )
      .run();
    return json({ id });
  }

  if (method === "GET" && path === "/api/matches") {
    const rows = await env.DB.prepare("SELECT * FROM matches ORDER BY finished_at DESC").all();
    return json({ matches: rows.results });
  }

  return json({ error: "not found" }, 404);
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/")) {
      try {
        return await handleApi(request, env, url);
      } catch (error) {
        return json({ error: String(error?.message || error) }, 400);
      }
    }
    // Single-page app: anything that is not a file serves index.html.
    const accept = request.headers.get("accept") || "";
    if (accept.includes("text/html") && !url.pathname.includes(".")) {
      const indexUrl = new URL("/index.html", url);
      return env.ASSETS.fetch(new Request(indexUrl, request));
    }
    return env.ASSETS.fetch(request);
  },
};
