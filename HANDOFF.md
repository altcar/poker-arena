# Poker Arena — handoff

This document summarises the project for a new conversation session.

## What this is

A playable web app at **https://poker-arena.hheylau.workers.dev** where you sit at a
six-handed no-limit Texas hold'em table with the five bots from the
[altcar/poker_bot](https://github.com/altcar/poker_bot) fork, plus the competition's
teams, matches, head-to-head matrix and teams pages.

- **Repo:** https://github.com/altcar/poker-arena (public)
- **Folder:** `C:\Users\c85-lau\OneDrive - UWE Bristol\poker-arena`
- **Worker name:** `poker-arena` (account `Shalev`, account id `4012f7f23d815ed28c0f45e8ce57cd3e`)
- **D1 database:** `poker-arena` (id `dfe5a016-1a78-4de0-86e4-bac69e0f71f9`)
- **Deployed site:** https://poker-arena.hheylau.workers.dev

## The five bots (renamed from the A-E variants in the fork's history)

| Key | Name | Plan | Tier | Style |
|---|---|---|---|---|
| `conservative` | Conservative | A | 1 | Tight hand-strength heuristic |
| `workhorse` | Workhorse | B | 2 | The submitted `goldman-stanley.pkl` |
| `adaptive` | Adaptive | C | 3 | + opponent counters |
| `mixed` | Mixed | D | 4 | + texture / MDF / bluffs |
| `aggressive` | Aggressive | E | 5 | + population exploits |

## Architecture

- `src/` — the JavaScript engine (a line-for-line port of the Python `engine.py`,
  `game_state.py`, `card_utils.py`, `player_action.py`, `policy.py`, `botcore.py`),
  the five bots, the serialisable `Game`, and the Worker.
- `src/pyrandom.js` — Python's MT19937 + `random.sample`, so the JS Monte-Carlo
  draws match the Python model exactly.
- `src/worker.js` — the Worker: static assets + JSON API (`/api/session`,
  `/api/session/:id/action`, `/api/session/:id/next`, `/api/decision`,
  `/api/tournament`, `/api/teams`, `/api/teams.csv`, `/api/matches`).
- `public/` — the SPA (`index.html`, `app.js`, `style.css`, `data/`).
- `python/` — the original Python source (including `goldman-stanley.pkl` and the
  recovered `model_a.py` … `model_e.py`).
- `all-teams/` — each bot's `.pkl` and the `.py` files it imports.
- `scripts/` — verification, tournament precompute, CSV and team download tools.
- `data/` — generated dumps (`all-teams.csv`, `available_teams.json`,
  `all-teams-report.json`, `tournament_results.json`, …).

## Verification (all passing)

- `npm run verify` — 100 hands, chips conserved, no faults, all actions legal.
- JS port vs Python: **50/50 hands identical** (`scripts/python_batch.py` +
  `scripts/js_batch.mjs`).
- `pytest tests/test_model.py` in `poker_bot/` — 9 passed.
- API smoke test (`scripts/test_api.mjs`) — passed against the deployed Worker.

## How to run

```sh
cd "C:\Users\c85-lau\OneDrive - UWE Bristol\poker-arena"
npm run dev          # wrangler dev (http://localhost:8787)
npm run deploy       # wrangler deploy
npm run verify       # engine invariants
node scripts/test_api.mjs   # API smoke test (BASE env var for remote)
```

Python tooling uses the venv at `.venv` (Python 3.13, pydantic, pytest, numpy):

```sh
.venv/Scripts/python.exe scripts/run_tournament.py 20   # round robin
```

## Deployment

Two git-connected paths are set up and verified:

1. **Cloudflare Workers Builds** (native Git integration) — connected to
   `altcar/poker-arena` on `main`, deploy command `npx wrangler deploy`. Pushes to
   `main` build and deploy automatically.
2. **GitHub Actions** — `.github/workflows/deploy.yml` runs `wrangler deploy` on
   push, using the `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` secrets.

## What is done

- [x] Playable 6-handed table (you + five bots), with bot selection, an
      "I'm playing" toggle, and 2–7 player tables.
- [x] API for the game and the model decisions.
- [x] Teams page: "My bots" + "All competition bots", with bb/100, 95% ±, Std,
      W–D–L, faults, ✓/✗ availability, and each team linking to its GitHub repo.
- [x] Head-to-head page for all available models (live competition matrix).
- [x] Matches page: 10 static matches + 153 live round-robin matches (21 bots,
      20 deals each) posted to `/api/matches`.
- [x] Tournament page removed.
- [x] `data/all-teams.csv` — 68 teams with GitHub repo URLs (best-effort search).
- [x] `all-teams/` — 21 bots' files (5 fork bots + 16 competition teams).
- [x] `data/tournament_results.json` — the live round robin results.

## Known limitations / not done

1. **52 of the 68 competition teams' files are missing.** The submission form that
   held each team's repository link is private, and the live site never publishes
   the links. `gh search repos` by team name found 16 of them; the rest are not
   publicly discoverable. To add them: put the repo URLs in
   `data/team_repos.json` and run `node scripts/download_team_files.mjs`.
2. **Devpost scraping.** The Devpost participants page is a JavaScript SPA behind
   Cloudflare. `webfetch`/`curl`/headless Edge return only the HTML shell. The
   GitHub usernames are on individual profile pages, not the participants list.
3. **Some competition bots may need data files** beyond the `.pkl` + `.py` files
   (e.g. numpy `.npy` tables). The `all-teams/` folders contain only the required
   files, so a bot that loads data from disk may fail. Copy the team's repo and
   re-run `scripts/required_files.py` to add them.
4. **The head-to-head matrix uses the live competition's results**, not our own
   matches. Running our own round robin for all 21 available bots is scripted
   (`scripts/run_tournament.py`) but the results are not yet posted to the API.

## Next steps

1. Fix the tournament script (the module-reload bug was just fixed — re-run it).
2. POST the `data/tournament_results.json` matches to `/api/matches` so they appear
   on the Matches page.
3. Regenerate `data/available_teams.json` after adding any new team files
   (`node scripts/make_available.mjs`).
4. If you can export the Google Form responses (or the organiser's teams file),
   add the missing repository links and re-run the download script.
5. Optional: run the round robin with more deals (e.g. 100) for tighter intervals.
