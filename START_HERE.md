# New session — start here

Poker Arena is a competitive arena for developer-built poker bots, live at
**https://poker-arena.hheylau.workers.dev**.

- **Repo:** https://github.com/altcar/poker-arena (public)
- **Worker:** `poker-arena` (account Shalev, `4012f7f23d815ed28c0f45e8ce57cd3e`)
- **D1:** `poker-arena` (`dfe5a016-1a78-4de0-86e4-bac69e0f71f9`)
- **Schema:** `src/schema.sql` (`npm run db:schema`)
- **Demo login:** `demo@poker-arena.local` / `arena-demo-2026`

## Stack

- **React 19 + Vite** in `frontend/`; built into `dist/`.
- **Astryx** design system (neutral theme extended in `frontend/theme.js`).
- **Cloudflare Worker + D1** in `src/`.
- **Pyodide** runs uploaded and competition Python in the browser.
- **Playwright** (headless Edge) validates the deployed site.

## Pages

Home, Play, Pricing, Prize pool, Business plan, Games, Leaderboard,
Tournaments, Live, Forum, The Stack, Newsletter, Rules, About, Teams,
Head-to-head, Matches, Season, Account, Upload, Bot test, Login, Signup.

## Product model

- Open (free), Pro ($19/mo), Studio ($99/mo); annual billing available.
- 40% of subscription revenue funds the prize pool, plus sponsors.
- Season 2 pool $25,000; $2,500 monthly; $500 weekly Pro Cup.
- No wagering, no paid entry fees; skill-based competition only.

Content lives in `src/business_data.js` and is served at `GET /api/business`.

## Important files

- `frontend/main.jsx` — imports Astryx reset/base CSS and mounts `<Theme>`.
- `frontend/theme.js` — `defineTheme({extends: themes[defaultThemeSlug]})`.
- `frontend/components/Shell.jsx` — top nav, footer, account state.
- `frontend/pages/marketing.jsx` — Home, Pricing, PrizePool, Business, Rules, About.
- `frontend/pages/content.jsx` — Games, Stack, Newsletter, Live, Forum, Season.
- `frontend/pages/data.jsx` — Leaderboard, Tournaments, Teams, Matrix, Matches.
- `frontend/pages/account.jsx` — Login, Signup, Account, Upload, rated BotTest.
- `frontend/play/` — play context, poker table, 2-7 and OFC tables.
- `src/platform.js` — platform API.
- `src/draw27.js`, `src/ofc.js` — the two extra game engines.
- `src/worker.js` — assets + game API + `/api/business`.

## Run and deploy

```sh
npm install
npm run db:schema
npm run seed
npm run dev            # Vite, proxies /api to :8787
npm run dev:worker     # Wrangler API in a second terminal
npm run build
npm run deploy         # wrangler.jsonc runs the Vite build first
```

## Validate

```sh
npm run verify          # engine invariants
npm run verify:python
npm run verify:js
npm run test:modes      # draw27 + OFC simulations
npm run test:e2e        # Playwright, 35 checks, desktop + mobile
npx astryx doctor --built dist
```

Playwright uses the installed Edge (`channel: "msedge"`), so no browser download
is required. It covers every route, annual pricing, leaderboard filtering,
forum, six-max, heads-up, boss run, 2-7, OFC, a real Pyodide challenge, login and
mobile horizontal overflow. Screenshots go to the system temp folder.

## Astryx conventions

- Read `AGENTS.md` first. Components come from `@astryxdesign/core/<Name>`.
- Use components for layout (`VStack`, `HStack`, `Grid`, `Section`, `Card`)
  instead of raw divs; the poker table and cards are the deliberate exception.
- Style with component props, then tokens (`var(--color-*)`, `--spacing-*`).
  Avoid raw hex and px.
- `npx astryx component <Name>` prints props; `npx astryx doctor --built dist`
  must pass before delivery.

## Known limits

- Uploaded code runs in the visitor's browser; rated results are self-reported
  and accepted in good faith.
- No payment provider is wired yet. Pricing and plan limits are product copy;
  enforcement and billing are the next step.
- No password reset or moderation UI.
- 11 of the 68 competition teams never published a discoverable repository.
- `legacy/` holds the previous vanilla UI and CDP tests for reference only.
