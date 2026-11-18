# New session — start here

Poker Arena is a free, no-stakes arena for developer-built poker bots, live at
**https://poker-arena.hheylau.workers.dev**.

- **Repo:** https://github.com/altcar/poker-arena (public)
- **Worker:** `poker-arena` (account Shalev, `4012f7f23d815ed28c0f45e8ce57cd3e`)
- **D1:** `poker-arena` (`dfe5a016-1a78-4de0-86e4-bac69e0f71f9`)
- **Schema:** `src/schema.sql` (`npm run db:schema`)
- **Local folder:** clone the repo anywhere; nothing local is required.

## What the site does

1. **Accounts** — email + password (PBKDF2, HttpOnly cookie sessions in D1).
2. **Bot upload** — `model.py`, up to six extra `.py` modules and an optional
   pickle. Stored in D1, executed only in the visitor's browser via Pyodide.
3. **Rated challenges** — 20 hands against a four-bot house table in the
   browser; the result updates the bot's Elo rating and division.
4. **Leaderboard** — Open / Raised / Elite. House ratings come from the
   round-robin score; uploaded bots start at 1200.
5. **Tournaments** — completed events with standings; open events with bot
   entry.
6. **Live** — a rolling feed generated from the round-robin pairings, refreshed
   every six seconds.
7. **Forum** — threads, replies, categories, pinned/locked support. Login to post.
8. **The Stack** — five articles served from `src/stack_data.js`.
9. **Newsletter** — email signup in D1.
10. **Five game modes**:
    - NLH 6-max (existing engine, 62 playable bots)
    - NLH heads-up (existing engine, 2 seats)
    - Big Bad Bots (three-boss gauntlet, per-seat stacks, no rebuys)
    - 2-7 Triple Draw (`public/engine/draw27.js`, fixed-limit lowball)
    - Pineapple OFC (`public/engine/ofc.js`, placement, fouls, royalties)

## Layout

- `src/worker.js` — assets + the original game/tournament API.
- `src/platform.js` — accounts, bots, leaderboard, forum, newsletter,
  tournaments, seasons, live and platform metadata.
- `src/platform_data.js` — games, rules, divisions, tournaments, seasons.
- `src/stack_data.js` — Stack articles.
- `src/schema.sql` — D1 tables (users, auth_sessions, user_bots, bot_results,
  threads, posts, newsletter, tournament_entries).
- `public/app.js` — router, six-max table, heads-up, boss run, upload/team
  pages, mode bar.
- `public/platform_client.js` — platform pages, auth forms, upload, rated test.
- `public/views_draw27.js` + `public/engine/draw27.js` — 2-7 Triple Draw.
- `public/views_ofc.js` + `public/engine/ofc.js` — Pineapple OFC.
- `public/engine/` — browser copy of the JS engine (generated) plus the two new
  standalone engines. `scripts/copy_browser_engine.mjs` no longer deletes the
  folder, so the extra engines survive regeneration.

## Running and deploying

```sh
npm install
npm run db:schema     # apply migrations
npm run seed          # demo account + bots + forum threads
npm run dev           # wrangler dev
npm run deploy
```

Demo login: `demo@poker-arena.local` / `arena-demo-2026`.

## Tests

```sh
npm run verify          # 100 hands, chips conserved, no faults
npm run verify:python   # real Python engine
npm run verify:js       # JS port
npm run test:modes      # 2-7 and OFC simulations (Node)
npm run test:platform   # every page + Pyodide challenge (headless Edge)
npm run test:forms      # login/upload/tournament/newsletter/forum forms
npm run test:rated      # rated 20-hand challenge through the UI
npm run test:modeui     # boss run, HU vs JS and Python bots
```

The browser tests use `--headless=new` Edge through the DevTools protocol. If a
run leaves an Edge process bound to its port, kill the process tree before the
next run (`netstat -ano | findstr 9341`).

## Known limits

- Uploaded code is not executed server-side. Rated results are self-reported by
  the browser and accepted in good faith; this is a free arena.
- No email verification, password reset or moderation UI yet. Forum threads can
  be edited in D1.
- `tvt.pkl` (33 MB) is excluded from browser assets; the model still loads from
  `model.py`.
- 11 of the 68 competition teams never published a discoverable repository.
- House bots are derived from the 1,891-match round robin; user bots only have
  rated results, not a full matrix.
