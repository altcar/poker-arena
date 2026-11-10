# Poker Arena

A playable web app where **you** sit at a six-handed no-limit Texas hold'em
table with the five bots from the
[altcar/poker_bot](https://github.com/altcar/poker_bot) fork, plus the
competition site's tournament, head-to-head matrix, teams and matches pages.

The engine and the bots are a line-for-line JavaScript port of the Python
code, verified hand-for-hand against the real Python engine (see
[Verification](#verification)).

## The five bots

The fork's history has five model variants, `model_a.py` … `model_e.py`,
built on a shared engine (`botcore.py` + `policy.py`). They are renamed here
by how they play:

| Seat | Name | Plan | Tier | Style |
|---|---|---|---|---|
| 1 | **Conservative** | A | 1 | Tight hand-strength heuristic. No Monte-Carlo, no opponent model, no bluffs. |
| 2 | **Workhorse** | B | 2 | The submitted `goldman-stanley.pkl` model: 25 bb preflop ranges, Monte-Carlo equity, pot-odds calls. |
| 3 | **Adaptive** | C | 3 | Workhorse plus per-team VPIP/PFR/aggression counters that drive the equity range filter. |
| 4 | **Mixed** | D | 4 | Adaptive plus board texture, MDF defence, blocker bluffs and mixed frequencies. |
| 5 | **Aggressive** | E | 5 | Maximum aggression: population exploits, overbet shoves, thin hero calls. |

The parameters are the actual class attributes from the Python files. The
original Python source is in [`python/`](python/), including the submitted
`goldman-stanley.pkl` and the recovered `model_a.py` … `model_e.py`.

## Run it locally

You need Node.js 20+ and Wrangler.

```sh
npm install            # installs wrangler locally (optional; a global wrangler works)
npm run dev            # wrangler dev, usually http://localhost:8787
```

Open <http://localhost:8787>, go to **Play**, and start a table. The session
is stored in the worker's D1 database, so you can close the tab and resume.

## Deploy to worker.dev

```sh
npm run deploy         # wrangler deploy
```

The worker is named `poker-arena` and serves the static app from `public/`
plus the JSON API. The D1 database is created and migrated with:

```sh
wrangler d1 create poker-arena
wrangler d1 execute poker-arena --command "CREATE TABLE ..."   # see src/worker.js
```

### Git-connected deployments

There are two ways to deploy on every push:

1. **Cloudflare's native Git integration (recommended).** In the dashboard go
   to **Workers & Pages → poker-arena → Settings → Builds → Connect**, choose
   GitHub, and select `altcar/poker-arena`. Pushes to `main` then build and
   deploy automatically.
2. **GitHub Actions + Wrangler.** `.github/workflows/deploy.yml` runs
   `wrangler deploy` on every push to `main`. Add two repository secrets:
   - `CLOUDFLARE_API_TOKEN` — a Cloudflare API token with
     `Cloudflare Workers: Edit` (and D1 if you want it to migrate),
   - `CLOUDFLARE_ACCOUNT_ID` — your account id.

   The workflow file is committed; pushing it needs the `workflow` scope on
   the GitHub token. Run `gh auth refresh -h github.com -s workflow`, enter
   the one-time code at <https://github.com/login/device>, then `git push`.
   Without that scope, add the file yourself from
   `.github/workflows/deploy.yml`.

## The API

| Route | Purpose |
|---|---|
| `POST /api/session` | Start a 6-player table. Body: `{ humanName, stack, smallBlind, bigBlind, seed }`. |
| `GET /api/session/:id` | Current state. |
| `POST /api/session/:id/action` | `{ kind: "fold" \| "call" \| "raise", amount }`. |
| `POST /api/session/:id/next` | Deal the next hand. |
| `POST /api/session/:id/rebuy` | Reset every stack. |
| `POST /api/decision` | Ask one bot for its move. Body: `{ bot, hole, playerId, state, actions }`. |
| `GET /api/tournament` | Standings, matrix, matches and team metadata. |
| `GET /api/teams` | Team metadata. |
| `GET /api/teams.csv` | The live competition's teams with their GitHub repositories. |
| `POST /api/matches` | Record a match result. |

The game is stateful: the worker stores the whole `Game` (stacks, button,
hand, and each bot's opponent model) as JSON in D1 and reloads it per
request.

## Verification

The JavaScript port is checked against the real Python engine, action for
action:

```sh
# 1. Play the same 50 hands with both engines (needs the venv in .venv)
.venv/Scripts/python.exe scripts/python_batch.py 50
node scripts/js_batch.mjs 50

# 2. Check the engine invariants and compare the two dumps
npm run verify
```

`npm run verify` plays 100 six-player hands and checks that chips are
conserved, no bot faults, and every action is legal. When the Python dump is
present it also compares the two dumps hand-for-hand; they are identical.

The exact match works because the port uses Python's own MT19937 and
`random.sample` (`src/pyrandom.js`), so the Monte-Carlo draws are the same as
the Python model's for a given seed and deck.

## The teams CSV

`scripts/make_teams_csv.mjs` reads the team names from the live competition
site (`https://poker-arena-site.vercel.app/data/teams.json`), searches GitHub
for each one with the `gh` CLI, and writes:

- `data/all-teams.csv` — team, site URL, search URL, GitHub repository, description
- `src/teams_data.js` — the same rows, served by `GET /api/teams.csv`

```sh
npm run csv
```

The competition is over, so the file is frozen.

## The all-teams folder

`all-teams/<team>/` holds the files needed to run each bot: the `.pkl` plus
the `.py` files it imports (the same set `submit.py` lists for a submission).
The five bots in this fork are created by `scripts/seed_my_bots.py`; the
competition teams are downloaded by `scripts/download_team_files.mjs`, which
finds each team's public repository with GitHub search, clones it, and copies
only the required files:

```sh
.venv/Scripts/python.exe scripts/seed_my_bots.py
node scripts/download_team_files.mjs
```

`data/all-teams-report.json` records which teams were found and which were
not. The competition's submission form (which held the repository links) is
private, so a few teams cannot be found by search; drop their repository URL
into `data/team_repos.json` and re-run the script to add them.

## Project layout

```
src/               the JavaScript engine, bots, game and worker
  card_utils.js    cards and hand evaluation
  player_action.js moves
  game_state.js    the table state machine
  engine.js        a hand, plus the interactive HandSession
  botcore.js       equity, preflop percentiles, opponent model
  pyrandom.js      Python's MT19937 and random.sample
  policy.js        the shared decision engine
  bots.js          the five renamed bots
  game.js          the 6-player table (serialisable)
  worker.js        the Worker: static assets + API
public/            the single-page app (Play, Tournament, Teams, Matrix, Matches, About)
  data/            the precomputed tournament and the live competition's board
all-teams/         each bot's .pkl and the .py files it needs
python/            the original Python source, including the pickle and the A-E variants
scripts/           verification, tournament precompute, CSV and team download tools
data/              generated dumps, the CSV and the live competition data
.github/workflows/ the Wrangler deploy workflow
```

## Notes

- The Python engine is not required to run the web app; it is used to
  verify the port and to precompute the tournament.
- The tournament data in `public/data/` is generated by
  `scripts/make_tournament.py` (100 duplicate deals per pairing, seats
  swapped) using the real Python engine.
- The five bots are the A-E variants from commit `14bc9b9` of the fork; the
  submitted `goldman-stanley.pkl` is variant B.
