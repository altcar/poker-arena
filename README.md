# Poker Arena

A free, no-stakes arena for developer-built poker bots. Upload a Python model,
challenge the house bots, enter tournaments, post on the forum and climb the
leaderboard. You can also sit down yourself in five formats.

**Live:** <https://poker-arena.hheylau.workers.dev>

## What is on the site

| Page | What it does |
|---|---|
| **Play** | Sit at a six-max table with the five JavaScript bots or any of the 57 competition Python models, or pick a game mode. |
| **Games** | NLH 6-max, NLH Heads-up, Big Bad Bots (a three-boss gauntlet), 2-7 Triple Draw and Pineapple OFC, each with its own rules page. |
| **Leaderboard** | House bots and uploaded bots in three divisions: Open, Raised and Elite. |
| **Tournaments** | Completed events with standings, plus open events you can enter with an uploaded bot. |
| **Live** | A rolling live-match feed generated from the round-robin pairings, refreshed every six seconds. |
| **Forum** | Threads and replies for development, tournaments and bugs. Login required to post. |
| **The Stack** | Articles about the match format, bot development, OFC and 2-7 strategy. |
| **Newsletter** | Email signup, stored in D1. |
| **Account / Upload** | Create an account, upload `model.py` plus modules and an optional pickle, run a rated 20-hand challenge. |

## Game modes

**No-Limit Hold'em 6-max** is the main event: two hole cards, five community
cards, no-limit betting, six seats. The 1,891-match round robin uses this
format.

**No-Limit Hold'em heads-up** is two seats. The button posts the small blind
and acts first before the flop, then last afterwards.

**Big Bad Bots** is a three-round boss rush. You start each round with 1,000
chips; the bosses start with 1,500, 2,500 and 4,000. Blinds rise from 10/20 to
15/30 to 25/50. No rebuys: take the boss's stack to advance, lose yours and the
run restarts.

**2-7 Triple Draw** is fixed-limit lowball. Five cards, three draws, four
betting rounds. Straights and flushes count against you and aces are always
high. The best hand is 7-5-4-3-2 in mixed suits.

**Pineapple OFC** is open-face Chinese poker. Round 1 deals five cards and you
place all five; rounds 2-5 deal three, you place two and discard one. The
bottom row must be at least as strong as the middle, and the middle at least as
strong as the top, or the board fouls. Scoring is row-by-row plus royalties.

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

The arena provides `card_utils`, `player_action` and `game_state` shims in the
browser, so submitted models run unchanged. Uploads are limited to 200 KB of
Python, six extra `.py` modules and a 700 KB pickle. Nothing is executed on
Cloudflare: the model runs in the visitor's browser through Pyodide.

A **rated challenge** loads the model and plays 20 hands against a four-bot
house table, then posts the result. The bot's Elo rating and division move with
the result.

## The house bots

The five JavaScript bots are the A-E variants from the
[altcar/poker_bot](https://github.com/altcar/poker_bot) fork, renamed by style:

| Seat | Name | Plan | Tier | Style |
|---|---|---|---|---|
| 1 | **Conservative** | A | 1 | Tight hand-strength heuristic. No Monte-Carlo, no opponent model, no bluffs. |
| 2 | **Workhorse** | B | 2 | The submitted `goldman-stanley.pkl` model: 25 bb preflop ranges, Monte-Carlo equity, pot-odds calls. |
| 3 | **Adaptive** | C | 3 | Workhorse plus per-team VPIP/PFR/aggression counters. |
| 4 | **Mixed** | D | 4 | Adaptive plus board texture, MDF defence, blocker bluffs and mixed frequencies. |
| 5 | **Aggressive** | E | 5 | Maximum aggression: population exploits, overbet shoves, thin hero calls. |

The 57 competition models keep their submitted Python and also run in the
browser. They were recovered from forks of
[`Manchester-AI-Society/poker_bot`](https://github.com/Manchester-AI-Society/poker_bot);
the Teams page links each one to the GitHub repository where its model was
found.

## Run it locally

You need Node.js 20+ and Wrangler.

```sh
npm install
npm run dev            # wrangler dev, usually http://localhost:8787
```

The app needs D1. The schema is in `src/schema.sql`:

```sh
npm run db:schema      # wrangler d1 execute poker-arena --remote --file=src/schema.sql
npm run seed           # demo account, three uploaded bots, forum threads
```

The demo account is `demo@poker-arena.local` / `arena-demo-2026`.

## Deploy

```sh
npm run deploy
```

The worker is `poker-arena` and serves the static app from `public/` plus the
JSON API. D1 database: `poker-arena`
(`dfe5a016-1a78-4de0-86e4-bac69e0f71f9`).

Pushes to `main` also deploy through Cloudflare Workers Builds and the GitHub
Actions workflow in `.github/workflows/deploy.yml`.

## API

| Route | Purpose |
|---|---|
| `POST /api/auth/signup` | `{ email, handle, password }`. Sets an HttpOnly cookie. |
| `POST /api/auth/login` | `{ email, password }`. |
| `POST /api/auth/logout` | Clear the session. |
| `GET /api/auth/me` | Current user and their bots. |
| `GET /api/platform` | Games, rules, divisions, tournaments, seasons, Stack index and counts. |
| `POST /api/bots` | Upload a bot. `{ name, description, mode, source, extras, pickle, pklName }`. |
| `GET /api/bots` | Public bot list. `?mine=1` for your own. |
| `GET /api/bots/:id` | Bot metadata. |
| `GET /api/bots/:id/files` | `model.py`, extras and pickle for browser execution. |
| `POST /api/bots/:id/result` | Record a rated challenge and update Elo. |
| `GET /api/bots/:id/matches` | The bot's recent results. |
| `DELETE /api/bots/:id` | Delete your bot. |
| `GET /api/leaderboard?division=` | Leaderboard for `all`, `open`, `raised` or `elite`. |
| `GET /api/tournaments` | Tournaments and entries. |
| `POST /api/tournaments/:id/enter` | `{ botId }`. |
| `GET /api/forum/threads` | Threads with reply counts. |
| `POST /api/forum/threads` | `{ title, body, category }`. |
| `GET /api/forum/threads/:id` | Thread and replies. |
| `POST /api/forum/threads/:id/posts` | `{ body }`. |
| `POST /api/newsletter` | `{ email }`. |
| `GET /api/seasons` | Seasons and standings. |
| `GET /api/live` | Live-match feed. |
| `GET /api/stack` | Stack article index. |
| `GET /api/stack/:slug` | One article. |
| `POST /api/session` | Server-side table with the five JS bots. |
| `GET /api/session/:id` | Current state. |
| `POST /api/session/:id/action` | `{ kind: "fold" \| "call" \| "raise", amount }`. |
| `POST /api/session/:id/next` | Next hand. |
| `POST /api/session/:id/rebuy` | Reset stacks. |
| `GET /api/tournament` | The 1,891-match round robin, matrix, teams and matches. |
| `GET /api/teams.csv` | Teams with the GitHub repositories where their models were found. |

## Verification

```sh
npm run verify          # 100 hands, chips conserved, no faults, legal actions
npm run verify:python   # the real Python engine
npm run verify:js       # the JavaScript port
npm run test:modes      # 2-7 Triple Draw and Pineapple OFC simulations
npm run test:platform   # headless Edge against the deployed site: every page
npm run test:forms      # login, upload, tournament entry, newsletter, forum
npm run test:rated      # 20-hand rated challenge through the UI
npm run test:modeui     # Big Bad Bots, heads-up against JS and Python bots
```

The JS port is checked against the real Python engine action for action. It
reproduces Python's MT19937 and `random.sample`, so a given seed and deck
produce the same Monte-Carlo draws.

## Project layout

```
src/
  card_utils.js, player_action.js, game_state.js, engine.js   the engine
  botcore.js, pyrandom.js, policy.js, bots.js                 the five JS bots
  game.js                                                     serialisable table
  worker.js                                                   assets + API
  platform.js                                                 accounts, bots, forum, ...
  platform_data.js, stack_data.js                             games, tournaments, articles
  schema.sql                                                  D1 tables
public/
  app.js                    router, table UI, play modes
  platform_client.js        platform pages and forms
  views_draw27.js           2-7 Triple Draw table
  views_ofc.js              Pineapple OFC table
  engine/                   browser copy of the JS engine + draw27.js + ofc.js
  py/shim.py                browser shims for the competition API
  python_runtime.js         Pyodide loader and PythonBot adapter
  team-files/               the 57 competition submissions, for the browser
all-teams/                  each competition team's model.py, pickle and imports
python/                     the original Python source
scripts/                    sync, tournament, seed and browser-test tools
data/                       team CSV, resolved repos, tournament results
```

## Notes

- Python is not required to run the site. It is used to verify the port, to
  precompute the round robin and to run models locally.
- Competition models are unchanged Python running through Pyodide. The five
  fork bots are hand-written JavaScript ports and start instantly.
- Browser runtime files are generated:
  `node scripts/prepare_browser_runtime.mjs` and
  `node scripts/copy_browser_engine.mjs`.
- `tvt.pkl` is excluded from the browser assets (33 MB); the model still loads
  from `model.py`.
- Ratings for house bots are derived from the round-robin score; uploaded bots
  start at 1200 and move on an Elo curve. Rated results are self-reported and
  accepted in good faith.
