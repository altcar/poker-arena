# New session — start here

Read this first. It summarises the whole project so a fresh conversation can
pick up where this one left off.

## Project

A playable web app at **https://poker-arena.hheylau.workers.dev** where you sit at a
six-handed no-limit Texas hold'em table with the five bots from the
[altcar/poker_bot](https://github.com/altcar/poker_bot) fork, plus the competition's
teams, matches, head-to-head and teams pages.

- **Repo:** https://github.com/altcar/poker-arena (public)
- **Folder:** `C:\Users\c85-lau\OneDrive - UWE Bristol\poker-arena`
- **Worker:** `poker-arena`, account `Shalev` (`4012f7f23d815ed28c0f45e8ce57cd3e`)
- **D1:** `poker-arena` (`dfe5a016-1a78-4de0-86e4-bac69e0f71f9`)
- **Latest commit:** `dc0e6c5 Post the 21-bot round robin results`

## The five bots

| Key | Name | Plan | Tier | Style |
|---|---|---|---|---|
| `conservative` | Conservative | A | 1 | Tight hand-strength heuristic |
| `workhorse` | Workhorse | B | 2 | The submitted `goldman-stanley.pkl` |
| `adaptive` | Adaptive | C | 3 | + opponent counters |
| `mixed` | Mixed | D | 4 | + texture / MDF / bluffs |
| `aggressive` | Aggressive | E | 5 | + population exploits |

Recovered from the fork's git history (commit `14bc9b9`); the final `main` only
has variant B.

## Architecture

- `src/` — JS engine (line-for-line port of the Python `engine.py`,
  `game_state.py`, `card_utils.py`, `player_action.py`, `policy.py`, `botcore.py`),
  the five bots, the serialisable `Game`, and the Worker.
- `src/pyrandom.js` — Python MT19937 + `random.sample`, so the JS Monte-Carlo
  draws match the Python model exactly.
- `src/worker.js` — static assets + JSON API.
- `public/` — SPA (`index.html`, `app.js`, `style.css`, `data/`).
- `python/` — original Python source + `goldman-stanley.pkl` + `model_a.py`…`model_e.py`.
- `all-teams/` — each bot's `.pkl` and the `.py` files it imports.
- `scripts/` — verification, tournament, CSV and team-download tools.
- `data/` — generated dumps (`all-teams.csv`, `available_teams.json`,
  `all-teams-report.json`, `tournament_results.json`, …).

## Verification (all passing)

- `npm run verify` — 100 hands, chips conserved, no faults, all actions legal.
- JS vs Python: **50/50 hands identical**.
- `pytest tests/test_model.py` in `poker_bot/` — 9 passed.
- `node scripts/test_api.mjs` — API smoke test passes against the deployed Worker.

## Deployment (both verified)

1. **Cloudflare Workers Builds** — git-connected to `altcar/poker-arena` on `main`,
   deploy command `npx wrangler deploy`. Pushes auto-build and deploy.
2. **GitHub Actions** — `.github/workflows/deploy.yml`, needs the
   `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` secrets.

## What is done

- [x] Playable 6-handed table (you + five bots), bot selection, "I'm playing"
      toggle, 2–7 player tables.
- [x] API for the game and model decisions.
- [x] Teams page: My bots + All competition bots, with bb/100, 95% ±, Std,
      W–D–L, faults, ✓/✗ availability, and each team linking to its GitHub repo.
- [x] Head-to-head page for all available models.
- [x] Matches page: 10 static + 153 live round-robin matches (21 bots, 20 deals).
- [x] Tournament page removed.
- [x] `data/all-teams.csv` — 68 teams with GitHub repo URLs.
- [x] `all-teams/` — 21 bots' files (5 fork + 16 competition teams).

## Known limitations

1. **52 of 68 competition teams' files are missing.** The submission form that held
   the repo links is private. Export the Google Form responses →
   `data/team_repos.json` → `node scripts/download_team_files.mjs`.
2. **Devpost scraping.** The participants page is a JS SPA behind Cloudflare.
   UIA (via `System.Windows.Automation` in PowerShell) read a profile's GitHub
   link (`https://github.com/You-now-Who`) and the participant count (439), but the
   participants list itself is client-rendered and the tab kept being discarded.
3. **Some competition bots need extra data files** (`guinnessssss` →
   `lab/data/preflop_eq.npy`, `study-holic`, `manchester_fried_chicken`).
4. **The head-to-head matrix uses the live competition's results**, not our own
   matches.

## Next steps

1. Export the Google Form responses; add the missing repo links; re-run
   `node scripts/download_team_files.mjs`.
2. Add the missing data files for the skipped bots.
3. Optional: re-run the round robin with more deals
   (`python scripts/run_tournament.py 100`) for tighter intervals.
4. Optional: post the round robin results to the head-to-head page instead of the
   live competition's matrix.

## Key commands

```sh
cd "C:\Users\c85-lau\OneDrive - UWE Bristol\poker-arena"
npm run dev          # wrangler dev
npm run deploy       # wrangler deploy
npm run verify       # engine invariants
node scripts/test_api.mjs   # API smoke test
```
