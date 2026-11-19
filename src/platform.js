// The platform API: accounts, uploaded bots, ratings, forum, newsletter,
// tournaments, seasons, the live feed and the static game/rules metadata.

import { GAMES, DIVISIONS, TOURNAMENTS, SEASONS } from "./platform_data.js";
import { STACK } from "./stack_data.js";
import { ourTournament } from "./our_tournament_data.js";
import { PLAN_LIMITS, PAPER_STARTING_BALANCE } from "./plans.js";

const COOKIE = "pa_session";
const SESSION_DAYS = 30;
const PBKDF2_ITERATIONS = 100000;
const MAX_SOURCE = 200000;
const MAX_EXTRA = 60000;
const MAX_PICKLE = 1000000;

const json = (data, status = 200, headers = {}) =>
  new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...headers,
    },
  });

const text = new TextEncoder();

function hex(buffer) {
  return [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function fromHex(value) {
  const bytes = new Uint8Array(value.length / 2);
  for (let i = 0; i < bytes.length; i += 1) bytes[i] = parseInt(value.slice(i * 2, i * 2 + 2), 16);
  return bytes;
}

async function hashPassword(password, salt) {
  const key = await crypto.subtle.importKey("raw", text.encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt: fromHex(salt), iterations: PBKDF2_ITERATIONS, hash: "SHA-256" },
    key,
    256
  );
  return hex(bits);
}

function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function cookies(request) {
  const out = {};
  for (const part of (request.headers.get("cookie") || "").split(";")) {
    const at = part.indexOf("=");
    if (at === -1) continue;
    out[part.slice(0, at).trim()] = decodeURIComponent(part.slice(at + 1).trim());
  }
  return out;
}

function sessionCookie(token, url, maxAge = SESSION_DAYS * 86400) {
  const secure = url.protocol === "https:" ? " Secure;" : "";
  return `${COOKIE}=${token}; Path=/; HttpOnly;${secure} SameSite=Lax; Max-Age=${maxAge}`;
}

export async function currentUser(request, env) {
  const token = cookies(request)[COOKIE];
  if (!token) return null;
  const row = await env.DB.prepare(
    `SELECT u.id, u.email, u.handle, u.role, u.created_at
     FROM auth_sessions s JOIN users u ON u.id = s.user_id
     WHERE s.token = ? AND s.expires_at > ?`
  )
    .bind(token, Date.now())
    .first();
  return row || null;
}

function publicUser(user) {
  if (!user) return null;
  return { id: user.id, handle: user.handle, email: user.email, role: user.role, createdAt: user.created_at };
}

export async function ensurePaperAccount(env, userId) {
  const existing = await env.DB.prepare("SELECT * FROM paper_accounts WHERE user_id = ?").bind(userId).first();
  if (existing) {
    const welcome = await env.DB.prepare(
      "SELECT id FROM paper_transactions WHERE user_id = ? AND kind = 'signup_bonus' LIMIT 1"
    ).bind(userId).first();
    if (!welcome) {
      await env.DB.prepare(
        "INSERT OR IGNORE INTO paper_transactions (id, user_id, kind, amount, balance_after, note, created_at) VALUES (?, ?, 'signup_bonus', ?, ?, ?, ?)"
      ).bind(crypto.randomUUID(), userId, existing.starting_balance, existing.balance, "Welcome paper bankroll", Date.now()).run();
    }
    return existing;
  }
  const now = Date.now();
  await env.DB.batch([
    env.DB.prepare(
      "INSERT OR IGNORE INTO paper_accounts (user_id, balance, starting_balance, currency, created_at, updated_at) VALUES (?, ?, ?, 'PAPER', ?, ?)"
    ).bind(userId, PAPER_STARTING_BALANCE, PAPER_STARTING_BALANCE, now, now),
    env.DB.prepare(
      "INSERT OR IGNORE INTO paper_transactions (id, user_id, kind, amount, balance_after, note, created_at) VALUES (?, ?, 'signup_bonus', ?, ?, ?, ?)"
    ).bind(crypto.randomUUID(), userId, PAPER_STARTING_BALANCE, PAPER_STARTING_BALANCE, "Welcome paper bankroll", now),
  ]);
  return env.DB.prepare("SELECT * FROM paper_accounts WHERE user_id = ?").bind(userId).first();
}

export async function addPaperTransaction(env, userId, kind, amount, note = "") {
  const account = await ensurePaperAccount(env, userId);
  const next = Math.max(0, account.balance + amount);
  const actual = next - account.balance;
  const now = Date.now();
  await env.DB.batch([
    env.DB.prepare("UPDATE paper_accounts SET balance = ?, updated_at = ? WHERE user_id = ?").bind(next, now, userId),
    env.DB.prepare(
      "INSERT INTO paper_transactions (id, user_id, kind, amount, balance_after, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
    ).bind(crypto.randomUUID(), userId, kind, actual, next, String(note).slice(0, 160), now),
  ]);
  return { account: { ...account, balance: next, updated_at: now }, transaction: { amount: actual } };
}

export async function getSubscription(env, userId) {
  return env.DB.prepare("SELECT * FROM subscriptions WHERE user_id = ?").bind(userId).first();
}

export async function getPlan(env, userId) {
  const subscription = await getSubscription(env, userId);
  if (subscription && ["active", "trialing"].includes(subscription.status) && subscription.plan !== "open") {
    return subscription.plan;
  }
  return "open";
}

export async function upsertSubscription(env, data) {
  const now = Date.now();
  await env.DB.prepare(
    `INSERT INTO subscriptions
       (user_id, plan, status, interval, stripe_customer_id, stripe_subscription_id, current_period_end, cancel_at_period_end, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT(user_id) DO UPDATE SET
       plan = excluded.plan,
       status = excluded.status,
       interval = excluded.interval,
       stripe_customer_id = COALESCE(excluded.stripe_customer_id, subscriptions.stripe_customer_id),
       stripe_subscription_id = COALESCE(excluded.stripe_subscription_id, subscriptions.stripe_subscription_id),
       current_period_end = COALESCE(excluded.current_period_end, subscriptions.current_period_end),
       cancel_at_period_end = excluded.cancel_at_period_end,
       updated_at = excluded.updated_at`
  )
    .bind(
      data.userId,
      data.plan || "open",
      data.status || "active",
      data.interval || "monthly",
      data.customerId || null,
      data.subscriptionId || null,
      data.periodEnd || null,
      data.cancelAtPeriodEnd ? 1 : 0,
      now,
    )
    .run();
}

export async function paperSummary(env, userId) {
  const account = await ensurePaperAccount(env, userId);
  const rows = await env.DB.prepare(
    "SELECT * FROM paper_transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT 100"
  ).bind(userId).all();
  return {
    account: {
      balance: account.balance,
      startingBalance: account.starting_balance,
      currency: account.currency,
      updatedAt: account.updated_at,
    },
    transactions: (rows.results || []).map((row) => ({
      id: row.id,
      kind: row.kind,
      amount: row.amount,
      balanceAfter: row.balance_after,
      note: row.note,
      createdAt: row.created_at,
    })),
  };
}

const slugify = (value) =>
  String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48) || "bot";

const botRow = (row) => ({
  id: row.id,
  name: row.name,
  slug: row.slug,
  description: row.description,
  mode: row.mode,
  status: row.status,
  rating: Math.round(row.rating),
  division: divisionFor(row.rating),
  matches: row.matches,
  wins: row.wins,
  losses: row.losses,
  draws: row.draws,
  createdAt: row.created_at,
  owner: row.owner || null,
});

function divisionFor(rating) {
  if (rating >= 1300) return "elite";
  if (rating >= 1200) return "raised";
  return "open";
}

function houseRating(team) {
  return Math.round(1200 + (Number(team.score) || 0) * 1.2);
}

function houseBots() {
  return (ourTournament.teams || []).map((t) => ({
    id: `house:${t.team}`,
    key: t.team,
    name: t.team,
    kind: "house",
    rating: houseRating(t),
    division: divisionFor(houseRating(t)),
    score: t.score,
    ci95: t.ci95,
    wins: t.wins,
    draws: t.draws,
    losses: t.losses,
    samples: t.samples,
    rank: t.rank,
  }));
}

function elo(rating, opponent, score) {
  const expected = 1 / (1 + 10 ** ((opponent - rating) / 400));
  return Math.round((rating + 24 * (score - expected)) * 10) / 10;
}

const CATEGORIES = ["general", "development", "tournaments", "bugs"];

function cleanEmail(value) {
  const email = String(value || "").trim().toLowerCase();
  return /^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(email) && email.length <= 160 ? email : null;
}

function handleFrom(value) {
  const handle = String(value || "").trim().toLowerCase();
  return /^[a-z0-9_][a-z0-9_-]{2,23}$/.test(handle) ? handle : null;
}

// Markdown is rendered in the browser; the API only stores and returns text.
function cleanText(value, max) {
  const body = String(value || "").replace(/\r\n/g, "\n").trim();
  if (body.length < 1 || body.length > max) return null;
  return body;
}

function liveFeed() {
  const matches = ourTournament.matches || [];
  if (!matches.length) return { generatedAt: Date.now(), tables: [] };
  const bucket = Math.floor(Date.now() / 8000);
  const tables = [];
  const seen = new Set();
  for (let i = 0; i < 8; i += 1) {
    const index = (bucket * 7 + i * 131) % matches.length;
    const match = matches[index];
    const id = `${match.a}|${match.b}`;
    if (seen.has(id)) continue;
    seen.add(id);
    const seed = Math.abs(Math.sin(bucket + i * 13.37)) * 1000;
    const hand = 20 + Math.floor(seed % 140);
    const a = Math.floor(600 + (seed * 7) % 900);
    const b = Math.floor(600 + (seed * 11) % 900);
    const actions = [
      `${match.a} raises to ${20 + Math.floor(seed % 80)}`,
      `${match.b} calls`,
      `Flop: ${["A♠", "K♥", "7♦", "Q♣", "9♠"][Math.floor(seed) % 5]} ${["K♥", "5♣", "T♦", "8♠", "3♥"][Math.floor(seed / 2) % 5]} ${["7♦", "J♠", "4♣", "2♥", "6♦"][Math.floor(seed / 3) % 5]}`,
      `${match.b} checks`,
      `${match.a} bets ${40 + Math.floor(seed % 120)}`,
    ];
    tables.push({
      id: `live-${bucket}-${i}`,
      mode: i % 3 === 0 ? "hu" : "nlh6",
      hand,
      players: [
        { name: match.a, stack: a, seat: 0 },
        { name: match.b, stack: b, seat: 1 },
      ],
      pot: 60 + Math.floor(seed % 400),
      board: actions[2].replace("Flop: ", ""),
      last: actions.slice(3),
      started: Date.now() - Math.floor(seed % 600) * 1000,
    });
  }
  return { generatedAt: Date.now(), tables };
}

async function platformOverview(env) {
  let counts = { bots: 0, threads: 0, posts: 0, subscribers: 0, users: 0 };
  try {
    const rows = await env.DB.batch([
      env.DB.prepare("SELECT COUNT(*) AS n FROM user_bots"),
      env.DB.prepare("SELECT COUNT(*) AS n FROM threads"),
      env.DB.prepare("SELECT COUNT(*) AS n FROM posts"),
      env.DB.prepare("SELECT COUNT(*) AS n FROM newsletter"),
      env.DB.prepare("SELECT COUNT(*) AS n FROM users"),
    ]);
    counts = {
      bots: rows[0].results[0]?.n || 0,
      threads: rows[1].results[0]?.n || 0,
      posts: rows[2].results[0]?.n || 0,
      subscribers: rows[3].results[0]?.n || 0,
      users: rows[4].results[0]?.n || 0,
    };
  } catch {
    // Tables may not exist yet on a fresh database; the static pages still work.
  }
  return {
    games: GAMES,
    divisions: DIVISIONS,
    tournaments: TOURNAMENTS,
    seasons: SEASONS,
    stack: STACK.map(({ body, ...rest }) => rest),
    counts: {
      ...counts,
      houseBots: ourTournament.teams?.length || 0,
      matches: ourTournament.matches?.length || 0,
    },
  };
}

async function readJson(request) {
  const raw = await request.text();
  if (raw.length > 2200000) throw new Error("the request is too large");
  try {
    return JSON.parse(raw || "{}");
  } catch {
    throw new Error("invalid JSON");
  }
}

export async function handlePlatform(request, env, url) {
  const path = url.pathname;
  const method = request.method;
  const user = await currentUser(request, env);

  // ---------------------------------------------------------------- auth

  if (method === "POST" && path === "/api/auth/signup") {
    const body = await readJson(request);
    const email = cleanEmail(body.email);
    const handle = handleFrom(body.handle);
    const password = String(body.password || "");
    if (!email) return json({ error: "enter a valid email address" }, 400);
    if (!handle) return json({ error: "handle must be 3-24 characters: letters, numbers, _ or -" }, 400);
    if (password.length < 8) return json({ error: "password must be at least 8 characters" }, 400);
    if (body.terms !== true) return json({ error: "accept the platform rules to create an account" }, 400);
    const taken = await env.DB.prepare("SELECT id FROM users WHERE email = ? OR handle = ?").bind(email, handle).first();
    if (taken) return json({ error: "that email or handle is already registered" }, 409);
    const salt = hex(crypto.getRandomValues(new Uint8Array(16)));
    const password_hash = await hashPassword(password, salt);
    const id = crypto.randomUUID();
    const token = hex(crypto.getRandomValues(new Uint8Array(24)));
    const now = Date.now();
    await env.DB.batch([
      env.DB.prepare(
        "INSERT INTO users (id, email, handle, password_hash, salt, role, created_at) VALUES (?, ?, ?, ?, ?, 'member', ?)"
      ).bind(id, email, handle, password_hash, salt, now),
      env.DB.prepare("INSERT INTO auth_sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)").bind(
        token,
        id,
        now,
        now + SESSION_DAYS * 86400000
      ),
    ]);
    const paper = await ensurePaperAccount(env, id);
    await upsertSubscription(env, { userId: id, plan: "open", status: "active", interval: "monthly" });
    return json(
      {
        user: { id, email, handle, role: "member", createdAt: now },
        paper: { balance: paper.balance, startingBalance: paper.starting_balance, currency: paper.currency },
        plan: "open",
      },
      201,
      { "set-cookie": sessionCookie(token, url) }
    );
  }

  if (method === "POST" && path === "/api/auth/login") {
    const body = await readJson(request);
    const email = cleanEmail(body.email);
    const password = String(body.password || "");
    if (!email || !password) return json({ error: "email and password are required" }, 400);
    const row = await env.DB.prepare("SELECT * FROM users WHERE email = ?").bind(email).first();
    if (!row) return json({ error: "email or password is incorrect" }, 401);
    const hash = await hashPassword(password, row.salt);
    if (!safeEqual(hash, row.password_hash)) return json({ error: "email or password is incorrect" }, 401);
    const token = hex(crypto.getRandomValues(new Uint8Array(24)));
    const now = Date.now();
    await env.DB.prepare("INSERT INTO auth_sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)")
      .bind(token, row.id, now, now + SESSION_DAYS * 86400000)
      .run();
    return json({ user: publicUser(row) }, 200, { "set-cookie": sessionCookie(token, url) });
  }

  if (method === "POST" && path === "/api/auth/logout") {
    const token = cookies(request)[COOKIE];
    if (token) await env.DB.prepare("DELETE FROM auth_sessions WHERE token = ?").bind(token).run();
    return json({ ok: true }, 200, { "set-cookie": sessionCookie("", url, 0) });
  }

  if (method === "GET" && path === "/api/auth/me") {
    if (!user) return json({ user: null });
    const [bots, paper, subscription] = await Promise.all([
      env.DB.prepare("SELECT * FROM user_bots WHERE user_id = ? ORDER BY created_at DESC").bind(user.id).all(),
      ensurePaperAccount(env, user.id),
      getSubscription(env, user.id),
    ]);
    const plan = subscription && ["active", "trialing"].includes(subscription.status) && subscription.plan !== "open"
      ? subscription.plan
      : "open";
    return json({
      user: publicUser(user),
      bots: (bots.results || []).map(botRow),
      paper: { balance: paper.balance, startingBalance: paper.starting_balance, currency: paper.currency },
      plan,
      planLabel: PLAN_LIMITS[plan]?.label || "Open",
      botLimit: PLAN_LIMITS[plan]?.bots || 1,
      subscription: subscription
        ? {
            plan: subscription.plan,
            status: subscription.status,
            interval: subscription.interval,
            currentPeriodEnd: subscription.current_period_end,
            cancelAtPeriodEnd: Boolean(subscription.cancel_at_period_end),
            hasCustomer: Boolean(subscription.stripe_customer_id),
          }
        : null,
    });
  }

  if (method === "GET" && path === "/api/paper") {
    if (!user) return json({ error: "log in to view your paper account" }, 401);
    const summary = await paperSummary(env, user.id);
    return json({ ...summary, plan: await getPlan(env, user.id) });
  }

  if (method === "POST" && path === "/api/paper/reset") {
    if (!user) return json({ error: "log in to reset your paper account" }, 401);
    const account = await ensurePaperAccount(env, user.id);
    const delta = account.starting_balance - account.balance;
    if (delta !== 0) {
      await addPaperTransaction(env, user.id, "reset", delta, "Paper bankroll reset to the starting balance");
    }
    return json({ ...(await paperSummary(env, user.id)), plan: await getPlan(env, user.id) });
  }

  // ---------------------------------------------------------------- platform data

  if (method === "GET" && path === "/api/platform") {
    return json(await platformOverview(env));
  }

  if (method === "GET" && path === "/api/stack") {
    return json({ articles: STACK });
  }

  if (method === "GET" && path.startsWith("/api/stack/")) {
    const slug = decodeURIComponent(path.slice("/api/stack/".length));
    const article = STACK.find((a) => a.slug === slug);
    return article ? json({ article }) : json({ error: "article not found" }, 404);
  }

  // ---------------------------------------------------------------- bots

  if (method === "GET" && path === "/api/bots") {
    const mineOnly = url.searchParams.get("mine") === "1";
    const query = mineOnly && user
      ? env.DB.prepare(
          `SELECT b.*, u.handle AS owner FROM user_bots b JOIN users u ON u.id = b.user_id
           WHERE b.user_id = ? ORDER BY b.created_at DESC`
        ).bind(user.id)
      : env.DB.prepare(
          `SELECT b.*, u.handle AS owner FROM user_bots b JOIN users u ON u.id = b.user_id
           ORDER BY b.rating DESC, b.created_at DESC LIMIT 100`
        );
    const rows = await query.all();
    return json({ bots: (rows.results || []).map(botRow) });
  }

  if (method === "POST" && path === "/api/bots") {
    if (!user) return json({ error: "log in to upload a bot" }, 401);
    const plan = await getPlan(env, user.id);
    const limit = PLAN_LIMITS[plan]?.bots || 1;
    const count = await env.DB.prepare("SELECT COUNT(*) AS n FROM user_bots WHERE user_id = ?").bind(user.id).first();
    if ((count?.n || 0) >= limit) {
      return json({
        error: `Your ${PLAN_LIMITS[plan]?.label || "Open"} plan includes ${limit} bot${limit === 1 ? "" : "s"}. Upgrade to add more.`,
        code: "plan_limit",
        plan,
        limit,
        count: count?.n || 0,
      }, 402);
    }
    const body = await readJson(request);
    const name = String(body.name || "").trim();
    if (name.length < 2 || name.length > 40) return json({ error: "bot name must be 2-40 characters" }, 400);
    const source = String(body.source || "");
    if (!source) return json({ error: "attach model.py" }, 400);
    if (source.length > MAX_SOURCE) return json({ error: "model.py is too large (200 KB limit)" }, 400);
    if (!/class\s+Model\b/.test(source)) return json({ error: "model.py must define class Model" }, 400);
    const rawExtras = Array.isArray(body.extras) ? body.extras : [];
    if (rawExtras.length > 6) return json({ error: "at most six extra .py files" }, 400);
    const extras = [];
    for (const extra of rawExtras) {
      const filename = String(extra?.name || "").replace(/[^A-Za-z0-9_.-]/g, "");
      const code = String(extra?.source || "");
      if (!/\.py$/.test(filename)) return json({ error: "extra files must be .py" }, 400);
      if (!code || code.length > MAX_EXTRA) return json({ error: `${filename} is empty or too large` }, 400);
      extras.push({ name: filename, source: code });
    }
    let pickle = null;
    let pklName = "";
    if (body.pickle) {
      if (String(body.pickle).length > MAX_PICKLE) return json({ error: "pickle is too large (1 MB limit)" }, 400);
      if (!/^[A-Za-z0-9+/=\r\n]+$/.test(String(body.pickle))) return json({ error: "pickle must be base64" }, 400);
      pickle = String(body.pickle);
      pklName = String(body.pklName || "model.pkl").replace(/[^A-Za-z0-9_.-]/g, "") || "model.pkl";
    }
    const id = crypto.randomUUID();
    const now = Date.now();
    await env.DB.prepare(
      `INSERT INTO user_bots (id, user_id, name, slug, description, mode, source, extras, pickle, pkl_name, status, rating, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1200, ?)`
    )
      .bind(
        id,
        user.id,
        name,
        slugify(name),
        String(body.description || "").slice(0, 600),
        String(body.mode || "nlh").slice(0, 20),
        source,
        JSON.stringify(extras),
        pickle,
        pklName,
        now
      )
      .run();
    const row = await env.DB.prepare("SELECT * FROM user_bots WHERE id = ?").bind(id).first();
    return json({ bot: botRow({ ...row, owner: user.handle }) }, 201);
  }

  const botMatch = path.match(/^\/api\/bots\/([^/]+)(\/(files|result|matches))?$/);
  if (botMatch) {
    const id = decodeURIComponent(botMatch[1]);
    const action = botMatch[3];
    const row = await env.DB.prepare(
      `SELECT b.*, u.handle AS owner FROM user_bots b JOIN users u ON u.id = b.user_id WHERE b.id = ?`
    )
      .bind(id)
      .first();
    if (!row) return json({ error: "bot not found" }, 404);

    if (method === "GET" && !action) {
      return json({ bot: botRow(row) });
    }

    if (method === "GET" && action === "files") {
      let extras = [];
      try {
        extras = JSON.parse(row.extras || "[]");
      } catch {
        extras = [];
      }
      return json({
        id: row.id,
        name: row.name,
        source: row.source,
        extras,
        pickle: row.pickle,
        pklName: row.pkl_name,
      });
    }

    if (method === "GET" && action === "matches") {
      const rows = await env.DB.prepare(
        "SELECT * FROM bot_results WHERE bot_id = ? ORDER BY created_at DESC LIMIT 50"
      )
        .bind(id)
        .all();
      return json({ matches: rows.results || [] });
    }

    if (method === "POST" && action === "result") {
      if (!user || user.id !== row.user_id) return json({ error: "not your bot" }, 403);
      const body = await readJson(request);
      const result = ["win", "loss", "draw"].includes(body.result) ? body.result : null;
      if (!result) return json({ error: "result must be win, loss or draw" }, 400);
      const hands = Math.max(1, Math.min(200, Number(body.hands) || 1));
      const opponent = String(body.opponent || "house").slice(0, 40);
      const houseTeam = (ourTournament.teams || []).find((t) => t.team === opponent);
      const opponentRating = houseTeam ? houseRating(houseTeam) : 1200;
      const score = result === "win" ? 1 : result === "loss" ? 0 : 0.5;
      const before = row.rating;
      const after = elo(before, opponentRating, score);
      const now = Date.now();
      await env.DB.batch([
        env.DB.prepare(
          `UPDATE user_bots SET rating = ?, matches = matches + 1,
             wins = wins + ?, losses = losses + ?, draws = draws + ? WHERE id = ?`
        )
          .bind(after, result === "win" ? 1 : 0, result === "loss" ? 1 : 0, result === "draw" ? 1 : 0, id),
        env.DB.prepare(
          `INSERT INTO bot_results (id, bot_id, opponent, mode, hands, result, chips, rating_before, rating_after, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
        )
          .bind(
            crypto.randomUUID(),
            id,
            opponent,
            String(body.mode || "nlh").slice(0, 20),
            hands,
            result,
            Number(body.chips) || 0,
            before,
            after,
            now
          ),
      ]);
      const paperAmount = result === "win" ? 250 : result === "loss" ? -100 : 0;
      let paper = null;
      if (paperAmount !== 0) {
        paper = await addPaperTransaction(
          env,
          user.id,
          result === "win" ? "rated_win" : "rated_loss",
          paperAmount,
          `${result === "win" ? "Won" : "Lost"} a rated challenge against ${opponent}`,
        );
      }
      const updated = await env.DB.prepare("SELECT * FROM user_bots WHERE id = ?").bind(id).first();
      return json({
        bot: botRow({ ...updated, owner: user.handle }),
        change: Math.round((after - before) * 10) / 10,
        paper: paper ? { balance: paper.account.balance, amount: paper.transaction.amount } : null,
      });
    }

    if (method === "DELETE" && !action) {
      if (!user || user.id !== row.user_id) return json({ error: "not your bot" }, 403);
      await env.DB.batch([
        env.DB.prepare("DELETE FROM bot_results WHERE bot_id = ?").bind(id),
        env.DB.prepare("DELETE FROM user_bots WHERE id = ?").bind(id),
      ]);
      return json({ ok: true });
    }
  }

  // ---------------------------------------------------------------- leaderboard

  if (method === "GET" && path === "/api/leaderboard") {
    const division = url.searchParams.get("division") || "all";
    const rows = await env.DB.prepare(
      `SELECT b.*, u.handle AS owner FROM user_bots b JOIN users u ON u.id = b.user_id
       ORDER BY b.rating DESC LIMIT 200`
    ).all();
    const bots = [
      ...houseBots(),
      ...(rows.results || []).map((r) => {
        const b = botRow(r);
        return { ...b, kind: "user", score: null, ci95: null, samples: b.matches };
      }),
    ]
      .filter((b) => division === "all" || b.division === division)
      .sort((a, b) => b.rating - a.rating)
      .map((b, i) => ({ ...b, place: i + 1 }));
    const grouped = {};
    for (const d of DIVISIONS) {
      grouped[d.key] = [
        ...houseBots().filter((b) => b.division === d.key),
        ...(rows.results || []).map((r) => botRow(r)).filter((b) => b.division === d.key),
      ].length;
    }
    return json({ division, divisions: DIVISIONS, bots, grouped });
  }

  // ---------------------------------------------------------------- forum

  if (method === "GET" && path === "/api/forum/threads") {
    const category = url.searchParams.get("category") || "";
    const rows = category && CATEGORIES.includes(category)
      ? await env.DB.prepare(
          `SELECT t.*, COALESCE(u.handle, 'Arena') AS author,
             (SELECT COUNT(*) FROM posts p WHERE p.thread_id = t.id) AS replies
           FROM threads t LEFT JOIN users u ON u.id = t.user_id
           WHERE t.category = ? ORDER BY t.pinned DESC, t.updated_at DESC LIMIT 100`
        ).bind(category).all()
      : await env.DB.prepare(
          `SELECT t.*, COALESCE(u.handle, 'Arena') AS author,
             (SELECT COUNT(*) FROM posts p WHERE p.thread_id = t.id) AS replies
           FROM threads t LEFT JOIN users u ON u.id = t.user_id
           ORDER BY t.pinned DESC, t.updated_at DESC LIMIT 100`
        ).all();
    return json({ threads: rows.results || [], categories: CATEGORIES });
  }

  if (method === "POST" && path === "/api/forum/threads") {
    if (!user) return json({ error: "log in to start a thread" }, 401);
    const body = await readJson(request);
    const title = cleanText(body.title, 120);
    const content = cleanText(body.body, 8000);
    if (!title || title.length < 4) return json({ error: "title must be 4-120 characters" }, 400);
    if (!content) return json({ error: "write something in the post" }, 400);
    const category = CATEGORIES.includes(body.category) ? body.category : "general";
    const id = crypto.randomUUID();
    const now = Date.now();
    await env.DB.prepare(
      "INSERT INTO threads (id, user_id, category, title, body, pinned, locked, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)"
    )
      .bind(id, user.id, category, title, content, now, now)
      .run();
    return json({ id }, 201);
  }

  const threadMatch = path.match(/^\/api\/forum\/threads\/([^/]+)(\/posts)?$/);
  if (threadMatch) {
    const id = decodeURIComponent(threadMatch[1]);
    const thread = await env.DB.prepare(
      `SELECT t.*, COALESCE(u.handle, 'Arena') AS author FROM threads t
       LEFT JOIN users u ON u.id = t.user_id WHERE t.id = ?`
    )
      .bind(id)
      .first();
    if (!thread) return json({ error: "thread not found" }, 404);
    if (method === "GET" && !threadMatch[2]) {
      const posts = await env.DB.prepare(
        `SELECT p.*, COALESCE(u.handle, 'Arena') AS author, COALESCE(u.role, 'member') AS role
         FROM posts p LEFT JOIN users u ON u.id = p.user_id
         WHERE p.thread_id = ? ORDER BY p.created_at ASC`
      )
        .bind(id)
        .all();
      return json({ thread, posts: posts.results || [] });
    }
    if (method === "POST" && threadMatch[2]) {
      if (!user) return json({ error: "log in to reply" }, 401);
      if (thread.locked) return json({ error: "this thread is locked" }, 403);
      const body = await readJson(request);
      const content = cleanText(body.body, 6000);
      if (!content) return json({ error: "write a reply first" }, 400);
      const now = Date.now();
      await env.DB.batch([
        env.DB.prepare("INSERT INTO posts (id, thread_id, user_id, body, created_at) VALUES (?, ?, ?, ?, ?)")
          .bind(crypto.randomUUID(), id, user.id, content, now),
        env.DB.prepare("UPDATE threads SET updated_at = ? WHERE id = ?").bind(now, id),
      ]);
      return json({ ok: true, id });
    }
  }

  // ---------------------------------------------------------------- newsletter

  if (method === "POST" && path === "/api/newsletter") {
    const body = await readJson(request);
    const email = cleanEmail(body.email);
    if (!email) return json({ error: "enter a valid email address" }, 400);
    const existing = await env.DB.prepare("SELECT id FROM newsletter WHERE email = ?").bind(email).first();
    if (existing) return json({ ok: true, already: true, message: "You are already on the list." });
    await env.DB.prepare("INSERT INTO newsletter (id, email, source, created_at) VALUES (?, ?, ?, ?)")
      .bind(crypto.randomUUID(), email, String(body.source || "site").slice(0, 30), Date.now())
      .run();
    return json({ ok: true, already: false, message: "You are on the list." }, 201);
  }

  if (method === "GET" && path === "/api/newsletter") {
    const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM newsletter").first();
    return json({ subscribers: row?.n || 0 });
  }

  // ---------------------------------------------------------------- tournaments

  if (method === "GET" && path === "/api/tournaments") {
    const rows = await env.DB.prepare(
      `SELECT e.tournament_id, e.user_id, e.bot_id, u.handle, b.name AS bot_name
       FROM tournament_entries e
       LEFT JOIN users u ON u.id = e.user_id
       LEFT JOIN user_bots b ON b.id = e.bot_id`
    ).all();
    const entries = rows.results || [];
    return json({
      tournaments: TOURNAMENTS.map((t) => ({
        ...t,
        entries: entries.filter((e) => e.tournament_id === t.id).map((e) => ({
          handle: e.handle,
          bot: e.bot_name,
        })),
      })),
    });
  }

  const tournamentMatch = path.match(/^\/api\/tournaments\/([^/]+)(\/enter)?$/);
  if (tournamentMatch) {
    const tournamentId = decodeURIComponent(tournamentMatch[1]);
    const tournament = TOURNAMENTS.find((t) => t.id === tournamentId);
    if (!tournament) return json({ error: "tournament not found" }, 404);
    if (method === "POST" && tournamentMatch[2]) {
      if (!user) return json({ error: "log in to enter" }, 401);
      const body = await readJson(request);
      const botId = body.botId ? String(body.botId) : null;
      if (botId) {
        const bot = await env.DB.prepare("SELECT id FROM user_bots WHERE id = ? AND user_id = ?").bind(botId, user.id).first();
        if (!bot) return json({ error: "that bot is not yours" }, 403);
      }
      await env.DB.prepare(
        `INSERT INTO tournament_entries (id, tournament_id, user_id, bot_id, created_at) VALUES (?, ?, ?, ?, ?)
         ON CONFLICT(tournament_id, user_id) DO UPDATE SET bot_id = excluded.bot_id`
      )
        .bind(crypto.randomUUID(), tournamentId, user.id, botId, Date.now())
        .run();
      return json({ ok: true });
    }
    if (method === "GET") {
      const entries = await env.DB.prepare(
        `SELECT e.*, u.handle, b.name AS bot_name, b.rating FROM tournament_entries e
         LEFT JOIN users u ON u.id = e.user_id LEFT JOIN user_bots b ON b.id = e.bot_id
         WHERE e.tournament_id = ? ORDER BY e.created_at ASC`
      )
        .bind(tournamentId)
        .all();
      return json({ tournament, entries: entries.results || [] });
    }
  }

  // ---------------------------------------------------------------- seasons, live

  if (method === "GET" && path === "/api/seasons") {
    const userBots = await env.DB.prepare(
      `SELECT b.*, u.handle AS owner FROM user_bots b JOIN users u ON u.id = b.user_id
       ORDER BY b.rating DESC LIMIT 50`
    ).all();
    const house = houseBots().map((b) => ({
      name: b.name,
      kind: "house",
      rating: b.rating,
      points: Math.round(1000 + (b.score || 0) * 3),
      record: `${b.wins}–${b.draws}–${b.losses}`,
    }));
    const users = (userBots.results || []).map((r) => ({
      name: r.name,
      kind: "user",
      owner: r.owner,
      rating: Math.round(r.rating),
      points: Math.round(r.rating),
      record: `${r.wins}–${r.draws}–${r.losses}`,
    }));
    const standings = [...house, ...users].sort((a, b) => b.points - a.points).slice(0, 50)
      .map((row, i) => ({ ...row, place: i + 1 }));
    return json({ seasons: SEASONS, standings });
  }

  if (method === "GET" && path === "/api/live") {
    return json(liveFeed());
  }

  return null;
}
