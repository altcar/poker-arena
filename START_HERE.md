# New session — start here

Read this first. It summarises the project so a fresh conversation can pick up
where this one left off.

## Project

A playable web app at **https://poker-arena.hheylau.workers.dev** where you sit at a
six-handed no-limit Texas hold'em table with the five bots from the
[altcar/poker_bot](https://github.com/altcar/poker_bot) fork, plus the
competition's teams, matches, head-to-head and teams pages.

- **Repo:** https://github.com/altcar/poker-arena (public)
- **Folder:** `C:\Users\c85-lau\OneDrive - UWE Bristol\poker-arena`
- **Worker:** `poker-arena`, account `Shalev` (`4012f7f23d815ed28c0f45e8ce57cd3e`)
- **D1:** `poker-arena` (`dfe5a016-1a78-4de0-86e4-bac69e0f71f9`)
- **Latest commit:** see `git log -1`

## The five playable bots

| Key | Name | Plan | Tier | Style |
|---|---|---|---|---|
| `conservative` | Conservative | A | 1 | Tight hand-strength heuristic |
| `workhorse` | Workhorse | B | 2 | The submitted `goldman-stanley.pkl` |
| `adaptive` | Adaptive | C | 3 | + opponent counters |
| `mixed` | Mixed | D | 4 | + texture / MDF / bluffs |
| `aggressive` | Aggressive | E | 5 | + population exploits |

These are the only models that run in the browser. The competition teams are
Python pickles; they play the offline round robin.

## What this session added

1. **Competition teams found from the official repo's forks.** Every team
   forked `Manchester-AI-Society/poker_bot`, so the fork list is the reliable
   map. `scripts/sync_team_files.mjs` maps a fork to a team from its pickle
   filename (e.g. `apex_poker.pkl`), clones it, and copies the pickle plus the
   `.py` files it imports into `all-teams/<team>/`.
   - **57 of 68 teams downloaded**, 11 not publicly discoverable.
   - `data/team_files.csv` — team, status, repo, `model.py`, pickle, files.
   - `data/team_repos_resolved.json` + `data/team_repos_extra.json` — verified
     repo URLs.
   - `all-teams/` now has 62 folders (5 fork bots + 57 competition teams).
2. **Teams page links to the repository where the model was found.** The
   `/api/tournament` response includes `teamsData`; the page uses the verified
   `github_repo` and falls back to the team detail page when no repo was found.
3. **First-time Play flow.** Play asks you to pick opponents on the Teams page.
   Teams has checkboxes for the five playable bots and a floating
   **Continue (N selected)** button bottom right. Continue returns to Play,
   asks “Ready to play?”, and starts the game (or lets you watch).
4. **Play layout.** Seat 0 (you) is at bottom centre and the other seats are
   spread around the oval with a gap at the bottom, so no bot covers your
   cards. A **Start a new game** button is always visible once a table exists.
5. **Our own round robin.** All 62 available models play each other (10
   duplicate deals per pairing, seats swapped). The Head-to-head page shows our
   matrix and standings first; the official competition matrix is in a
   collapsible section. The Matches page shows our matches with a team filter
   and a JSON download.

## Architecture

- `src/` — JS engine (line-for-line port of the Python `engine.py`,
  `game_state.py`, `card_utils.py`, `player_action.py`, `policy.py`,
  `botcore.py`), the five bots, the serialisable `Game`, and the Worker.
- `src/pyrandom.js` — Python MT19937 + `random.sample`, so the JS Monte-Carlo
  draws match the Python model exactly.
- `src/worker.js` — static assets + JSON API.
- `src/our_tournament_data.js` — generated from `data/tournament_results.json`.
- `public/` — SPA (`index.html`, `app.js`, `style.css`, `data/`).
- `python/` — original Python source + `goldman-stanley.pkl` + `model_a.py`…`model_e.py`.
- `all-teams/` — each bot's `.pkl` and the `.py` files it imports.
- `scripts/` — fork sync, CSV, tournament and verification tools.
- `data/` — generated dumps (`team_files.csv`, `team_repos_resolved.json`,
  `tournament_results.json`, `available_teams.json`, …).

## Verification

- `npm run verify` — 100 hands, chips conserved, no faults, all actions legal.
- JS vs Python: **50/50 hands identical**.
- `pytest tests/test_model.py` in `poker_bot/` — 9 passed.
- `scripts/test_team_models.py` — all 56 competition model folders load.
- `scripts/test_all_pkls.py` — the pickles load with the correct path order.
- Headless Edge checked the new Play and Teams pages locally.

## Team sync commands

```sh
cd "C:\Users\c85-lau\OneDrive - UWE Bristol\poker-arena"
node scripts/sync_team_files.mjs      # forks -> all-teams/
node scripts/finalize_team_files.mjs  # rebuild data/team_files.csv
node scripts/make_teams_data.mjs      # verified repo links in src/teams_data.js
```

## Tournament commands

```sh
.venv/Scripts/python.exe scripts/run_tournament.py --deals 10 --shard 0/4
.venv/Scripts/python.exe scripts/merge_tournament.py
node scripts/make_our_tournament.mjs
```

## Deployment

1. `npm run deploy` (wrangler), or push to `main` (Workers Builds).
2. GitHub Actions is also configured in `.github/workflows/deploy.yml`.

## Known limitations

1. **11 of 68 teams are missing** (`ayo`, `byteclub`, `fh2`,
   `ineffective-altruists`, `ludus`, `oridox-solutions`, `pocketaces`,
   `prefer-not-to-say`, `shivateja`, `teammeat`, `zd`). They did not publish a
   discoverable repository. `data/missing_candidates.json` has the search
   results; add a URL to `data/team_repos_extra.json` and rerun the sync.
2. **Competition models are not playable in the browser.** They are Python
   pickles; only the five fork bots have a JS port. The picker therefore offers
   the five playable bots (you + five = six seats).
3. **Our round robin uses 10 deals per pairing**, so the confidence intervals
   are wide. Re-run with `--deals 20` or more for tighter intervals (it takes
   longer).
4. Some competition models fault (the engine checks/folds for them); the
   faults are shown in the matches and standings.

## Key commands

```sh
npm run dev          # wrangler dev
npm run deploy       # wrangler deploy
npm run verify       # engine invariants
node scripts/test_api.mjs   # API smoke test
```
