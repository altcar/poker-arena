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

Both methods are now set up for this project:

1. **Cloudflare's native Git integration (verified).** The Worker is connected
   to `altcar/poker-arena` via Workers Builds: pushes to `main` trigger a
   build that runs `wrangler deploy`. A test build succeeded end-to-end on
   2026-11-10. Manage it in the dashboard under
   **Workers & Pages → poker-arena → Settings → Builds**.
2. **GitHub Actions + Wrangler.** `.github/workflows/deploy.yml` runs
   `wrangler deploy` on every push to `main`. Add the repository secrets
   `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`. The workflow file is
   on `main`.

## The API

| Route | Purpose |
|---|---|
| `POST /api/session` | Start a table. Body: `{ humanName, botKeys, humanPlaying, stack, smallBlind, bigBlind, seed }`. |
| `GET /api/session/:id` | Current state. |
| `POST /api/session/:id/action` | `{ kind: "fold" \| "call" \| "raise", amount }`. |
| `POST /api/session/:id/next` | Deal the next hand. |
| `POST /api/session/:id/rebuy` | Reset every stack. |
| `POST /api/decision` | Ask one bot for its move. Body: `{ bot, hole, playerId, state, actions }`. |
| `GET /api/tournament` | Our round robin (standings, matrix, matches), the official competition data, and team metadata. |
| `GET /api/teams` | Team metadata and the downloaded competition teams. |
| `GET /api/teams.csv` | The teams with the GitHub repositories where their models were found. |
| `GET /api/matches` | Our round-robin matches. |
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

## Competition teams and their models

Every team forked the official
[`Manchester-AI-Society/poker_bot`](https://github.com/Manchester-AI-Society/poker_bot)
repository, so the fork list is the reliable way to find a team's model (a
team-name search alone matches unrelated repositories). The sync script:

1. lists the 60 forks of the official repository;
2. maps each fork to a team from its pickle filename (e.g. `apex_poker.pkl`),
   its repository/owner name, or a manual alias;
3. clones the fork and keeps the pickle plus the `.py` files it imports;
4. falls back to `gh search repos <team>` for teams that did not fork;
5. writes `data/team_files.csv` and `data/team_repos_resolved.json`.

```sh
node scripts/sync_team_files.mjs     # download model.py + pkl into all-teams/
node scripts/finalize_team_files.mjs # rebuild team_files.csv
node scripts/make_teams_data.mjs     # verified repo links in src/teams_data.js
```

`data/team_files.csv` has one row per competition team: status, repository,
`model.py`, pickle, and the other files. 57 of the 68 teams were found. The
other 11 (e.g. `ayo`, `byteclub`, `fh2`) did not publish a discoverable
repository; `data/missing_candidates.json` records the search results.

`all-teams/<team>/` holds the files needed to run each bot: the `.pkl` plus
the `.py` files it imports (the same set `submit.py` lists for a submission).
The five bots in this fork are created by `scripts/seed_my_bots.py`.

## Our round robin

The head-to-head and matches pages use our own round robin, not the live
competition's matrix. It is run with the real Python engine over every model
whose files we have, 10 duplicate deals per pairing with the seats swapped:

```sh
# 4 shards in parallel (PowerShell)
0..3 | ForEach-Object {
  Start-Process .venv/Scripts/python.exe -ArgumentList "scripts/run_tournament.py","--deals","10","--shard","$_/4"
}
.venv/Scripts/python.exe scripts/merge_tournament.py
node scripts/make_our_tournament.mjs
```

The results are in `data/tournament_results.json` (matches, per-deal values,
standings and the two-way matrix) and are served from
`src/our_tournament_data.js` at `GET /api/tournament`.

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
public/            the single-page app (Play, Teams, Head-to-head, Matches, About)
  engine/          browser copy of the JS engine (generated)
  py/shim.py       browser shims for card_utils, player_action, game_state
  python_runtime.js  Pyodide loader and PythonBot adapter
  team-files/      each competition team's submission files for the browser
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
- The five-bot board in `public/data/` is generated by
  `scripts/make_tournament.py` (duplicate deals, seats swapped) using the real
  Python engine. Our all-model round robin is generated by
  `scripts/make_our_tournament.mjs`.
- The five bots are the A-E variants from commit `14bc9b9` of the fork; the
  submitted `goldman-stanley.pkl` is variant B.
- Competition models are Python pickles: they play the offline round robin,
  and they also play live in the browser through Pyodide. The five fork bots
  are hand-written JavaScript ports and start instantly.
- The browser runtime files are generated:
  `node scripts/prepare_browser_runtime.mjs` (team files) and
  `node scripts/copy_browser_engine.mjs` (JS engine).
