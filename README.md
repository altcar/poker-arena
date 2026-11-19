# Poker Arena

A competitive arena for developer-built poker bots. Upload a Python model, climb
the ladder, enter prize-pool tournaments and play five poker formats. Free to
play; subscriptions and sponsors fund the pool.

**Live:** <https://poker-arena.hheylau.workers.dev>

## What is on the site

| Page | What it does |
|---|---|
| **Home** | The pitch, the live ladder, the purse and the formats. |
| **Play** | Six-max, heads-up, Big Bad Bots, 2-7 Triple Draw and Pineapple OFC. |
| **Pricing** | Open, Pro and Studio plans, annual billing, comparison and FAQ. |
| **Prize pool** | Season 2 purse, monthly and weekly pools, payout splits and qualification. |
| **Business plan** | Problem, product, market, revenue streams, unit economics, projections, risks. |
| **Leaderboard** | House and uploaded bots in Open, Raised and Elite divisions. |
| **Tournaments** | Completed events with standings and open events you can enter. |
| **Live** | A rolling feed of tables in progress, refreshed every six seconds. |
| **Forum** | Threads and replies for development, tournaments and bugs. |
| **The Stack** | Five articles about formats, ratings and bot building. |
| **Newsletter** | Email signup stored in D1. |
| **Account / Upload** | Sign up, upload `model.py`, run rated 20-hand challenges. |
| **Teams / Matrix / Matches** | The 68 competition teams, the 62×62 matrix and 1,891 matches. |

## The business model

- **Open** is free: one bot, three rated challenges a day, open tournaments.
- **Pro** is $19/month or $190/year: five bots, unlimited challenges, two paid
  entries a month and full match analytics.
- **Studio** is $99/month or $990/year: 25 bots, five seats, private leagues,
  API access and sponsor slots.
- **40% of subscription revenue** is ring-fenced for the prize pool, topped up by
  sponsors. Season 2 pool: **$25,000**. Monthly purse: **$2,500**. Weekly Pro
  Cup: **$500**.
- No wagering and no paid entry fees. Prizes are skill-based and funded by
  subscriptions and sponsors. The full plan, unit economics and projections are
  on `#/business`.

## Game modes

**No-Limit Hold'em 6-max** is the main event: six seats, no-limit betting, the
format behind the 1,891-match round robin.

**No-Limit Hold'em heads-up** is two seats. The button posts the small blind and
acts first before the flop, then last afterwards.

**Big Bad Bots** is a three-round boss rush. You start each round with 1,000
chips; the bosses start with 1,500, 2,500 and 4,000. Blinds rise from 10/20 to
15/30 to 25/50. No rebuys.

**2-7 Triple Draw** is fixed-limit lowball. Five cards, three draws, four betting
rounds. Straights and flushes count against you, aces are high, and 7-5-4-3-2 is
the nuts.

**Pineapple OFC** deals five cards and you place all five; then four streets of
three, place two and discard one. The bottom row must be at least as strong as
the middle, and the middle at least as strong as the top, or the board fouls.

## Uploading a bot

A bot is one Python file defining `class Model`:

```python
from card_utils import Card
from player_action import PlayerAction

class Model:
    def seed(self, hand, player_id):
        self.hand = hand
        self.player_id = player_id

    def run(self, actions, state):
        if state.call_amount == 0:
            return PlayerAction.call(self.player_id)
        return PlayerAction.fold(self.player_id)

    def end_game(self, actions, state):
        pass
```

Uploads are limited to 200 KB of Python, six extra `.py` modules and a 700 KB
pickle. Nothing is executed on Cloudflare: uploaded code runs in the visitor's
browser through Pyodide. A rated challenge plays 20 hands against a four-bot
house table and updates the bot's Elo rating.

## Front end

The site is a React 19 single-page app built with Vite and the
[Astryx](https://astryx.design) design system. The installed neutral theme is
extended in `frontend/theme.js` with the arena's radius and typography.

```sh
npm install
npm run dev          # Vite on http://localhost:5173
npm run dev:worker   # Wrangler API on http://localhost:8787 (separate terminal)
npm run build        # production build into dist/
npm run deploy       # wrangler deploy; the build runs automatically
```

`npm run dev` proxies `/api` to Wrangler. `npm run preview` serves the built app
and proxies `/api` to the live Worker.

### Design system

- `npx astryx theme list` — installed themes.
- `npx astryx theme add neutral` — writes `src/astryx-themes.js`.
- `npx astryx component <Name>` — props and examples for a component.
- `npx astryx doctor --built dist` — setup check.

The app imports `themes` and `defaultThemeSlug` from `src/astryx-themes.js` once
and passes `themes[defaultThemeSlug]` into `defineTheme({extends, ...})` in
`frontend/theme.js`. `<Theme theme={arenaTheme} mode="system">` wraps the app in
`frontend/main.jsx`.

## Back end

- `src/worker.js` — assets plus the original game and tournament API.
- `src/platform.js` — accounts, bots, leaderboard, forum, newsletter,
  tournaments, seasons and live.
- `src/business_data.js` — plans, prize pool and business plan; served at
  `GET /api/business`.
- `src/schema.sql` — D1 tables (`users`, `auth_sessions`, `user_bots`,
  `bot_results`, `threads`, `posts`, `newsletter`, `tournament_entries`).

```sh
npm run db:schema     # apply D1 migrations
npm run seed          # demo account, three bots, forum threads
```

Demo login: `demo@poker-arena.local` / `arena-demo-2026`.

## API

| Route | Purpose |
|---|---|
| `POST /api/auth/signup` / `login` / `logout` | Account and session. |
| `GET /api/auth/me` | Current user and owned bots. |
| `GET /api/business` | Plans, prize pool, FAQ and business plan. |
| `GET /api/platform` | Games, rules, divisions, seasons, Stack index and counts. |
| `POST /api/bots` | Upload `{ name, description, mode, source, extras, pickle, pklName }`. |
| `GET /api/bots/:id/files` | `model.py`, extras and pickle for Pyodide. |
| `POST /api/bots/:id/result` | Record a rated 20-hand challenge. |
| `GET /api/leaderboard?division=` | `all`, `open`, `raised` or `elite`. |
| `GET /api/tournaments` / `:id` | Events, entries and results. |
| `POST /api/tournaments/:id/enter` | `{ botId }`. |
| `GET /api/forum/threads` / `:id` | Threads and replies. |
| `POST /api/newsletter` | `{ email }`. |
| `GET /api/live` | Rolling live feed. |
| `GET /api/stack` / `:slug` | Articles. |
| `POST /api/session` | Server-side table with the five JavaScript bots. |
| `GET /api/tournament` | The 1,891-match round robin, matrix, teams and matches. |

## Verification

```sh
npm run verify          # 100 hands, chips conserved, no faults
npm run verify:python   # real Python engine
npm run verify:js       # JavaScript port
npm run test:modes      # 2-7 Triple Draw and OFC simulations
npm run test:e2e        # Playwright: every page, all game modes, Pyodide, mobile
```

`npm run test:e2e` drives headless Edge through Playwright against the deployed
site. It checks 35 assertions, including a real 20-hand Pyodide challenge, the
six-max table, heads-up, the boss run, 2-7, OFC, login, annual pricing and mobile
overflow. Screenshots are written to the system temp folder.

## Layout

```
frontend/
  main.jsx, App.jsx, theme.js, styles.css
  api.js, auth.jsx, router.js
  components/       Shell, page header, stats, footer
  pages/            marketing, content, data and account pages
  play/             play context, poker table, draw27 and OFC tables
src/
  worker.js, platform.js, business_data.js, schema.sql
  engine.js, game.js, bots.js, policy.js, ...   the JS engine
  draw27.js, ofc.js                             the two new game engines
public/
  team-files/       the 57 competition submissions
  py/shim.py        browser shims for the competition API
  data/             downloadable tournament JSON
legacy/             the previous vanilla UI and browser tests, for reference
```

## Notes

- The 57 competition models keep their original Python and run through Pyodide.
  The five house JavaScript bots start instantly.
- Uploaded Python never executes on the server.
- Ratings for house bots are derived from the round-robin score; uploaded bots
  start at 1200 and move on an Elo curve. Rated results are self-reported.
- `tvt.pkl` (33 MB) is excluded from browser assets; the model still loads from
  `model.py`.
