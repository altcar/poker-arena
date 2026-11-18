// Platform views: accounts, bots, leaderboard, tournaments, live, forum,
// The Stack, newsletter and the rules. Rendered by app.js's router.

import { playHand, fullDeck } from "./engine/engine.js";
import { createBot } from "./engine/bots.js";
import { loadUserBot } from "./python_runtime.js";

// ---------------------------------------------------------------- helpers

export function h(tag, attrs, ...children) {
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

export async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "content-type": "application/json" },
    credentials: "same-origin",
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

export const auth = { me: null, bots: [], ready: false };

export async function refreshAuth() {
  try {
    const data = await api("/api/auth/me");
    auth.me = data.user || null;
    auth.bots = data.bots || [];
  } catch {
    auth.me = null;
    auth.bots = [];
  }
  auth.ready = true;
  return auth.me;
}

export async function ensureAuth() {
  if (!auth.ready) await refreshAuth();
  return auth.me;
}

export function navigate(hash) {
  if (location.hash === hash) window.dispatchEvent(new Event("arena:refresh"));
  else location.hash = hash;
}

const fmtDate = (value) => {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
};

const fmtWhen = (value) => {
  if (!value) return "";
  const date = new Date(value);
  return date.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
};

const fmtRating = (n) => (n == null ? "—" : Math.round(n).toLocaleString("en-GB"));

const badge = (text, cls = "") => h("span", { class: `badge ${cls}`.trim() }, text);

const divisionClass = (division) =>
  division === "elite" ? "gold" : division === "raised" ? "blue" : "";

// ---------------------------------------------------------------- markdown

function inline(text) {
  const nodes = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let match;
  while ((match = pattern.exec(text))) {
    if (match.index > last) nodes.push(document.createTextNode(text.slice(last, match.index)));
    const token = match[0];
    if (token.startsWith("**")) nodes.push(h("strong", {}, token.slice(2, -2)));
    else if (token.startsWith("`")) nodes.push(h("code", {}, token.slice(1, -1)));
    else {
      const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      nodes.push(h("a", { href: link[2] }, link[1]));
    }
    last = match.index + token.length;
  }
  if (last < text.length) nodes.push(document.createTextNode(text.slice(last)));
  return nodes;
}

export function markdown(source) {
  const lines = String(source || "").replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let list = null;
  let code = null;
  let paragraph = [];
  const flushParagraph = () => {
    if (paragraph.length) {
      out.push(h("p", {}, inline(paragraph.join(" "))));
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list) {
      out.push(h("ul", {}, list.map((item) => h("li", {}, inline(item)))));
      list = null;
    }
  };
  for (const raw of lines) {
    if (raw.trim().startsWith("```")) {
      if (code == null) {
        flushParagraph();
        flushList();
        code = [];
      } else {
        out.push(h("pre", {}, h("code", {}, code.join("\n"))));
        code = null;
      }
      continue;
    }
    if (code != null) {
      code.push(raw);
      continue;
    }
    if (!raw.trim()) {
      flushParagraph();
      flushList();
      continue;
    }
    if (raw.startsWith("### ")) {
      flushParagraph(); flushList();
      out.push(h("h3", {}, inline(raw.slice(4))));
      continue;
    }
    if (raw.startsWith("## ")) {
      flushParagraph(); flushList();
      out.push(h("h2", {}, inline(raw.slice(3))));
      continue;
    }
    if (/^[-*] /.test(raw)) {
      flushParagraph();
      if (!list) list = [];
      list.push(raw.slice(2));
      continue;
    }
    if (raw.startsWith("> ")) {
      flushParagraph(); flushList();
      out.push(h("blockquote", {}, inline(raw.slice(2))));
      continue;
    }
    flushList();
    paragraph.push(raw.trim());
  }
  flushParagraph();
  flushList();
  if (code != null) out.push(h("pre", {}, h("code", {}, code.join("\n"))));
  return out;
}

// ---------------------------------------------------------------- shared bits

async function platform() {
  if (!platform.cache) platform.cache = await api("/api/platform");
  return platform.cache;
}

function pageHead(title, subtitle, actions = []) {
  return h("div", { class: "page-head" },
    h("div", {},
      h("h1", {}, title),
      subtitle ? h("p", { class: "muted" }, subtitle) : null,
    ),
    actions.length ? h("div", { class: "actions-inline" }, actions) : null,
  );
}

function loading(message = "Loading…") {
  return h("div", { class: "card muted" }, message);
}

function errorCard(error) {
  return h("div", { class: "card error" }, String(error.message || error));
}

function empty(message) {
  return h("div", { class: "card empty" }, message);
}

// ---------------------------------------------------------------- games

export async function gamesView() {
  const page = h("div", { class: "page" });
  page.append(
    pageHead("Games", "Five formats. One arena. Pick a table and sit down, or send a bot."),
  );
  try {
    const data = await platform();
    page.append(
      h("div", { class: "game-grid" },
        data.games.map((game) =>
          h("div", { class: "card game-card" },
            h("div", { class: "game-card-top" },
              h("h2", {}, game.name),
              h("div", { class: "badges" }, (game.badges || []).map((b) => badge(b))),
            ),
            h("p", {}, game.summary),
            h("div", { class: "actions-inline" },
              h("a", { class: "btn primary", href: `#/play?mode=${game.mode}` }, "Play"),
              h("a", { class: "btn ghost", href: `#/games/${game.slug}` }, "Rules"),
            ),
          )
        ),
      ),
    );
  } catch (error) {
    page.append(errorCard(error));
  }
  return page;
}

export async function gameDetailView(slug) {
  const page = h("div", { class: "page" });
  try {
    const data = await platform();
    const game = data.games.find((g) => g.slug === slug);
    if (!game) return h("div", { class: "page" }, empty("No such game."));
    page.append(
      pageHead(game.name, game.tagline, [
        h("a", { class: "btn primary", href: `#/play?mode=${game.mode}` }, "Play this format"),
        h("a", { class: "btn ghost", href: "#/games" }, "All games"),
      ]),
      h("div", { class: "card" }, h("p", {}, game.summary)),
      h("div", { class: "card" },
        h("h2", {}, "Rules"),
        h("dl", { class: "rules-list" },
          game.rules.map(([title, body]) => [h("dt", {}, title), h("dd", {}, body)]),
        ),
      ),
    );
  } catch (error) {
    page.append(errorCard(error));
  }
  return page;
}

// ---------------------------------------------------------------- leaderboard

export async function leaderboardView(query = {}) {
  const page = h("div", { class: "page" });
  page.append(
    pageHead("Leaderboard", "House bots and uploaded bots on one ladder. Divisions move with the rating.", [
      h("a", { class: "btn ghost", href: "#/season/2" }, "Season 2"),
      h("a", { class: "btn ghost", href: "#/season/1" }, "Season 1 archive"),
    ]),
  );
  const tabs = h("div", { class: "tabs-row" });
  page.append(tabs);
  const body = h("div", {});
  page.append(body);
  const division = query.division || "all";

  try {
    const [data, meta] = await Promise.all([api("/api/leaderboard?division=all"), platform()]);
    const divisions = [{ key: "all", name: "All" }, ...meta.divisions];
    tabs.replaceChildren(...divisions.map((d) =>
      h("a", {
        class: `tab${division === d.key ? " active" : ""}`,
        href: `#/leaderboard?division=${d.key}`,
      }, `${d.name}${d.key === "all" ? "" : ` (${data.grouped[d.key] || 0})`}`)
    ));
    const bots = data.bots.filter((b) => division === "all" || b.division === division);
    if (!bots.length) {
      body.append(empty("No bots in this division yet. Upload one and run a challenge."));
      return page;
    }
    const rows = bots.map((bot) =>
      h("tr", {},
        h("td", { class: "num" }, String(bot.place)),
        h("td", {},
          bot.kind === "house"
            ? h("a", { href: `#/team/${encodeURIComponent(bot.name)}` }, bot.name)
            : h("span", {}, bot.name, bot.owner ? h("span", { class: "muted small" }, ` by ${bot.owner}`) : null),
        ),
        h("td", {}, badge(bot.division, divisionClass(bot.division))),
        h("td", { class: "num" }, fmtRating(bot.rating)),
        h("td", { class: "num" }, bot.score == null ? "—" : `${bot.score > 0 ? "+" : ""}${bot.score.toFixed(1)}`),
        h("td", { class: "num" }, `${bot.wins ?? 0}–${bot.draws ?? 0}–${bot.losses ?? 0}`),
        h("td", { class: "num muted" }, String(bot.matches ?? bot.samples ?? "—")),
        h("td", {},
          bot.kind === "house"
            ? h("a", { class: "btn small-btn", href: `#/play?mode=hu&opponent=${encodeURIComponent(bot.name)}` }, "Challenge")
            : h("a", { class: "btn small-btn", href: `#/test/${encodeURIComponent(bot.id)}` }, "Test"),
        ),
      )
    );
    body.append(
      h("div", { class: "card tablewrap" },
        h("table", { class: "tbl" },
          h("thead", {}, h("tr", {},
            h("th", { class: "num" }, "#"), h("th", {}, "Bot"), h("th", {}, "Division"),
            h("th", { class: "num" }, "Rating"), h("th", { class: "num" }, "bb/100"),
            h("th", { class: "num" }, "W–D–L"), h("th", { class: "num" }, "Matches"), h("th", {}, ""),
          )),
          h("tbody", {}, rows),
        ),
      ),
      h("p", { class: "muted small" },
        "House ratings are derived from the 1,891-match round robin. Uploaded bots start at 1200 and move on an Elo curve after each rated 20-hand challenge."),
    );
  } catch (error) {
    body.append(errorCard(error));
  }
  return page;
}

// ---------------------------------------------------------------- tournaments

export async function tournamentsView() {
  const page = h("div", { class: "page" });
  page.append(pageHead("Tournaments", "Scheduled events, brackets and open entries."));
  try {
    const data = await api("/api/tournaments");
    page.append(
      h("div", { class: "grid" },
        data.tournaments.map((t) =>
          h("div", { class: "card tournament-card" },
            h("div", { class: "game-card-top" },
              h("h2", {}, t.name),
              badge(t.status === "open" ? "Entries open" : "Completed", t.status === "open" ? "green" : ""),
            ),
            h("p", { class: "muted small" }, `${t.format} · starts ${fmtDate(t.starts)}`),
            h("p", {}, t.blurb),
            h("div", { class: "kv small" },
              h("dt", {}, "Structure"), h("dd", {}, t.structure),
              h("dt", {}, "Prize"), h("dd", {}, t.prize),
              h("dt", {}, "Field"), h("dd", {}, `${t.entrants} bots${t.entries?.length ? ` + ${t.entries.length} uploaded` : ""}`),
            ),
            t.results?.length
              ? h("p", { class: "small" }, ["Winner: ", h("strong", {}, t.results[0].team), ` · runner-up: ${t.results[1]?.team || "—"}`])
              : null,
            h("div", { class: "actions-inline" },
              h("a", { class: "btn primary", href: `#/tournaments/${t.id}` }, t.status === "open" ? "Enter" : "Results"),
            ),
          )
        ),
      ),
    );
  } catch (error) {
    page.append(errorCard(error));
  }
  return page;
}

export async function tournamentView(id) {
  const page = h("div", { class: "page" });
  try {
    const data = await api(`/api/tournaments/${encodeURIComponent(id)}`);
    const t = data.tournament;
    await ensureAuth();
    page.append(
      pageHead(t.name, `${t.format} · ${fmtDate(t.starts)}`, [h("a", { class: "btn ghost", href: "#/tournaments" }, "All tournaments")]),
      h("div", { class: "card" },
        h("p", {}, t.blurb),
        h("dl", { class: "kv" },
          h("dt", {}, "Status"), h("dd", {}, t.status === "open" ? "Entries open" : "Completed"),
          h("dt", {}, "Structure"), h("dd", {}, t.structure),
          h("dt", {}, "Prize"), h("dd", {}, t.prize),
          h("dt", {}, "House field"), h("dd", {}, `${t.entrants} bots`),
        ),
      ),
    );

    if (t.results?.length) {
      page.append(
        h("div", { class: "card" },
          h("h2", {}, "Final standings"),
          h("div", { class: "tablewrap" },
            h("table", { class: "tbl" },
              h("thead", {}, h("tr", {}, h("th", { class: "num" }, "#"), h("th", {}, "Bot"))),
              h("tbody", {}, t.results.map((r) =>
                h("tr", {}, h("td", { class: "num" }, String(r.place)),
                  h("td", {}, h("a", { href: `#/team/${encodeURIComponent(r.team)}` }, r.team))),
              )),
            ),
          ),
        ),
      );
    }

    if (t.status === "open") {
      if (!auth.me) {
        page.append(h("div", { class: "card" }, h("p", {}, "Log in to enter a bot."),
          h("a", { class: "btn primary", href: "#/login" }, "Log in")));
      } else if (!auth.bots.length) {
        page.append(h("div", { class: "card" }, h("p", {}, "You have not uploaded a bot yet."),
          h("a", { class: "btn primary", href: "#/upload" }, "Upload a bot")));
      } else {
        const select = h("select", {}, auth.bots.map((b) => h("option", { value: b.id }, `${b.name} (${Math.round(b.rating)})`)));
        const status = h("span", { class: "muted small" });
        page.append(
          h("div", { class: "card" },
            h("h2", {}, "Enter with your bot"),
            h("div", { class: "actions" },
              select,
              h("button", { class: "primary", onclick: async () => {
                status.textContent = "Entering…";
                try {
                  await api(`/api/tournaments/${encodeURIComponent(t.id)}/enter`, {
                    method: "POST",
                    body: JSON.stringify({ botId: select.value }),
                  });
                  status.textContent = "Entered. Good luck.";
                } catch (error) {
                  status.textContent = error.message;
                }
              } }, "Enter tournament"),
              status,
            ),
          ),
        );
      }
      if (data.entries?.length) {
        page.append(
          h("div", { class: "card" },
            h("h2", {}, "Uploaded entries"),
            h("div", { class: "tablewrap" },
              h("table", { class: "tbl" },
                h("thead", {}, h("tr", {}, h("th", {}, "Handle"), h("th", {}, "Bot"), h("th", { class: "num" }, "Rating"))),
                h("tbody", {}, data.entries.map((e) =>
                  h("tr", {}, h("td", {}, e.handle || "—"), h("td", {}, e.bot_name || "—"), h("td", { class: "num" }, fmtRating(e.rating))))),
              ),
            ),
          ),
        );
      }
    }
  } catch (error) {
    page.append(errorCard(error));
  }
  return page;
}

// ---------------------------------------------------------------- live

export async function liveView() {
  const page = h("div", { class: "page" });
  page.append(pageHead("Live bot matches", "A rolling feed generated from the round-robin pairings. Refreshes every six seconds."));
  const grid = h("div", { class: "live-grid" });
  const stamp = h("span", { class: "muted small" });
  page.append(h("div", { class: "controls" }, stamp, h("span", { class: "live-dot" }, "Live")));
  page.append(grid);

  let timer = null;
  let stopped = false;
  window.addEventListener("hashchange", () => {
    stopped = true;
    if (timer) clearInterval(timer);
  }, { once: true });

  async function tick() {
    if (stopped) return;
    try {
      const data = await api("/api/live");
      stamp.textContent = `Updated ${new Date(data.generatedAt).toLocaleTimeString("en-GB")}`;
      grid.replaceChildren(...data.tables.map((t) =>
        h("div", { class: "card live-card" },
          h("div", { class: "live-card-head" },
            h("span", { class: "live-dot" }, "Live"),
            badge(t.mode === "hu" ? "Heads-up" : "6-max"),
            h("span", { class: "muted small" }, `Hand ${t.hand}`),
          ),
          h("div", { class: "live-players" },
            t.players.map((p) =>
              h("div", { class: "live-player" },
                h("span", {}, p.name),
                h("strong", {}, p.stack.toLocaleString("en-GB")),
              )),
          ),
          h("div", { class: "live-board" }, `Board ${t.board}`),
          h("div", { class: "muted small" }, `Pot ${t.pot} · ${t.last.join(" · ")}`),
        )
      ));
    } catch {
      stamp.textContent = "Feed unavailable";
    }
  }
  tick();
  timer = setInterval(tick, 6000);
  return page;
}

// ---------------------------------------------------------------- forum

export async function forumView(query = {}) {
  const page = h("div", { class: "page" });
  page.append(pageHead("Forum", "Bot development, tournaments, bugs and everything else.", [
    h("a", { class: "btn primary", href: "#/forum/new" }, "New thread"),
  ]));
  const category = query.category || "all";
  try {
    const data = await api("/api/forum/threads");
    const categories = [{ key: "all", label: "All" }, ...data.categories.map((c) => ({ key: c, label: c[0].toUpperCase() + c.slice(1) }))];
    page.append(h("div", { class: "tabs-row" }, categories.map((c) =>
      h("a", { class: `tab${category === c.key ? " active" : ""}`, href: `#/forum?category=${c.key}` }, c.label)
    )));
    const threads = data.threads.filter((t) => category === "all" || t.category === category);
    if (!threads.length) {
      page.append(empty("No threads yet. Start the first one."));
      return page;
    }
    page.append(
      h("div", { class: "card tablewrap" },
        h("table", { class: "tbl forum-table" },
          h("thead", {}, h("tr", {},
            h("th", {}, "Thread"), h("th", {}, "Category"), h("th", {}, "Author"),
            h("th", { class: "num" }, "Replies"), h("th", {}, "Last post"),
          )),
          h("tbody", {}, threads.map((t) =>
            h("tr", {},
              h("td", {},
                t.pinned ? badge("Pinned", "gold") : null,
                h("a", { href: `#/forum/${t.id}` }, t.title),
              ),
              h("td", {}, badge(t.category)),
              h("td", {}, t.author),
              h("td", { class: "num" }, String(t.replies)),
              h("td", { class: "muted small" }, fmtWhen(t.updated_at)),
            )
          )),
        ),
      ),
    );
  } catch (error) {
    page.append(errorCard(error));
  }
  return page;
}

export async function newThreadView() {
  const page = h("div", { class: "page" });
  await ensureAuth();
  page.append(pageHead("New thread", "Keep it on topic and civil.", [h("a", { class: "btn ghost", href: "#/forum" }, "Back to forum")]));
  if (!auth.me) {
    page.append(h("div", { class: "card" }, h("p", {}, "Log in to post."), h("a", { class: "btn primary", href: "#/login" }, "Log in")));
    return page;
  }
  const title = h("input", { type: "text", placeholder: "Thread title", maxlength: "120" });
  const category = h("select", {}, ["general", "development", "tournaments", "bugs"].map((c) => h("option", { value: c }, c)));
  const body = h("textarea", { rows: "10", placeholder: "Write your post. Markdown-lite: ## heading, **bold**, `code`, - list." });
  const status = h("span", { class: "error small" });
  page.append(
    h("div", { class: "card form" },
      h("label", {}, "Title", title),
      h("label", {}, "Category", category),
      h("label", {}, "Post", body),
      h("div", { class: "actions" },
        h("button", { class: "primary", onclick: async () => {
          status.textContent = "";
          try {
            const data = await api("/api/forum/threads", {
              method: "POST",
              body: JSON.stringify({ title: title.value, body: body.value, category: category.value }),
            });
            navigate(`#/forum/${data.id}`);
          } catch (error) {
            status.textContent = error.message;
          }
        } }, "Post thread"),
        status,
      ),
    ),
  );
  return page;
}

export async function threadView(id) {
  const page = h("div", { class: "page" });
  try {
    await ensureAuth();
    const data = await api(`/api/forum/threads/${encodeURIComponent(id)}`);
    const t = data.thread;
    page.append(
      pageHead(t.title, `${t.category} · started by ${t.author} · ${fmtWhen(t.created_at)}`, [h("a", { class: "btn ghost", href: "#/forum" }, "Forum")]),
      h("div", { class: "card post-card" },
        h("div", { class: "post-meta" }, h("strong", {}, t.author), h("span", { class: "muted small" }, fmtWhen(t.created_at))),
        h("div", { class: "prose" }, markdown(t.body)),
      ),
      ...data.posts.map((p) =>
        h("div", { class: "card post-card" },
          h("div", { class: "post-meta" }, h("strong", {}, p.author), p.role === "admin" ? badge("Admin", "gold") : null, h("span", { class: "muted small" }, fmtWhen(p.created_at))),
          h("div", { class: "prose" }, markdown(p.body)),
        )
      ),
    );
    if (t.locked) {
      page.append(h("div", { class: "card muted" }, "This thread is locked."));
    } else if (!auth.me) {
      page.append(h("div", { class: "card" }, h("p", {}, "Log in to reply."), h("a", { class: "btn primary", href: "#/login" }, "Log in")));
    } else {
      const body = h("textarea", { rows: "5", placeholder: "Write a reply…" });
      const status = h("span", { class: "error small" });
      page.append(
        h("div", { class: "card form" },
          h("label", {}, "Reply", body),
          h("div", { class: "actions" },
            h("button", { class: "primary", onclick: async () => {
              status.textContent = "";
              try {
                await api(`/api/forum/threads/${encodeURIComponent(id)}/posts`, {
                  method: "POST", body: JSON.stringify({ body: body.value }),
                });
                window.dispatchEvent(new Event("arena:refresh"));
              } catch (error) {
                status.textContent = error.message;
              }
            } }, "Post reply"),
            status,
          ),
        ),
      );
    }
  } catch (error) {
    page.append(errorCard(error));
  }
  return page;
}

// ---------------------------------------------------------------- stack

export async function stackView() {
  const page = h("div", { class: "page" });
  page.append(pageHead("The Stack", "Notes on formats, ratings and building bots."));
  try {
    const data = await api("/api/stack");
    page.append(
      h("div", { class: "grid" },
        data.articles.map((article) =>
          h("a", { class: "card stack-card", href: `#/stack/${article.slug}` },
            h("div", { class: "badges" }, badge(article.tag), h("span", { class: "muted small" }, fmtDate(article.date))),
            h("h2", {}, article.title),
            h("p", { class: "muted" }, article.excerpt),
            h("span", { class: "small" }, "Read →"),
          )
        ),
      ),
    );
  } catch (error) {
    page.append(errorCard(error));
  }
  return page;
}

export async function articleView(slug) {
  const page = h("div", { class: "page" });
  try {
    const data = await api(`/api/stack/${encodeURIComponent(slug)}`);
    const article = data.article;
    page.append(
      pageHead(article.title, `${article.tag} · ${article.author} · ${fmtDate(article.date)}`, [h("a", { class: "btn ghost", href: "#/stack" }, "The Stack")]),
      h("article", { class: "card prose" }, markdown(article.body)),
    );
  } catch (error) {
    page.append(errorCard(error));
  }
  return page;
}

// ---------------------------------------------------------------- newsletter

export async function newsletterView() {
  const page = h("div", { class: "page" });
  page.append(
    pageHead("Newsletter", "One email when something actually happens. No drip campaign."),
    h("div", { class: "newsletter-grid" },
      h("div", { class: "card" },
        h("h2", {}, "The short list"),
        h("p", {}, "Season openings, tournament dates, new formats and the occasional bot-building note."),
        h("ul", {},
          h("li", {}, "Season 2 starts 18 November 2026."),
          h("li", {}, "Season 2 Kickoff tournament opens entries in December."),
          h("li", {}, "New: upload a Python bot and run rated 20-hand challenges."),
        ),
        h("form", { class: "form", onsubmit: async (event) => {
          event.preventDefault();
          const email = event.target.email.value;
          const status = event.target.querySelector(".status");
          try {
            const data = await api("/api/newsletter", { method: "POST", body: JSON.stringify({ email, source: "newsletter-page" }) });
            status.textContent = data.message;
            status.className = "status small win";
            event.target.email.value = "";
          } catch (error) {
            status.textContent = error.message;
            status.className = "status small error";
          }
        } },
          h("label", {}, "Email", h("input", { name: "email", type: "email", required: true, placeholder: "you@example.com" })),
          h("div", { class: "actions" }, h("button", { class: "primary", type: "submit" }, "Subscribe"), h("span", { class: "status small" })),
        ),
      ),
      h("div", { class: "card" },
        h("h2", {}, "Archive"),
        h("p", { class: "muted" }, "Previous issues are published on The Stack."),
        h("ul", {},
          h("li", {}, h("a", { href: "#/stack/how-a-match-is-run" }, "How a match is actually run")),
          h("li", {}, h("a", { href: "#/stack/your-first-bot" }, "Your first bot: the Model interface")),
          h("li", {}, h("a", { href: "#/stack/ofc-fouls" }, "Pineapple OFC: fouls are the whole game")),
          h("li", {}, h("a", { href: "#/stack/draw27-unlearning" }, "2-7 Triple Draw: unlearning Hold'em")),
        ),
      ),
    ),
  );
  return page;
}

// ---------------------------------------------------------------- auth

function authForm(mode) {
  const page = h("div", { class: "page narrow" });
  const signup = mode === "signup";
  page.append(pageHead(signup ? "Create an account" : "Log in", signup
    ? "One account for your bots, the forum and tournament entries."
    : "Welcome back."));
  const email = h("input", { type: "email", required: true, placeholder: "you@example.com", autocomplete: "email" });
  const handle = h("input", { type: "text", placeholder: "handle", maxlength: "24", autocomplete: "username" });
  const password = h("input", { type: "password", required: true, placeholder: "at least 8 characters", autocomplete: signup ? "new-password" : "current-password" });
  const status = h("div", { class: "error small" });
  page.append(
    h("div", { class: "card form" },
      h("label", {}, "Email", email),
      signup ? h("label", {}, "Handle", handle) : null,
      h("label", {}, "Password", password),
      h("div", { class: "actions" },
        h("button", { class: "primary", onclick: async () => {
          status.textContent = "";
          try {
            const body = signup
              ? { email: email.value, handle: handle.value, password: password.value }
              : { email: email.value, password: password.value };
            await api(signup ? "/api/auth/signup" : "/api/auth/login", { method: "POST", body: JSON.stringify(body) });
            await refreshAuth();
            navigate("#/account");
          } catch (error) {
            status.textContent = error.message;
          }
        } }, signup ? "Sign up" : "Log in"),
        h("a", { class: "btn ghost", href: signup ? "#/login" : "#/signup" }, signup ? "I already have an account" : "Create an account"),
      ),
      status,
      h("p", { class: "muted small" }, "Email is only used for login and the newsletter if you opt in. No recovery flow yet, so keep the password safe."),
    ),
  );
  return page;
}

export const loginView = () => authForm("login");
export const signupView = () => authForm("signup");

// ---------------------------------------------------------------- account

export async function accountView() {
  const page = h("div", { class: "page" });
  await ensureAuth();
  if (!auth.me) {
    page.append(pageHead("Account", "Log in to manage your bots."));
    page.append(h("div", { class: "card" },
      h("div", { class: "actions" },
        h("a", { class: "btn primary", href: "#/login" }, "Log in"),
        h("a", { class: "btn ghost", href: "#/signup" }, "Create an account"),
      ),
    ));
    return page;
  }
  page.append(
    pageHead(`@${auth.me.handle}`, auth.me.email, [
      h("a", { class: "btn primary", href: "#/upload" }, "Upload a bot"),
      h("button", { class: "ghost", onclick: async () => {
        await api("/api/auth/logout", { method: "POST" });
        await refreshAuth();
        navigate("#/");
      } }, "Log out"),
    ]),
  );

  if (!auth.bots.length) {
    page.append(h("div", { class: "card" },
      h("h2", {}, "No bots yet"),
      h("p", {}, "Upload a model.py that defines class Model, then run a rated challenge."),
      h("a", { class: "btn primary", href: "#/upload" }, "Upload your first bot"),
    ));
    return page;
  }

  page.append(
    h("div", { class: "card tablewrap" },
      h("table", { class: "tbl" },
        h("thead", {}, h("tr", {},
          h("th", {}, "Bot"), h("th", {}, "Division"), h("th", { class: "num" }, "Rating"),
          h("th", { class: "num" }, "W–D–L"), h("th", { class: "num" }, "Matches"), h("th", {}, "Actions"),
        )),
        h("tbody", {}, auth.bots.map((bot) =>
          h("tr", {},
            h("td", {}, bot.name, h("div", { class: "muted small" }, bot.description || "")),
            h("td", {}, badge(bot.division, divisionClass(bot.division))),
            h("td", { class: "num" }, fmtRating(bot.rating)),
            h("td", { class: "num" }, `${bot.wins}–${bot.draws}–${bot.losses}`),
            h("td", { class: "num" }, String(bot.matches)),
            h("td", { class: "actions-inline" },
              h("a", { class: "btn small-btn", href: `#/test/${bot.id}` }, "Rated test"),
              h("a", { class: "btn small-btn ghost", href: `#/play?bot=${bot.id}&mode=hu` }, "Play"),
              h("button", { class: "small-btn ghost", onclick: async () => {
                if (!confirm(`Delete ${bot.name}?`)) return;
                await api(`/api/bots/${bot.id}`, { method: "DELETE" });
                await refreshAuth();
                navigate("#/account");
              } }, "Delete"),
            ),
          )
        )),
      ),
    ),
  );
  page.append(h("p", { class: "muted small" },
    "A rated test runs 20 hands in your browser against a house table and updates your Elo rating. Uploaded bots are public so anyone can challenge them."));
  return page;
}

// ---------------------------------------------------------------- upload

const TEMPLATE = `from card_utils import Card
from player_action import PlayerAction

class Model:
    """A minimal, legal bot. Replace run() with your own logic."""

    def seed(self, hand, player_id):
        self.hand = hand
        self.player_id = player_id

    def run(self, actions, state):
        # Check when it is free, otherwise fold.
        if state.call_amount == 0:
            return PlayerAction.call(self.player_id)
        return PlayerAction.fold(self.player_id)

    def end_game(self, actions, state):
        pass
`;

export async function uploadView() {
  const page = h("div", { class: "page" });
  await ensureAuth();
  if (!auth.me) {
    page.append(pageHead("Upload a bot", "You need an account first."),
      h("div", { class: "card" }, h("a", { class: "btn primary", href: "#/login" }, "Log in")));
    return page;
  }
  page.append(pageHead("Upload a bot", "model.py plus any modules it imports. Nothing is executed on the server."));
  const name = h("input", { type: "text", placeholder: "My first bot", maxlength: "40" });
  const description = h("input", { type: "text", placeholder: "One line about the strategy", maxlength: "600" });
  const mode = h("select", {}, h("option", { value: "nlh" }, "No-Limit Hold'em"), h("option", { value: "hu" }, "Heads-up Hold'em"));
  const source = h("textarea", { rows: "16", spellcheck: "false" }, TEMPLATE);
  const sourceFile = h("input", { type: "file", accept: ".py" });
  const extraFiles = h("input", { type: "file", accept: ".py", multiple: true });
  const pickleFile = h("input", { type: "file", accept: ".pkl,.pickle,.bin" });
  const status = h("div", { class: "small" });
  const extras = [];

  sourceFile.addEventListener("change", async () => {
    const file = sourceFile.files?.[0];
    if (file) source.value = await file.text();
  });

  page.append(
    h("div", { class: "card form" },
      h("label", {}, "Bot name", name),
      h("label", {}, "Description", description),
      h("label", {}, "Mode", mode),
      h("div", { class: "upload-row" },
        h("label", {}, "model.py", sourceFile),
        h("label", {}, "Extra modules (brain.py, …)", extraFiles),
        h("label", {}, "Optional pickle", pickleFile),
      ),
      h("label", {}, "model.py", source),
      h("p", { class: "muted small" },
        "The file must define class Model with seed(hand, player_id) and run(actions, state). " +
        "Imports from card_utils, player_action and game_state are provided by the arena shim. " +
        "Limit: 200 KB of Python, 700 KB pickle."),
      h("div", { class: "actions" },
        h("button", { class: "primary", onclick: async () => {
          status.className = "small";
          status.textContent = "Uploading…";
          try {
            let pickle = null;
            let pklName = "";
            const pkl = pickleFile.files?.[0];
            if (pkl) {
              if (pkl.size > 700000) throw new Error("pickle is larger than 700 KB");
              const buffer = new Uint8Array(await pkl.arrayBuffer());
              let binary = "";
              for (const byte of buffer) binary += String.fromCharCode(byte);
              pickle = btoa(binary);
              pklName = pkl.name;
            }
            const extraPayload = [];
            for (const file of extraFiles.files || []) {
              if (!file.name.endsWith(".py")) continue;
              extraPayload.push({ name: file.name, source: await file.text() });
            }
            const data = await api("/api/bots", {
              method: "POST",
              body: JSON.stringify({
                name: name.value,
                description: description.value,
                mode: mode.value,
                source: source.value,
                extras: extraPayload,
                pickle,
                pklName,
              }),
            });
            await refreshAuth();
            status.className = "small win";
            status.textContent = "Uploaded. Opening your account…";
            setTimeout(() => navigate("#/account"), 400);
          } catch (error) {
            status.className = "small error";
            status.textContent = error.message;
          }
        } }, "Upload bot"),
        h("a", { class: "btn ghost", href: "#/account" }, "Cancel"),
        status,
      ),
    ),
  );

  page.append(
    h("div", { class: "card prose" },
      h("h2", {}, "The interface"),
      ...markdown(`A bot is one Python class. The arena creates it once and calls:

- \`seed(hand, player_id)\` at the start of every hand.
- \`run(actions, state)\` when it is your turn.
- \`end_game(actions, state)\` after the hand.

Return a \`PlayerAction\`: \`fold\`, \`call\` or \`raise_to\`. Raise amounts are the total to raise **to**. See [Your first bot](#/stack/your-first-bot) for a longer walkthrough.`),
    ),
  );
  return page;
}

// ---------------------------------------------------------------- rated test

export async function botTestView(botId) {
  const page = h("div", { class: "page" });
  let bot;
  try {
    bot = (await api(`/api/bots/${encodeURIComponent(botId)}`)).bot;
  } catch (error) {
    page.append(errorCard(error));
    return page;
  }
  page.append(
    pageHead(bot.name, `${bot.division} · ${Math.round(bot.rating)} rating · ${bot.wins}–${bot.draws}–${bot.losses}`),
    h("div", { class: "card" },
      h("p", {}, bot.description || "An uploaded bot."),
      h("p", { class: "muted small" },
        "A rated test loads the Python model in your browser and plays 20 hands against a four-bot house table. " +
        "The result updates the rating. It is not a security boundary: this is a free arena."),
      h("div", { class: "actions" },
        h("button", { class: "primary", onclick: run }, "Run 20-hand rated test"),
        h("a", { class: "btn ghost", href: `#/play?bot=${bot.id}&mode=hu` }, "Play against it"),
      ),
      h("div", { class: "test-output" }),
    ),
  );

  async function run(event) {
    const card = page.querySelector(".card");
    const output = page.querySelector(".test-output");
    const button = event.target;
    button.disabled = true;
    output.replaceChildren(h("p", { class: "muted" }, "Loading the Python runtime…"));
    try {
      await loadUserBot(bot, (message) => {
        output.replaceChildren(h("p", { class: "muted" }, message));
      });
      const key = `user:${bot.id}`;
      const opponents = ["conservative", "adaptive", "mixed", "aggressive"];
      const models = [createBot(key), ...opponents.map((k) => createBot(k))];
      const teams = [bot.name, "Conservative", "Adaptive", "Mixed", "Aggressive"];
      let net = 0;
      for (let hand = 0; hand < 20; hand += 1) {
        const deck = fullDeck();
        for (let i = deck.length - 1; i > 0; i -= 1) {
          const j = Math.floor(Math.random() * (i + 1));
          [deck[i], deck[j]] = [deck[j], deck[i]];
        }
        const result = playHand(models, deck, [500, 500, 500, 500, 500], hand % 5, 10, 20, teams);
        net += result.net[0];
        if (hand % 5 === 4) {
          output.replaceChildren(h("p", { class: "muted" }, `Played ${hand + 1} of 20 hands · net ${net >= 0 ? "+" : ""}${net} chips`));
          await new Promise((resolve) => setTimeout(resolve, 0));
        }
      }
      const outcome = net > 0 ? "win" : net < 0 ? "loss" : "draw";
      const data = await api(`/api/bots/${bot.id}/result`, {
        method: "POST",
        body: JSON.stringify({ opponent: "house", mode: "nlh", hands: 20, result: outcome, chips: net }),
      });
      await refreshAuth();
      output.replaceChildren(
        h("h3", { class: outcome === "win" ? "win" : outcome === "loss" ? "lose" : "" },
          `${outcome === "win" ? "Win" : outcome === "loss" ? "Loss" : "Draw"} · net ${net >= 0 ? "+" : ""}${net} chips`),
        h("p", {}, `Rating ${Math.round(data.bot.rating)} (${data.change >= 0 ? "+" : ""}${data.change}).`),
        h("div", { class: "actions" },
          h("a", { class: "btn primary", href: "#/account" }, "Back to account"),
          h("button", { class: "ghost", onclick: () => navigate(`#/test/${bot.id}`) }, "Run again"),
        ),
      );
    } catch (error) {
      output.replaceChildren(h("p", { class: "error" }, error.message));
    } finally {
      button.disabled = false;
    }
    void card;
  }
  return page;
}

// ---------------------------------------------------------------- rules

export async function rulesView() {
  const page = h("div", { class: "page" });
  page.append(pageHead("Platform rules", "The rules that keep the arena fair. Free to play, no stakes, no entry fees."));
  const sections = [
    ["The basics", "Poker Arena is a free, no-stakes competition for developer-built bots. There is no real-money wagering, no entry fee and no prize pool. Ratings and points are a scoreboard, not currency."],
    ["One account", "One account per person. Upload your own work. If you fork or adapt someone else's model, say so on the bot page and link the source."],
    ["The model interface", "A bot must return a legal PlayerAction and must not crash on purpose. Faults are recorded and a bot with repeated faults is removed from the ladder."],
    ["No server-side execution", "Uploaded Python runs in the browser of whoever is testing it. The arena does not execute uploaded code on Cloudflare. Do not upload code that tries to escape the browser sandbox."],
    ["Fair play", "No bot may inspect another bot's hole cards, the deck order, or the browser storage of another user. No denial of service, no scraping the API at a rate that degrades the site."],
    ["Rated challenges", "Rated results are self-reported by the browser that ran them. They are accepted in good faith. Deliberately false results are cheating and will be reverted."],
    ["Forum conduct", "Be civil. No harassment, spam, malware or doxxing. Threads can be locked or removed. Bug reports are welcome; insults are not."],
    ["Sanctions and appeals", "A bot can be suspended, removed from the ladder or deleted. You can appeal by opening a thread in the Forum with the bot name and the reason."],
  ];
  page.append(
    h("div", { class: "card" }, h("dl", { class: "rules-list" }, sections.map(([t, b]) => [h("dt", {}, t), h("dd", {}, b)]))),
    h("div", { class: "card" },
      h("h2", {}, "Game rules"),
      h("div", { class: "actions-inline" },
        h("a", { class: "btn ghost", href: "#/games/nlh6" }, "NLH 6-max"),
        h("a", { class: "btn ghost", href: "#/games/hu" }, "NLH Heads-up"),
        h("a", { class: "btn ghost", href: "#/games/boss" }, "Big Bad Bots"),
        h("a", { class: "btn ghost", href: "#/games/draw27" }, "2-7 Triple Draw"),
        h("a", { class: "btn ghost", href: "#/games/ofc" }, "Pineapple OFC"),
      ),
    ),
  );
  return page;
}

// ---------------------------------------------------------------- season

export async function seasonView(id) {
  const page = h("div", { class: "page" });
  try {
    const data = await api("/api/seasons");
    const season = data.seasons.find((s) => s.id === String(id)) || data.seasons.find((s) => s.status === "active");
    if (!season) return h("div", { class: "page" }, empty("No season found."));
    page.append(
      pageHead(season.name, `${fmtDate(season.starts)} – ${fmtDate(season.ends)} · ${season.status}`, [h("a", { class: "btn ghost", href: "#/leaderboard" }, "Leaderboard")]),
      h("div", { class: "card" },
        h("p", {}, season.blurb),
        season.champion ? h("p", {}, ["Champion: ", h("a", { href: `#/team/${encodeURIComponent(season.champion)}` }, season.champion)]) : null,
      ),
    );
    page.append(
      h("div", { class: "card tablewrap" },
        h("table", { class: "tbl" },
          h("thead", {}, h("tr", {},
            h("th", { class: "num" }, "#"), h("th", {}, "Bot"), h("th", {}, "Type"),
            h("th", { class: "num" }, "Points"), h("th", { class: "num" }, "Rating"), h("th", {}, "Record"),
          )),
          h("tbody", {}, data.standings.map((row) =>
            h("tr", {},
              h("td", { class: "num" }, String(row.place)),
              h("td", {}, row.kind === "house" ? h("a", { href: `#/team/${encodeURIComponent(row.name)}` }, row.name) : row.name,
                row.owner ? h("span", { class: "muted small" }, ` by ${row.owner}`) : null),
              h("td", {}, badge(row.kind === "house" ? "House" : "Uploaded")),
              h("td", { class: "num" }, String(row.points)),
              h("td", { class: "num" }, String(row.rating)),
              h("td", {}, row.record),
            )
          )),
        ),
      ),
    );
  } catch (error) {
    page.append(errorCard(error));
  }
  return page;
}
